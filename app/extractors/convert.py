"""Conversión HTML → markdown MULTI-MOTOR. Lo mejor de cada uno. Ver Capa 5.

El diferencial de Fisherboy no es elegir un conversor, sino correr varios y quedarse
con el mejor resultado POR PÁGINA. Distintos motores ganan en distintos sitios:

- Crawl4AI (fit_markdown): poda nav/boilerplate por densidad. Brilla en sitios con
  estructura compleja (docs, e-commerce, apps).
- Trafilatura: el mejor extractor de texto principal de artículos/noticias.

Se corren los disponibles, se puntúa cada salida por densidad de prosa real (premia
párrafos, castiga listas de links/nav), y se devuelve la ganadora. Si solo hay uno, ese.
Docling/MarkItDown quedan para documentos (los maneja el sub-pipeline de Escriba).
"""
from __future__ import annotations

import importlib.util
import re

from .text_main import ExtractError, html_to_markdown


def _has(mod: str) -> bool:
    return importlib.util.find_spec(mod) is not None


def _crawl4ai_markdown(html: str, url: str | None) -> str:
    """Markdown de Crawl4AI con PruningContentFilter → fit_markdown (LLM-ready)."""
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

    gen = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=0.45, threshold_type="fixed")
    )
    result = gen.generate_markdown(input_html=html, base_url=url or "")
    fit = (getattr(result, "fit_markdown", None) or "").strip()
    raw = (getattr(result, "raw_markdown", None) or "").strip()
    if fit and len(fit) >= 0.2 * max(len(raw), 1):
        return fit
    return raw or fit


_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _prose_score(md: str) -> int:
    """Puntúa la salida por cantidad de PROSA real (no nav/links).

    Suma la longitud de las líneas sustanciales (párrafos largos, encabezados),
    descontando las que son mayormente markup de links (típico de menús/nav).
    """
    if not md:
        return 0
    score = 0
    for line in md.splitlines():
        s = line.strip()
        if len(s) < 40 and not s.startswith("#"):
            continue
        link_chars = sum(len(m.group(0)) for m in _LINK_RE.finditer(s))
        if link_chars > 0.4 * len(s):
            continue  # línea dominada por links → nav, no prosa
        score += len(s)
    return score


def html_to_markdown_rich(html: str, *, url: str | None = None) -> tuple[str, str]:
    """Corre los motores disponibles, puntúa y devuelve (markdown, motor_ganador)."""
    candidates: list[tuple[str, str]] = []

    if _has("crawl4ai"):
        try:
            md = _crawl4ai_markdown(html, url)
            if md and md.strip():
                candidates.append(("crawl4ai", md.strip()))
        except Exception:  # noqa: BLE001 — un motor que falla no frena al resto
            pass

    try:
        md_t = html_to_markdown(html, url=url)
        if md_t and md_t.strip():
            candidates.append(("trafilatura", md_t.strip()))
    except ExtractError:
        pass

    if not candidates:
        raise ExtractError("Ningún motor de conversión encontró contenido.")

    best_name, best_md = max(candidates, key=lambda c: _prose_score(c[1]))
    return best_md, best_name


__all__ = ["html_to_markdown_rich", "ExtractError"]
