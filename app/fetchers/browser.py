"""Tier 3 — browser de último recurso (nodriver / Playwright), endurecido. Ver ADR-006.

El escalón más caro y más capaz: Chrome real por CDP. nodriver (sucesor de
undetected-chromedriver) es lo más difícil de detectar; Playwright es el fallback.

Hardening anti-detección (clave para targets hostiles):
- args que apagan las señales de automatización (AutomationControlled, etc.),
- `headless` configurable: en modo headful (con display o xvfb) la huella es mucho más
  creíble; en server se deja headless,
- proxy inyectado en el LAUNCH del browser (no solo en httpx),
- espera de asentado (`settle_s`) + scroll para disparar contenido lazy/JS,
- UA y locale realistas.

Aun así, contra un anti-bot serio sin proxy residencial el sitio puede ganar: ahí entra
el override de proxy/solver por job desde la UI. Esto sube la vara, no garantiza magia.
"""
from __future__ import annotations

from ..net import captcha
from ..security.ssrf import resolve_and_validate
from .base import BlockedError, CaptchaError, FetchContext, FetchError, FetchResult


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


def _stealth_args(ctx: FetchContext) -> list[str]:
    args = [
        "--window-size=1920,1080",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-infobars",
        f"--lang={ctx.locale}",
    ]
    if ctx.proxy:
        args.append(f"--proxy-server={ctx.proxy}")
    return args


class BrowserFetcher:
    tier = 3
    name = "browser"

    def available(self) -> bool:
        return _has("nodriver") or _has("playwright")

    def _render_nodriver(self, url: str, ctx: FetchContext) -> tuple[str, int, dict, str]:
        import asyncio

        import nodriver as uc

        async def _run() -> tuple[str, str]:
            browser = await uc.start(headless=ctx.headless, browser_args=_stealth_args(ctx))
            page = await browser.get(url)
            await page.sleep(ctx.settle_s)            # deja asentar el JS / anti-bot
            if ctx.scroll:                            # dispara contenido lazy
                for _ in range(4):
                    try:
                        await page.scroll_down(600)
                    except Exception:  # noqa: BLE001
                        break
                    await page.sleep(0.6)
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

        launch = {"headless": ctx.headless, "args": _stealth_args(ctx)}
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
            resp = page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="networkidle")
            page.wait_for_timeout(int(ctx.settle_s * 1000))
            if ctx.scroll:
                for _ in range(4):
                    page.mouse.wheel(0, 600)
                    page.wait_for_timeout(500)
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
