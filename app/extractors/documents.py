"""Sub-pipeline documental: delega PDFs y docs a Escriba. Ver Capa 5 y ADR-001.

Fisherboy no reimplementa la conversión documental: cuando lo que bajó es un PDF o un
doc de oficina, se lo manda a Escriba, que ya hace esa conversión muy bien (MarkItDown
+ OCR + layout). Regla del pulpo (ADR-001): solo HTTP y datos serializados, sin
importar código de Escriba.

Contrato real (Fase 0, markitdown-web/app/main.py):
  POST /api/convert  (multipart: file | url + opciones)
    resp JSON: { source, title, markdown, chars, words, anonymized, pii_count, ... }
  AUTH: hoy exige sesión por cookie + CSRF. Para servicio-a-servicio, Escriba debe
  sumar un token de servicio (análogo a Anonimal en ADR-003). Mientras tanto, este
  cliente manda el token por header/cookie si ESCRIBA_TOKEN está seteado.

Se envían los BYTES ya descargados por el router (no la URL): evita que Escriba
re-fetchee y vuelva a exponer el SSRF de salida.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from .text_main import ExtractError

# Tipos/extensiones que NO son HTML y conviene delegar a Escriba.
_DOC_CONTENT_TYPES = (
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/epub",
    "application/rtf",
    "application/vnd.oasis.opendocument",
)
_DOC_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".epub", ".rtf", ".odt", ".ods", ".odp",
)


class DocumentError(ExtractError):
    """Falló la conversión documental delegada. Es un ExtractError (el pipeline lo captura)."""


def is_document(content_type: str, url: str) -> bool:
    """True si el recurso es un documento (no HTML) que conviene delegar a Escriba."""
    ct = (content_type or "").lower()
    if any(t in ct for t in _DOC_CONTENT_TYPES):
        return True
    if "html" in ct or "text/plain" in ct:
        return False
    path = urlsplit(url).path.lower()
    return path.endswith(_DOC_EXTENSIONS)


class EscribaClient:
    """Cliente HTTP hacia el /api/convert de Escriba. Sin auth propia todavía (ver ADR)."""

    def __init__(self, base_url: str, *, token: str = "", timeout_s: float = 180.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.base_url)

    def convert(self, content: bytes, *, filename: str = "documento.pdf") -> str:
        """Manda los bytes a Escriba y devuelve el markdown. Falla cerrado a DocumentError."""
        if not self.base_url:
            raise DocumentError("Conversión documental no configurada (ESCRIBA_URL vacío).")
        import httpx

        headers = {}
        cookies = {}
        if self.token:  # cuando Escriba sume auth de servicio (ADR-003 análogo)
            headers["X-Service-Token"] = self.token
            cookies["session"] = self.token
        try:
            resp = httpx.post(
                f"{self.base_url}/api/convert",
                files={"file": (filename, content)},
                headers=headers,
                cookies=cookies,
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as e:
            raise DocumentError(f"No se pudo contactar a Escriba: {type(e).__name__}.") from e
        if resp.status_code in (401, 403):
            raise DocumentError(
                "Escriba rechazó la conversión (auth de servicio pendiente, ver ADR-001)."
            )
        if not resp.is_success:
            raise DocumentError(f"Escriba respondió {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as e:
            raise DocumentError("Respuesta inválida de Escriba.") from e
        md = data.get("markdown")
        if not md or not md.strip():
            raise DocumentError("Escriba no devolvió markdown.")
        return md.strip()


def filename_from_url(url: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    return name or "documento"
