"""Defensa SSRF de entrada y de salida. Ver ADR-004.

Scraping hace fetch a URLs arbitrarias (SSRF de entrada) y el worker hace POST al
`callback_url` que provee el usuario (SSRF de salida). Las dos puertas se validan
con el mismo criterio: resolver el DNS y bloquear todo lo que apunte a la red
interna, loopback, link-local o la IP de metadata de cloud (169.254.169.254).

Contra DNS rebinding: `resolve_and_validate()` devuelve las IPs ya validadas, y el
fetcher se conecta a esa IP, re-validando en cada redirect. Validar el hostname no
alcanza: el atacante controla qué resuelve.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Rangos extra a bloquear más allá de los que ya marca ipaddress como privados.
# 169.254.169.254 (metadata de la mayoría de los clouds) cae en link-local, pero
# lo dejamos explícito por claridad y por si algún proveedor usa otro link-local.
_EXTRA_BLOCKED_V4 = [
    ipaddress.ip_network("169.254.0.0/16"),   # link-local (incluye metadata AWS/GCP/Azure)
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT / carrier-grade NAT
]


class SSRFError(ValueError):
    """La URL apunta a un destino prohibido o malformado."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        if any(ip in net for net in _EXTRA_BLOCKED_V4):
            return True
    else:
        # IPv6: bloquear mapeadas/embebidas de IPv4 que escondan una privada.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None and _ip_is_blocked(mapped):
            return True
        if ip.is_site_local:  # fec0::/10 (deprecado, pero por las dudas)
            return True
    return False


def validate_scheme_and_host(url: str) -> tuple[str, str, int]:
    """Valida esquema y extrae (host, port). No resuelve DNS todavía."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFError(f"Esquema no permitido: {parts.scheme!r}. Solo http/https.")
    host = parts.hostname
    if not host:
        raise SSRFError("URL sin host.")
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    return parts.scheme.lower(), host, port


def resolve_and_validate(url: str, *, allow_private: bool = False) -> list[str]:
    """Resuelve el host y valida CADA IP. Devuelve la lista de IPs seguras.

    Si cualquier IP del host cae en un rango bloqueado, falla cerrado: no se
    devuelve ninguna. `allow_private` es solo para dev/test local.
    """
    _scheme, host, _port = validate_scheme_and_host(url)

    # Si el host ya es una IP literal, validarla directo.
    try:
        literal = ipaddress.ip_address(host)
        if not allow_private and _ip_is_blocked(literal):
            raise SSRFError(f"Destino bloqueado (IP {host}).")
        return [str(literal)]
    except ValueError:
        pass  # no es IP literal; resolvemos por DNS

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise SSRFError(f"No se pudo resolver el host {host!r}.") from e

    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not allow_private and _ip_is_blocked(ip):
            raise SSRFError(f"El host {host!r} resuelve a un destino bloqueado ({addr}).")
        ips.append(str(ip))

    if not ips:
        raise SSRFError(f"El host {host!r} no resolvió a ninguna IP utilizable.")
    return ips


_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})


def validate_proxy_url(proxy_url: str, *, allow_private: bool = False) -> None:
    """Valida que el proxy (override por job) no apunte a la red interna.

    El tráfico se enruta POR el proxy, así que un proxy a 127.0.0.1/10.x/metadata
    permitiría sondear/alcanzar servicios internos esquivando la denylist (que solo
    mira la URL objetivo). Falla cerrado. Ver auditoría 2026-06."""
    parts = urlsplit(proxy_url)
    if parts.scheme.lower() not in _PROXY_SCHEMES:
        raise SSRFError(f"Esquema de proxy no permitido: {parts.scheme!r}.")
    host = parts.hostname
    if not host:
        raise SSRFError("Proxy sin host.")
    # Reusa la resolución+denylist; socks/http da igual: lo que importa es a dónde apunta.
    fake = f"http://{host}:{parts.port or 80}"
    resolve_and_validate(fake, allow_private=allow_private)


def validate_callback_url(
    url: str, *, allowlist: list[str] | None = None, allow_private: bool = False
) -> None:
    """Valida un callback_url contra los mismos bloques que el fetch + allowlist.

    Si hay allowlist configurada, el host debe estar en ella (ADR-004 punto 3).
    Falla cerrado: cualquier duda → SSRFError.
    """
    _scheme, host, _port = validate_scheme_and_host(url)
    if allowlist:
        if host.lower() not in allowlist:
            raise SSRFError(
                f"callback_url no permitido: {host!r} no está en la allowlist."
            )
    resolve_and_validate(url, allow_private=allow_private)
