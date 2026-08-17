"""Sesiones de browser persistentes — que un login sobreviva al job. Ver ADR-011.

Loguearse cuesta caro (un browser, varios pasos, a veces un 2FA). Sin esto habría que repetir
el login en cada job. Acá se guarda el `storage_state` de Playwright (cookies + localStorage)
bajo un nombre elegido por el usuario, y el próximo job que pida esa sesión arranca ya logueado.

    job 1: {"actions": [...login...], "session": "mi-sitio"}   → guarda el estado
    job 2: {"url": ".../panel", "session": "mi-sitio"}          → entra directo, sin login

Seguridad, dos decisiones que importan:
- El estado es UN SECRETO (son cookies de sesión vivas). Vive solo en Redis con TTL, nunca en
  el Sobre ni en el disco, y no se devuelve por ninguna API: se usa y se descarta.
- La clave incluye al DUEÑO. Sin eso, cualquiera que adivinara el nombre "mi-sitio" heredaría
  la sesión logueada de otro. El nombre es del usuario; el namespace lo pone el server.
"""
from __future__ import annotations

import hashlib
import json
import re

_KEY_RE = re.compile(r"[^a-z0-9_.-]+")
MAX_STATE_BYTES = 512 * 1024      # un storage_state sano pesa KBs; más que esto es sospechoso


def _slug(name: str) -> str:
    return _KEY_RE.sub("-", (name or "").strip().lower())[:64]


def _key(owner: str, name: str) -> str:
    # El dueño va hasheado: la clave de Redis no tiene por qué exponer el jti de nadie.
    who = hashlib.sha256((owner or "anon").encode("utf-8")).hexdigest()[:16]
    return f"fisherboy:bsession:{who}:{_slug(name)}"


class BrowserSessionStore:
    """Guarda/lee el storage_state de una sesión de browser. Best-effort: si Redis no está,
    todo sigue andando (solo que la sesión no persiste entre jobs)."""

    def __init__(self, redis_client, ttl_s: int = 7 * 24 * 3600) -> None:
        self._r = redis_client
        self._ttl = int(ttl_s)

    def load(self, owner: str, name: str) -> dict | None:
        if not (self._r and name):
            return None
        try:
            raw = self._r.get(_key(owner, name))
        except Exception:  # noqa: BLE001 — Redis caído: seguimos sin sesión
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None

    def save(self, owner: str, name: str, state: dict) -> bool:
        if not (self._r and name and isinstance(state, dict)):
            return False
        try:
            blob = json.dumps(state)
        except (TypeError, ValueError):
            return False
        if len(blob) > MAX_STATE_BYTES:
            return False
        try:
            self._r.set(_key(owner, name), blob, ex=self._ttl)
            return True
        except Exception:  # noqa: BLE001
            return False

    def delete(self, owner: str, name: str) -> bool:
        if not (self._r and name):
            return False
        try:
            return bool(self._r.delete(_key(owner, name)))
        except Exception:  # noqa: BLE001
            return False
