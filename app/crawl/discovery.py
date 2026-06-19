"""Discovery de URLs: links del HTML, sitemap.xml y RSS. Ver Capa 2.

Funciones puras sobre texto ya traído (el fetch lo hace el router). Katana queda
como hook perezoso para mapeo profundo de endpoints; sin él, links + sitemap + RSS
cubren el grueso del descubrimiento.
"""
from __future__ import annotations

import importlib.util
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


def _same_domain(a: str, b: str) -> bool:
    return urlsplit(a).hostname == urlsplit(b).hostname


# Segmentos de "chrome" de navegación: aparecen en casi todo sitio y no son contenido.
# Genérico (no ML): login, cuenta, carrito, términos, ayuda, etc.
_CHROME = re.compile(
    r"(?:^|/)(login|signin|sign-in|signup|sign-up|register|registro|registrate|"
    r"logout|salir|logoff|cart|carrito|checkout|account|cuenta|mi-cuenta|myaccount|"
    r"help|ayuda|soporte|support|terms|terminos|tos|privacy|privacidad|legal|cookies|"
    r"feedback|accesibilidad|accessibility|contact|contacto|about|acerca|sitemap|rss|"
    r"share|compartir|print|addresses|notifications|newsletter|preferences|ajustes)"
    r"(?:[/?.#]|$)",
    re.I,
)


def _is_chrome(url: str) -> bool:
    return bool(_CHROME.search(urlsplit(url).path))


def extract_links(html: str, base_url: str, *, same_domain: bool = True,
                  drop_chrome: bool = False, scope_path: str | None = None) -> list[str]:
    """Links absolutos del HTML. Filtra fragmentos, mailto/js y (opcional) externos.

    `drop_chrome`: descarta links de navegación/boilerplate (login, carrito, términos…).
    `scope_path`: si se pasa, solo links cuyo path arranca con ese prefijo (foco de sección).
    """
    parser = _LinkParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 — HTML roto: lo que se pudo parsear alcanza
        pass
    out: list[str] = []
    seen = set()
    for href in parser.hrefs:
        href = href.strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:", "data:")):
            continue
        absolute = urljoin(base_url, href).split("#", 1)[0]
        if not absolute.startswith(("http://", "https://")):
            continue
        if same_domain and not _same_domain(absolute, base_url):
            continue
        if drop_chrome and _is_chrome(absolute):
            continue
        if scope_path and not urlsplit(absolute).path.startswith(scope_path):
            continue
        if absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return out


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()  # ignora el namespace


def parse_sitemap(xml_text: str) -> list[str]:
    """URLs de un sitemap o sitemapindex (<loc>...</loc>)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    locs = [el.text.strip() for el in root.iter() if _localname(el.tag) == "loc" and el.text]
    return [u for u in locs if u.startswith(("http://", "https://"))]


def parse_rss(xml_text: str) -> list[str]:
    """Links de un feed RSS/Atom (<item><link> o <entry><link href=...>)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[str] = []
    for el in root.iter():
        if _localname(el.tag) != "link":
            continue
        url = (el.text or "").strip() or el.attrib.get("href", "").strip()
        if url.startswith(("http://", "https://")):
            out.append(url)
    return out


def katana_available() -> bool:
    """Katana es un binario externo (Go). Hook: detectarlo en PATH."""
    import shutil

    return shutil.which("katana") is not None
