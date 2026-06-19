"""Autenticación y roles. Espejo de Escriba (markitdown-web/app/auth.py).

Tres roles con privilegios crecientes: humano < angel < dios. Login por contraseña
(una por rol, por entorno), sesión firmada con HMAC-SHA256 sin dependencias. Token en
cookie HttpOnly (UI) o `Authorization: Bearer` (REST/MCP).

Usa los MISMOS nombres de entorno que Escriba (GOD_PASSWORD/ANGEL_PASSWORD/
HUMAN_PASSWORD + SECRET_KEY): así la misma clave sirve en los dos y se siente la misma
familia. Si no hay contraseñas y HUMAN_OPEN=1, todo entra como humano (dev/standalone
abierto). Las CAPACIDADES por rol gatean las armas caras (browser, proxies, captura, solver).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from ..logging import get_logger

log = get_logger("fisherboy.auth")

COOKIE_NAME = "fb_session"
SESSION_TTL = int(os.getenv("SESSION_TTL_HOURS", "12")) * 3600

SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_hex(32)
_SECRET = SECRET_KEY.encode()

_PASSWORDS = {
    "dios": os.getenv("GOD_PASSWORD"),
    "angel": os.getenv("ANGEL_PASSWORD"),
    "humano": os.getenv("HUMAN_PASSWORD"),
}
HUMAN_OPEN = (os.getenv("HUMAN_OPEN", "") or "").strip().lower() in ("1", "true", "yes", "on")
API_TOKEN = os.getenv("API_TOKEN")
API_TOKEN_ROLE = os.getenv("API_TOKEN_ROLE", "angel")

# Capacidades por rol: gatean las armas caras (ADR-011, decisión del dueño).
ROLE_CAPS = {
    "dios":   {"max_tier": 3, "proxy": True,  "capture": True,  "solver": True,  "crawl": True,  "paginate": True,  "tarantula": True},
    "angel":  {"max_tier": 2, "proxy": True,  "capture": True,  "solver": False, "crawl": True,  "paginate": True,  "tarantula": False},
    "humano": {"max_tier": 1, "proxy": False, "capture": False, "solver": False, "crawl": False, "paginate": True,  "tarantula": False},
}


def auth_enabled() -> bool:
    """True si hay alguna contraseña configurada (si no, modo abierto/dev)."""
    return any(_PASSWORDS.values())


def caps_for(role: str) -> dict:
    return ROLE_CAPS.get(role, ROLE_CAPS["humano"])


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(role: str) -> str:
    payload = {"role": role, "exp": int(time.time()) + SESSION_TTL, "jti": secrets.token_urlsafe(12)}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str) -> str | None:
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            return None
        role = payload.get("role")
        return role if role in ROLE_CAPS else None
    except Exception:  # noqa: BLE001 — token corrupto = sin sesión
        return None


def role_for_password(password: str) -> str | None:
    """Compara la clave (tiempo constante) contra cada rol configurado."""
    match = None
    for role, real in _PASSWORDS.items():
        if real and hmac.compare_digest(password, real):
            match = role
    return match


def _api_role(headers) -> str | None:
    if not API_TOKEN:
        return None
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and hmac.compare_digest(auth[7:].strip(), API_TOKEN):
        return API_TOKEN_ROLE if API_TOKEN_ROLE in ROLE_CAPS else "angel"
    xkey = headers.get("x-api-key", "")
    if xkey and hmac.compare_digest(xkey, API_TOKEN):
        return API_TOKEN_ROLE if API_TOKEN_ROLE in ROLE_CAPS else "angel"
    return None


def role_from_request(request) -> str | None:
    """Rol efectivo del request: API token → cookie/Bearer → HUMAN_OPEN → None."""
    api = _api_role(request.headers)
    if api:
        return api
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    role = verify_token(token) if token else None
    if role:
        return role
    if not auth_enabled() and HUMAN_OPEN:
        return "humano"
    if not auth_enabled():
        # Sin auth configurada: standalone abierto como dios (dev local).
        return "dios"
    return None
