"""Contrato común de fetch. Todos los tiers implementan `Fetcher`. Ver ADR-006.

El router escalona por costo: arranca en el tier más barato y sube solo cuando el
sitio bloquea. Para que el router sea ciego al tier, todos hablan el mismo idioma:
reciben una URL + `FetchContext` (proxy, timeouts, solver) y devuelven un
`FetchResult`, o levantan `BlockedError`/`CaptchaError` para pedir escalado.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class FetchError(Exception):
    """No se pudo traer la URL (red, status, tamaño, redirects). No escala por sí solo."""


class BlockedError(FetchError):
    """El destino bloqueó este tier (403/429 anti-bot, WAF, contenido vacío). Escalá."""

    def __init__(self, message: str, *, signal: str = "") -> None:
        super().__init__(message)
        self.signal = signal


class CaptchaError(BlockedError):
    """Se detectó un desafío CAPTCHA. Escalá a un tier que pueda resolverlo/prevenirlo."""

    def __init__(self, message: str, *, vendor: str = "") -> None:
        super().__init__(message, signal=f"captcha:{vendor}")
        self.vendor = vendor


@dataclass
class FetchContext:
    """Todo lo que un fetcher necesita por intento, inyectado por el router."""

    timeout_s: float = 20.0
    max_bytes: int = 10 * 1024 * 1024
    max_redirects: int = 5
    allow_private: bool = False
    user_agent: str = (
        "Mozilla/5.0 (compatible; Fisherboy/1.0; +https://github.com/diegoparras/fisherboy)"
    )
    proxy: str | None = None          # URL del proxy para este intento (lo elige el pool)
    method: str = "GET"               # GET | POST (POST para postback ASP.NET / forms)
    data: dict | None = None          # cuerpo del POST (form-urlencoded)
    headers: dict = field(default_factory=dict)
    solver: object | None = None      # CaptchaSolver, inyectado en tiers con browser
    cookies: dict = field(default_factory=dict)  # sesión por dominio (SessionStore)
    # Stealth de los browser tiers (2/3).
    headless: bool = True
    settle_s: float = 3.5             # espera tras cargar (deja asentar el JS anti-bot)
    scroll: bool = True               # scrollea para disparar contenido lazy
    locale: str = "es-AR"
    extra: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    url: str            # URL final tras redirects
    status_code: int
    content: bytes
    text: str
    content_type: str
    tier: int | None = None           # tier que efectivamente trajo el contenido
    proxy_used: str | None = None
    headers: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)


@runtime_checkable
class Fetcher(Protocol):
    """Un escalón de fetch. `tier` es su costo; `available()` dice si sus deps están."""

    tier: int
    name: str

    def available(self) -> bool:
        """True si las dependencias del tier están instaladas y se puede usar."""
        ...

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        """Trae la URL o levanta FetchError/BlockedError/CaptchaError."""
        ...
