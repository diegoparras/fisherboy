"""El Sobre y los enums. Contrato compartido: todo el sistema lo importa.

Es la única estructura que viaja entre capas (REST → cola → worker → callback) y
entre servicios. Si algo cambia acá, cambia en todos lados; por eso vive solo y
sin dependencias de las demás capas. Ver FISHERBOY-build.md §3 y ADR-001.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, IntEnum

from pydantic import BaseModel, Field, HttpUrl


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PrivacyMode(str, Enum):
    """Modo de privacidad a nivel de job. Acotado por el rol. Ver ADR-002."""

    REVERSIBLE = "reversible"
    OPACO = "opaco"
    DIRECTO = "directo"


class Rol(str, Enum):
    """Rol del solicitante. Decide qué modos de privacidad habilita. Ver privacy_matrix.yaml."""

    DIOS = "dios"
    ANGEL = "angel"
    HUMANO = "humano"


class FetchTier(IntEnum):
    """Escalón de fetch por costo. v1 solo usa ESTATICO; el resto entra por fases."""

    ESTATICO = 0
    TLS = 1
    STEALTH = 2
    BROWSER = 3


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    LLMS_TXT = "llms_txt"


class JobStatus(str, Enum):
    PENDIENTE = "pendiente"
    EN_PROCESO = "en_proceso"
    OK = "ok"
    ERROR = "error"


class JobRequest(BaseModel):
    """Lo que entra por POST /api/jobs. Se valida antes de encolar."""

    url: HttpUrl
    rol: Rol
    privacy_mode: PrivacyMode | None = None  # None → default de la matriz, validado contra el rol
    output_format: OutputFormat = OutputFormat.MARKDOWN
    extract_schema: dict | None = None
    crawl_depth: int = Field(default=0, ge=0, le=5)
    max_pages: int = Field(default=1, ge=1, le=1000)
    paginate: bool = False                # barrer el paginado de la URL (postback/links/?page=)
    capture_api: bool = False             # capturar el JSON/XHR oculto en vez del HTML (ADR-010)
    tier_hint: FetchTier | None = None
    callback_url: HttpUrl | None = None
    # Overrides por job (panel Avanzado de la UI): para cuando nada más sirve.
    proxy: str | None = None              # proxy puntual para este job (override del pool)
    captcha_api_url: str | None = None    # servicio resolvedor de CAPTCHA (estilo 2captcha)
    captcha_api_key: str | None = None
    cookies: str | None = None            # cookies de sesión "k=v; k2=v2" (gate por login/ubicación)


class RevertRequest(BaseModel):
    """Pedido de rehidratación de contenido pseudonimizado. Ver ADR-005."""

    content: str
    mapping_ref: str
    rol: Rol


class Sobre(BaseModel):
    """El job en vuelo y su resultado. Se serializa a Redis y al callback.

    `mapping_ref` queda reservado para el modo reversible (v2): es la referencia
    opaca a la tabla de mapeo cifrada que vive en Anonimal, nunca el mapeo en sí.
    """

    job_id: str
    source_url: HttpUrl
    privacy_mode: PrivacyMode
    rol: Rol
    output_format: OutputFormat = OutputFormat.MARKDOWN
    status: JobStatus = JobStatus.PENDIENTE
    tier_usado: FetchTier | None = None
    content_md: str | None = None
    content_json: dict | None = None
    mapping_ref: str | None = None
    anonimizado: bool = False
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)
    fetched_at: datetime | None = None
    meta: dict = Field(default_factory=dict)
