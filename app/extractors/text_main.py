"""Extracción del texto principal con Trafilatura. v1. Ver FISHERBOY-build §5.

Trafilatura aísla el cuerpo del artículo del boilerplate (nav, footers, ads) y lo
devuelve como markdown. En v2 Crawl4AI toma la conversión rica; en v1 alcanza con
esto para atravesar una URL de punta a punta.
"""
from __future__ import annotations


class ExtractError(Exception):
    """No se pudo extraer texto útil del HTML."""


def html_to_markdown(html: str, *, url: str | None = None) -> str:
    """HTML → markdown del contenido principal. Lanza ExtractError si no hay nada."""
    try:
        import trafilatura
    except ImportError as e:  # pragma: no cover - dependencia obligatoria en runtime
        raise ExtractError("Trafilatura no está instalado.") from e

    md = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_links=True,
        include_images=False,
        favor_precision=True,
    )
    if not md or not md.strip():
        # Fallback: texto plano sin formato antes de rendirse.
        md = trafilatura.extract(html, url=url, favor_recall=True)
    if not md or not md.strip():
        raise ExtractError("No se encontró contenido principal en la página.")
    return md.strip()
