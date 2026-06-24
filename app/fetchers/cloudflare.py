"""Tier de browser EN LA NUBE: Cloudflare Browser Rendering (endpoint /content).

Renderiza la página con un Chrome headless en la red de Cloudflare y devuelve el HTML
ya con el JS ejecutado — el mismo trabajo que el Chromium local (tier 3) pero sin hornear
~1 GB de navegador en la imagen, y desde las IPs de Cloudflare (mejor contra anti-bot).

Opt-in y gateado: `available()` es True solo si hay CF_ACCOUNT_ID + CF_API_TOKEN. Cuando
está configurado, el router lo prueba ANTES del Chromium local (mismo tier 3) y cae al
local si Cloudflare falla. Privacidad: a Cloudflare le va la URL a renderizar (no datos del
usuario); por eso es opcional, no el default.

API: POST https://api.cloudflare.com/client/v4/accounts/{acct}/browser-rendering/content
con `Authorization: Bearer <token>`. Doc: https://developers.cloudflare.com/browser-rendering/
"""
from __future__ import annotations

import json

import httpx

from .base import BlockedError, FetchContext, FetchError, FetchResult

_API = "https://api.cloudflare.com/client/v4/accounts/{acct}/browser-rendering/content"


class CloudflareBrowserFetcher:
    """Browser headless de Cloudflare como tier 3. Sin deps locales (solo httpx + credenciales)."""

    tier = 3
    name = "cloudflare"

    def __init__(self, account_id: str = "", api_token: str = "") -> None:
        self.account_id = (account_id or "").strip()
        self.api_token = (api_token or "").strip()

    def available(self) -> bool:
        return bool(self.account_id and self.api_token)

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        # Cloudflare renderiza desde SU red: el proxy del pool (ctx.proxy) no aplica al render.
        body: dict = {
            "url": url,
            "userAgent": ctx.user_agent,
            "gotoOptions": {"waitUntil": "networkidle2", "timeout": int(ctx.timeout_s * 1000)},
        }
        if ctx.headers:
            body["setExtraHTTPHeaders"] = dict(ctx.headers)
        endpoint = _API.format(acct=self.account_id)
        try:
            r = httpx.post(
                endpoint, json=body,
                headers={"Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json"},
                timeout=ctx.timeout_s + 15.0,   # margen sobre el timeout del render remoto
            )
        except httpx.HTTPError as e:
            raise FetchError(f"cloudflare browser: {e}") from e

        if r.status_code in (401, 403):
            raise FetchError(f"cloudflare browser: credenciales o permiso inválidos ({r.status_code}). "
                             "Revisá CF_ACCOUNT_ID y un token con permiso 'Browser Rendering - Edit'.")
        if r.status_code == 429:
            raise BlockedError("cloudflare browser: rate-limit de la cuenta", signal="cf:429")

        try:
            data = r.json()
        except json.JSONDecodeError as e:
            raise FetchError(f"cloudflare browser: respuesta no-JSON ({r.status_code}).") from e

        if not data.get("success"):
            errs = data.get("errors") or []
            msg = "; ".join(x.get("message", "") for x in errs) or f"HTTP {r.status_code}"
            raise FetchError(f"cloudflare browser: {msg}")

        html = data.get("result") or ""
        if not html.strip():
            raise BlockedError("cloudflare browser: HTML vacío (¿bloqueo del destino?)", signal="cf:empty")

        meta = data.get("meta") or {}
        status = int(meta.get("status") or 200)
        content = html.encode("utf-8", "replace")
        return FetchResult(
            url=url, status_code=status, content=content, text=html,
            content_type="text/html; charset=utf-8", tier=self.tier,
            meta={"engine": "cloudflare-browser", "cf_title": meta.get("title", "")},
        )
