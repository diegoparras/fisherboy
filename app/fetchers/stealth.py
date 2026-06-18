"""Tier 2 — browser stealth (Camoufox / Patchright). Ver ADR-006.

Cuando el sitio exige JS y además corre anti-bot de fingerprint de browser
(canvas, WebGL, navigator.*), un Playwright pelado se detecta al instante. Camoufox
(Firefox endurecido) y Patchright (Chromium parcheado) presentan un browser
indetectable. "Prevención primero": render real + fingerprint creíble hace que el
CAPTCHA muchas veces ni aparezca.

Prefiere Camoufox; cae a Patchright si solo ese está. Import perezoso: si ninguno
está instalado, `available()` es False y el router salta este tier.
"""
from __future__ import annotations

from ..net import captcha
from ..security.ssrf import resolve_and_validate
from .base import BlockedError, CaptchaError, FetchContext, FetchError, FetchResult


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


class StealthFetcher:
    tier = 2
    name = "stealth"

    def available(self) -> bool:
        return _has("camoufox") or _has("patchright")

    def _settle_scroll(self, page, ctx: FetchContext) -> None:
        page.wait_for_timeout(int(ctx.settle_s * 1000))   # deja asentar el JS
        if ctx.scroll:
            for _ in range(4):
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(500)

    def _render_camoufox(self, url: str, ctx: FetchContext) -> tuple[str, int, dict, str]:
        from camoufox.sync_api import Camoufox

        proxy = {"server": ctx.proxy} if ctx.proxy else None
        with Camoufox(headless=ctx.headless, proxy=proxy, humanize=True, locale=ctx.locale) as browser:
            page = browser.new_page()
            resp = page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="domcontentloaded")
            self._settle_scroll(page, ctx)
            html = page.content()
            status = resp.status if resp else 200
            headers = dict(resp.headers) if resp else {}
            final = page.url
            page.close()
            return html, status, headers, final

    def _render_patchright(self, url: str, ctx: FetchContext) -> tuple[str, int, dict, str]:
        from patchright.sync_api import sync_playwright

        launch = {"headless": ctx.headless}
        if ctx.proxy:
            launch["proxy"] = {"server": ctx.proxy}
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            context = browser.new_context(
                user_agent=ctx.user_agent, locale=ctx.locale,
                viewport={"width": 1920, "height": 1080},
            )
            if ctx.cookies:
                context.add_cookies([
                    {"name": k, "value": str(v), "url": url} for k, v in ctx.cookies.items()
                ])
            page = context.new_page()
            resp = page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="domcontentloaded")
            self._settle_scroll(page, ctx)
            html = page.content()
            status = resp.status if resp else 200
            headers = dict(resp.headers) if resp else {}
            final = page.url
            browser.close()
            return html, status, headers, final

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        resolve_and_validate(url, allow_private=ctx.allow_private)

        try:
            if _has("camoufox"):
                html, status, headers, final = self._render_camoufox(url, ctx)
            elif _has("patchright"):
                html, status, headers, final = self._render_patchright(url, ctx)
            else:  # pragma: no cover
                raise FetchError("tier 2 no disponible: instalá camoufox o patchright.")
        except FetchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FetchError(f"Fallo de browser stealth en tier 2: {type(e).__name__}.") from e

        resolve_and_validate(final, allow_private=ctx.allow_private)
        content = html.encode("utf-8", errors="replace")
        if len(content) > ctx.max_bytes:
            raise FetchError(f"El recurso supera el límite de {ctx.max_bytes} bytes.")

        klass, signal = captcha.classify(status, headers, html, min_content_len=0)
        if klass == "captcha":
            # Último recurso: si hay solver configurado y puede con este proveedor.
            solver = ctx.solver
            if solver is not None and getattr(solver, "can_solve", lambda v: False)(signal):
                # El sitekey se extraería del DOM acá; queda como hook documentado.
                token = solver.solve(vendor=signal, url=final, sitekey=ctx.extra.get("sitekey"))
                if not token:
                    raise CaptchaError(f"CAPTCHA {signal} no resuelto en tier 2.", vendor=signal)
            else:
                raise CaptchaError(f"CAPTCHA {signal} en tier 2.", vendor=signal)
        if klass == "blocked":
            raise BlockedError(f"Bloqueado en tier 2 ({signal}).", signal=signal)

        return FetchResult(
            url=final,
            status_code=status,
            content=content,
            text=html,
            content_type="text/html",
            tier=self.tier,
            proxy_used=ctx.proxy,
            headers=headers,
        )
