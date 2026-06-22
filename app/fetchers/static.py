"""Tier 0 — fetch estático con httpx. El escalón más barato. Ver ADR-006.

Sin browser, sin JS: pide el HTML crudo. Soporta proxy (lo elige el pool por
intento) y detecta señales de bloqueo/CAPTCHA para que el router escale. Defensa
SSRF en cada salto (anti DNS rebinding), tope de bytes, timeout y máx de redirects.
"""
from __future__ import annotations

import httpx

from ..net import captcha
from ..security.ssrf import (
    SSRFError,
    guarded_client,
    resolve_and_validate,
    validate_scheme_and_host,
)
from .base import (
    BlockedError,
    CaptchaError,
    FetchContext,
    FetchError,
    FetchResult,
)

# Umbral de cuerpo mínimo para 200 sospechosos de soft-block (tier 0 ve poco JS).
_MIN_OK_BODY = 64
_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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
        if ctx.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in ctx.cookies.items())
        current = url
        client_kwargs = dict(
            follow_redirects=False,
            timeout=ctx.timeout_s,
            headers=headers,
            limits=httpx.Limits(max_connections=4),
        )

        # guarded_client pinea la IP validada (anti DNS-rebinding) cuando no hay proxy.
        with guarded_client(allow_private=ctx.allow_private, proxy=ctx.proxy, **client_kwargs) as client:
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
def fetch_post(
    url: str,
    data: dict,
    *,
    timeout_s: float = 25.0,
    max_bytes: int = 10 * 1024 * 1024,
    max_redirects: int = 5,
    allow_private: bool = False,
    cookies: dict | None = None,
    proxy: str | None = None,
    user_agent: str = _DEFAULT_UA,
) -> FetchResult:
    """POST form-urlencoded SSRF-safe (postback ASP.NET / forms). Devuelve FetchResult.

    Sigue los redirects A MANO re-validando CADA salto (igual que el GET): NO se delega
    a httpx con follow_redirects=True, porque eso conectaba a destinos intermedios
    (p.ej. 302 → 169.254.169.254) antes de cualquier chequeo SSRF. Tras 301/302/303 el
    método baja a GET (comportamiento estándar de navegadores y clientes HTTP)."""
    resolve_and_validate(url, allow_private=allow_private)
    headers = {"User-Agent": user_agent, "Content-Type": "application/x-www-form-urlencoded"}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    client_kwargs = dict(follow_redirects=False, timeout=timeout_s, headers=headers)

    current = url
    method = "POST"
    try:
        with guarded_client(allow_private=allow_private, proxy=proxy, **client_kwargs) as client:
            for _hop in range(max_redirects + 1):
                # El body solo viaja en el POST inicial; tras un redirect se hace GET sin body.
                stream = (client.stream("POST", current, data=data) if method == "POST"
                          else client.stream("GET", current))
                with stream as resp:
                    if resp.is_redirect:
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError("Redirect sin header Location.")
                        nxt = str(httpx.URL(current).join(location))
                        validate_scheme_and_host(nxt)
                        resolve_and_validate(nxt, allow_private=allow_private)  # cada salto
                        current = nxt
                        method = "GET"   # 301/302/303 → GET (no reenviar el form)
                        continue
                    raw = _enforce_byte_cap(resp, max_bytes)
                    enc = resp.encoding or "utf-8"
                    try:
                        text = raw.decode(enc, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        text = raw.decode("utf-8", errors="replace")
                    return FetchResult(
                        url=str(resp.url), status_code=resp.status_code, content=raw, text=text,
                        content_type=resp.headers.get("content-type", ""), tier=0,
                        proxy_used=proxy, headers=dict(resp.headers),
                    )
    except SSRFError:
        raise
    except httpx.HTTPError as e:
        raise FetchError(f"Fallo de red en POST: {type(e).__name__}.") from e
    raise FetchError(f"Demasiados redirects (>{max_redirects}).")


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
