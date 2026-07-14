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


def is_auth_required(message: str) -> bool:
    """¿El error de yt-dlp es un pedido de sesión / anti-bot (la IP del server está
    bloqueada), y no un fallo del video en sí? En ese caso la salida es pasar cookies de una
    sesión logueada (o un proxy residencial), no reintentar. Se compara en minúsculas y por
    subcadenas que evitan el apóstrofe tipográfico de "you're" para no depender de él."""
    m = (message or "").lower()
    needles = (
        "sign in to confirm",      # "...you're not a bot" / "...your age"
        "sign in if",              # "Private video. Sign in if you've been granted access"
        "not a bot",
        "use --cookies", "cookies-from-browser",
        "login required", "login_required",
        "private video", "members-only", "join this channel",
        "this video is only available", "account on this device",
    )
    return any(n in m for n in needles)


def is_no_formats(message: str) -> bool:
    """¿El fallo es "YouTube no me dio formatos descargables"? Es la cara visible de SABR:
    YouTube exige un PO Token (proof-of-origin) y, si no lo tiene, entrega una lista de
    formatos vacía o mocha (solo storyboards). yt-dlp lo reporta como "Requested format is
    not available". Distinto de is_auth_required (ahí pide sesión): acá la salida es el PO
    token provider o probar otro player_client, no las cookies."""
    m = (message or "").lower()
    needles = (
        "requested format is not available",
        "no video formats", "no formats found", "no suitable formats",
        "only images are available",
        "unable to extract player", "failed to extract any player response",
        "sabr",
    )
    return any(n in m for n in needles)


def ytdlp_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("yt_dlp") is not None


def pot_available() -> bool:
    """¿Está instalado el plugin que fabrica PO Tokens (bgutil)? Sin él, YouTube esconde
    los formatos de audio y el mp3 no sale."""
    import importlib.util
    try:
        return importlib.util.find_spec("yt_dlp_plugins.extractor.getpot_bgutil") is not None
    except (ImportError, ValueError):   # el namespace del plugin ni existe
        return False


def pot_plugin_version() -> str:
    """Versión del plugin bgutil que quedó horneada en ESTA imagen."""
    try:
        from importlib.metadata import version
        return version("bgutil-ytdlp-pot-provider")
    except Exception:  # noqa: BLE001 — no instalado
        return ""


def pot_probe(pot_url: str, timeout_s: float = 3.0) -> dict:
    """Le pregunta al sidecar quién es (GET /ping → {version, server_uptime}) y compara su
    versión con la del plugin de esta imagen.

    Existe porque plugin y servidor son DOS piezas que se despliegan por separado (la imagen
    de Fisherboy y el contenedor de bgutil) y tienen que hablar el mismo protocolo: si sus
    majors difieren, no se entienden y YouTube vuelve a no entregar formatos. Sin este chequeo
    el síntoma sería otra vez "Requested format is not available" — un error que no dice nada
    de la causa real. No corre en el camino feliz: solo cuando una descarga ya falló, o si lo
    pedís por /api/download/pot/health."""
    out = {"url": pot_url, "plugin": pot_plugin_version(), "server": "",
           "ok": False, "mismatch": False, "error": ""}
    if not pot_url:
        out["error"] = "YT_POT_URL vacío (proveedor apagado)"
        return out
    import httpx
    try:
        r = httpx.get(pot_url.rstrip("/") + "/ping", timeout=timeout_s)
        r.raise_for_status()
        out["server"] = str((r.json() or {}).get("version") or "")
    except Exception as e:  # noqa: BLE001 — no responde, DNS, 404, JSON roto…
        out["error"] = f"{type(e).__name__}: {e}"[:160]
        return out
    out["ok"] = True
    major = lambda v: (v or "").split(".")[0]   # noqa: E731
    if out["plugin"] and out["server"] and major(out["plugin"]) != major(out["server"]):
        out["mismatch"] = True
    return out


def no_formats_hint(pot_url: str) -> str:
    """El mensaje que ve el usuario cuando YouTube no entregó formatos. Interroga al sidecar
    para decir QUÉ pasó, en vez del "Requested format is not available / usá --list-formats"
    de yt-dlp, que no le sirve a nadie desde una UI."""
    base = "YouTube no entregó formatos descargables (protección SABR). "
    if not pot_url:
        return (base + "No hay proveedor de PO tokens configurado (YT_POT_URL): sin él YouTube "
                "esconde el audio. También podés cargar cookies de sesión y un proxy en Avanzado.")
    p = pot_probe(pot_url)
    if not p["ok"]:
        return (base + f"El proveedor de PO tokens no responde en {pot_url} ({p['error']}): "
                "revisá que el servicio bgutil esté levantado y que YT_POT_URL apunte a su "
                "hostname interno.")
    if p["mismatch"]:
        return (base + f"El proveedor de PO tokens es la versión {p['server']} y el plugin de "
                f"esta imagen la {p['plugin']}: son majors distintas y no se entienden. "
                "Actualizá la imagen de Fisherboy (redeploy) para que las dos vuelvan a "
                "coincidir.")
    return (base + f"El proveedor de PO tokens responde bien (v{p['server']}), así que el "
            "bloqueo es por IP: cargá cookies de sesión y un proxy residencial en Avanzado.")


