"""Comentarios de varias plataformas, con un router por host.

- Reddit  → API JSON oficial (append `.json`). Confiable y legítimo.
- YouTube → yt-dlp (getcomments). Confiable.
- X/Twitter, TikTok → yt-dlp best-effort. EXPERIMENTAL: estas plataformas rompen seguido
  (cerraron/limitaron sus APIs); puede no traer nada. La UI avisa con un modal antes.

Instagram NO pasa por acá: tiene su propio camino con instaloader (sesión + más datos).
Devuelve registros uniformes [{author, text, ...}] para tabla/JSON/CSV.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

_UA = "Fisherboy/1.0 (+comentarios; uso personal)"


class CommentsAuthRequired(RuntimeError):
    """La plataforma exige sesión: hacen falta cookies (o las cargadas vencieron)."""


# Marcas que yt-dlp/YouTube tira cuando el problema es de sesión y unas cookies lo resuelven.
# Comparación en minúsculas. No incluye "private video" puro (cookies no siempre alcanzan).
_AUTH_WALL = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "use --cookies",
    "cookies-from-browser",
    "sign in to view",
    "login required",
    "this video may be inappropriate",
    "age-restricted",
    "members-only",
    "join this channel",
)


def _is_auth_wall(msg: str) -> bool:
    low = (msg or "").lower()
    return any(m in low for m in _AUTH_WALL)

# Plataforma por host y su confiabilidad.
_PLATFORM_HOSTS = {
    "youtube": ("youtube.com", "youtu.be"),
    "reddit": ("reddit.com", "redd.it"),
    "twitter": ("twitter.com", "x.com"),
    "tiktok": ("tiktok.com",),
}
# YouTube (yt-dlp) es el confiable. Reddit endureció el acceso `.json` y devuelve 403 desde
# IPs de servidor/datacenter (anda desde residencial); X/TikTok cerraron sus APIs. Todos esos
# son EXPERIMENTAL: la UI avisa con un modal antes de intentar.
RELIABLE = frozenset({"youtube"})
EXPERIMENTAL = frozenset({"reddit", "twitter", "tiktok"})


def comment_platform(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    for plat, hosts in _PLATFORM_HOSTS.items():
        if any(host == h or host.endswith("." + h) for h in hosts):
            return plat
    return None


def is_experimental(url: str) -> bool:
    return comment_platform(url) in EXPERIMENTAL


def _iso(ts) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat() if ts else ""
    except (TypeError, ValueError, OSError):
        return ""


def get_comments(url: str, *, max_items: int = 300, timeout_s: int = 30,
                 proxy: str = "", cookiefile: str = "") -> list[dict]:
    plat = comment_platform(url)
    if plat == "reddit":
        return _reddit_comments(url, max_items=max_items, timeout_s=timeout_s, proxy=proxy)
    if plat in ("youtube", "twitter", "tiktok"):
        return _ytdlp_comments(url, max_items=max_items, proxy=proxy, cookiefile=cookiefile)
    raise RuntimeError("Esta plataforma no tiene soporte de comentarios.")


def _reddit_comments(url: str, *, max_items: int, timeout_s: int, proxy: str = "") -> list[dict]:
    import httpx
    api = url.split("?", 1)[0].rstrip("/") + "/.json?limit=" + str(min(max_items, 500))
    kw = {"timeout": timeout_s, "headers": {"User-Agent": _UA, "Accept": "application/json"},
          "follow_redirects": True}
    if proxy:
        kw["proxy"] = proxy
    with httpx.Client(**kw) as c:
        resp = c.get(api)
    ctype = resp.headers.get("content-type", "")
    if resp.status_code == 403 or "application/json" not in ctype:
        raise RuntimeError("Reddit bloqueó el pedido (pasa desde servidores/datacenter). "
                           "Probá desde una IP residencial o con un proxy residencial.")
    try:
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("Reddit no devolvió JSON (¿bloqueado o URL inválida?).") from e
    out: list[dict] = []

    def walk(children):
        for ch in children:
            if len(out) >= max_items:
                return
            d = ch.get("data", {})
            if ch.get("kind") == "t1" and d.get("body"):
                out.append({"author": d.get("author", ""), "text": d.get("body", ""),
                            "score": d.get("score", 0), "created_at": _iso(d.get("created_utc"))})
                replies = d.get("replies")
                if isinstance(replies, dict):
                    walk(replies.get("data", {}).get("children", []))

    if isinstance(data, list) and len(data) > 1:
        walk(data[1].get("data", {}).get("children", []))
    return out


def _ytdlp_comments(url: str, *, max_items: int, proxy: str = "", cookiefile: str = "") -> list[dict]:
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True, "getcomments": True,
        "extractor_args": {"youtube": {"max_comments": [str(max_items), "all", "0"]}},
    }
    if proxy:
        opts["proxy"] = proxy
    if cookiefile:
        opts["cookiefile"] = cookiefile
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001 — yt-dlp lanza tipos varios
        if _is_auth_wall(str(e)):
            raise CommentsAuthRequired(str(e).splitlines()[0][:160]) from e
        raise
    comments = info.get("comments") or []
    out: list[dict] = []
    for c in comments[:max_items]:
        out.append({
            "author": c.get("author") or c.get("author_id") or "",
            "text": c.get("text", ""),
            "likes": c.get("like_count", 0) or 0,
            "created_at": _iso(c.get("timestamp")),
        })
    return out
