"""Descarga vía Fisherboy: stream SSRF-seguro de un archivo remoto al cliente.

El navegador puede bajar un link directo solo. Pero a veces conviene que el archivo
pase POR Fisherboy: para usar el proxy/cookies de la sesión, para esquivar un
hotlink-protection, o para empaquetar varios en un ZIP. Este módulo hace eso con las
mismas defensas que el fetcher: valida SSRF en CADA salto (anti DNS rebinding), corta
por tamaño y por timeout, sigue los redirects a mano.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

import httpx

from ..fetchers.base import FetchError
from ..security.ssrf import (
    SSRFError,
    guarded_client,
    resolve_and_validate,
    validate_scheme_and_host,
)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_FNAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', re.IGNORECASE)
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ()]+")


def safe_filename(url: str, content_disposition: str = "", content_type: str = "") -> str:
    """Nombre de archivo seguro: prioriza el Content-Disposition, si no el path de la URL."""
    name = ""
    if content_disposition:
        m = _FNAME_RE.search(content_disposition)
        if m:
            name = unquote(m.group(1)).strip()
    if not name:
        seg = urlsplit(url).path.rsplit("/", 1)[-1]
        name = unquote(seg).strip()
    name = _SAFE_NAME.sub("_", name)
    name = re.sub(r"\.{2,}", "_", name).strip(". ") or "descarga"   # colapsa '..' (anti-traversal)
    if "." not in name:   # darle una extensión razonable desde el content-type
        ext = {
            "application/pdf": "pdf", "application/zip": "zip", "image/jpeg": "jpg",
            "image/png": "png", "image/gif": "gif", "image/webp": "webp",
            "audio/mpeg": "mp3", "video/mp4": "mp4", "text/csv": "csv",
        }.get((content_type or "").split(";", 1)[0].strip().lower())
        if ext:
            name = f"{name}.{ext}"
    return name[:150]


def open_stream(
    url: str,
    *,
    allow_private: bool = False,
    proxy: str | None = None,
    cookies: dict | None = None,
    timeout_s: float = 30.0,
    max_redirects: int = 5,
) -> tuple[httpx.Client, httpx.Response]:
    """Abre el stream del archivo siguiendo redirects con validación SSRF en cada salto.

    Devuelve (client, response) con el body SIN consumir. El que llama DEBE cerrar los
    dos (response.close() + client.close()), idealmente en el `finally` del generador.
    Falla cerrado ante cualquier destino prohibido.
    """
    resolve_and_validate(url, allow_private=allow_private)
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    client = guarded_client(
        allow_private=allow_private, proxy=proxy,
        follow_redirects=False, timeout=timeout_s, headers=headers,
        limits=httpx.Limits(max_connections=4),
    )
    current = url
    try:
        for _hop in range(max_redirects + 1):
            resp = client.send(client.build_request("GET", current), stream=True)
            if resp.is_redirect:
                location = resp.headers.get("location")
                resp.close()
                if not location:
                    raise FetchError("Redirect sin header Location.")
                nxt = str(httpx.URL(current).join(location))
                validate_scheme_and_host(nxt)
                resolve_and_validate(nxt, allow_private=allow_private)
                current = nxt
                continue
            if resp.status_code >= 400:
                code = resp.status_code
                resp.close()
                raise FetchError(f"El servidor respondió {code} al bajar el archivo.")
            return client, resp
        raise FetchError(f"Demasiados redirects (>{max_redirects}).")
    except (SSRFError, FetchError):
        client.close()
        raise
    except httpx.HTTPError as e:
        client.close()
        raise FetchError(f"Fallo de red al bajar el archivo: {type(e).__name__}.") from e


def fetch_bytes(
    url: str,
    *,
    max_bytes: int,
    allow_private: bool = False,
    proxy: str | None = None,
    cookies: dict | None = None,
    timeout_s: float = 30.0,
) -> tuple[bytes, str, str]:
    """Baja un archivo completo a memoria (para el ZIP). Devuelve (bytes, nombre, content_type).
    Corta por tamaño (FetchError si lo supera)."""
    client, resp = open_stream(url, allow_private=allow_private, proxy=proxy,
                               cookies=cookies, timeout_s=timeout_s)
    try:
        ctype = resp.headers.get("content-type", "application/octet-stream")
        name = safe_filename(url, resp.headers.get("content-disposition", ""), ctype)
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise FetchError(f"El archivo supera el límite de {max_bytes} bytes.")
            chunks.append(chunk)
        return b"".join(chunks), name, ctype
    finally:
        resp.close()
        client.close()
