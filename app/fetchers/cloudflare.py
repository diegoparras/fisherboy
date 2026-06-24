"""Tier de browser EN LA NUBE: Cloudflare Browser Rendering (endpoint /content).

Renderiza la página con un Chrome headless en la red de Cloudflare y devuelve el HTML
ya con el JS ejecutado — el mismo trabajo que el Chromium local (tier 3) pero sin hornear
~1 GB de navegador en la imagen, y desde las IPs de Cloudflare (mejor contra anti-bot).

Opt-in y gateado. Las credenciales salen de DOS lugares, con prioridad:
  1. Config de la UI persistida en Redis (key `fisherboy:cf`): el superadmin la prende/apaga
     y carga account_id + token desde el panel, sin redeploy.
  2. Variables de entorno CF_ACCOUNT_ID / CF_API_TOKEN (fallback si la UI no configuró nada).

Cuando hay creds + enabled, `available()` es True y el router lo prueba ANTES del Chromium
local (mismo tier 3); cae al local si falla. Privacidad: a Cloudflare le va la URL a
renderizar (no datos del usuario); por eso es opcional, no el default.

API: POST https://api.cloudflare.com/client/v4/accounts/{acct}/browser-rendering/content
con `Authorization: Bearer <token>`. Doc: https://developers.cloudflare.com/browser-rendering/
"""
from __future__ import annotations

import json
import time

import httpx

from .base import BlockedError, FetchContext, FetchError, FetchResult

_API = "https://api.cloudflare.com/client/v4/accounts/{acct}/browser-rendering/content"
REDIS_KEY = "fisherboy:cf"   # {"enabled": bool, "account_id": str, "api_token": str}


class CloudflareBrowserFetcher:
    """Browser headless de Cloudflare como tier 3. Sin deps locales (solo httpx + credenciales)."""

    tier = 3
    name = "cloudflare"

    def __init__(self, account_id: str = "", api_token: str = "", *, redis_client=None) -> None:
        self._env_acct = (account_id or "").strip()   # fallback de entorno
        self._env_tok = (api_token or "").strip()
        self._r = redis_client
        self._cache: tuple[float, dict] | None = None  # (ts_monotonic, cfg de Redis)

    def _config(self) -> dict:
        """Lee la config de la UI desde Redis, cacheada 30 s (best-effort)."""
        now = time.monotonic()
        if self._cache and now - self._cache[0] < 30.0:
            return self._cache[1]
        cfg: dict = {}
        if self._r is not None:
            try:
                raw = self._r.get(REDIS_KEY)
                if raw:
                    cfg = json.loads(raw)
            except Exception:  # noqa: BLE001 — Redis caído / json roto: caemos a env
                cfg = {}
        self._cache = (now, cfg)
        return cfg

    def _creds(self) -> tuple[str, str]:
        """Credenciales efectivas: la config de la UI (Redis) gana sobre el entorno."""
        cfg = self._config()
        if "enabled" in cfg:                       # la UI ya configuró algo (gana sobre env)
            if cfg.get("enabled") and cfg.get("account_id") and cfg.get("api_token"):
                return str(cfg["account_id"]).strip(), str(cfg["api_token"]).strip()
            return "", ""                           # la UI lo apagó o quedó sin creds → off
        if self._env_acct and self._env_tok:        # sin config de UI → fallback a entorno
            return self._env_acct, self._env_tok
        return "", ""

    def available(self) -> bool:
        a, t = self._creds()
        return bool(a and t)

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        acct, tok = self._creds()
        if not (acct and tok):
            raise FetchError("cloudflare browser: no configurado.")
        # Cloudflare renderiza desde SU red: el proxy del pool (ctx.proxy) no aplica al render.
        body: dict = {
            "url": url,
            "userAgent": ctx.user_agent,
            "gotoOptions": {"waitUntil": "networkidle2", "timeout": int(ctx.timeout_s * 1000)},
        }
        if ctx.headers:
            body["setExtraHTTPHeaders"] = dict(ctx.headers)
        endpoint = _API.format(acct=acct)
        try:
            r = httpx.post(
                endpoint, json=body,
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                timeout=ctx.timeout_s + 15.0,   # margen sobre el timeout del render remoto
            )
        except httpx.HTTPError as e:
            raise FetchError(f"cloudflare browser: {e}") from e

        if r.status_code in (401, 403):
            raise FetchError(f"cloudflare browser: credenciales o permiso inválidos ({r.status_code}). "
                             "Revisá el Account ID y un token con permiso 'Browser Rendering - Edit'.")
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
