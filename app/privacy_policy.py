"""Matriz rol×modo, cargada desde privacy_matrix.yaml. No hardcodeada. Ver ADR-004.

Antes de encolar, el gateway valida el rol contra el modo pedido. Si el rol no lo
habilita, responde 403 y no encola. Nunca se baja de modo en silencio.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from .models import PrivacyMode, Rol


class PolicyDenied(Exception):
    """El rol no habilita el modo de privacidad pedido. → 403."""


class PrivacyPolicy:
    def __init__(self, matrix: dict) -> None:
        roles = matrix.get("roles") or {}
        self._allowed: dict[Rol, set[PrivacyMode]] = {}
        for rol in Rol:
            entry = roles.get(rol.value) or {}
            modes = entry.get("allowed_modes") or []
            self._allowed[rol] = {PrivacyMode(m) for m in modes}

        raw_default = matrix.get("default_mode", "opaco")
        self._default = PrivacyMode(raw_default)

        if (matrix.get("on_denied") or "deny") != "deny":
            raise ValueError("on_denied solo admite 'deny' (ADR-004).")

    def allowed_modes(self, rol: Rol) -> set[PrivacyMode]:
        return self._allowed.get(rol, set())

    def resolve_mode(self, rol: Rol, requested: PrivacyMode | None) -> PrivacyMode:
        """Resuelve el modo efectivo y valida contra el rol. Lanza PolicyDenied si no.

        Si `requested` es None, usa el default de la matriz — pero igual lo valida
        contra el rol, así un default mal configurado no abre un agujero.
        """
        mode = requested if requested is not None else self._default
        allowed = self.allowed_modes(rol)
        if mode not in allowed:
            permitidos = ", ".join(sorted(m.value for m in allowed)) or "ninguno"
            raise PolicyDenied(
                f"El rol '{rol.value}' no habilita el modo '{mode.value}'. "
                f"Permitidos: {permitidos}."
            )
        return mode


def load_policy(path: str) -> PrivacyPolicy:
    with open(path, encoding="utf-8") as fh:
        matrix = yaml.safe_load(fh) or {}
    return PrivacyPolicy(matrix)


@lru_cache(maxsize=4)
def get_policy(path: str) -> PrivacyPolicy:
    return load_policy(path)
