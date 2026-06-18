"""Captura de API/XHR oculto — la técnica más confiable de scraping. Ver ADR-010.

El movimiento pro: en vez de pelear el HTML renderizado, se observa qué llamadas
XHR/fetch hace la página (los endpoints JSON que ya consume el front) y se queda con
esos datos. Más estable (la API cambia menos que el DOM), más liviano (50 KB de JSON
vs 2 MB de HTML) y suele estar menos defendido que la página.

Se renderiza con un browser real (interceptando `response`), se acumulan las respuestas
JSON, y se devuelven los endpoints con su cuerpo parseado. El que llama decide cuál es
el "endpoint de datos" (normalmente el JSON más grande / con más filas).

Necesita patchright o playwright. Import perezoso; si no están, no disponible.
"""
from __future__ import annotations

import importlib.util
import json

from ..security.ssrf import resolve_and_validate
from .base import FetchContext, FetchError


def available() -> bool:
    return importlib.util.find_spec("patchright") is not None or \
        importlib.util.find_spec("playwright") is not None


def _sync_playwright():
    """Prefiere patchright (stealth) sobre playwright."""
    if importlib.util.find_spec("patchright") is not None:
        from patchright.sync_api import sync_playwright
        return sync_playwright
    from playwright.sync_api import sync_playwright
    return sync_playwright


def capture_xhr(url: str, ctx: FetchContext, *, max_endpoints: int = 40,
                min_bytes: int = 0) -> list[dict]:
    """Renderiza `url` y captura las respuestas XHR/fetch JSON. Devuelve endpoints.

    Cada endpoint: {url, status, content_type, bytes, json (parseado) | text}.
    """
    if not available():  # pragma: no cover
        raise FetchError("Captura de API no disponible: instalá patchright o playwright.")
    resolve_and_validate(url, allow_private=ctx.allow_private)

    sync_playwright = _sync_playwright()
    captured: list[dict] = []
    seen_urls: set[str] = set()

    def _on_response(resp) -> None:
        try:
            ct = (resp.headers or {}).get("content-type", "")
            rtype = getattr(resp.request, "resource_type", "")
            is_api = "json" in ct.lower() or rtype in ("xhr", "fetch")
            if not is_api or resp.url in seen_urls:
                return
            if not ("json" in ct.lower() or resp.url.lower().endswith((".json",))):
                # XHR/fetch que no es JSON: lo saltamos (HTML fragment lo cubre el render)
                if "json" not in ct.lower():
                    return
            body = resp.body()
            if len(body) < min_bytes:
                return
            seen_urls.add(resp.url)
            entry = {"url": resp.url, "status": resp.status, "content_type": ct,
                     "bytes": len(body)}
            try:
                entry["json"] = json.loads(body.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                entry["text"] = body[:50_000].decode("utf-8", "replace")
            captured.append(entry)
        except Exception:  # noqa: BLE001 — una response que no se puede leer no corta la captura
            return

    launch: dict = {"headless": ctx.headless}
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
        page.on("response", _on_response)
        try:
            page.goto(url, timeout=ctx.timeout_s * 1000, wait_until="networkidle")
            page.wait_for_timeout(int(ctx.settle_s * 1000))
            if ctx.scroll:                          # dispara XHR de lazy-load / scroll infinito
                for _ in range(5):
                    page.mouse.wheel(0, 800)
                    page.wait_for_timeout(700)
        except Exception as e:  # noqa: BLE001
            browser.close()
            raise FetchError(f"Fallo al capturar API: {type(e).__name__}.") from e
        browser.close()

    # Más grande primero: el endpoint de datos suele ser el JSON más pesado.
    captured.sort(key=lambda e: e.get("bytes", 0), reverse=True)
    return captured[:max_endpoints]
