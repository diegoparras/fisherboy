"""TierRouter — el cerebro del fetch escalonado por costo. Ver ADR-006.

Arranca en el tier más barato disponible y sube solo cuando el sitio bloquea:

  tier 0 httpx → tier 1 TLS → tier 2 stealth → tier 3 browser

Reglas de escalado:
- BLOQUEO (403/429/WAF): puede ser por IP. Rota proxy dentro del MISMO tier hasta
  `proxy_attempts`; si sigue bloqueado, sube de tier.
- CAPTCHA: un proxy no lo arregla; sube de tier directo (un browser previene o, en
  último recurso, el solver lo resuelve).
- ERROR real (404, tamaño, demasiados redirects): NO escala (subir no lo arregla).
  Si se usó proxy, reintenta una vez por si el proxy está muerto; si no, falla.

Cachea el tier que ganó por dominio (Redis con TTL, o memoria): la próxima URL del
mismo sitio arranca directo en el tier que ya sabemos que funciona, sin pagar el
escalado de nuevo.
"""
from __future__ import annotations

from urllib.parse import urlsplit

from ..logging import get_logger
from ..net.captcha import CaptchaSolver, NoopSolver
from ..net.proxies import ProxyPool
from .base import BlockedError, CaptchaError, FetchContext, FetchError, FetchResult
from .browser import BrowserFetcher
from .static import StaticFetcher
from .stealth import StealthFetcher
from .tls import TLSFetcher

log = get_logger("fisherboy.router")


# ---------------------------------------------------------------------------
# Cache de tier por dominio
# ---------------------------------------------------------------------------
class InMemoryTierCache:
    def __init__(self) -> None:
        self._d: dict[str, int] = {}

    def get(self, domain: str) -> int | None:
        return self._d.get(domain)

    def set(self, domain: str, tier: int) -> None:
        self._d[domain] = tier


