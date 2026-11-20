"""Recetas de la sesión de agente: el recorrido fijo que se corre sin IA. Ver docs/AGENT.md.

Una receta es una lista de pasos con los verbos de `fetchers/actions.py` más seis propios de
la sesión viva (que el job declarativo no puede ofrecer porque no tiene pestañas ni humano):

    wait_url    espera a que la URL contenga `contains`
    switch_tab  pasa a la pestaña nueva (o popup) que se abrió
    download    click en `sel` que dispara una descarga y la guarda
    capture     desde acá guarda las respuestas JSON cuyo URL matchee `pattern`
    human_if    si `sel` está visible (captcha, 2FA), pausa y pide a un humano
    goal        sub-objetivo en lenguaje natural (`objetivo`) para la IA

Los textos aceptan `{{var:nombre}}` y `{{secret:nombre}}`. Se sustituyen recién al ejecutar
cada paso, adentro de la sesión: el log y el resumen guardan el placeholder, nunca el valor.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from ..fetchers.actions import SAFE_ACTIONS

VERBOS_PROPIOS = frozenset({"wait_url", "switch_tab", "download", "capture", "human_if", "goal"})
VERBOS_RECETA = SAFE_ACTIONS | VERBOS_PROPIOS
MAX_PASOS_RECETA = 60

_REQUISITOS = {
    "click": ("sel",), "type": ("sel", "text"), "fill": ("sel", "text"), "hover": ("sel",),
    "select": ("sel", "value"), "wait_for": ("sel",), "press": ("key",), "goto": ("url",),
    "wait_url": ("contains",), "download": ("sel",), "capture": ("pattern",),
    "human_if": ("sel",), "goal": ("objetivo",),
}
_PLACEHOLDER = re.compile(r"\{\{(var|secret):([A-Za-z0-9_]+)\}\}")


def validar_receta(pasos) -> list[dict]:
    """Valida y normaliza. ValueError con un mensaje que se le puede mostrar a quien la escribió."""
    if pasos is None:
        return []
    if not isinstance(pasos, list):
        raise ValueError("La receta tiene que ser una lista de pasos.")
    if len(pasos) > MAX_PASOS_RECETA:
        raise ValueError(f"Demasiados pasos ({len(pasos)}); el tope es {MAX_PASOS_RECETA}.")
    out: list[dict] = []
    for i, raw in enumerate(pasos):
        if not isinstance(raw, dict):
            raise ValueError(f"Paso #{i + 1}: cada paso es un objeto.")
        do = str(raw.get("do") or "").strip().lower()
        if do not in VERBOS_RECETA:
            raise ValueError(f"Paso #{i + 1}: '{do}' no es un verbo de receta. "
                             f"Disponibles: {', '.join(sorted(VERBOS_RECETA))}.")
        for campo in _REQUISITOS.get(do, ()):
            if raw.get(campo) in (None, ""):
                raise ValueError(f"Paso #{i + 1} ('{do}'): falta `{campo}`.")
        if do == "capture":
            try:
                re.compile(str(raw["pattern"]))
            except re.error as e:
                raise ValueError(f"Paso #{i + 1} ('capture'): `pattern` no es una regex válida.") from e
        out.append(dict(raw, do=do))
    return out


def sustituir(valor: str, variables: dict, secretos: dict) -> str:
    """Reemplaza los placeholders. Uno desconocido es un error (mejor que tipear un literal)."""
    def _rep(m: re.Match) -> str:
        fuente = variables if m.group(1) == "var" else secretos
        if m.group(2) not in fuente:
            raise ValueError(f"La receta usa {{{{{m.group(1)}:{m.group(2)}}}}} y no vino en la sesión.")
        return str(fuente[m.group(2)])
    return _PLACEHOLDER.sub(_rep, str(valor))


def redactar(paso: dict) -> dict:
    """Copia del paso apta para el log: placeholders tal cual, nunca valores sustituidos."""
    return {k: v for k, v in paso.items() if k != "_resuelto"}


def dominio_permitido(url: str, dominios: list[str]) -> bool:
    """El host es uno de `dominios` o subdominio. Lista vacía = sin restricción.

    `about:blank` pasa siempre: es donde nace toda pestaña nueva antes de navegar."""
    if not dominios:
        return True
    if str(url).startswith("about:"):
        return True
    host = (urlsplit(str(url)).hostname or "").lower().rstrip(".")
    for d in dominios:
        d = d.lower().strip().lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False
