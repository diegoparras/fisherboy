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

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .logging import get_logger, setup_logging
from .models import JobRequest, JobStatus, Rol, RevertRequest, Sobre
from .privacy_policy import PolicyDenied, PrivacyPolicy, get_policy
from .queue import JobQueue, get_queue
from .security import auth, ratelimit
from .security.ssrf import SSRFError, validate_callback_url, validate_proxy_url

log = get_logger("fisherboy.api")

_RANK = {"humano": 0, "angel": 1, "dios": 2}


class LoginRequest(BaseModel):
    key: str


def create_app(
    settings: Settings | None = None,
    *,
    queue: JobQueue | None = None,
    policy: PrivacyPolicy | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    # Avisos de arranque ruidosos (fail-closed igual protege con 401, pero que se vea).
    _warns = [auth.insecure_open_warning(), auth.secret_key_warning()]
    if settings.allow_private_targets:
        _warns.append("ALLOW_PRIVATE_TARGETS activo: la defensa SSRF está DESACTIVADA "
                      "(fetch y callback pueden alcanzar la red interna/metadata). Solo dev/test.")
    for _w in _warns:
        if _w:
            log.warning("SEGURIDAD: %s", _w)

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

    # --- Auth (espejo de Escriba): login por rol, cookie + Bearer ---------------
    @app.post("/api/login")
    async def login(body: LoginRequest):
        role = auth.role_for_password(body.key)
        if role is None:
            raise HTTPException(status_code=401, detail="Clave inválida.")
        resp = JSONResponse({"role": role, "caps": auth.caps_for(role)})
        resp.set_cookie(auth.COOKIE_NAME, auth.make_token(role), httponly=True,
                        secure=settings.cookie_secure, samesite="lax",
                        max_age=auth.SESSION_TTL, path="/")
        return resp

    @app.post("/api/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE_NAME, path="/", samesite="lax",
                           secure=settings.cookie_secure)
        return resp

    @app.get("/api/me")
    async def me(request: Request):
        role = auth.role_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        return {"role": role, "caps": auth.caps_for(role), "auth_enabled": auth.auth_enabled()}

    def _effective_role(request: Request, body_rol, session: str | None = None) -> str:
        """Rol de sesión; si el body pide un rol IGUAL o MENOR, se respeta (downgrade).
        Nunca se puede escalar por encima del rol de sesión (anti-escalada)."""
        if session is None:
            session = auth.role_from_request(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if body_rol is not None and _RANK.get(body_rol.value, 9) <= _RANK[session]:
            return body_rol.value
        return session

    @app.post("/api/jobs", status_code=202)
    async def create_job(req: JobRequest, request: Request):
        # 1. autenticación + rol efectivo (de la sesión, no escalable) + identidad
        session_role, owner_jti = auth.identity_from_request(request)
        role = _effective_role(request, req.rol, session_role)
        # 1b. rate-limit de admisión por IP (anti-flood; falla abierto si no hay Redis)
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"jobs:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiados jobs; probá en un minuto.")
        rol_enum = Rol(role)
        caps = auth.caps_for(role)

        # 2. rol × modo de privacidad (matriz)
        try:
            mode = app.state.policy.resolve_mode(rol_enum, req.privacy_mode)
        except PolicyDenied as e:
            raise HTTPException(status_code=403, detail=str(e))

        # 2b. gating de capacidades (mismo helper que el MCP; incluye veto por sidekick)
        try:
            auth.enforce_job_caps(role, req, is_sidekick=settings.is_sidekick)
        except auth.CapDenied as e:
            raise HTTPException(status_code=403, detail=str(e))

        # 3. callback_url + proxy contra bloques SSRF
        if req.callback_url is not None:
            try:
                validate_callback_url(
                    str(req.callback_url),
                    allowlist=settings.callback_allowlist,
                    allow_private=settings.allow_private_targets,
                )
            except SSRFError as e:
                raise HTTPException(status_code=400, detail=f"callback_url inválido: {e}")
        if req.proxy:
            try:
                validate_proxy_url(req.proxy, allow_private=settings.allow_private_targets)
            except SSRFError as e:
                raise HTTPException(status_code=400, detail=f"proxy inválido: {e}")

        # 4. encolar
        job_id = uuid.uuid4().hex
        sobre = Sobre(
            job_id=job_id,
            source_url=req.url,
            privacy_mode=mode,
            rol=rol_enum,
            output_format=req.output_format,
        )
        sobre.meta["max_tier"] = caps["max_tier"]   # cap del escalado automático por rol
        if owner_jti:
            sobre.meta["owner_jti"] = owner_jti      # dueño de la sesión (ownership en lectura)
        if req.callback_url is not None:
            sobre.meta["callback_url"] = str(req.callback_url)
        if req.tier_hint is not None:
            sobre.meta["tier_hint"] = int(req.tier_hint)
        sobre.meta["crawl_depth"] = int(req.crawl_depth)
        sobre.meta["max_pages"] = min(int(req.max_pages), settings.crawl_max_pages)  # tope duro
        if req.paginate:
            sobre.meta["paginate"] = True
        if req.crawl_scope == "path":
            sobre.meta["crawl_scope"] = "path"
        if req.capture_api:
            sobre.meta["capture_api"] = True
        if req.tarantula:
            sobre.meta["tarantula"] = True
        if req.extract_schema is not None:
            sobre.meta["extract_schema"] = req.extract_schema
        if req.proxy:
            sobre.meta["proxy"] = req.proxy
        if req.captcha_api_url and req.captcha_api_key:
            sobre.meta["captcha_api_url"] = req.captcha_api_url
            sobre.meta["captcha_api_key"] = req.captcha_api_key
        if req.cookies:
            sobre.meta["cookies"] = req.cookies
        if req.cookies_browser:
            sobre.meta["cookies_browser"] = req.cookies_browser
        _queue().enqueue(sobre)

        log.info("job encolado", extra={"job_id": job_id, "mode": mode.value, "rol": role})
        return {"job_id": job_id, "status": JobStatus.PENDIENTE.value}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        role, jti = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        sobre = _queue().get(job_id)
        if sobre is None:
            raise HTTPException(status_code=404, detail="job no encontrado")
        # Ownership: si el job tiene dueño y no sos vos (ni dios), 404 (no filtra existencia).
        owner = sobre.meta.get("owner_jti")
        if owner and role != "dios" and owner != jti:
            raise HTTPException(status_code=404, detail="job no encontrado")
        # Nunca devolver los secretos por-job (proxy/captcha/cookies). Ver public_dump.
        return JSONResponse(sobre.public_dump(mode="json"))

    @app.post("/api/revert")
    async def revert(req: "RevertRequest", request: Request):
        """Rehidrata contenido pseudonimizado con un mapping_ref (modo reversible).

        El rol sale de la SESIÓN (no del body, sin downgrade): valida contra el dueño del
        mapeo y la matriz. Un solo uso: el mapeo se borra tras revertir. Ver ADR-005.
        """
        role = auth.role_from_request(request)   # NO _effective_role: el downgrade del body
        if role is None:                          # permitiría a un rol alto hacerse pasar por el owner
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        rev = _reversible()
        if rev is None:
            raise HTTPException(status_code=503, detail="Modo reversible no disponible (falta cripto).")
        try:
            content = rev.revert(req.content, req.mapping_ref, Rol(role))
        except Exception as e:  # ReversibleError u otra falla controlada
            raise HTTPException(status_code=403, detail=str(e))
        return {"content": content}

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        # Si hay auth configurada, /metrics también la exige (no exponer telemetría abierta).
        if auth.auth_enabled() and auth.role_from_request(request) is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        from .obs.metrics import get_metrics

        body, ctype = get_metrics().render()
        return Response(content=body, media_type=ctype)

    # UI solo en standalone (ADR-001): el núcleo REST/MCP queda en los dos modos.
    if settings.is_standalone:
        from .ui.router import build_ui_router

        app.include_router(build_ui_router())

    return app


app = create_app()
