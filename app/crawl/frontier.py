"""Frontera de crawl persistente + sesiones. Espíritu Crawlee. Ver Capa 1.

El crawler en memoria (crawler.py) sirve para jobs chicos. Para crawls grandes o
resumibles, la frontera (cola de pendientes + set de visitados) vive en Redis bajo un
`crawl_id`: si el worker muere, otro retoma donde quedó. Las sesiones guardan las
cookies por dominio, así un re-crawl reusa la sesión (clave para sitios con login o
fingerprint de sesión).

Crawlee-python queda como hook (es pesado y orientado a su propio runtime); esta
frontera propia cubre la cola persistente y las sesiones con Redis, y se testea con
fakeredis. La dedup por contenido (SHA-256) la sigue haciendo el crawler.
"""
from __future__ import annotations

import json


def _decode(v):
    return v.decode("utf-8") if isinstance(v, bytes) else v


class RedisFrontier:
    """Cola de URLs pendientes + set de visitadas, persistente por crawl_id."""

    def __init__(self, redis_client, crawl_id: str) -> None:
        self._r = redis_client
        self.crawl_id = crawl_id
        self._queue = f"fisherboy:frontier:{crawl_id}:queue"
        self._visited = f"fisherboy:frontier:{crawl_id}:visited"

    def push(self, url: str, depth: int = 0) -> bool:
        """Encola la URL si no se vio antes. Devuelve True si se agregó."""
        if self._r.sadd(self._visited, url) == 0:
            return False  # ya estaba en visitadas
        self._r.rpush(self._queue, f"{depth}|{url}")
        return True

    def pop(self) -> tuple[str, int] | None:
        """Saca el próximo (url, depth) de la cola, o None si está vacía."""
        item = self._r.lpop(self._queue)
        if item is None:
            return None
        depth_str, _, url = _decode(item).partition("|")
        return url, int(depth_str or 0)

    def seen(self, url: str) -> bool:
        return bool(self._r.sismember(self._visited, url))

    def pending(self) -> int:
        return int(self._r.llen(self._queue))

    def visited_count(self) -> int:
        return int(self._r.scard(self._visited))

    def clear(self) -> None:
        self._r.delete(self._queue, self._visited)


class SessionStore:
    """Cookies por dominio, persistentes. Reusa sesión entre crawls del mismo sitio."""

    def __init__(self, redis_client, *, ttl_s: int = 7 * 24 * 3600) -> None:
        self._r = redis_client
        self._ttl = ttl_s

    def _key(self, domain: str) -> str:
        return f"fisherboy:session:{domain}"

    def get_cookies(self, domain: str) -> dict:
        raw = self._r.get(self._key(domain))
        if raw is None:
            return {}
        try:
            return json.loads(_decode(raw))
        except ValueError:
            return {}

    def set_cookies(self, domain: str, cookies: dict) -> None:
        self._r.set(self._key(domain), json.dumps(cookies), ex=self._ttl)

    def clear(self, domain: str) -> None:
        self._r.delete(self._key(domain))


def crawlee_available() -> bool:
    """Crawlee-python como hook alternativo (runtime propio, pesado)."""
    import importlib.util

    return importlib.util.find_spec("crawlee") is not None
