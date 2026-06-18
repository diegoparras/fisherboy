"""Tier 0 — fetch estático con httpx. El escalón más barato. Ver ADR-006.

Sin browser, sin JS: pide el HTML crudo. Soporta proxy (lo elige el pool por
intento) y detecta señales de bloqueo/CAPTCHA para que el router escale. Defensa
SSRF en cada salto (anti DNS rebinding), tope de bytes, timeout y máx de redirects.
"""
from __future__ import annotations

import httpx

from ..net import captcha
from ..security.ssrf import SSRFError, resolve_and_validate, validate_scheme_and_host
from .base import (
    BlockedError,
    CaptchaError,
    FetchContext,
    FetchError,
    FetchResult,
)

# Umbral de cuerpo mínimo para 200 sospechosos de soft-block (tier 0 ve poco JS).
_MIN_OK_BODY = 64


def _enforce_byte_cap(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            response.close()
            raise FetchError(f"El recurso supera el límite de {max_bytes} bytes.")
        chunks.append(chunk)
    return b"".join(chunks)


class StaticFetcher:
    """tier 0. Siempre disponible: httpx es dependencia base."""

    tier = 0
    name = "static"

    def available(self) -> bool:
        return True

    def fetch(self, url: str, ctx: FetchContext) -> FetchResult:
        resolve_and_validate(url, allow_private=ctx.allow_private)  # falla cerrado pre-red

        headers = {"User-Agent": ctx.user_agent, "Accept-Encoding": "gzip, deflate", **ctx.headers}
        current = url
        client_kwargs = dict(
            follow_redirects=False,
            timeout=ctx.timeout_s,
            headers=headers,
            limits=httpx.Limits(max_connections=4),
        )
        if ctx.proxy:
            client_kwargs["proxy"] = ctx.proxy

        with httpx.Client(**client_kwargs) as client:
            for _hop in range(ctx.max_redirects + 1):
                try:
                    with client.stream("GET", current) as resp:
                        if resp.is_redirect:
                            location = resp.headers.get("location")
                            if not location:
                                raise FetchError("Redirect sin header Location.")
                            nxt = str(httpx.URL(current).join(location))
                            validate_scheme_and_host(nxt)
                            resolve_and_validate(nxt, allow_private=ctx.allow_private)
                            current = nxt
                            continue

                        raw = _enforce_byte_cap(resp, ctx.max_bytes)
                        ctype = resp.headers.get("content-type", "")
                        encoding = resp.encoding or "utf-8"
                        try:
                            text = raw.decode(encoding, errors="replace")
                        except (LookupError, UnicodeDecodeError):
                            text = raw.decode("utf-8", errors="replace")

                        # Prevención primero: ¿bloqueo o CAPTCHA? → pedir escalado.
                        klass, signal = captcha.classify(
                            resp.status_code, dict(resp.headers), text,
                            min_content_len=_MIN_OK_BODY,
                        )
                        if klass == "captcha":
                            raise CaptchaError(f"CAPTCHA {signal} en tier 0.", vendor=signal)
                        if klass == "blocked":
                            raise BlockedError(f"Bloqueado en tier 0 ({signal}).", signal=signal)

                        if resp.status_code >= 400:
                            raise FetchError(f"El servidor respondió {resp.status_code}.")

                        return FetchResult(
                            url=str(resp.url),
                            status_code=resp.status_code,
                            content=raw,
                            text=text,
                            content_type=ctype,
                            tier=self.tier,
                            proxy_used=ctx.proxy,
                            headers=dict(resp.headers),
                        )
                except SSRFError:
                    raise
                except httpx.HTTPError as e:
                    raise FetchError(f"Fallo de red al traer la URL: {type(e).__name__}.") from e

        raise FetchError(f"Demasiados redirects (>{ctx.max_redirects}).")


# ---------------------------------------------------------------------------
# Compat / conveniencia: API funcional de v1, ahora sobre el Fetcher.
# ---------------------------------------------------------------------------
def fetch_static(
    url: str,
    *,
    timeout_s: float = 20.0,
    max_bytes: int = 10 * 1024 * 1024,
    max_redirects: int = 5,
    allow_private: bool = False,
    user_agent: str | None = None,
    proxy: str | None = None,
) -> FetchResult:
    ctx = FetchContext(
        timeout_s=timeout_s,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        allow_private=allow_private,
        proxy=proxy,
    )
    if user_agent:
        ctx.user_agent = user_agent
    return StaticFetcher().fetch(url, ctx)
