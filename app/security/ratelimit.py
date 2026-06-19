"""Rate-limit de admisión de jobs. Ver auditoría 2026-06.

Ventana fija por minuto sobre Redis (INCR + EXPIRE): barato, suficiente para frenar
un flood de POST /api/jobs (cada job puede arrastrar un crawl o un browser). Falla
ABIERTO si Redis no responde: nunca debe tumbar la admisión por un hipo de Redis.

No reemplaza un WAF/reverse-proxy con rate-limit de red; es la última red de
contención dentro de la app, compartida por el REST y el MCP.
"""
from __future__ import annotations

import time

from ..logging import get_logger

log = get_logger("fisherboy.ratelimit")

_PREFIX = "fisherboy:rl:"


def allow(redis_client, bucket: str, *, limit: int, window_s: int = 60) -> bool:
    """¿Se permite una acción más en esta ventana? Cuenta el intento si sí.

    limit<=0 desactiva el límite. Falla abierto ante cualquier error de Redis.
    """
    if limit <= 0 or redis_client is None:
        return True
    window = int(time.time() // window_s)
    key = f"{_PREFIX}{bucket}:{window}"
    try:
        n = redis_client.incr(key)
        if n == 1:
            redis_client.expire(key, window_s)
        return int(n) <= limit
    except Exception:  # noqa: BLE001 — un Redis caído no debe bloquear la admisión
        log.warning("rate-limit no disponible (Redis); admitiendo igual")
        return True
