"""Conversión HTML → markdown. Crawl4AI si está, si no Trafilatura. Ver Capa 5.

Crawl4AI produce un markdown más rico (preserva tablas, estructura, links) y filtra
boilerplate con heurísticas más finas que Trafilatura. Pero es pesado (trae su
propio stack). Import perezoso: si no está, caemos a Trafilatura, que ya cubre el
texto principal. La interfaz es la misma para el pipeline: HTML → markdown.
"""
from __future__ import annotations

import importlib.util

from .text_main import ExtractError, html_to_markdown


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _crawl4ai_markdown(html: str, url: str | None) -> str:
    """Usa el generador de markdown de Crawl4AI sobre HTML ya traído (sin re-fetch).

    Fisherboy ya hizo el fetch con su router de tiers; acá solo convertimos el HTML.
    Con PruningContentFilter, Crawl4AI produce `fit_markdown`: la versión LLM-ready que
    poda navegación, menús, footers y boilerplate por densidad de contenido (lo mejor
    para meter en un LLM). Si el filtro deja muy poco, caemos al markdown crudo.
    """
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    gen = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=0.45, threshold_type="fixed")
    )
    result = gen.generate_markdown(input_html=html, base_url=url or "")
    fit = (getattr(result, "fit_markdown", None) or "").strip()
    raw = (getattr(result, "raw_markdown", None) or "").strip()
    # fit es la salida limpia; si quedó demasiado corta (filtro agresivo), usamos raw.
    if fit and len(fit) >= 0.2 * max(len(raw), 1):
        return fit
    return raw or fit


def html_to_markdown_rich(html: str, *, url: str | None = None) -> tuple[str, str]:
    """Devuelve (markdown, motor). Cae a Trafilatura si Crawl4AI no está o falla."""
    if _has("crawl4ai"):
        try:
            md = _crawl4ai_markdown(html, url)
            if md:
                return md, "crawl4ai"
        except Exception:  # noqa: BLE001 — si Crawl4AI falla, no rompemos: caemos al fallback
            pass
    return html_to_markdown(html, url=url), "trafilatura"


__all__ = ["html_to_markdown_rich", "ExtractError"]
