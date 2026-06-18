"""Detección de bloqueo y CAPTCHA, y hook de solver. "Prevención primero". Ver ADR-006.

La estrategia del build doc es prevenir antes que resolver: un tier stealth bien
hecho (TLS realista, browser indetectable) hace que el CAPTCHA nunca aparezca. Por
eso esto vive en dos partes:

1. DETECCIÓN — barata, corre en cada respuesta de cualquier tier. Si huele a desafío
   anti-bot, el fetcher levanta BlockedError/CaptchaError y el router ESCALA. Subir de
   tier suele bastar; el solver es el último recurso.
2. SOLVER — interfaz pluggable. `none` (default) no resuelve, solo escala. `external`
   delega a un servicio de resolución por API (2captcha-style) si se configura.
"""
from __future__ import annotations

import re

# Marcadores por proveedor anti-bot. Conservador: matchear señales fuertes, no
# cualquier mención de la palabra "captcha" en una nota sobre seguridad.
_VENDOR_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("cloudflare", re.compile(r"cf-chl|challenge-platform|/cdn-cgi/challenge|just a moment\.\.\.", re.I)),
    ("recaptcha", re.compile(r"g-recaptcha|recaptcha/api\.js|grecaptcha", re.I)),
    ("hcaptcha", re.compile(r"\bh-captcha\b|hcaptcha\.com/captcha|js\.hcaptcha\.com", re.I)),
    ("datadome", re.compile(r"captcha-delivery\.com|datadome", re.I)),
    ("perimeterx", re.compile(r"px-captcha|_pxhd|human/challenge|perimeterx", re.I)),
    ("arkose", re.compile(r"funcaptcha|arkoselabs", re.I)),
]

# Headers que delatan un WAF/anti-bot aunque el body venga ofuscado.
_BLOCK_HEADERS = {
    "cf-mitigated": "cloudflare",
    "x-datadome": "datadome",
    "x-datadome-cid": "datadome",
    "x-px-": "perimeterx",
}

# Status que, salvo prueba en contrario, significan bloqueo anti-bot.
_BLOCK_STATUS = {403, 429}
_CHALLENGE_STATUS = {503}  # Cloudflare usa 503 para el "Just a moment".


def detect_captcha(text: str, headers: dict | None = None) -> str | None:
    """Devuelve el proveedor del CAPTCHA si el contenido es un desafío, o None."""
    hay = (text or "")[:200_000]  # topea el escaneo en páginas enormes
    for vendor, pat in _VENDOR_PATTERNS:
        if pat.search(hay):
            return vendor
    return None


def _header_block_signal(headers: dict | None) -> str | None:
    if not headers:
        return None
    low = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    for key, vendor in _BLOCK_HEADERS.items():
        if key.endswith("-"):  # prefijo (ej. x-px-*)
            if any(h.startswith(key) for h in low):
                return vendor
        elif key in low:
            return vendor
    server = low.get("server", "")
    if "cloudflare" in server and low.get("cf-mitigated"):
        return "cloudflare"
    return None


def classify(
    status_code: int,
    headers: dict | None,
    text: str,
    *,
    min_content_len: int = 0,
) -> tuple[str, str]:
    """Clasifica una respuesta. Devuelve (clase, señal).

    clase ∈ {"ok", "captcha", "blocked"}:
      - "captcha"  → desafío detectado; señal = proveedor.
      - "blocked"  → bloqueo anti-bot sin captcha explícito; señal = motivo.
      - "ok"       → seguir normal.
    """
    vendor = detect_captcha(text, headers)
    if vendor:
        return "captcha", vendor

    hdr = _header_block_signal(headers)
    if hdr:
        return "blocked", f"waf:{hdr}"

    if status_code in _BLOCK_STATUS:
        return "blocked", f"status:{status_code}"
    if status_code in _CHALLENGE_STATUS:
        return "blocked", f"status:{status_code}"

    # Soft block: 200 pero cuerpo sospechosamente vacío para una página real.
    if status_code == 200 and min_content_len and len(text.strip()) < min_content_len:
        return "blocked", "empty_body"

    return "ok", ""


# ---------------------------------------------------------------------------
# Solver pluggable. Prevención primero: el default no resuelve, solo deja escalar.
# ---------------------------------------------------------------------------
class CaptchaSolver:
    """Interfaz de solver. Un tier con browser le pasa el desafío detectado."""

    name = "base"

    def can_solve(self, vendor: str) -> bool:
        return False

    def solve(self, *, vendor: str, url: str, sitekey: str | None = None) -> str | None:
        """Devuelve el token de la solución, o None si no puede. No levanta."""
        return None


class NoopSolver(CaptchaSolver):
    """No resuelve nada. La defensa es escalar de tier (prevención)."""

    name = "none"


class ExternalSolver(CaptchaSolver):
    """Delega a un servicio externo de resolución por API (estilo 2captcha).

    Hook: se activa con CAPTCHA_SOLVER=external + URL/API key. La integración fina
    (formato del request por proveedor) se completa al sumar un tier con browser que
    extraiga el sitekey; acá queda el cableado y el fail-safe.
    """

    name = "external"
    _SUPPORTED = frozenset({"recaptcha", "hcaptcha", "arkose"})

    def __init__(self, api_url: str, api_key: str, *, timeout_s: float = 120.0) -> None:
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s

    def can_solve(self, vendor: str) -> bool:
        return bool(self.api_url and self.api_key) and vendor in self._SUPPORTED

    def solve(self, *, vendor: str, url: str, sitekey: str | None = None) -> str | None:
        if not self.can_solve(vendor) or not sitekey:
            return None
        import httpx

        try:
            resp = httpx.post(
                f"{self.api_url}/solve",
                json={"vendor": vendor, "url": url, "sitekey": sitekey, "key": self.api_key},
                timeout=self.timeout_s,
            )
            if resp.is_success:
                return (resp.json() or {}).get("token")
        except httpx.HTTPError:
            return None
        return None


def build_solver(kind: str, *, api_url: str = "", api_key: str = "") -> CaptchaSolver:
    if (kind or "none").lower() == "external":
        return ExternalSolver(api_url, api_key)
    return NoopSolver()
