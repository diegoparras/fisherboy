"""Descarga de galerías/imágenes de redes con gallery-dl. El gemelo de yt-dlp.

yt-dlp baja video; gallery-dl baja las imágenes/galerías de Instagram, X/Twitter, Reddit,
Pinterest, Tumblr, Flickr, DeviantArt, etc. Se invoca como subproceso (`python -m
gallery_dl`) para aislar su red y su propio manejo de extractores; el resultado se empaqueta
en un ZIP en la capa de arriba.

Defensa: la URL se restringe a una allowlist de plataformas conocidas (no es un descargador
genérico). Tope de cantidad (--range) y de tamaño por archivo (--filesize-max); el tope de
bytes total lo aplica el endpoint al armar el ZIP. ffmpeg NO hace falta.
"""
from __future__ import annotations

import os
import subprocess
import sys
from urllib.parse import urlsplit

# Plataformas de imágenes/galerías permitidas (host raíz; cualquier subdominio vale).
ALLOWED_GALLERY_HOSTS = frozenset({
    "instagram.com", "twitter.com", "x.com", "nitter.net",
    "reddit.com", "redd.it",
    "pinterest.com", "pin.it",
    "tumblr.com", "flickr.com", "deviantart.com",
    "imgur.com", "artstation.com", "behance.net",
    "weibo.com", "vk.com", "500px.com",
})


def gallery_host_allowed(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_GALLERY_HOSTS)


_GALLERY_LABELS = {
    "instagram.com": "instagram", "twitter.com": "twitter", "x.com": "twitter",
    "reddit.com": "reddit", "redd.it": "reddit", "pinterest.com": "pinterest",
    "pin.it": "pinterest", "tumblr.com": "tumblr", "flickr.com": "flickr",
    "deviantart.com": "deviantart", "imgur.com": "imgur", "artstation.com": "artstation",
    "behance.net": "behance", "weibo.com": "weibo", "vk.com": "vk",
    "500px.com": "500px",
}


def gallery_provider(url: str) -> str | None:
    """Si la URL es de una plataforma de galerías soportada, devuelve el proveedor; si no, None."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    for h, label in _GALLERY_LABELS.items():
        if host == h or host.endswith("." + h):
            return label
    return "galería" if gallery_host_allowed(url) else None


def gallerydl_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("gallery_dl") is not None


def download_gallery(
    url: str,
    *,
    tmpdir: str,
    max_files: int = 50,
    filesize_max: str = "100M",
    proxy: str = "",
    cookiefile: str = "",
    timeout_s: int = 180,
) -> list[str]:
    """Baja la galería a `tmpdir` (sin subcarpetas) y devuelve la lista de archivos. Lanza si
    no bajó nada. Bloqueante: el endpoint lo corre en un threadpool."""
    cmd = [sys.executable, "-m", "gallery_dl", "-q", "-D", tmpdir,
           "--range", f"1-{max(1, max_files)}", "--filesize-max", filesize_max,
           "--no-mtime"]
    if proxy:
        cmd += ["--proxy", proxy]
    if cookiefile:
        cmd += ["-C", cookiefile]
    cmd.append(url)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("la descarga de la galería tardó demasiado (timeout).") from e

    files = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)
             if os.path.isfile(os.path.join(tmpdir, f))]
    if not files:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        reason = tail[-1][:160] if tail else "gallery-dl no bajó ninguna imagen (¿privado/login?)."
        raise RuntimeError(reason)
    return files
