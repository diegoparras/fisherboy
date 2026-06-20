"""Datos de Instagram con instaloader: comentarios de un post + seguidores/seguidos.

Esto NO es media (de eso se encarga gallery-dl): saca DATOS estructurados (un hilo de
comentarios, una lista de cuentas) y los devuelve como registros para tabla/JSON.

Requiere SESIÓN logueada: Instagram no muestra comentarios completos ni listas de
seguidores sin login. Se usa el cookie `sessionid` (IG_SESSIONID) de una sesión real.

ADVERTENCIA operativa: bajar seguidores es de lo que Instagram más vigila (rate-limit
agresivo, puede marcar/limitar la cuenta). Por eso hay un tope de items y conviene ir
despacio. Es responsabilidad de quien lo usa (cuenta propia / contenido que puede ver).
"""
from __future__ import annotations

from urllib.parse import urlsplit

# Segmentos de path que NO son un usuario (rutas reservadas de Instagram).
_RESERVED = {"p", "reel", "reels", "tv", "explore", "stories", "accounts", "direct",
             "about", "developer", "legal", "directory", "lite", "web", "graphql"}
_POST_SEGMENTS = {"p", "reel", "reels", "tv"}


def instaloader_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("instaloader") is not None


def _segments(url: str) -> list[str]:
    return [s for s in urlsplit(url).path.split("/") if s]


def is_instagram(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "instagram.com" or host.endswith(".instagram.com")


def url_kind(url: str) -> str | None:
    """'post' (un posteo/reel), 'profile' (un usuario) o None."""
    if not is_instagram(url):
        return None
    seg = _segments(url)
    if not seg:
        return None
    if seg[0] in _POST_SEGMENTS:
        return "post"
    if seg[0] in _RESERVED:
        return None
    return "profile"


def extract_shortcode(url: str) -> str | None:
    seg = _segments(url)
    if len(seg) >= 2 and seg[0] in _POST_SEGMENTS:
        return seg[1]
    return None


def extract_username(url: str) -> str | None:
    seg = _segments(url)
    if seg and seg[0] not in _RESERVED:
        return seg[0]
    return None


def _loader(sessionid: str):
    """Arma un Instaloader con la sesión del cookie y valida que esté logueada."""
    import instaloader
    if not sessionid:
        raise RuntimeError("Falta IG_SESSIONID (el cookie de sesión de Instagram).")
    L = instaloader.Instaloader(quiet=True, download_pictures=False, download_videos=False,
                                download_video_thumbnails=False, download_geotags=False,
                                download_comments=False, save_metadata=False)
    L.context._session.cookies.set("sessionid", sessionid.strip(), domain=".instagram.com")
    try:
        user = L.test_login()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"no se pudo validar la sesión de Instagram: {type(e).__name__}.") from e
    if not user:
        raise RuntimeError("sesión de Instagram inválida o vencida (revisá IG_SESSIONID).")
    return L


def get_comments(url: str, sessionid: str, *, max_items: int = 500) -> list[dict]:
    """Comentarios de un post: [{author, text, created_at, likes}]."""
    import instaloader
    sc = extract_shortcode(url)
    if not sc:
        raise RuntimeError("La URL no es un post de Instagram (esperaba /p/, /reel/ o /tv/).")
    L = _loader(sessionid)
    post = instaloader.Post.from_shortcode(L.context, sc)
    out: list[dict] = []
    for c in post.get_comments():
        out.append({
            "author": getattr(c.owner, "username", ""),
            "text": c.text or "",
            "created_at": c.created_at_utc.isoformat() if getattr(c, "created_at_utc", None) else "",
            "likes": getattr(c, "likes_count", 0) or 0,
        })
        if len(out) >= max_items:
            break
    return out


def get_follows(url: str, sessionid: str, *, which: str = "followers",
                max_items: int = 500) -> list[dict]:
    """Lista de seguidores o seguidos: [{username, full_name, private, verified}]."""
    import instaloader
    un = extract_username(url)
    if not un:
        raise RuntimeError("La URL no es un perfil de Instagram (esperaba instagram.com/usuario).")
    L = _loader(sessionid)
    profile = instaloader.Profile.from_username(L.context, un)
    iterator = profile.get_followees() if which == "followees" else profile.get_followers()
    out: list[dict] = []
    for p in iterator:
        out.append({
            "username": p.username,
            "full_name": getattr(p, "full_name", "") or "",
            "private": bool(getattr(p, "is_private", False)),
            "verified": bool(getattr(p, "is_verified", False)),
        })
        if len(out) >= max_items:
            break
    return out
