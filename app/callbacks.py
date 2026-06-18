"""Retorno del resultado por webhook. SSRF de salida. Ver ADR-004 punto 3.

El `callback_url` lo provee el usuario, así que se valida contra los mismos bloques
que el fetch de entrada, más la allowlist de producción si está configurada. Se
re-valida acá aunque ya se haya validado al encolar: el DNS pudo cambiar.
"""
from __future__ import annotations

import httpx

from .logging import get_logger
from .models import Sobre
from .security.ssrf import SSRFError, validate_callback_url

log = get_logger("fisherboy.callback")


def post_callback(
    sobre: Sobre,
    callback_url: str,
    *,
    timeout_s: float = 15.0,
    allowlist: list[str] | None = None,
    allow_private: bool = False,
) -> bool:
    """POST del sobre serializado al callback. Devuelve True si entregó.

    No lanza: un fallo de callback no debe tumbar el worker ni reabrir el job.
    Solo se loguea (sin contenido sensible).
    """
    try:
        validate_callback_url(callback_url, allowlist=allowlist, allow_private=allow_private)
    except SSRFError as e:
        log.warning("callback bloqueado por SSRF", extra={"job_id": sobre.job_id, "reason": str(e)})
        return False

    payload = sobre.model_dump(mode="json")
    try:
        resp = httpx.post(callback_url, json=payload, timeout=timeout_s, follow_redirects=False)
        ok = resp.is_success
        log.info(
            "callback entregado" if ok else "callback rechazado",
            extra={"job_id": sobre.job_id, "status": resp.status_code},
        )
        return ok
    except httpx.HTTPError as e:
        log.warning("callback falló", extra={"job_id": sobre.job_id, "error": type(e).__name__})
        return False
