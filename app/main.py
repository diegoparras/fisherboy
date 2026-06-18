"""Capa 0 — superficie REST de Fisherboy. Ver FISHERBOY-build §10.

Tres endpoints en v1: encolar job, consultar job, health. El router de UI se monta
solo si APP_MODE=standalone (ADR-001). El REST queda disponible en los dos modos.

Orden de validación en POST /api/jobs (no negociable, ADR-004):
  1. schema (Pydantic valida JobRequest)
  2. rol × modo (privacy_policy; 403 si el rol no habilita el modo)
  3. callback_url contra bloques SSRF (400 si apunta a destino prohibido)
  4. recién entonces se encola
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException, Response

from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .logging import get_logger, setup_logging
from .models import JobRequest, JobStatus, RevertRequest, Sobre
from .privacy_policy import PolicyDenied, PrivacyPolicy, get_policy
from .queue import JobQueue, get_queue
from .security.ssrf import SSRFError, validate_callback_url

log = get_logger("fisherboy.api")


def create_app(
    settings: Settings | None = None,
    *,
    queue: JobQueue | None = None,
    policy: PrivacyPolicy | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    app = FastAPI(title="Fisherboy", version="1.0.0")
    app.state.settings = settings
    app.state.queue = queue
    app.state.policy = policy or get_policy(settings.privacy_matrix_path)

    def _queue() -> JobQueue:
        if app.state.queue is None:  # construcción perezosa: el worker/redis no hace falta en tests
            app.state.queue = get_queue(settings)
        return app.state.queue

    app.state.reversible = None
    app.state.reversible_built = False

    def _reversible():
        if not app.state.reversible_built:
            app.state.reversible_built = True
            try:
                from .privacy.anonimal_client import AnonimalClient
                from .privacy.reversible import build_reversible_anonymizer
                client = AnonimalClient(settings.anonimal_url, service_token=settings.anonimal_token)
                redis_client = None
                try:
                    from .queue import build_redis
                    redis_client = build_redis(settings.redis_url)
                except Exception:  # noqa: BLE001
                    redis_client = None
                app.state.reversible = build_reversible_anonymizer(
                    settings, client, policy=app.state.policy, redis_client=redis_client
                )
            except Exception as exc:  # noqa: BLE001 — sin cripto, reversible queda None
                log.warning("reversible no disponible", extra={"error": type(exc).__name__})
                app.state.reversible = None
        return app.state.reversible

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "app_mode": settings.app_mode.value, "version": "1.0.0"}

    @app.post("/api/jobs", status_code=202)
    async def create_job(req: JobRequest):
        # 2. rol × modo
        try:
            mode = app.state.policy.resolve_mode(req.rol, req.privacy_mode)
        except PolicyDenied as e:
            raise HTTPException(status_code=403, detail=str(e))

        # 3. callback_url contra bloques SSRF
        if req.callback_url is not None:
            try:
                validate_callback_url(
                    str(req.callback_url),
                    allowlist=settings.callback_allowlist,
                    allow_private=settings.allow_private_targets,
                )
            except SSRFError as e:
                raise HTTPException(status_code=400, detail=f"callback_url inválido: {e}")

        # 4. encolar
        job_id = uuid.uuid4().hex
        sobre = Sobre(
            job_id=job_id,
            source_url=req.url,
            privacy_mode=mode,
            rol=req.rol,
            output_format=req.output_format,
        )
        if req.callback_url is not None:
            sobre.meta["callback_url"] = str(req.callback_url)
        if req.tier_hint is not None:
            sobre.meta["tier_hint"] = int(req.tier_hint)
        sobre.meta["crawl_depth"] = int(req.crawl_depth)
        sobre.meta["max_pages"] = int(req.max_pages)
        if req.extract_schema is not None:
            sobre.meta["extract_schema"] = req.extract_schema
        if req.proxy:
            sobre.meta["proxy"] = req.proxy
        if req.captcha_api_url and req.captcha_api_key:
            sobre.meta["captcha_api_url"] = req.captcha_api_url
            sobre.meta["captcha_api_key"] = req.captcha_api_key
        _queue().enqueue(sobre)

        log.info("job encolado", extra={"job_id": job_id, "mode": mode.value, "rol": req.rol.value})
        return {"job_id": job_id, "status": JobStatus.PENDIENTE.value}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        sobre = _queue().get(job_id)
        if sobre is None:
            raise HTTPException(status_code=404, detail="job no encontrado")
        return JSONResponse(sobre.model_dump(mode="json"))

    @app.post("/api/revert")
    async def revert(req: "RevertRequest"):
        """Rehidrata contenido pseudonimizado con un mapping_ref (modo reversible).

        Valida el rol contra el que creó el mapeo y contra la matriz. Un solo uso:
        el mapeo se borra tras revertir. Ver ADR-005.
        """
        rev = _reversible()
        if rev is None:
            raise HTTPException(status_code=503, detail="Modo reversible no disponible (falta cripto).")
        try:
            content = rev.revert(req.content, req.mapping_ref, req.rol)
        except Exception as e:  # ReversibleError u otra falla controlada
            raise HTTPException(status_code=403, detail=str(e))
        return {"content": content}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        from .obs.metrics import get_metrics

        body, ctype = get_metrics().render()
        return Response(content=body, media_type=ctype)

    # UI solo en standalone (ADR-001): el núcleo REST/MCP queda en los dos modos.
    if settings.is_standalone:
        from .ui.router import build_ui_router

        app.include_router(build_ui_router())

    return app


app = create_app()
