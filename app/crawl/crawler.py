"""Crawler multipágina BFS con dedup. Ver Capas 1-2.

Recorre desde una URL semilla en anchura hasta `max_depth` saltos o `max_pages`
páginas, lo que llegue primero. Dedup por URL normalizada y por SHA-256 del contenido
(misma página servida en dos URLs no se procesa dos veces). Respeta robots si se le
pasa un checker. El fetch de cada página pasa por el router de tiers (inyectado), así
hereda proxies, escalado y anti-captcha. En v3 Crawlee toma la cola persistente.
"""
from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Callable

from ..fetchers.base import FetchResult
from ..logging import get_logger
from .discovery import extract_links

log = get_logger("fisherboy.crawler")


@dataclass
class CrawlPage:
    url: str
    result: FetchResult
    depth: int
    parent: str | None = None   # URL que linkeó a esta (para armar el árbol)


def crawl(
    seed_url: str,
    *,
    fetch: Callable[[str], FetchResult],
    robots_allowed: Callable[[str], bool] | None = None,
    max_pages: int = 10,
    max_depth: int = 1,
    same_domain: bool = True,
) -> list[CrawlPage]:
    """BFS desde `seed_url`. Devuelve las páginas traídas (semilla incluida)."""
    seen_urls: set[str] = {seed_url}
    seen_hashes: set[str] = set()
    pages: list[CrawlPage] = []
    queue: deque[tuple[str, int, str | None]] = deque([(seed_url, 0, None)])

    while queue and len(pages) < max_pages:
        url, depth, parent = queue.popleft()

        if robots_allowed is not None and not robots_allowed(url):
            log.info("crawl: bloqueado por robots", extra={"url": url})
            continue

        try:
            result = fetch(url)
        except Exception as e:  # noqa: BLE001 — una página que falla no corta el crawl
            log.info("crawl: fetch falló", extra={"url": url, "error": type(e).__name__})
            continue

        digest = hashlib.sha256(result.content).hexdigest()
        if digest in seen_hashes:
            continue  # contenido duplicado (misma página, otra URL)
        seen_hashes.add(digest)
        pages.append(CrawlPage(url=url, result=result, depth=depth, parent=parent))

        if depth < max_depth and len(pages) < max_pages:
            for link in extract_links(result.text, result.url, same_domain=same_domain):
                if link not in seen_urls:
                    seen_urls.add(link)
                    queue.append((link, depth + 1, url))

    log.info("crawl ok", extra={"seed": seed_url, "paginas": len(pages), "depth": max_depth})
    return pages


def build_tree(pages: list[CrawlPage]) -> dict:
    """Arma la jerarquía padre→hijos a partir del parentesco registrado en el crawl."""
    nodes: dict[str, dict] = {}
    for p in pages:
        n = nodes.setdefault(p.url, {"url": p.url, "depth": p.depth, "children": []})
        n["depth"] = p.depth
        bytes_ = len(p.result.content) if p.result else 0
        n["bytes"] = bytes_
        title = next((l.lstrip("# ").strip() for l in (p.result.text or "").splitlines()
                      if l.strip().startswith("#")), None) if p.result else None
        if title:
            n["title"] = title[:90]
    roots = []
    for p in pages:
        node = nodes[p.url]
        parent = nodes.get(p.parent) if p.parent else None
        if parent is not None and parent is not node:
            parent["children"].append(node)
        else:
            roots.append(node)
    return roots[0] if len(roots) == 1 else {"url": "(varios)", "depth": -1, "children": roots}
