"""Tier 1 — fetch con fingerprint TLS realista (curl_cffi). Ver ADR-006.

Muchos sitios bloquean por JA3/JA4: el handshake TLS de httpx/requests no se parece
al de un Chrome real, y el WAF lo cachetea sin siquiera mirar el User-Agent.
curl_cffi impersona el TLS de navegadores reales (`impersonate="chrome"`), lo que
pasa la mayoría de esos filtros sin pagar el costo de un browser.

Sin JS todavía: si el sitio exige render, escala a tier 2/3. Import perezoso: si
curl_cffi no está instalado, `available()` es False y el router salta este tier.
"""
from __future__ import annotations

from ..net import captcha
from ..security.ssrf import resolve_and_validate
from .base import BlockedError, CaptchaError, FetchContext, FetchError, FetchResult

_MIN_OK_BODY = 64
# Perfil de impersonación por defecto. curl_cffi rota esto bien con un Chrome reciente.
_IMPERSONATE = "chrome"


class TLSFetcher:
    tier = 1
    name = "tls"

    def available(self) -> bool:
        try:
            import curl_cffi  # noqa: F401
            return True
        except ImportError:
            return False

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        try:
            from curl_cffi import requests as creq
        except ImportError as e:  # pragma: no cover
            raise FetchError("tier 1 no disponible: instalá curl_cffi.") from e

        resolve_and_validate(url, allow_private=ctx.allow_private)

        proxies = {"http": ctx.proxy, "https": ctx.proxy} if ctx.proxy else None
        headers = {"User-Agent": ctx.user_agent, **ctx.headers}
        try:
            resp = creq.get(
                url,
                impersonate=ctx.extra.get("impersonate", _IMPERSONATE),
                proxies=proxies,
                headers=headers,
                timeout=ctx.timeout_s,
                max_redirects=ctx.max_redirects,
                allow_redirects=True,
            )
        except Exception as e:  # noqa: BLE001 — curl_cffi tira su propia jerarquía
            raise FetchError(f"Fallo de red en tier 1: {type(e).__name__}.") from e

        content = resp.content or b""
        if len(content) > ctx.max_bytes:
            raise FetchError(f"El recurso supera el límite de {ctx.max_bytes} bytes.")
        text = resp.text or ""

        # Re-validar el destino final tras los redirects que siguió curl_cffi.
        final_url = str(resp.url)
        resolve_and_validate(final_url, allow_private=ctx.allow_private)

        klass, signal = captcha.classify(
            resp.status_code, dict(resp.headers), text, min_content_len=_MIN_OK_BODY
        )
        if klass == "captcha":
            raise CaptchaError(f"CAPTCHA {signal} en tier 1.", vendor=signal)
        if klass == "blocked":
            raise BlockedError(f"Bloqueado en tier 1 ({signal}).", signal=signal)
        if resp.status_code >= 400:
            raise FetchError(f"El servidor respondió {resp.status_code}.")

        return FetchResult(
            url=final_url,
            status_code=resp.status_code,
            content=content,
            text=text,
            content_type=resp.headers.get("content-type", ""),
            tier=self.tier,
            proxy_used=ctx.proxy,
            headers=dict(resp.headers),
        )