# Planes de extracción, en orden. YouTube rota qué cliente exige PO Token, así que en vez de
# casarnos con uno probamos en cascada y nos quedamos con el primero que entregue formatos:
#   1. default   — los clientes que elija yt-dlp. Con el PO Token provider levantado, es el
#                  que da la mejor calidad (formatos DASH completos → 1080p y audio real).
#   2. tv,web_embedded — no exigen PO Token hoy; el salvavidas cuando el provider está caído.
#   3. android_vr      — tampoco lo exige; último recurso, catálogo de formatos más pobre.
# La lista vacía = "no fuerces player_client" (default de yt-dlp).
_PLAYER_PLANS: tuple[tuple[str, ...], ...] = (
    (),
    ("tv", "web_embedded"),
    ("android_vr",),
)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _format_selector(max_height: int) -> str:
    """Elige el formato. Con ffmpeg: mejor video+audio hasta max_height (muxea). Sin
    ffmpeg: el mejor PROGRESIVO (un archivo con audio+video, no necesita mux)."""
    if ffmpeg_available():
        # Cae con gracia: mp4 hasta la altura → cualquier códec hasta la altura → progresivo
        # hasta la altura → el mejor video+audio sin tope (muxea) → lo que haya. El penúltimo
        # evita "Requested format is not available" cuando no hay formato bajo la altura pedida.
        return (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
                f"bv*[height<={max_height}]+ba/b[height<={max_height}]/"
                f"bv*+ba/b")
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
    pot_url: str = "",
    timeout_s: int = 30,
    progress_hook=None,
) -> tuple[str, str]:
    """Baja el video (o solo el audio mp3) a `tmpdir` y devuelve (ruta, nombre). Lanza si falla.

    `quality`: 'best' o una altura ('1080','720',...). El server la capa a VIDEO_MAX_HEIGHT.
    `audio_only`: solo el audio → mp3 si hay ffmpeg, si no el formato nativo (m4a/webm).
    `pot_url`: URL del PO Token provider (bgutil). Sin él YouTube esconde los formatos.
    Si YouTube no entrega formatos, reintenta con otros player_client (_PLAYER_PLANS) antes
    de darse por vencido. Bloqueante (red + disco): el endpoint lo corre en un threadpool."""
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
        # Baja varios fragmentos del DASH en paralelo (video+audio fragmentados, como el 1080p).
        # En serie (1) es el cuello de botella principal; 4 acelera mucho sin saturar el proxy.
        "concurrent_fragment_downloads": 4,
        "restrictfilenames": True,
    }
    if progress_hook:   # yt-dlp llama el hook con {status, downloaded_bytes, total_bytes, ...}
        ydl_opts["progress_hooks"] = [progress_hook]
    if audio_only:
        # bestaudio son formatos DASH: los primeros que YouTube esconde sin PO Token. El
        # fallback a un progresivo (que trae video+audio) salva el mp3 aunque el DASH no esté
        # —ffmpeg le arranca la pista de audio igual—, a costa de bajar unos MB de más.
        ydl_opts["format"] = "bestaudio/bestaudio*/b"
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

    extractor_args: dict[str, dict[str, list[str]]] = {}
    if pot_url:   # el plugin bgutil lee de acá a qué sidecar pedirle los PO Tokens
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [pot_url]}

    # Cascada: si YouTube no entrega formatos con un plan, se prueba el siguiente. Los errores
    # que NO son de formatos (video privado, red, sesión) cortan de una: reintentar no ayuda.
    last_exc: Exception | None = None
    for plan in _PLAYER_PLANS:
        opts = dict(ydl_opts)
        args = dict(extractor_args)
        if plan:
            args["youtube"] = {"player_client": list(plan)}
        if args:
            opts["extractor_args"] = args
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
            break
        except Exception as e:  # noqa: BLE001 — yt-dlp lanza tipos varios
            msg = str(e)
            if is_auth_required(msg) or not is_no_formats(msg):
                raise
            last_exc = e
            # Plan fallido: limpiar los .part que dejó antes de reintentar con otro cliente.
            for f in os.listdir(tmpdir):
                if f.endswith((".part", ".ytdl")):
                    try:
                        os.unlink(os.path.join(tmpdir, f))
                    except OSError:
                        pass
    else:
        # Ningún plan entregó formatos: es SABR/PO Token, no un video roto. Acá se le pregunta
        # al sidecar qué le pasa (¿caído? ¿major distinta? ¿sano → entonces es la IP?) para que
        # el mensaje diga la causa real y no el "usá --list-formats" de yt-dlp.
        raise RuntimeError(no_formats_hint(pot_url)) from last_exc

    # Localiza el archivo realmente bajado (yt-dlp puede cambiar la extensión al muxear).
    candidates = [os.path.join(tmpdir, f) for f in os.listdir(tmpdir)]
    candidates = [c for c in candidates if os.path.isfile(c) and not c.endswith((".part", ".ytdl"))]
    if not candidates:
        raise RuntimeError("yt-dlp no produjo ningún archivo (¿video privado/geo-bloqueado?).")
    path = max(candidates, key=os.path.getsize)
    return path, os.path.basename(path)
