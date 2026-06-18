"""Pipeline de un job: gather (fetch/crawl) → markdown → privacidad → salida → sobre.

Un job vive entero en un worker (ADR-001): no se reparte entre procesos. Esta
función es pura respecto de la infraestructura — recibe sus dependencias por
inyección, así se testea sin red, sin Redis, sin Anonimal ni LLM reales.

Dos ramas de salida, con privacidad distinta (ADR-002):

- LOCAL (markdown / llms.txt): SIEMPRE pasa por Anonimal en modo opaco antes de
  salir, sin importar el privacy_mode. Si Anonimal falla, el job termina en error sin
  devolver contenido crudo (fail-closed).
- LLM (json, extracción estructurada): acá manda el privacy_mode. `directo` manda el
  texto crudo; `opaco` lo pseudonimiza y deja la salida pseudonimizada; `reversible`
  lo pseudonimiza, extrae, y RE-HIDRATA la salida local con la tabla de mapeo.

Las dependencias avanzadas (crawl, reversible, llm, persist, metrics) son opcionales:
si no están inyectadas, esa capacidad simplemente no se usa.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from .extractors.documents import filename_from_url, is_document
from .extractors.llm_extract import LLMError, extract_structured
from .extractors.text_main import ExtractError, html_to_markdown
from .fetchers.base import FetchError, FetchResult
from .logging import get_logger
from .models import FetchTier, JobStatus, OutputFormat, PrivacyMode, Sobre
from .output.formats import bundle_pages, title_from_markdown, to_llms_txt
from .privacy.anonimal_client import AnonimalClient, AnonimalError

log = get_logger("fisherboy.pipeline")


@dataclass
class PipelineDeps:
    """Dependencias inyectables. Las tres primeras son obligatorias; el resto opcional."""

    fetch: Callable[..., FetchResult]                   # (url, tier_hint=None) -> FetchResult
    extract: Callable[[str, str | None], str]           # (html, url) -> markdown
    anonymize_opaco: Callable[[str], tuple[str, int]]   # texto -> (anon, n_entidades)
    crawl: Callable[..., list[FetchResult]] | None = None       # (seed, tier_hint, max_pages, max_depth)
    convert_document: Callable[[bytes, str], str] | None = None  # (bytes, filename) -> markdown
    reversible: object | None = None                    # ReversibleAnonymizer
    llm_complete: Callable[[str, str], str] | None = None       # (system, user) -> str
    persist: Callable[[Sobre], bool] | None = None
    index_content: Callable[[Sobre], bool] | None = None        # embeddings → vector store
    metrics: object | None = None


def build_default_deps(settings, *, redis_client=None) -> PipelineDeps:
    """Arma las dependencias reales desde la config: router, Anonimal, conversión,
    crawler, reversible, LLM, persistencia y métricas."""
    from .crawl.crawler import crawl as _crawl_bfs
    from .crawl.discovery import extract_links  # noqa: F401  (lo usa el crawler)
    from .crawl.robots import RobotsChecker
    from .extractors.convert import html_to_markdown_rich
    from .fetchers.router import build_router

    anon = AnonimalClient(
        settings.anonimal_url,
        timeout_s=settings.anonimal_timeout_s,
        service_token=settings.anonimal_token,
    )

    if redis_client is None:
        try:
            from .queue import build_redis
            redis_client = build_redis(settings.redis_url)
        except Exception:  # noqa: BLE001 — sin Redis, cache de tier/reversible en memoria
            redis_client = None

    router = build_router(settings, redis_client=redis_client)

    def _fetch(url: str, tier_hint: int | None = None) -> FetchResult:
        return router.fetch(url, tier_hint=tier_hint)

    def _extract(html: str, url: str | None) -> str:
        return html_to_markdown_rich(html, url=url)[0]

    # robots: trae el robots.txt con el mismo router (tier 0), tolerante a fallos.
    def _robots_text(robots_url: str) -> str | None:
        try:
            return router.fetch(robots_url, tier_hint=0).text
        except Exception:  # noqa: BLE001
            return None

    robots = RobotsChecker(_robots_text, user_agent="Fisherboy")

    def _crawl(seed: str, *, tier_hint=None, max_pages=10, max_depth=1) -> list[FetchResult]:
        pages = _crawl_bfs(
            seed,
            fetch=lambda u: router.fetch(u, tier_hint=tier_hint),
            robots_allowed=robots.allowed if settings.respect_robots else None,
            max_pages=max_pages,
            max_depth=max_depth,
        )
        return [p.result for p in pages]

    deps = PipelineDeps(
        fetch=_fetch,
        extract=_extract,
        anonymize_opaco=anon.process_opaco,
        crawl=_crawl,
    )

    # Sub-pipeline documental: delega PDFs/docs a Escriba si ESCRIBA_URL está.
    if settings.escriba_url:
        from .extractors.documents import EscribaClient
        escriba = EscribaClient(settings.escriba_url, token=getattr(settings, "escriba_token", ""))

        def _convert_document(content: bytes, filename: str) -> str:
            return escriba.convert(content, filename=filename)

        deps.convert_document = _convert_document

    # Reversible (cripto): opcional, falla cerrado si se pide sin cripto.
    try:
        from .privacy.reversible import build_reversible_anonymizer
        from .privacy_policy import get_policy
        policy = get_policy(settings.privacy_matrix_path)
        deps.reversible = build_reversible_anonymizer(
            settings, anon, policy=policy, redis_client=redis_client
        )
    except Exception as e:  # noqa: BLE001 — sin cripto, reversible no disponible
        log.warning("reversible no disponible", extra={"error": type(e).__name__})

    # LLM: opcional. Sin base_url/key, la rama JSON dará error claro.
    if settings.llm_api_base_url and settings.llm_api_key:
        from .extractors.llm_extract import LLMClient
        client = LLMClient(settings.llm_api_base_url, settings.llm_api_key, settings.llm_model)
        deps.llm_complete = client.complete

    # Persistencia: opcional (Postgres).
    from .store.postgres import build_store
    store = build_store(settings)
    if store is not None:
        deps.persist = store.save_sobre

    # Métricas: singleton (no-op si prometheus_client no está).
    from .obs.metrics import get_metrics
    deps.metrics = get_metrics()

    # Embeddings / vector store: opcional. Necesita LLM + Postgres+pgvector.
    if settings.embeddings_enabled and settings.llm_api_base_url and settings.llm_api_key and store is not None:
        from .store.vectors import EmbeddingClient, PgVectorStore
        emb = EmbeddingClient(settings.llm_api_base_url, settings.llm_api_key, settings.embedding_model)
        vstore = PgVectorStore(store)

        def _index(sobre: Sobre) -> bool:
            text = sobre.content_md or ""
            if not text.strip() or not emb.available():
                return False
            try:
                vector = emb.embed([text[:8000]])[0]
            except Exception as exc:  # noqa: BLE001 — embeddings no debe tumbar el job
                log.warning("embeddings falló", extra={"job_id": sobre.job_id, "error": type(exc).__name__})
                return False
            return vstore.save_embedding(sobre.job_id, vector)

        deps.index_content = _index

    return deps


def _looks_like_html(content_type: str, text: str) -> bool:
    ct = (content_type or "").lower()
    if "html" in ct:
        return True
    if "text/plain" in ct or "markdown" in ct:
        return False
    head = text[:512].lstrip().lower()
    return head.startswith("<!doctype html") or "<html" in head


def _to_markdown(result: FetchResult, deps: PipelineDeps) -> str:
    # Documentos (PDF/doc/xls…) → sub-pipeline de Escriba (Capa 5, ADR-001).
    if deps.convert_document is not None and is_document(result.content_type, result.url):
        return deps.convert_document(result.content, filename_from_url(result.url))
    if _looks_like_html(result.content_type, result.text):
        return deps.extract(result.text, result.url)
    return result.text.strip()


def _gather(sobre: Sobre, deps: PipelineDeps) -> list[FetchResult]:
    """Trae la(s) página(s): crawl si se pidió y hay crawler, si no un solo fetch."""
    url = str(sobre.source_url)
    tier_hint = sobre.meta.get("tier_hint")
    crawl_depth = int(sobre.meta.get("crawl_depth", 0) or 0)
    max_pages = int(sobre.meta.get("max_pages", 1) or 1)

    if deps.crawl is not None and (crawl_depth > 0 or max_pages > 1):
        pages = deps.crawl(url, tier_hint=tier_hint, max_pages=max_pages, max_depth=crawl_depth)
        if not pages:
            raise FetchError("El crawl no trajo ninguna página.")
        return pages
    return [deps.fetch(url, tier_hint)]


def _json_branch(sobre: Sobre, combined_md: str, deps: PipelineDeps) -> None:
    """Rama de extracción estructurada por LLM. Acá manda el privacy_mode (ADR-002)."""
    if deps.llm_complete is None:
        raise LLMError("La salida JSON necesita un LLM configurado (LLM_API_BASE_URL/KEY).")
    schema = sobre.meta.get("extract_schema")
    if not schema:
        raise LLMError("La salida JSON necesita extract_schema en el job.")

    mode = sobre.privacy_mode
    mapping_ref: str | None = None

    if mode is PrivacyMode.DIRECTO:
        text_for_llm = combined_md
    elif mode is PrivacyMode.REVERSIBLE and deps.reversible is not None:
        text_for_llm, mapping_ref, n = deps.reversible.process(combined_md, sobre.rol)
        sobre.anonimizado = True
        sobre.meta["entidades_anonimizadas"] = n
    else:  # opaco (o reversible sin cripto → cae a opaco, nunca crudo)
        text_for_llm, n = deps.anonymize_opaco(combined_md)
        sobre.anonimizado = True
        sobre.meta["entidades_anonimizadas"] = n

    extracted = extract_structured(text_for_llm, schema, complete=deps.llm_complete)

    # Reversible: rehidratar la salida del LLM con la tabla de mapeo (local).
    if mode is PrivacyMode.REVERSIBLE and mapping_ref and deps.reversible is not None:
        reverted = deps.reversible.revert(
            json.dumps(extracted, ensure_ascii=False), mapping_ref, sobre.rol
        )
        extracted = json.loads(reverted)

    sobre.content_json = extracted


def process_job(sobre: Sobre, deps: PipelineDeps) -> Sobre:
    """Procesa un job de punta a punta. Nunca lanza: las fallas quedan en el sobre."""
    sobre.status = JobStatus.EN_PROCESO
    url = str(sobre.source_url)
    log.info("job en proceso", extra={"job_id": sobre.job_id, "url": url})

    try:
        pages = _gather(sobre, deps)
        first = pages[0]
        sobre.tier_usado = FetchTier(first.tier) if first.tier is not None else FetchTier.ESTATICO
        sobre.fetched_at = datetime.now(timezone.utc)

        sections = [(p.url, _to_markdown(p, deps)) for p in pages]
        combined = bundle_pages(sections) if len(sections) > 1 else sections[0][1]
        if not combined or not combined.strip():
            raise ExtractError("No se encontró contenido utilizable.")

        if sobre.output_format is OutputFormat.JSON:
            _json_branch(sobre, combined, deps)
        else:
            # Rama local: SIEMPRE opaco antes de salir (fail-closed).
            anon_md, n_entidades = deps.anonymize_opaco(combined)
            if sobre.output_format is OutputFormat.LLMS_TXT:
                anon_md = to_llms_txt(
                    anon_md, title=title_from_markdown(anon_md), source_url=url
                )
            sobre.content_md = anon_md
            sobre.anonimizado = True
            sobre.meta["entidades_anonimizadas"] = n_entidades

        sobre.status = JobStatus.OK
        sobre.meta.update(
            {
                "final_url": first.url,
                "http_status": first.status_code,
                "content_type": first.content_type,
                "bytes": sum(len(p.content) for p in pages),
                "paginas": len(pages),
                "tier_name": first.meta.get("tier_name"),
                "escalation": first.meta.get("escalation", []),
                "proxied": bool(first.proxy_used),
            }
        )
        if deps.persist is not None:
            deps.persist(sobre)
        if deps.index_content is not None:
            deps.index_content(sobre)
        if deps.metrics is not None:
            deps.metrics.inc_job("ok")
            deps.metrics.inc_tier(int(sobre.tier_usado) if sobre.tier_usado is not None else None)
        log.info("job ok", extra={"job_id": sobre.job_id, "paginas": len(pages),
                                  "tier": int(sobre.tier_usado)})

    except (FetchError, ExtractError, LLMError) as e:
        sobre.status = JobStatus.ERROR
        sobre.error = str(e)
        if deps.metrics is not None:
            deps.metrics.inc_job("error")
        log.info("job error", extra={"job_id": sobre.job_id, "error": str(e)})
    except AnonimalError as e:
        # Fail-closed: NO devolver contenido crudo si no se pudo anonimizar.
        sobre.content_md = None
        sobre.content_json = None
        sobre.anonimizado = False
        sobre.status = JobStatus.ERROR
        sobre.error = f"Anonimización falló, no se devuelve contenido: {e}"
        if deps.metrics is not None:
            deps.metrics.inc_job("error")
        log.warning("fail-closed: anonimización falló", extra={"job_id": sobre.job_id})
    except Exception as e:  # noqa: BLE001 — red de seguridad: nunca tumbar el worker
        sobre.status = JobStatus.ERROR
        sobre.error = f"Error inesperado: {type(e).__name__}."
        if deps.metrics is not None:
            deps.metrics.inc_job("error")
        log.exception("job error inesperado", extra={"job_id": sobre.job_id})

    return sobre