class RedisTierCache:
    """Cache compartida entre workers. Key: fisherboy:tier:{domain}."""

    def __init__(self, redis_client, ttl_s: int = 7 * 24 * 3600) -> None:
        self._r = redis_client
        self._ttl = ttl_s

    def get(self, domain: str) -> int | None:
        try:
            v = self._r.get(f"fisherboy:tier:{domain}")
        except Exception:  # noqa: BLE001 — Redis caído: la cache es best-effort
            return None
        if v is None:
            return None
        if isinstance(v, bytes):
            v = v.decode("utf-8")
        try:
            return int(v)
        except ValueError:
            return None

    def set(self, domain: str, tier: int) -> None:
        try:
            self._r.set(f"fisherboy:tier:{domain}", int(tier), ex=self._ttl)
        except Exception:  # noqa: BLE001
            pass


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
class TierRouter:
    def __init__(
        self,
        fetchers: list,
        *,
        cache=None,
        proxies: ProxyPool | None = None,
        solver: CaptchaSolver | None = None,
        ctx_template: FetchContext | None = None,
        max_tier: int = 3,
        proxy_attempts: int = 2,
    ) -> None:
        # Orden estable por tier; el router consulta available() en caliente.
        self.fetchers = sorted(fetchers, key=lambda f: f.tier)
        self.cache = cache or InMemoryTierCache()
        self.proxies = proxies or ProxyPool([])
        self.solver = solver or NoopSolver()
        self.ctx_template = ctx_template or FetchContext()
        self.max_tier = max_tier
        self.proxy_attempts = max(1, proxy_attempts)

    def _build_ctx(self, proxy: str | None) -> FetchContext:
        t = self.ctx_template
        return FetchContext(
            timeout_s=t.timeout_s,
            max_bytes=t.max_bytes,
            max_redirects=t.max_redirects,
            allow_private=t.allow_private,
            user_agent=t.user_agent,
            proxy=proxy,
            headers=dict(t.headers),
            solver=self.solver,
            cookies=dict(t.cookies),
            headless=t.headless,
            settle_s=t.settle_s,
            scroll=t.scroll,
            locale=t.locale,
            extra=dict(t.extra),
        )

    def _eligible(self, start_tier: int) -> list:
        avail = [f for f in self.fetchers if f.tier <= self.max_tier and f.available()]
        chain = [f for f in avail if f.tier >= start_tier]
        if not chain and avail:
            chain = [avail[0]]  # siempre intentar algo (el más barato disponible)
        return chain

    def fetch(
        self,
        url: str,
        *,
        tier_hint: int | None = None,
        proxy_override: str | None = None,
        solver_override=None,
        cookies_override: dict | None = None,
        max_tier_override: int | None = None,
    ) -> FetchResult:
        domain = _domain(url)
        cached = self.cache.get(domain)
        cap = self.max_tier if max_tier_override is None else min(self.max_tier, int(max_tier_override))
        start = min(max(int(tier_hint or 0), int(cached or 0)), cap)
        chain = [f for f in self._eligible(start) if f.tier <= cap]
        if not chain:
            chain = self._eligible(start)[:1]
        if not chain:
            raise FetchError("No hay ningún tier de fetch disponible.")

        last_exc: Exception | None = None
        escalation: list[str] = []

        for fetcher in chain:
            for attempt in range(self.proxy_attempts):
                # Override por job (UI Avanzado): proxy fijo, sin rotar el pool.
                proxy = proxy_override or self.proxies.acquire(domain=domain)
                ctx = self._build_ctx(proxy)
                if solver_override is not None:
                    ctx.solver = solver_override
                if cookies_override:
                    ctx.cookies = cookies_override
                try:
                    result = fetcher.fetch(url, ctx)
                    self.proxies.report_success(proxy)
                    self.cache.set(domain, fetcher.tier)
                    result.meta.update(
                        {"escalation": escalation, "tier_name": fetcher.name,
                         "proxy_attempts": attempt + 1}
                    )
                    log.info(
                        "fetch ok",
                        extra={"url": url, "tier": fetcher.tier, "tier_name": fetcher.name,
                               "proxied": bool(proxy), "escalation": escalation},
                    )
                    return result

                except CaptchaError as e:
                    last_exc = e
                    self.proxies.report_failure(proxy)
                    escalation.append(f"t{fetcher.tier}:{e.signal}")
                    break  # captcha: rotar proxy no ayuda → escalar tier

                except BlockedError as e:
                    last_exc = e
                    self.proxies.report_failure(proxy)
                    escalation.append(f"t{fetcher.tier}:{e.signal}")
                    if proxy_override:
                        break  # proxy fijo del job: rotar no ayuda → escalar tier
                    continue  # bloqueo por IP: probar otro proxy en el mismo tier

                except FetchError as e:
                    last_exc = e
                    escalation.append(f"t{fetcher.tier}:error")
                    if proxy and not proxy_override:
                        self.proxies.report_failure(proxy)
                        continue  # quizás el proxy del pool está muerto: un reintento
                    # Error real (404, tamaño, redirects) o proxy fijo: subir no arregla.
                    raise

        log.info("fetch agotó todos los tiers", extra={"url": url, "escalation": escalation})
        if last_exc is not None:
            raise last_exc
        raise FetchError("Todos los tiers de fetch se agotaron.")


# ---------------------------------------------------------------------------
# Construcción desde settings
# ---------------------------------------------------------------------------
def build_router(settings, *, redis_client=None) -> TierRouter:
    from ..net.captcha import build_solver
    from ..net.proxies import build_pool

    fetchers = [StaticFetcher(), TLSFetcher(), StealthFetcher(), BrowserFetcher()]

    pool = build_pool(
        settings.proxies_raw,
        strategy=settings.proxy_rotation,
        cooldown_s=settings.proxy_cooldown_s,
    )
    solver = build_solver(
        settings.captcha_solver,
        api_url=settings.captcha_solver_url,
        api_key=settings.captcha_solver_key,
    )
    cache = (
        RedisTierCache(redis_client, ttl_s=settings.tier_cache_ttl_s)
        if redis_client is not None
        else InMemoryTierCache()
    )
    ctx = FetchContext(
        timeout_s=settings.fetch_timeout_s,
        max_bytes=settings.fetch_max_bytes,
        max_redirects=settings.fetch_max_redirects,
        allow_private=settings.allow_private_targets,
        headless=settings.browser_headless,
        settle_s=settings.browser_settle_s,
        scroll=settings.browser_scroll,
        user_agent=settings.browser_user_agent,
        locale=settings.browser_locale,
    )
    return TierRouter(
        fetchers,
        cache=cache,
        proxies=pool,
        solver=solver,
        ctx_template=ctx,
        max_tier=settings.max_fetch_tier,
        proxy_attempts=settings.proxy_attempts,
    )
