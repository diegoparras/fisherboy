"""Logs JSON estructurados desde el día uno. Nunca PII ni contenido scrapeado.

ADR-004 punto 5: los logs no incluyen contenido sensible. Se loguea metadata del
job (job_id, url, tier, status, dominio) pero nunca el texto fetcheado ni el
resultado anonimizado. Los secretos viven en el entorno, jamás en un log.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

# Campos de log que son URLs: se les saca la querystring (puede llevar tokens, email,
# dni…). La política dice "nunca PII"; la query del target la trae el usuario. (Auditoría 2026-06)
_URL_FIELDS = frozenset({"url", "seed", "source_url", "final_url", "callback_url"})


def safe_url(u) -> str:
    """Devuelve scheme://host/path sin querystring ni fragment (no PII en logs)."""
    try:
        s = urlsplit(str(u))
        return f"{s.scheme}://{s.hostname}{s.path}" if s.scheme else str(u)
    except Exception:  # noqa: BLE001
        return "?"


class JsonFormatter(logging.Formatter):
    """Una línea JSON por evento. Los `extra=` del logger entran como campos."""

    _RESERVED = frozenset(
        vars(logging.makeLogRecord({})).keys()
        | {"message", "asctime", "taskName"}
    )

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                # Defensa en profundidad: redactar la query de cualquier campo URL.
                payload[key] = safe_url(val) if key in _URL_FIELDS and val else val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured = False


def setup_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
