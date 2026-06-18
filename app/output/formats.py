"""Armado de la salida final según output_format. Ver Capa 7.

markdown  → el texto ya convertido y anonimizado, tal cual (o bundle multipágina).
llms_txt  → markdown con un encabezado LLM-friendly (título + fuente) por documento.
json      → la extracción estructurada del LLM (la arma extractors/llm_extract).

Todas las entradas ya vienen anonimizadas: la rama local pasa siempre por Anonimal
antes de llegar acá (ADR-002), así que estos formateadores no tocan privacidad.
"""
from __future__ import annotations


def to_llms_txt(body_md: str, *, title: str | None = None, source_url: str | None = None) -> str:
    """Envuelve el markdown en un encabezado llms.txt-style (título + fuente)."""
    head = []
    head.append(f"# {title.strip()}" if title and title.strip() else "# Documento")
    if source_url:
        head.append(f"\n> Fuente: {source_url}")
    return "\n".join(head) + "\n\n" + (body_md or "").strip() + "\n"


def bundle_pages(sections: list[tuple[str, str]]) -> str:
    """Concatena varias páginas crawleadas en un solo markdown, con encabezado por URL.

    `sections` = lista de (url, markdown_anonimizado).
    """
    parts: list[str] = []
    for url, md in sections:
        parts.append(f"## {url}\n\n{(md or '').strip()}")
    return "\n\n---\n\n".join(parts).strip() + "\n"


def title_from_markdown(md: str) -> str | None:
    """Saca un título tentativo del primer encabezado markdown, si hay."""
    for line in (md or "").splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip() or None
        if line:
            break
    return None
