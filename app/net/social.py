"""Redes sociales — posts como datos, no como HTML. Ver ADR-012.

El problema de scrapear X/LinkedIn/Facebook en 2026 no es el HTML: es que todo vive detrás de
un login y de una API interna (GraphQL / Voyager) cuyos identificadores ROTAN cada pocas
semanas. Los caminos fáciles murieron (snscrape, Twint, Nitter) y los guest tokens quedaron
atados a la huella del navegador, con las IPs de datacenter baneadas.

Lo que sí aguanta, y es lo que hace este módulo:

  navegador con sesión real  →  la página llama a su propia API  →  interceptamos ESA respuesta

Y sobre lo interceptado no se busca por RUTA sino por FORMA: se recorre el JSON y se levanta
todo objeto que "parezca un post" (texto + autor + fecha). Si mañana X mueve un campo de
`data.user.result.timeline` a otro lado, el extractor sigue andando — mientras el objeto siga
teniendo la misma pinta. Es la diferencia entre un scraper que dura semanas y uno que dura meses.

No reemplaza a la sesión: sin cookies de una cuenta logueada estas plataformas no muestran
nada. Ver docs/SOCIAL.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

# Host raíz → plataforma. Las que tienen extractor propio acá; el resto cae al scraping normal.
_HOSTS = {
    "x.com": "x", "twitter.com": "x",
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "tiktok.com": "tiktok",
    "reddit.com": "reddit",
}

# Cuántos posts entra, más o menos, por cada vuelta de scroll.
_POSTS_POR_VUELTA = 10


def social_platform(url: str) -> str | None:
    """'x' | 'linkedin' | 'facebook' | … o None si no es una red conocida."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    for h, plat in _HOSTS.items():
        if host == h or host.endswith("." + h):
            return plat
    return None


def scroll_actions(max_posts: int, *, pause_s: float = 1.4) -> list[dict]:
    """Acciones para que la página cargue `max_posts` publicaciones.

    La pausa NO es decorativa: bajar a toda velocidad es la forma más rápida de que te
    detecten. Es el equilibrio entre juntar datos y que la cuenta sobreviva."""
    vueltas = max(3, min(60, -(-int(max_posts) // _POSTS_POR_VUELTA) + 2))
    return [{"do": "scroll_until", "max_rounds": vueltas, "pause_s": pause_s}]


# ---------------------------------------------------------------------------
# Recorrido del JSON capturado
# ---------------------------------------------------------------------------
def _walk(obj, budget: int = 200_000):
    """Recorre el JSON entregando cada dict. Con presupuesto: las respuestas de estas APIs
    son enormes y un recorrido sin tope puede colgar al worker."""
    stack, n = [obj], 0
    while stack and n < budget:
        cur = stack.pop()
        n += 1
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


def _first(d: dict, *claves, default=None):
    """El primer campo presente y no vacío. Estas APIs renombran campos todo el tiempo."""
    for k in claves:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return default


def _num(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _iso(v) -> str:
    """Fecha a ISO. Acepta el formato de X ('Wed Oct 10 20:19:24 +0000 2018'), epoch e ISO."""
    if v in (None, ""):
        return ""
    if isinstance(v, (int, float)) or (isinstance(v, str) and str(v).isdigit()):
        try:
            ts = float(v)
            if ts > 1e11:      # venía en milisegundos
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, OSError, OverflowError):
            return str(v)
    s = str(v)
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
    return s


def _post(**kw) -> dict:
    """Registro normalizado. Misma forma para todas las redes: lo que se entrega no depende de
    la plataforma, así el CSV/JSON de salida es comparable entre ellas."""
    base = {"platform": "", "id": "", "url": "", "author": "", "author_name": "",
            "text": "", "created_at": "", "likes": None, "replies": None,
            "reposts": None, "views": None, "media": []}
    base.update({k: v for k, v in kw.items() if k in base})
    return base


# ---------------------------------------------------------------------------
# X / Twitter
# ---------------------------------------------------------------------------
def _x_author(node: dict) -> tuple[str, str]:
    """(handle, nombre) del tuit. El autor viaja anidado y la ruta cambia seguido, así que se
    busca por forma: el primer sub-objeto que tenga `screen_name`."""
    for d in _walk(node, budget=4000):
        sn = d.get("screen_name")
        if isinstance(sn, str) and sn:
            return sn, str(d.get("name") or "")
    return "", ""


def _x_media(legacy: dict) -> list[str]:
    ent = legacy.get("extended_entities") or legacy.get("entities") or {}
    out = []
    for m in (ent.get("media") or []):
        u = m.get("media_url_https") or m.get("media_url")
        if u:
            out.append(str(u))
    return out


def _x_posts(endpoints: list[dict], max_posts: int) -> list[dict]:
    """Levanta tuits de lo capturado.

    Marcador de forma: `full_text` (el texto completo del tuit). Es de los pocos nombres que X
    no cambió en años y no aparece en otros objetos, así que sirve de ancla sin depender de la
    ruta ni del doc_id de GraphQL del día."""
    vistos: set[str] = set()
    out: list[dict] = []
    for ep in endpoints:
        data = ep.get("json")
        if data is None:
            continue
        for node in _walk(data):
            legacy = node.get("legacy") if isinstance(node.get("legacy"), dict) else None
            src = legacy or node
            texto = _first(src, "full_text", "text")
            if not isinstance(texto, str) or not texto:
                continue
            # `full_text` es la firma del tuit; sin él exigimos otras señales para no levantar
            # cualquier objeto que tenga un campo "text" (hay muchísimos).
            if "full_text" not in src and not ({"created_at", "favorite_count"} <= set(src)):
                continue
            tid = str(_first(node, "rest_id", "id_str") or _first(src, "id_str", "id") or "")
            if not tid or tid in vistos:
                continue
            vistos.add(tid)
            handle, nombre = _x_author(node)
            vistas = node.get("views")
            out.append(_post(
                platform="x", id=tid, text=texto,
                url="https://x.com/" + (handle or "i") + "/status/" + tid,
                author=handle, author_name=nombre,
                created_at=_iso(src.get("created_at")),
                likes=_num(src.get("favorite_count")),
                replies=_num(src.get("reply_count")),
                reposts=_num(src.get("retweet_count")),
                views=_num(vistas.get("count") if isinstance(vistas, dict) else None),
                media=_x_media(src),
            ))
            if len(out) >= max_posts:
                return out
    return out


# ---------------------------------------------------------------------------
# Entrada única
# ---------------------------------------------------------------------------
_EXTRACTORES = {"x": _x_posts}


def extract_posts(platform: str, endpoints: list[dict], *, max_posts: int = 200) -> list[dict]:
    """Posts normalizados a partir de los endpoints capturados. Lista vacía si la plataforma
    todavía no tiene extractor (el job igual entrega el HTML/JSON crudo)."""
    fn = _EXTRACTORES.get(platform or "")
    if fn is None:
        return []
    try:
        return fn(endpoints, max(1, int(max_posts)))
    except Exception:  # noqa: BLE001 — un cambio de formato no puede tumbar el job entero
        return []


def supported() -> list[str]:
    """Plataformas con extractor de posts propio (hoy)."""
    return sorted(_EXTRACTORES)


def needs_session(platform: str) -> bool:
    """¿Esta red exige sesión logueada para mostrar algo? (Todas las grandes, en 2026.)"""
    return (platform or "") in {"x", "linkedin", "facebook", "instagram"}
