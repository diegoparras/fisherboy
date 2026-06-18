"""Tests de la Capa 3: detección de CAPTCHA, pool de proxies y escalado del router."""
from __future__ import annotations

import pytest

from app.fetchers.base import BlockedError, CaptchaError, FetchContext, FetchError, FetchResult
from app.fetchers.router import InMemoryTierCache, TierRouter
from app.net import captcha
from app.net.proxies import ProxyPool, load_proxies


# --------------------------------------------------------------------------- captcha
def test_classify_cloudflare_captcha():
    body = "<html><head><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'></script></head></html>"
    klass, signal = captcha.classify(403, {"server": "cloudflare"}, body)
    assert klass == "captcha"
    assert signal == "cloudflare"


def test_classify_recaptcha():
    body = "<div class='g-recaptcha' data-sitekey='abc'></div>"
    assert captcha.classify(200, {}, body) == ("captcha", "recaptcha")


def test_classify_blocked_status():
    assert captcha.classify(429, {}, "rate limited")[0] == "blocked"
    assert captcha.classify(403, {}, "forbidden")[0] == "blocked"


def test_classify_ok():
    assert captcha.classify(200, {}, "<html><body>contenido normal y largo</body></html>") == ("ok", "")


def test_classify_soft_block_empty():
    assert captcha.classify(200, {}, "  ", min_content_len=64)[0] == "blocked"


# --------------------------------------------------------------------------- proxies
def test_load_proxies_filters_invalid():
    raw = "http://a:8080, socks5://b:1080, not-a-proxy, https://user:pass@c:3128"
    got = load_proxies(raw)
    assert got == ["http://a:8080", "socks5://b:1080", "https://user:pass@c:3128"]


def test_pool_round_robin_rotates():
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"], strategy="round_robin")
    picks = {pool.acquire() for _ in range(6)}
    assert picks == {"http://a:1", "http://b:2", "http://c:3"}


def test_pool_sticky_same_domain():
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"], strategy="sticky")
    first = pool.acquire(domain="ejemplo.com")
    assert all(pool.acquire(domain="ejemplo.com") == first for _ in range(5))


def test_pool_cooldown_removes_failed():
    pool = ProxyPool(["http://a:1"], strategy="round_robin", cooldown_s=999)
    pool.report_failure("http://a:1")
    assert pool.acquire() is None      # único proxy quemado → salida directa
    assert pool.stats()["healthy"] == 0


def test_empty_pool_returns_none():
    assert ProxyPool([]).acquire() is None


# --------------------------------------------------------------------------- router
def _result(tier: int) -> FetchResult:
    return FetchResult(url="https://x.com/a", status_code=200, content=b"ok",
                       text="ok", content_type="text/html", tier=tier)


class FakeFetcher:
    def __init__(self, tier, name, behavior):
        self.tier = tier
        self.name = name
        self.behavior = behavior
        self.calls = 0

    def available(self):
        return True

    def fetch(self, url, ctx):
        self.calls += 1
        b = self.behavior(self.calls) if callable(self.behavior) else self.behavior
        if isinstance(b, Exception):
            raise b
        return b


def test_router_escalates_on_block_and_caches_tier():
    f0 = FakeFetcher(0, "static", BlockedError("403", signal="status:403"))
    f1 = FakeFetcher(1, "tls", _result(1))
    cache = InMemoryTierCache()
    router = TierRouter([f0, f1], cache=cache, proxy_attempts=1)

    res = router.fetch("https://x.com/a")
    assert res.tier == 1
    assert any("t0" in e for e in res.meta["escalation"])
    assert cache.get("x.com") == 1

    # Segunda URL del mismo dominio arranca en tier 1: tier 0 no se vuelve a tocar.
    f0.calls = 0
    res2 = router.fetch("https://x.com/b")
    assert res2.tier == 1
    assert f0.calls == 0


def test_router_captcha_escalates_immediately():
    f0 = FakeFetcher(0, "static", CaptchaError("captcha", vendor="recaptcha"))
    f1 = FakeFetcher(1, "tls", _result(1))
    router = TierRouter([f0, f1], proxy_attempts=3)  # captcha no reintenta proxy
    res = router.fetch("https://x.com/a")
    assert res.tier == 1
    assert f0.calls == 1   # un solo intento: el captcha rompe a escalar


def test_router_retries_proxy_within_tier():
    # Bloquea el 1er intento, gana el 2do (rotando proxy en el mismo tier 0).
    f0 = FakeFetcher(0, "static", lambda n: _result(0) if n >= 2 else BlockedError("403", signal="ip"))
    pool = ProxyPool(["http://a:1", "http://b:2"], strategy="round_robin")
    router = TierRouter([f0], proxies=pool, proxy_attempts=2)
    res = router.fetch("https://x.com/a")
    assert res.tier == 0
    assert f0.calls == 2


def test_router_tier_hint_forces_start():
    f0 = FakeFetcher(0, "static", _result(0))
    f1 = FakeFetcher(1, "tls", _result(1))
    router = TierRouter([f0, f1])
    res = router.fetch("https://x.com/a", tier_hint=1)
    assert res.tier == 1
    assert f0.calls == 0


def test_router_plain_error_does_not_escalate():
    # Un 404 real no se arregla subiendo de tier: se levanta sin tocar tier 1.
    f0 = FakeFetcher(0, "static", FetchError("404"))
    f1 = FakeFetcher(1, "tls", _result(1))
    router = TierRouter([f0, f1])  # pool vacío → sin proxy
    with pytest.raises(FetchError):
        router.fetch("https://x.com/a")
    assert f1.calls == 0


def test_router_all_blocked_raises():
    f0 = FakeFetcher(0, "static", BlockedError("403", signal="s"))
    f1 = FakeFetcher(1, "tls", BlockedError("403", signal="s"))
    router = TierRouter([f0, f1], max_tier=1, proxy_attempts=1)
    with pytest.raises(BlockedError):
        router.fetch("https://x.com/a")
