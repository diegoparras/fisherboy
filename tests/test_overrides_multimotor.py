"""Tests del override de proxy por job y del conversor multi-motor."""
from __future__ import annotations

from app.extractors.convert import _prose_score
from app.fetchers.base import BlockedError, FetchContext, FetchResult
from app.fetchers.router import TierRouter
from app.net.proxies import ProxyPool


class _SpyFetcher:
    """Registra el proxy que recibió en el ctx."""

    tier = 0
    name = "static"

    def __init__(self):
        self.proxies_seen = []

    def available(self):
        return True

    def fetch(self, url, ctx: FetchContext):
        self.proxies_seen.append(ctx.proxy)
        return FetchResult(url=url, status_code=200, content=b"ok", text="ok",
                           content_type="text/html", tier=self.tier)


def test_proxy_override_used_instead_of_pool():
    spy = _SpyFetcher()
    pool = ProxyPool(["http://pool-proxy:8080"], strategy="round_robin")
    router = TierRouter([spy], proxies=pool)
    router.fetch("https://x.com/a", proxy_override="http://mi-proxy:3128")
    assert spy.proxies_seen == ["http://mi-proxy:3128"]   # usó el del job, no el del pool


def test_proxy_override_block_escalates_not_rotates():
    # Con proxy fijo, un bloqueo no reintenta el mismo proxy: escala de tier.
    class _Blocker(_SpyFetcher):
        tier = 0
        name = "static"
        def fetch(self, url, ctx):
            self.proxies_seen.append(ctx.proxy)
            raise BlockedError("403", signal="ip")

    b = _Blocker()
    ok = _SpyFetcher(); ok.tier = 1; ok.name = "tls"
    router = TierRouter([b, ok], proxy_attempts=3)
    res = router.fetch("https://x.com/a", proxy_override="http://p:1")
    assert res.tier == 1
    assert len(b.proxies_seen) == 1   # un solo intento en tier 0 (no rotó)


def test_solver_override_injected_into_ctx():
    captured = {}

    class _F(_SpyFetcher):
        def fetch(self, url, ctx):
            captured["solver"] = ctx.solver
            return FetchResult(url=url, status_code=200, content=b"ok", text="ok",
                               content_type="text/html", tier=0)

    sentinel = object()
    router = TierRouter([_F()])
    router.fetch("https://x.com/a", solver_override=sentinel)
    assert captured["solver"] is sentinel


def test_prose_score_prefers_clean_content():
    clean = "# Título\n\n" + ("Este es un párrafo largo con información real y sustancial. " * 4)
    navish = "\n".join(f"[Link {i}](https://x.com/{i})" for i in range(20))
    assert _prose_score(clean) > _prose_score(navish)


def test_multimotor_picks_best(monkeypatch):
    import app.extractors.convert as conv
    # Simulamos dos motores: crawl4ai devuelve nav, trafilatura devuelve prosa limpia.
    monkeypatch.setattr(conv, "_has", lambda mod: mod == "crawl4ai")
    monkeypatch.setattr(conv, "_crawl4ai_markdown",
                        lambda html, url: "[a](/a) [b](/b) [c](/c) [d](/d) [e](/e)")
    monkeypatch.setattr(conv, "html_to_markdown",
                        lambda html, url=None: "# Real\n\n" + ("Contenido principal con prosa de verdad. " * 5))
    md, engine = conv.html_to_markdown_rich("<html></html>", url="https://x.com/")
    assert engine == "trafilatura"
    assert "Contenido principal" in md
