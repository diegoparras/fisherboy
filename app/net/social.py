"""Redes sociales — posts como datos, no como HTML. Ver ADR-012.

El problema de scrapear X/LinkedIn/Facebook en 2026 no es el HTML: es que todo vive detrás de
un login y de una API interna (GraphQL / Voyager) cuyos identificadores ROTAN cada pocas
semanas. Los caminos fáciles murieron (snscrape, Twint, Nitter) y los guest tokens quedaron
atados a la huella del navegador, con las IPs de datacenter baneadas.

Lo que sí aguanta, y es lo que hace este módulo:

  navegador con sesión real  →  la página llama a su propia API  →  interceptamos ESA respuesta

Y sobre lo interceptado no se busca por RUTA sino por FORMA: se recorre el JSON y se levanta
todo objeto que "parezca un post". Cada red trae su propio marcador de forma, estable en el
tiempo aunque el árbol alrededor cambie:

  X          `full_text`                      (el texto del tuit)
  LinkedIn   `$type: com.linkedin.voyager…`   (el discriminador de entidad de Voyager)
  Facebook   `__typename: Story`              (el discriminador de Relay)

Si mañana mueven el objeto de lugar, el extractor sigue andando. Es la diferencia entre un
scraper que dura semanas y uno que dura meses, y evita hardcodear los doc_id de GraphQL.

Facebook tiene además un segundo camino: `mbasic.facebook.com` sirve HTML plano, sin JS. Es
más frágil que el JSON pero a veces es lo único que hay, así que el extractor mira los dos.

No reemplaza a la sesión: sin cookies de una cuenta logueada estas plataformas no muestran
nada. Ver docs/SOCIAL.md.
"""
from __future__ import annotations

import re
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

# Cuántos posts entran, más o menos, por cada vuelta de scroll.
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


