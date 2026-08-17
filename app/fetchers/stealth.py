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
from .actions import persist_session, run_actions, session_state
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

    def _act(self, page, ctx: FetchContext):
        """Corre las acciones del job (ADR-011), si las hay. Devuelve el resultado o None."""
        if not ctx.actions:
            return None
        return run_actions(page, ctx.actions, allow_private=ctx.allow_private,
                           default_timeout_s=min(ctx.timeout_s, 30.0))

    def _render_camoufox(self, url: str, ctx: FetchContext):
        from camoufox.sync_api import Camoufox

        proxy = {"server": ctx.proxy} if ctx.proxy else None
        with Camoufox(headless=ctx.headless, proxy=proxy, humanize=True, locale=ctx.locale) as browser:
            page = browser.new_page()
            # Camoufox arma su propio contexto endurecido en new_page(); crear uno a mano
            # perdería ese fingerprint. Por eso la sesión se restaura inyectando las cookies
            # (que es lo que sostiene un login) en vez de con storage_state completo.
            state = session_state(ctx)
            if state and state.get("cookies"):
                try:
                    page.context.add_cookies(state["cookies"])
                except Exception:  # noqa: BLE001 — cookies vencidas/ajenas: seguimos sin sesión
                    pass
            resp = page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="domcontentloaded")
            self._settle_scroll(page, ctx)
            outcome = self._act(page, ctx)
            html = page.content()
            status = resp.status if resp else 200
            headers = dict(resp.headers) if resp else {}
            final = page.url
            persist_session(page.context, ctx)
            page.close()
            return html, status, headers, final, outcome

    def _render_patchright(self, url: str, ctx: FetchContext):
        from patchright.sync_api import sync_playwright

        launch = {"headless": ctx.headless}
        if ctx.proxy:
            launch["proxy"] = {"server": ctx.proxy}
        with sync_playwright() as p:
            browser = p.chromium.launch(**launch)
            opts = {"user_agent": ctx.user_agent, "locale": ctx.locale,
                    "viewport": {"width": 1920, "height": 1080}}
            state = session_state(ctx)      # sesión guardada → arranca ya logueado
            if state:
                opts["storage_state"] = state
            context = browser.new_context(**opts)
            if ctx.cookies:
                context.add_cookies([
                    {"name": k, "value": str(v), "url": url} for k, v in ctx.cookies.items()
                ])
            page = context.new_page()
            resp = page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="domcontentloaded")
            self._settle_scroll(page, ctx)
            outcome = self._act(page, ctx)
            html = page.content()
            status = resp.status if resp else 200
            headers = dict(resp.headers) if resp else {}
            final = page.url
            persist_session(context, ctx)
            browser.close()
            return html, status, headers, final, outcome

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        resolve_and_validate(url, allow_private=ctx.allow_private)

        try:
            if _has("camoufox"):
                html, status, headers, final, outcome = self._render_camoufox(url, ctx)
            elif _has("patchright"):
                html, status, headers, final, outcome = self._render_patchright(url, ctx)
            else:  # pragma: no cover
                raise FetchError("tier 2 no disponible: instalá camoufox o patchright.")
        except FetchError:
            raise
        except Exception as e:  # noqa: BLE001
            raise FetchError(f"Fallo de browser stealth en tier 2: {type(e).__name__}.") from e

        resolve_and_validate(final, allow_private=ctx.allow_private)
        # Una acción no opcional que falló significa que la página no quedó en el estado que el
        # usuario pidió: devolver ese HTML sería entregarle la página equivocada sin avisar.
        if outcome is not None and outcome.failed:
            raise FetchError(f"Acciones de browser: {outcome.failed}")
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

        result = FetchResult(
            url=final,
            status_code=status,
            content=content,
            text=html,
            content_type="text/html",
            tier=self.tier,
            proxy_used=ctx.proxy,
            headers=headers,
        )
        if outcome is not None:
            result.meta["actions"] = outcome.log            # sin secretos (los redacta el motor)
            result.meta["artifacts"] = outcome.artifacts    # screenshots/PDF en bytes
        return result
