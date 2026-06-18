"""Tier 3 — browser de último recurso (nodriver / Playwright). Ver ADR-006.

El escalón más caro y más capaz: un Chrome real manejado por CDP. nodriver es el
sucesor de undetected-chromedriver (no usa webdriver, cuesta más detectarlo);
Playwright es el fallback robusto. Solo se llega acá si tier 0/1/2 fallaron, porque
arrancar un Chrome por job es lento y pesado.

nodriver es async; lo corremos en un event loop propio para mantener la interfaz
síncrona del Fetcher. Import perezoso: si nada está, `available()` es False.
"""
from __future__ import annotations

from ..net import captcha
from ..security.ssrf import resolve_and_validate
from .base import BlockedError, CaptchaError, FetchContext, FetchError, FetchResult


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


class BrowserFetcher:
    tier = 3
    name = "browser"

    def available(self) -> bool:
        return _has("nodriver") or _has("playwright")

    def _render_nodriver(self, url: str, ctx: FetchContext) -> tuple[str, int, dict, str]:
        import asyncio

        import nodriver as uc

        async def _run() -> tuple[str, str]:
            args = []
            if ctx.proxy:
                args.append(f"--proxy-server={ctx.proxy}")
            browser = await uc.start(headless=True, browser_args=args)
            page = await browser.get(url)
            await page.sleep(2)  # deja asentar el render/JS anti-bot
            html = await page.get_content()
            final = page.url or url
            browser.stop()
            return html, final

        loop = asyncio.new_event_loop()
        try:
            html, final = loop.run_until_complete(_run())
        finally:
            loop.close()
        return html, 200, {}, final

    def _render_playwright(self, url: str, ctx: FetchContext) -> tuple[str, int, dict, str]:
        from playwright.sync_api import sync_playwright

        launch = {"headless": True}
        if ctx.proxy:
            launch["proxy"] = {"server": ctx.proxy}
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            page = browser.new_page(user_agent=ctx.user_agent)
            resp = page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="networkidle")
            html = page.content()
            status = resp.status if resp else 200
            headers = dict(resp.headers) if resp else {}
            final = page.url
            browser.close()
            return html, status, headers, final

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        resolve_and_validate(url, allow_private=ctx.allow_private)

        try:
            if _has("nodriver"):
                html, status, headers, final = self._render_nodriver(url, ctx)
            elif _has("playwright"):
                html, status, headers, final = self._render_playwright(url, ctx)
            else:  # pragma: no cover
                raise FetchError("tier 3 no disponible: instalá nodriver o playwright.")
        except FetchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FetchError(f"Fallo de browser en tier 3: {type(e).__name__}.") from e

        resolve_and_validate(final, allow_private=ctx.allow_private)
        content = html.encode("utf-8", errors="replace")
        if len(content) > ctx.max_bytes:
            raise FetchError(f"El recurso supera el límite de {ctx.max_bytes} bytes.")

        klass, signal = captcha.classify(status, headers, html, min_content_len=0)
        if klass == "captcha":
            solver = ctx.solver
            if solver is not None and getattr(solver, "can_solve", lambda v: False)(signal):
                token = solver.solve(vendor=signal, url=final, sitekey=ctx.extra.get("sitekey"))
                if not token:
                    raise CaptchaError(f"CAPTCHA {signal} no resuelto en tier 3.", vendor=signal)
            else:
                raise CaptchaError(f"CAPTCHA {signal} en tier 3 (último recurso).", vendor=signal)
        if klass == "blocked":
            raise BlockedError(f"Bloqueado en tier 3 ({signal}).", signal=signal)

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