def prefer_url(url: str) -> str:
    """Versión de la URL que conviene pedir.

    Facebook: `mbasic` sirve HTML plano, sin JS ni GraphQL ofuscado. Es MUCHO más fácil de
    leer que el sitio normal, así que si el usuario pegó www lo mandamos ahí."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.endswith("facebook.com") and not host.startswith("mbasic."):
        resto = url.split(host, 1)[-1]
        return "https://mbasic.facebook.com" + resto
    return url


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


def _deep_text(obj, *claves) -> str:
    """Busca la primera cadena no vacía bajo alguna de esas claves, a cualquier profundidad.

    LinkedIn y Facebook envuelven el texto en capas ({text:{text:"…"}}, {message:{text:"…"}})
    y la cantidad de capas cambia según el tipo de post. Buscar por clave y no por ruta
    ahorra tener un caso especial por cada variante."""
    for d in _walk(obj, budget=8000):
        for k in claves:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                t = v.get("text")
                if isinstance(t, str) and t.strip():
                    return t
    return ""


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


def _x_posts(endpoints, max_posts, html=""):
    """Marcador de forma: `full_text`. Es de los pocos nombres que X no cambió en años y no
    aparece en otros objetos, así que ancla sin depender del doc_id del día."""
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
# LinkedIn (API Voyager)
# ---------------------------------------------------------------------------
_LI_URN = re.compile(r"urn:li:(?:activity|share|ugcPost):(\d+)")


def _li_urn(node: dict) -> str:
    """El id del post sale de la URN de LinkedIn, que viaja en varios campos distintos."""
    for k in ("entityUrn", "urn", "objectUrn", "dashEntityUrn", "preDashEntityUrn"):
        m = _LI_URN.search(str(node.get(k) or ""))
        if m:
            return m.group(1)
    meta = node.get("updateMetadata")
    if isinstance(meta, dict):
        m = _LI_URN.search(str(meta.get("urn") or ""))
        if m:
            return m.group(1)
    return ""


def _li_author(node: dict) -> tuple[str, str]:
    """(handle, nombre). El handle sale del link al perfil (/in/alguien)."""
    actor = node.get("actor") if isinstance(node.get("actor"), dict) else node
    nombre = _deep_text(actor, "name") if isinstance(actor, dict) else ""
    handle = ""
    for d in _walk(actor if isinstance(actor, dict) else node, budget=4000):
        for k in ("actionTarget", "url", "navigationUrl"):
            m = re.search(r"linkedin\.com/(?:in|company)/([^/?#]+)", str(d.get(k) or ""))
            if m:
                handle = m.group(1)
                break
        if handle:
            break
    return handle, nombre


def _li_counts(node: dict) -> tuple[int | None, int | None, int | None]:
    """(likes, comentarios, republicaciones) de socialDetail, que también se mueve de lugar."""
    for d in _walk(node, budget=6000):
        if "numLikes" in d or "numComments" in d:
            return (_num(d.get("numLikes")), _num(d.get("numComments")),
                    _num(d.get("numShares")))
    return None, None, None


def _li_posts(endpoints, max_posts, html=""):
    """Marcador de forma: el `$type` de Voyager. LinkedIn discrimina cada entidad con
    `com.linkedin.voyager.…`, y los posts del feed son UpdateV2 / Update."""
    vistos: set[str] = set()
    out: list[dict] = []
    for ep in endpoints:
        data = ep.get("json")
        if data is None:
            continue
        for node in _walk(data):
            tipo = str(node.get("$type") or node.get("_type") or "")
            es_update = "voyager" in tipo and ("Update" in tipo or "Share" in tipo)
            # Algunas respuestas no traen $type; ahí sirve la combinación commentary + actor.
            if not es_update and not ("commentary" in node or "commentaryV2" in node):
                continue
            texto = _deep_text(
                node.get("commentary") or node.get("commentaryV2") or {}, "text")
            if not texto:
                continue
            pid = _li_urn(node)
            clave = pid or texto[:80]
            if clave in vistos:
                continue
            vistos.add(clave)
            handle, nombre = _li_author(node)
            likes, comentarios, compartidos = _li_counts(node)
            out.append(_post(
                platform="linkedin", id=pid, text=texto,
                url=("https://www.linkedin.com/feed/update/urn:li:activity:" + pid) if pid else "",
                author=handle, author_name=nombre,
                created_at=_iso(_first(node, "createdAt", "publishedAt", "createdTime")),
                likes=likes, replies=comentarios, reposts=compartidos,
            ))
            if len(out) >= max_posts:
                return out
    return out


# ---------------------------------------------------------------------------
# Facebook (GraphQL + mbasic HTML)
# ---------------------------------------------------------------------------
_FB_STORY_ID = re.compile(r"story_fbid=(\d+)")
_FB_TAG = re.compile(r"<[^>]+>")


def _fb_from_json(endpoints, max_posts, vistos):
    """Marcador de forma: `__typename: Story`, el discriminador de Relay."""
    out = []
    for ep in endpoints:
        data = ep.get("json")
        if data is None:
            continue
        for node in _walk(data):
            if str(node.get("__typename") or "") != "Story":
                continue
            texto = _deep_text(node.get("message") or node.get("comet_sections") or {}, "text")
            if not texto:
                continue
            pid = str(_first(node, "post_id", "id", default="") or "")
            clave = pid or texto[:80]
            if clave in vistos:
                continue
            vistos.add(clave)
            actores = node.get("actors") or []
            actor = actores[0] if isinstance(actores, list) and actores else {}
            likes = None
            for d in _walk(node.get("feedback") or {}, budget=3000):
                if "reaction_count" in d and isinstance(d["reaction_count"], dict):
                    likes = _num(d["reaction_count"].get("count"))
                    break
            out.append(_post(
                platform="facebook", id=pid, text=texto,
                url=str(_first(node, "url", "permalink_url", default="") or ""),
                author=str((actor or {}).get("id") or ""),
                author_name=str((actor or {}).get("name") or ""),
                created_at=_iso(_first(node, "creation_time", "created_time")),
                likes=likes,
            ))
            if len(out) >= max_posts:
                return out
    return out


def _fb_from_html(html, max_posts, vistos):
    """Camino de `mbasic.facebook.com`: HTML plano, sin JS.

    Es MÁS FRÁGIL que el JSON (es HTML de verdad, cambia sin aviso) pero a veces es lo único
    que hay, y no necesita que la página corra GraphQL. Se ancla en el link al permalink del
    post, que es lo más estable de esa página."""
    if not html:
        return []
    out = []
    try:
        from lxml import html as lx
        doc = lx.fromstring(html)
    except Exception:  # noqa: BLE001 — sin lxml o HTML roto: no es un error del job
        return []
    for cont in doc.xpath("//div[@data-ft] | //article"):
        enlaces = cont.xpath(".//a[contains(@href,'story_fbid=')]/@href")
        pid = ""
        for h in enlaces:
            m = _FB_STORY_ID.search(str(h))
            if m:
                pid = m.group(1)
                break
        texto = " ".join(t.strip() for t in cont.itertext() if t.strip())[:5000]
        if not texto:
            continue
        clave = pid or texto[:80]
        if clave in vistos:
            continue
        vistos.add(clave)
        autor = (cont.xpath(".//h3//a/text()") or cont.xpath(".//strong//a/text()") or [""])[0]
        out.append(_post(
            platform="facebook", id=pid, text=texto, author_name=str(autor).strip(),
            url=("https://mbasic.facebook.com/story.php?story_fbid=" + pid) if pid else "",
        ))
        if len(out) >= max_posts:
            break
    return out


def _fb_posts(endpoints, max_posts, html=""):
    """Primero el JSON (más confiable); si no dio nada, el HTML de mbasic."""
    vistos: set[str] = set()
    out = _fb_from_json(endpoints, max_posts, vistos)
    if len(out) < max_posts:
        out += _fb_from_html(html, max_posts - len(out), vistos)
    return out[:max_posts]


# ---------------------------------------------------------------------------
# Entrada única
# ---------------------------------------------------------------------------
_EXTRACTORES = {"x": _x_posts, "linkedin": _li_posts, "facebook": _fb_posts}


def extract_posts(platform: str, endpoints: list[dict], *, max_posts: int = 200,
                  html: str = "") -> list[dict]:
    """Posts normalizados a partir de lo capturado. `html` solo lo usa Facebook (mbasic).
    Lista vacía si la plataforma no tiene extractor: el job igual entrega el contenido crudo."""
    fn = _EXTRACTORES.get(platform or "")
    if fn is None:
        return []
    try:
        return fn(endpoints or [], max(1, int(max_posts)), html or "")
    except Exception:  # noqa: BLE001 — un cambio de formato no puede tumbar el job entero
        return []


def supported() -> list[str]:
    """Plataformas con extractor de posts propio (hoy)."""
    return sorted(_EXTRACTORES)


def needs_session(platform: str) -> bool:
    """¿Esta red exige sesión logueada para mostrar algo? (Todas las grandes, en 2026.)"""
    return (platform or "") in {"x", "linkedin", "facebook", "instagram"}


# ---------------------------------------------------------------------------
# Extractor GENÉRICO por nombres de campo (ADR-014)
# ---------------------------------------------------------------------------
# Los extractores de arriba tienen el ancla escrita a mano. Este toma las anclas como DATO:
# {"texto": "full_text", "autor": "screen_name", ...}. Sirve para dos cosas — que una red sin
# extractor propio funcione igual, y que cuando una plataforma cambie de formato se pueda
# reparar sin tocar código (las anclas nuevas las descubre la IA; ver net/social_ai.py).
#
# Importante: quien descubre las anclas NUNCA produce los datos. Las anclas son solo nombres
# de campo; los valores salen siempre del JSON real que devolvió la plataforma.
ANCLAS = ("texto", "autor", "autor_nombre", "id", "fecha", "likes", "respuestas", "compartidos")


def _walk_con_padres(obj, budget: int = 200_000, max_ancestros: int = 2):
    """Como _walk pero entregando (nodo, ancestros). Hace falta porque el texto suele vivir en
    un sub-objeto ("legacy" en X, "message" en Facebook) mientras el autor y los contadores son
    HERMANOS, colgando del objeto padre. Buscando solo hacia adentro nunca se los encuentra.

    Se guardan pocos ancestros a proposito: subir demasiado haria que un post levante el autor
    del post de al lado."""
    stack, n = [(obj, ())], 0
    while stack and n < budget:
        cur, ancestros = stack.pop()
        n += 1
        if isinstance(cur, dict):
            yield cur, ancestros
            hijos = ancestros[-max_ancestros:] + (cur,)
            stack.extend((v, hijos) for v in cur.values())
        elif isinstance(cur, list):
            stack.extend((v, ancestros) for v in cur)


def extract_with_anchors(endpoints: list[dict], anclas: dict, *, max_posts: int = 200,
                         platform: str = "") -> list[dict]:
    """Levanta posts usando los nombres de campo de `anclas`. `texto` es obligatorio: sin él
    no hay forma de saber qué objeto es un post."""
    campo_texto = str((anclas or {}).get("texto") or "")
    if not campo_texto:
        return []
    vistos: set[str] = set()
    out: list[dict] = []

    def val(raices, clave: str):
        """El valor del campo `clave` buscando en el nodo y en sus ancestros cercanos. Las APIs
        reparten los datos de un mismo post entre varias capas."""
        nombre = (anclas or {}).get(clave)
        if not nombre:
            return None
        for raiz in raices:
            for d in _walk(raiz, budget=4000):
                v = d.get(nombre)
                if v not in (None, "", [], {}):
                    return v.get("text") if isinstance(v, dict) and "text" in v else v
        return None

    for ep in endpoints or []:
        data = ep.get("json")
        if data is None:
            continue
        for nodo, ancestros in _walk_con_padres(data):
            crudo = nodo.get(campo_texto)
            if isinstance(crudo, dict):
                crudo = crudo.get("text")
            if not isinstance(crudo, str) or not crudo.strip():
                continue
            # Se busca primero en el nodo del texto y despues en sus ancestros (del mas cercano
            # al mas lejano), que es donde suelen estar el autor y los contadores.
            raices = (nodo,) + tuple(reversed(ancestros))
            ident = str(val(raices, "id") or "") or crudo[:80]
            if ident in vistos:
                continue
            vistos.add(ident)
            out.append(_post(
                platform=platform or "", id=str(val(raices, "id") or ""), text=crudo,
                author=str(val(raices, "autor") or ""),
                author_name=str(val(raices, "autor_nombre") or ""),
                created_at=_iso(val(raices, "fecha")),
                likes=_num(val(raices, "likes")),
                replies=_num(val(raices, "respuestas")),
                reposts=_num(val(raices, "compartidos")),
            ))
            if len(out) >= max_posts:
                return out
    return out
