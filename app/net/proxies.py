"""Pool de proxies con rotación, cooldown y afinidad por dominio. Ver ADR-006.

La rotación de proxy es el arma de los tiers altos: cuando un sitio bloquea por IP,
cambiás de salida. Estrategias:

- `round_robin` — reparte parejo, ignora el dominio.
- `random` — al azar (con índice derivado, sin Math.random global).
- `sticky` — el mismo dominio sale siempre por el mismo proxy mientras esté sano
  (mantiene cookies/sesión coherentes; clave para sitios con fingerprint de IP).

Ante un fallo (proxy muerto o quemado), el proxy entra en COOLDOWN y deja de
ofrecerse hasta que expira. Si todos están en cooldown, se devuelve None (salida
directa) en vez de fallar: mejor intentar directo que no intentar.

Los proxies se cargan de env (PROXIES, coma-separado) o de un archivo. Soporta
proxies autenticados: el formato es una URL estándar `scheme://user:pass@host:port`.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class _ProxyState:
    url: str
    cooldown_until: float = 0.0
    fails: int = 0
    uses: int = 0


@dataclass
class ProxyPool:
    """Pool con estado mutable protegido por lock (el worker es multi-thread-safe)."""

    proxies: list[str]
    strategy: str = "round_robin"          # round_robin | random | sticky
    cooldown_s: float = 120.0
    _states: list[_ProxyState] = field(default_factory=list, init=False)
    _rr: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        seen, clean = set(), []
        for p in self.proxies:
            p = (p or "").strip()
            if p and p not in seen:
                seen.add(p)
                clean.append(p)
        self.proxies = clean
        self._states = [_ProxyState(url=p) for p in clean]

    @property
    def enabled(self) -> bool:
        return bool(self._states)

    def _healthy(self, now: float) -> list[_ProxyState]:
        return [s for s in self._states if s.cooldown_until <= now]

    def _domain_index(self, domain: str, n: int) -> int:
        # Hash estable del dominio → índice; misma IP de salida por dominio (sticky).
        h = 0
        for ch in domain:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        return h % n

    def acquire(self, *, domain: str = "") -> str | None:
        """Elige un proxy según la estrategia. None = salida directa."""
        if not self._states:
            return None
        with self._lock:
            now = time.monotonic()
            healthy = self._healthy(now)
            if not healthy:
                return None  # todos quemados: mejor directo que nada

            if self.strategy == "sticky" and domain:
                idx = self._domain_index(domain, len(healthy))
                chosen = healthy[idx]
            elif self.strategy == "random" and domain:
                idx = self._domain_index(domain + str(int(now)), len(healthy))
                chosen = healthy[idx]
            else:  # round_robin
                self._rr = (self._rr + 1) % len(healthy)
                chosen = healthy[self._rr]

            chosen.uses += 1
            return chosen.url

    def report_failure(self, proxy: str | None) -> None:
        """Marca un proxy como quemado: cooldown progresivo según fallos repetidos."""
        if not proxy:
            return
        with self._lock:
            for s in self._states:
                if s.url == proxy:
                    s.fails += 1
                    backoff = self.cooldown_s * min(s.fails, 5)
                    s.cooldown_until = time.monotonic() + backoff
                    return

    def report_success(self, proxy: str | None) -> None:
        if not proxy:
            return
        with self._lock:
            for s in self._states:
                if s.url == proxy:
                    s.fails = 0
                    s.cooldown_until = 0.0
                    return

    def stats(self) -> dict:
        now = time.monotonic()
        return {
            "total": len(self._states),
            "healthy": len(self._healthy(now)),
            "strategy": self.strategy,
        }


def _validate_proxy_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in ("http", "https", "socks5", "socks5h") and bool(parts.hostname)


def load_proxies(raw: str) -> list[str]:
    """Parsea la lista de proxies (coma o salto de línea). Descarta inválidos."""
    if not raw:
        return []
    items = re.split(r"[,\n]", raw)
    return [p.strip() for p in items if p.strip() and _validate_proxy_url(p.strip())]


def build_pool(raw: str, strategy: str = "round_robin", cooldown_s: float = 120.0) -> ProxyPool:
    return ProxyPool(load_proxies(raw), strategy=strategy, cooldown_s=cooldown_s)
