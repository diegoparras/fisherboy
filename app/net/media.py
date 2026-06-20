"""Descarga de video con yt-dlp (mp4). El satélite hace solo su trabajo.

Fisherboy standalone no depende de Escriba para esto: baja el archivo de video armado
de YouTube/Vimeo/etc. Si hay ffmpeg en el sistema, muxea audio+video hasta la calidad
pedida; si no, cae al mejor formato progresivo (un solo archivo, sin mux).

Defensa: la URL de entrada se restringe a una allowlist de plataformas de video conocidas
(evita que el endpoint sea un SSRF/proxy genérico). El tope de tamaño lo aplica yt-dlp por
formato. El binario de ffmpeg es opcional (mejora la calidad, no es obligatorio).
"""
from __future__ import annotations

import os
import shutil
from urllib.parse import urlsplit

# Plataformas de video permitidas (host raíz; se acepta cualquier subdominio).
ALLOWED_VIDEO_HOSTS = frozenset({
    "youtube.com", "youtu.be", "youtube-nocookie.com",
    "vimeo.com", "dailymotion.com", "dai.ly",
    "twitch.tv", "tiktok.com",
    "twitter.com", "x.com",
    "facebook.com", "fb.watch", "instagram.com",
    "soundcloud.com", "bandcamp.com",
    "streamable.com", "rumble.com", "odysee.com",
})


def host_allowed(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_VIDEO_HOSTS)


# Host raíz → etiqueta de proveedor (para mostrar y para el flag dl_video).
_PROVIDER_LABELS = {
    "youtube.com": "youtube", "youtu.be": "youtube", "youtube-nocookie.com": "youtube",
    "vimeo.com": "vimeo", "dailymotion.com": "dailymotion", "dai.ly": "dailymotion",
    "twitch.tv": "twitch", "tiktok.com": "tiktok",
    "twitter.com": "twitter", "x.com": "twitter",
    "facebook.com": "facebook", "fb.watch": "facebook", "instagram.com": "instagram",
    "soundcloud.com": "soundcloud", "bandcamp.com": "bandcamp",
    "streamable.com": "streamable", "rumble.com": "rumble", "odysee.com": "odysee",
}


def video_provider(url: str) -> str | None:
    """Si la URL es de una plataforma de video bajable con yt-dlp, devuelve el proveedor
    ('youtube', 'vimeo', ...). Si no, None. Sirve para reconocer cuando la URL semilla del
    job ES un video (no solo embebidos dentro de otra página)."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    for h, label in _PROVIDER_LABELS.items():
        if host == h or host.endswith("." + h):
            return label
    return "video" if host_allowed(url) else None


def ytdlp_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("yt_dlp") is not None


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _format_selector(max_height: int) -> str:
    """Elige el formato. Con ffmpeg: mejor video+audio hasta max_height (muxea). Sin
    ffmpeg: el mejor PROGRESIVO (un archivo con audio+video, no necesita mux)."""
    if ffmpeg_available():
        return (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
                f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b")
    # progresivo: un solo archivo con ambos streams (acodec y vcodec presentes)
    return ("b[ext=mp4][acodec!=none][vcodec!=none]/"
            "b[acodec!=none][vcodec!=none]/b")


QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")


def download_video(
    url: str,
    *,
    tmpdir: str,
    max_bytes: int,
    max_height: int = 1080,
    quality: str = "best",
    audio_only: bool = False,
    proxy: str = "",
    cookiefile: str = "",
    timeout_s: int = 30,
) -> tuple[str, str]:
    """Baja el video (o solo el audio mp3) a `tmpdir` y devuelve (ruta, nombre). Lanza si falla.

    `quality`: 'best' o una altura ('1080','720',...). El server la capa a VIDEO_MAX_HEIGHT.
    `audio_only`: solo el audio → mp3 si hay ffmpeg, si no el formato nativo (m4a/webm).
    Bloqueante (red + disco): el endpoint lo corre en un threadpool."""
    import yt_dlp

    # Altura efectiva: lo que pidió el usuario, capado por el tope del server.
    height = max_height
    if quality and quality.isdigit():
        height = min(int(quality), max_height)

    ydl_opts = {
        "outtmpl": os.path.join(tmpdir, "%(title).80s.%(ext)s"),
        "noplaylist": True,
        "playlist_items": "1",
        "max_filesize": max_bytes,        # aborta si el formato elegido excede el tope
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": timeout_s,
        "retries": 2,
        "concurrent_fragment_downloads": 1,
        "restrictfilenames": True,
    }
    if audio_only:
        ydl_opts["format"] = "bestaudio/best"
        if ffmpeg_available():    # convertir a mp3 necesita ffmpeg; sin él baja el nativo
            ydl_opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192",
            }]
    else:
        ydl_opts["format"] = _format_selector(height)
        ydl_opts["merge_output_format"] = "mp4"
    if proxy:
        ydl_opts["proxy"] = proxy
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    # Localiza el archivo realmente bajado (yt-dlp puede cambiar la extensión al muxear).
    candidates = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
    candidates = [c for c in candidates if os.path.isfile(c) and not c.endswith((".part", ".ytdl"))]
    if not candidates:
        raise RuntimeError("yt-dlp no produjo ningún archivo (¿video privado/geo-bloqueado?).")
    path = max(candidates, key=os.path.getsize)
    return path, os.path.basename(path)
