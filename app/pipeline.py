"""Pipeline de un job: gather (fetch/crawl) → markdown → privacidad → salida → sobre.

Un job vive entero en un worker (ADR-001): no se reparte entre procesos. Esta
función es pura respecto de la infraestructura — recibe sus dependencias por
inyección, así se testea sin red, sin Redis, sin Anonimal ni LLM reales.

El privacy_mode manda en las DOS ramas de salida (ADR-002, rev. 2026-06-18):

- `directo`            → contenido CRUDO, no pasa por Anonimal. Solo para data no
  sensible; es responsabilidad de quien lo pide.
- `opaco`              → la PII se enmascara con marcadores tipados estables
  («PERSONA_1»). Fail-closed: si Anonimal falla, el job termina en error sin devolver
  contenido crudo.
- `reversible`         → como opaco en la rama local; en la rama LLM (json) además
  re-hidrata la salida con la tabla de mapeo cifrada.

Antes la rama local anonimizaba SIEMPRE; se cambió para que `directo` signifique de
verdad "crudo" en todos los formatos (decisión del dueño del proyecto).

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
    post: Callable[..., FetchResult] | None = None              # POST SSRF-safe (paginado ASP.NET)
    capture: Callable[..., list] | None = None                  # captura de XHR/JSON oculto (ADR-010)
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

    def _fetch(url: str, tier_hint=None, proxy=None, solver=None, cookies=None, max_tier=None) -> FetchResult:
        return router.fetch(url, tier_hint=tier_hint, proxy_override=proxy,
                            solver_override=solver, cookies_override=cookies,
                            max_tier_override=max_tier)

    def _extract(html: str, url: str | None) -> str:
        return html_to_markdown_rich(html, url=url)[0]

    def _post(u: str, data: dict, proxy=None, cookies=None) -> FetchResult:
        from .fetchers.static import fetch_post
        return fetch_post(u, data, allow_private=settings.allow_private_targets,
                          max_bytes=settings.fetch_max_bytes, cookies=cookies, proxy=proxy)

    def _capture(u: str, tier_hint=None, proxy=None, solver=None, cookies=None, max_tier=None) -> list:
        from .fetchers.base import FetchContext
        from .fetchers.capture import capture_xhr
        ctx = FetchContext(
            timeout_s=settings.fetch_timeout_s, max_bytes=settings.fetch_max_bytes,
            allow_private=settings.allow_private_targets, headless=settings.browser_headless,
            settle_s=settings.browser_settle_s, scroll=settings.browser_scroll,
            user_agent=settings.browser_user_agent, locale=settings.browser_locale,
            proxy=proxy, cookies=cookies or {},
        )
        return capture_xhr(u, ctx)

    # robots: trae el robots.txt con el mismo router (tier 0), tolerante a fallos.
    def _robots_text(robots_url: str) -> str | None:
        try:
            return router.fetch(robots_url, tier_hint=0).text
        except Exception:  # noqa: BLE001
            return None

    robots = RobotsChecker(_robots_text, user_agent="Fisherboy")

    def _crawl(seed: str, *, tier_hint=None, max_pages=10, max_depth=1,
               proxy=None, solver=None, cookies=None, max_tier=None) -> list[FetchResult]:
        pages = _crawl_bfs(
            seed,
            fetch=lambda u: router.fetch(u, tier_hint=tier_hint, proxy_override=proxy,
                                         solver_override=solver, cookies_override=cookies,
                                         max_tier_override=max_tier),
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
        post=_post,
        capture=_capture,
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


def _job_overrides(sobre: Sobre) -> dict:
    """Overrides por job (panel Avanzado): proxy fijo y solver de CAPTCHA externo."""
    kw: dict = {}
    proxy = sobre.meta.get("proxy")
    if proxy:
        kw["proxy"] = proxy
    cu, ck = sobre.meta.get("captcha_api_url"), sobre.meta.get("captcha_api_key")
    if cu and ck:
        from .net.captcha import ExternalSolver
        kw["solver"] = ExternalSolver(cu, ck)
    raw_cookies = sobre.meta.get("cookies")
    if raw_cookies:
        from .security.cookies import parse_cookies
        jar = parse_cookies(raw_cookies)
        if jar:
            kw["cookies"] = jar
    if sobre.meta.get("max_tier") is not None:
        kw["max_tier"] = int(sobre.meta["max_tier"])
    return kw


def _sweep_pagination(sobre, deps, url, tier_hint, max_pages, overrides) -> list[FetchResult]:
    """Barre el paginado de la URL semilla y junta los hipervínculos de cada página."""
    from .crawl.discovery import extract_links
    from .crawl.pagination import paginate

    first = deps.fetch(url, tier_hint, **overrides)
    get_text = lambda u: deps.fetch(u, tier_hint, **overrides).text  # noqa: E731

    post_text = None
    if deps.post is not None:
        post_kw = {k: overrides[k] for k in ("proxy", "cookies") if k in overrides}
        post_text = lambda u, data: deps.post(u, data, **post_kw).text  # noqa: E731

    swept = paginate(first.text, first.url, get_text=get_text, post_text=post_text,
                     max_pages=max(max_pages, 1))

    results, links, seen = [], [], set()
    for u, html in swept:
        results.append(FetchResult(
            url=u, status_code=200, content=html.encode("utf-8", "replace"),
            text=html, content_type="text/html", tier=first.tier,
        ))
        for link in extract_links(html, u, same_domain=False):
            if link not in seen:
                seen.add(link)
                links.append(link)

    sobre.meta["hyperlinks"] = links[:1000]
    sobre.meta["hyperlinks_total"] = len(links)
    sobre.meta["paginas_barridas"] = len(results)
    return results


def _gather(sobre: Sobre, deps: PipelineDeps) -> list[FetchResult]:
    """Trae la(s) página(s): crawl si se pidió y hay crawler, si no un solo fetch."""
    url = str(sobre.source_url)
    tier_hint = sobre.meta.get("tier_hint")
    crawl_depth = int(sobre.meta.get("crawl_depth", 0) or 0)
    max_pages = int(sobre.meta.get("max_pages", 1) or 1)
    overrides = _job_overrides(sobre)

    # Modo barrer paginado: recorre TODAS las páginas (postback ASP.NET / links / ?page=)
    # y junta los hipervínculos de cada una.
    if sobre.meta.get("paginate"):
        return _sweep_pagination(sobre, deps, url, tier_hint, max_pages, overrides)

    if deps.crawl is not None and (crawl_depth > 0 or max_pages > 1):
        pages = deps.crawl(
            url, tier_hint=tier_hint, max_pages=max_pages, max_depth=crawl_depth, **overrides
        )
        if not pages:
            raise FetchError("El crawl no trajo ninguna página.")
        return pages
    return [deps.fetch(url, tier_hint, **overrides)]


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


def _capture_branch(sobre: Sobre, deps: PipelineDeps) -> Sobre:
    """Captura los endpoints JSON/XHR ocultos y los entrega. Respeta privacy_mode."""
    url = str(sobre.source_url)
    tier_hint = sobre.meta.get("tier_hint")
    overrides = _job_overrides(sobre)
    endpoints = deps.capture(url, tier_hint, **overrides)
    sobre.tier_usado = FetchTier.BROWSER
    sobre.fetched_at = datetime.now(timezone.utc)
    sobre.meta.update({
        "api_endpoints": len(endpoints),
        "api_urls": [e.get("url") for e in endpoints[:20]],
    })
    body = json.dumps(endpoints, ensure_ascii=False, indent=2)

    if sobre.privacy_mode is PrivacyMode.DIRECTO:
        sobre.content_json = {"endpoints": endpoints}
        sobre.content_md = body
        sobre.anonimizado = False
    else:  # opaco/reversible: enmascara la representación (puede traer PII)
        anon, n = deps.anonymize_opaco(body)
        sobre.content_md = anon
        sobre.anonimizado = True
        sobre.meta["entidades_anonimizadas"] = n

    sobre.status = JobStatus.OK if endpoints else JobStatus.ERROR
    if not endpoints:
        sobre.error = (
            "Solo se vieron endpoints de telemetría/tracking, no un API de datos. "
            "Probable: la página está gateada (login/ubicación) o el dato necesita "
            "interacción (buscar/filtrar). Probá con cookies de sesión o el tier browser."
        )
    if deps.persist is not None:
        deps.persist(sobre)
    if deps.metrics is not None:
        deps.metrics.inc_job(sobre.status.value)
    log.info("captura API", extra={"job_id": sobre.job_id, "endpoints": len(endpoints)})
    return sobre


def process_job(sobre: Sobre, deps: PipelineDeps) -> Sobre:
    """Procesa un job de punta a punta. Nunca lanza: las fallas quedan en el sobre."""
    sobre.status = JobStatus.EN_PROCESO
    url = str(sobre.source_url)
    log.info("job en proceso", extra={"job_id": sobre.job_id, "url": url})

    try:
        # Keystone (ADR-010): capturar el API/XHR oculto en vez de pelear el HTML.
        if sobre.meta.get("capture_api") and deps.capture is not None:
            return _capture_branch(sobre, deps)

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
            # El privacy_mode manda también en la rama local (ADR-002, rev. 2026-06-18):
            #   directo            → crudo, NO pasa por Anonimal (solo data no sensible)
            #   opaco / reversible → enmascarado opaco con marcadores estables (fail-closed)
            if sobre.privacy_mode is PrivacyMode.DIRECTO:
                body, n_entidades = combined, 0
                sobre.anonimizado = False
            else:
                body, n_entidades = deps.anonymize_opaco(combined)
                sobre.anonimizado = True
            if sobre.output_format is OutputFormat.LLMS_TXT:
                body = to_llms_txt(body, title=title_from_markdown(body), source_url=url)
            sobre.content_md = body
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
