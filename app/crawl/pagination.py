"""Motor de paginado. Barre TODAS las páginas, no solo la primera. Ver Capa 1-2.

Cubre los tres mecanismos reales más comunes:

1. ASP.NET WebForms (postback): el "siguiente"/número de página es un
   `__doPostBack('grid','Page$N')` que POSTea el form con `__VIEWSTATE` +
   `__VIEWSTATEGENERATOR` (+ `__EVENTVALIDATION` si está). El viewstate CAMBIA por
   página, así que se re-extrae de cada respuesta. Es el caso de buenosairescompras.gob.ar.
2. Links de paginado: rel="next", anchors "siguiente"/"próxima"/"next"/"›", numerados.
3. Query param: ?page=N / ?pagina=N incrementable.

`paginate()` recibe funciones get/post (SSRF-safe, las provee el pipeline) y devuelve la
lista de (url, html) de todas las páginas barridas, deduplicando por contenido para no
caer en loops. Paginado por AJAX/UpdatePanel parcial o pager 100% JS cae al browser tier.
"""
from __future__ import annotations

import html as _html
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

_HIDDEN_RE = re.compile(
    r'<input[^>]*type=["\']hidden["\'][^>]*>', re.I)
_NAME_RE = re.compile(r'name=["\']([^"\']+)["\']', re.I)
_VALUE_RE = re.compile(r'value=["\']([^"\']*)["\']', re.I)
_FORM_ACTION_RE = re.compile(r'<form[^>]*action=["\']([^"\']*)["\']', re.I)
_POSTBACK_RE = re.compile(r"__doPostBack\('([^']+)','(Page\$[^']*)'\)")
_NEXT_REL_RE = re.compile(r'<(?:a|link)[^>]+rel=["\']next["\'][^>]*href=["\']([^"\']+)["\']', re.I)
_ANCHOR_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_NEXT_TEXT = re.compile(r'\b(siguiente|pr[oó]xima|next)\b|^\s*[›»>]\s*$', re.I)


def is_aspnet(html: str) -> bool:
    return "__VIEWSTATE" in html and "__doPostBack" in html


def parse_form_state(html: str) -> dict:
    """Extrae action + TODOS los inputs hidden del form (viewstate incluido)."""
    fields: dict[str, str] = {}
    for tag in _HIDDEN_RE.findall(html):
        name = _NAME_RE.search(tag)
        if not name:
            continue
        val = _VALUE_RE.search(tag)
        fields[_html.unescape(name.group(1))] = _html.unescape(val.group(1)) if val else ""
    action = _FORM_ACTION_RE.search(html)
    return {"action": _html.unescape(action.group(1)) if action else "", "fields": fields}


def find_postback_pagers(html: str) -> list[tuple[str, str]]:
    """(target, arg) de cada link de paginado postback (arg = 'Page$N' / 'Page$Next')."""
    seen, out = set(), []
    for target, arg in _POSTBACK_RE.findall(_html.unescape(html)):
        key = (target, arg)
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _pick_next_pager(pagers: list[tuple[str, str]], page_num: int) -> tuple[str, str] | None:
    """Elige el postback para la página siguiente: Page$(n+1) si está, si no Page$Next."""
    want = f"Page${page_num + 1}"
    for target, arg in pagers:
        if arg == want:
            return target, arg
    for target, arg in pagers:
        if arg in ("Page$Next", "Page$Last"):
            return target, arg
    return None


def build_postback(state: dict, target: str, arg: str, base_url: str) -> tuple[str, dict]:
    """Arma (url, data) del POST de paginado ASP.NET."""
    data = dict(state["fields"])
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = arg
    action = state.get("action") or ""
    url = urljoin(base_url, action) if action else base_url
    return url, data


def _bump_query_page(url: str) -> str | None:
    """Si la URL tiene ?page=/?pagina=, devuelve la URL con el número +1."""
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    for key in ("page", "pagina", "p", "pg", "offset"):
        if key in q:
            try:
                n = int(q[key][0])
            except (ValueError, IndexError):
                continue
            q[key] = [str(n + 1)]
            new_q = urlencode({k: v[0] for k, v in q.items()})
            return urlunsplit((parts.scheme, parts.netloc, parts.path, new_q, ""))
    return None


def find_next_link(html: str, base_url: str) -> str | None:
    """URL de la página siguiente vía rel=next / anchor 'siguiente'/'›' / ?page=."""
    m = _NEXT_REL_RE.search(html)
    if m:
        return urljoin(base_url, _html.unescape(m.group(1)))
    for href, text in _ANCHOR_RE.findall(html):
        label = re.sub(r"<[^>]+>", "", text).strip()
        if _NEXT_TEXT.search(label) and not href.lower().startswith("javascript:"):
            return urljoin(base_url, _html.unescape(href))
    return _bump_query_page(base_url)


def paginate(html0: str, url0: str, *, get_text, post_text=None, max_pages: int = 10,
             max_total_bytes: int = 80 * 1024 * 1024, deadline_s: float = 180.0) -> list[tuple[str, str]]:
    """Barre hasta `max_pages` páginas desde (url0, html0). Devuelve [(url, html), ...].

    `get_text(url)->html` y `post_text(url, data)->html` los provee el llamador
    (SSRF-safe). Si `post_text` es None, no se intenta el postback ASP.NET.

    Cortes anti-DoS además de max_pages (auditoría 2026-06): presupuesto de bytes
    ACUMULADOS y deadline de wall-clock, para que un sitio que genera contenido
    distinto por página (dedup por hash nunca dispara) no barra indefinidamente."""
    import time
    pages = [(url0, html0)]
    seen_hashes = {hash(html0)}
    html, url, page_num = html0, url0, 1
    total_bytes = len(html0.encode("utf-8", "replace"))
    deadline = time.monotonic() + deadline_s if deadline_s else None

    while len(pages) < max_pages:
        if deadline is not None and time.monotonic() > deadline:
            break
        if max_total_bytes and total_bytes >= max_total_bytes:
            break
        nxt_url, nxt_html = None, None

        if is_aspnet(html) and post_text is not None:
            pagers = find_postback_pagers(html)
            choice = _pick_next_pager(pagers, page_num)
            if choice:
                post_url, data = build_postback(parse_form_state(html), *choice, url)
                try:
                    nxt_html = post_text(post_url, data)
                    nxt_url = post_url
                except Exception:  # noqa: BLE001 — una página que falla corta el barrido
                    break

        if nxt_html is None:
            link = find_next_link(html, url)
            if link and link != url:
                try:
                    nxt_html = get_text(link)
                    nxt_url = link
                except Exception:  # noqa: BLE001
                    break

        if not nxt_html:
            break
        h = hash(nxt_html)
        if h in seen_hashes:        # misma página → se acabó el paginado real
            break
        seen_hashes.add(h)
        pages.append((nxt_url, nxt_html))
        total_bytes += len(nxt_html.encode("utf-8", "replace"))
        html, url, page_num = nxt_html, nxt_url, page_num + 1

    return pages
