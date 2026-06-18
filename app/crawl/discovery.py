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


def extract_links(html: str, base_url: str, *, same_domain: bool = True) -> list[str]:
    """Links absolutos del HTML. Filtra fragmentos, mailto/js y (opcional) externos."""
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
