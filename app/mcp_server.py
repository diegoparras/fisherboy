"""Servidor MCP de Fisherboy. Disponible en los dos modos (ADR-001).

Expone el mismo pipeline que el REST como herramientas MCP, para que n8n, Claude
Code o Escriba encolen y consulten jobs sin hablar HTTP a mano. Comparte cola y
política con el gateway: la validación de rol×modo y SSRF es idéntica.

Correr: `python -m app.mcp_server` (requiere fastmcp instalado).
"""
from __future__ import annotations

import uuid

from .config import get_settings
from .logging import setup_logging
from .models import JobRequest, JobStatus, Sobre
from .privacy_policy import PolicyDenied, get_policy
from .queue import get_queue
from .security.ssrf import SSRFError, validate_callback_url


def build_server():
    try:
        from fastmcp import FastMCP
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "fastmcp no está instalado. Instalá con: pip install fastmcp"
        ) from e

    settings = get_settings()
    setup_logging(settings.log_level)
    policy = get_policy(settings.privacy_matrix_path)
    queue = get_queue(settings)

    mcp = FastMCP("fisherboy")

    @mcp.tool()
    def submit_job(
        url: str,
        rol: str = "humano",
        privacy_mode: str | None = None,
        output_format: str = "markdown",
        callback_url: str | None = None,
        tier_hint: int | None = None,
        crawl_depth: int = 0,
        max_pages: int = 1,
        extract_schema: dict | None = None,
        proxy: str | None = None,
        captcha_api_url: str | None = None,
        captcha_api_key: str | None = None,
        cookies: str | None = None,
    ) -> dict:
        """Encola un job de scraping. Devuelve job_id y status.

        Valida rol×modo y callback_url igual que el REST. Lanza ValueError si el
        rol no habilita el modo o el callback apunta a un destino prohibido.
        `tier_hint` (0-3) fuerza el tier de fetch mínimo. `crawl_depth`/`max_pages`
        habilitan el crawl multipágina. `extract_schema` (con output_format='json')
        dispara la extracción estructurada por LLM.
        """
        req = JobRequest(
            url=url,
            rol=rol,
            privacy_mode=privacy_mode,
            output_format=output_format,
            callback_url=callback_url,
            tier_hint=tier_hint,
            crawl_depth=crawl_depth,
            max_pages=max_pages,
            extract_schema=extract_schema,
            proxy=proxy,
            captcha_api_url=captcha_api_url,
            captcha_api_key=captcha_api_key,
        )
        try:
            mode = policy.resolve_mode(req.rol, req.privacy_mode)
        except PolicyDenied as e:
            raise ValueError(str(e))
        if req.callback_url is not None:
            try:
                validate_callback_url(
                    str(req.callback_url),
                    allowlist=settings.callback_allowlist,
                    allow_private=settings.allow_private_targets,
                )
            except SSRFError as e:
                raise ValueError(f"callback_url inválido: {e}")

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
        if req.cookies:
            sobre.meta["cookies"] = req.cookies
        queue.enqueue(sobre)
        return {"job_id": job_id, "status": JobStatus.PENDIENTE.value}

    @mcp.tool()
    def get_job(job_id: str) -> dict:
        """Devuelve el sobre de un job (estado y resultado), o un error si no existe."""
        sobre = queue.get(job_id)
        if sobre is None:
            return {"error": "job no encontrado", "job_id": job_id}
        return sobre.model_dump(mode="json")

    @mcp.tool()
    def revert(content: str, mapping_ref: str, rol: str = "humano") -> dict:
        """Rehidrata contenido pseudonimizado (modo reversible). Valida rol; un solo uso."""
        from .models import Rol
        from .privacy.reversible import build_reversible_anonymizer

        try:
            rev = build_reversible_anonymizer(settings, _anon_for_revert(), policy=policy, redis_client=queue._r)
            return {"content": rev.revert(content, mapping_ref, Rol(rol))}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)}

    return mcp


def _anon_for_revert():
    from .config import get_settings
    from .privacy.anonimal_client import AnonimalClient

    s = get_settings()
    return AnonimalClient(s.anonimal_url, service_token=s.anonimal_token)


def run() -> None:
    build_server().run()


if __name__ == "__main__":
    run()
