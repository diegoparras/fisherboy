"""Autenticación y roles. Espejo de Escriba (markitdown-web/app/auth.py).

Tres roles con privilegios crecientes: humano < angel < dios. Login por contraseña
(una por rol, por entorno), sesión firmada con HMAC-SHA256 sin dependencias. Token en
cookie HttpOnly (UI) o `Authorization: Bearer` (REST/MCP).

Usa los MISMOS nombres de entorno que Escriba (GOD_PASSWORD/ANGEL_PASSWORD/
HUMAN_PASSWORD + SECRET_KEY): así la misma clave sirve en los dos y se siente la misma
familia. Las CAPACIDADES por rol gatean las armas caras (browser, proxies, captura, solver).

FAIL-CLOSED por defecto (auditoría 2026-06): si no hay contraseñas configuradas, el
acceso se RECHAZA (401), no se concede como dios. El modo abierto de dev exige un opt-in
EXPLÍCITO y ruidoso:
  - HUMAN_OPEN=1          → todo entra como 'humano' (modo demo acotado).
  - FISHERBOY_OPEN_GOD=1  → todo entra como 'dios' (SOLO dev local; NUNCA en prod).
Sin ninguno de los dos y sin contraseñas, role_from_request devuelve None.
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
API_TOKEN = os.getenv("API_TOKEN")
API_TOKEN_ROLE = os.getenv("API_TOKEN_ROLE", "angel")

_TRUTHY = ("1", "true", "yes", "on")


def _env_truthy(name: str) -> bool:
    """Lee un flag de entorno EN CADA LLAMADA (no al importar): así el devserver/tests
    pueden setearlo antes de usarlo sin pelear con el orden de import."""
    return (os.getenv(name, "") or "").strip().lower() in _TRUTHY


def human_open() -> bool:
    return _env_truthy("HUMAN_OPEN")


def open_god() -> bool:
    """Modo abierto como dios: SOLO dev local, opt-in explícito. Nunca en prod."""
    return _env_truthy("FISHERBOY_OPEN_GOD")

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


class CapDenied(Exception):
    """El rol (o el modo de deploy) no habilita la capacidad pedida."""


# Armas que, además del rol, NUNCA deben correr en modo sidekick/servidor: ahí el
# "navegador local" y la araña profunda no son del usuario sino del host. Ver
# browser_cookies.py y la auditoría 2026-06.
def enforce_job_caps(role: str, req, *, is_sidekick: bool = False) -> None:
    """Gating de capacidades por rol (y por modo de deploy). Lanza CapDenied.

    Es la MISMA puerta para el REST y el MCP: una sola fuente de verdad evita que
    un camino (p.ej. MCP) quede sin gatear como pasaba antes de la auditoría.
    """
    caps = caps_for(role)

    def deny(msg: str) -> None:
        raise CapDenied(f"Tu rol '{role}' no habilita {msg}.")

    if req.tier_hint is not None and int(req.tier_hint) > caps["max_tier"]:
        deny(f"el tier {int(req.tier_hint)} (máx {caps['max_tier']})")
    if req.capture_api and not caps["capture"]:
        deny("capturar API/XHR")
    if req.proxy and not caps["proxy"]:
        deny("proxy propio")
    if req.captcha_api_url and req.captcha_api_key and not caps["solver"]:
        deny("solver de CAPTCHA")
    if req.crawl_depth and not caps["crawl"]:
        deny("crawling multipágina")
    if req.paginate and not caps["paginate"]:
        deny("barrer paginado")
    if req.tarantula and (not caps.get("tarantula") or is_sidekick):
        deny("la araña profunda (tarántula)" + (" en modo sidekick" if is_sidekick else ""))
    if req.cookies_browser and (role != "dios" or is_sidekick):
        deny("leer las cookies de tu navegador" + (" en modo sidekick" if is_sidekick else ""))


def insecure_open_warning() -> str | None:
    """Si el servicio quedaría abierto sin auth, devuelve un texto de advertencia
    para loguear ruidosamente en el arranque (fail-closed igual lo protege con 401)."""
    if auth_enabled() or API_TOKEN:
        return None
    if open_god():
        return ("FISHERBOY_OPEN_GOD=1: acceso ABIERTO como dios SIN autenticación. "
                "Solo para dev local — NUNCA en producción.")
    if human_open():
        return ("HUMAN_OPEN=1: acceso abierto como humano sin autenticación (modo demo).")
    return None


def secret_key_warning() -> str | None:
    """Avisa si hay auth pero SECRET_KEY no es persistente (rota sesiones entre
    réplicas/reinicios). No es forjable, pero rompe la auth en multi-worker."""
    if (auth_enabled() or API_TOKEN) and not os.getenv("SECRET_KEY"):
        return ("SECRET_KEY no seteada con auth activa: cada proceso firma con una clave "
                "distinta → sesiones inválidas entre réplicas y tras reiniciar. "
                "Seteá SECRET_KEY (igual en todos los workers).")
    return None


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(role: str) -> str:
    payload = {"role": role, "exp": int(time.time()) + SESSION_TTL, "jti": secrets.token_urlsafe(12)}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token_payload(token: str) -> dict | None:
    """Verifica firma+exp y devuelve el payload {role, exp, jti} o None."""
    try:
        body, sig = token.split(".", 1)
        expected = _b64e(hmac.new(_SECRET, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body))
        if payload.get("exp", 0) < time.time():
            return None
        if payload.get("role") not in ROLE_CAPS:
            return None
        return payload
    except Exception:  # noqa: BLE001 — token corrupto = sin sesión
        return None


def verify_token(token: str) -> str | None:
    payload = verify_token_payload(token)
    return payload.get("role") if payload else None


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


def identity_from_request(request) -> tuple[str | None, str | None]:
    """(rol, jti) del request. jti identifica la sesión (para ownership); None si
    el rol viene de API token o del modo abierto (no hay sesión individual).

    Orden: API token → cookie/Bearer firmado → modo abierto opt-in → (None, None).
    FAIL-CLOSED: sin credencial y sin opt-in explícito de dev, no hay acceso.
    """
    api = _api_role(request.headers)
    if api:
        return api, None
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    payload = verify_token_payload(token) if token else None
    if payload:
        return payload.get("role"), payload.get("jti")
    # Sin credencial válida: solo el opt-in EXPLÍCITO de dev abre la puerta.
    if not auth_enabled():
        if open_god():
            return "dios", None      # FISHERBOY_OPEN_GOD=1 — solo dev local
        if human_open():
            return "humano", None    # HUMAN_OPEN=1 — demo acotada
    return None, None


def role_from_request(request) -> str | None:
    """Rol efectivo del request (sin la identidad). Ver identity_from_request."""
    return identity_from_request(request)[0]
