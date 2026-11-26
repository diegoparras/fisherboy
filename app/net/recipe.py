"""Recetas de la sesión de agente: el recorrido fijo que se corre sin IA. Ver docs/AGENT.md.

Una receta es una lista de pasos con los verbos de `fetchers/actions.py` más siete propios de
la sesión viva (que el job declarativo no puede ofrecer porque no tiene pestañas, ni humano,
ni la allowlist de dominios de la sesión):

    wait_url    espera a que la URL contenga `contains`
    switch_tab  pasa a la pestaña nueva (o popup) que se abrió
    download    click en `sel` que dispara una descarga y la guarda
    capture     desde acá guarda las respuestas JSON cuyo URL matchee `pattern`
    human_if    si `sel` está visible (captcha, 2FA), pausa y pide a un humano
    goal        sub-objetivo en lenguaje natural (`objetivo`) para la IA
    fetch       GET a `url` desde la página, con su sesión; espera JSON. `guardar` lo deja
                para `{{res:...}}`, `name` lo guarda como archivo

Los textos aceptan `{{var:nombre}}` y `{{secret:nombre}}`, y `{{res:nombre.ruta}}` para lo que
guardó un `fetch` anterior. Se sustituyen recién al ejecutar cada paso, adentro de la sesión:
el log y el resumen guardan el placeholder, nunca el valor.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from ..fetchers.actions import SAFE_ACTIONS

VERBOS_PROPIOS = frozenset({"wait_url", "switch_tab", "download", "capture", "human_if", "goal",
                            "fetch"})
VERBOS_RECETA = SAFE_ACTIONS | VERBOS_PROPIOS
MAX_PASOS_RECETA = 60

_REQUISITOS = {
    "click": ("sel",), "type": ("sel", "text"), "fill": ("sel", "text"), "hover": ("sel",),
    "select": ("sel", "value"), "wait_for": ("sel",), "press": ("key",), "goto": ("url",),
    "wait_url": ("contains",), "download": ("sel",), "capture": ("pattern",),
    "human_if": ("sel",), "goal": ("objetivo",), "fetch": ("url",),
}
_PLACEHOLDER = re.compile(r"\{\{(var|secret):([A-Za-z0-9_]+)\}\}")
_NOMBRE = re.compile(r"[A-Za-z0-9_]+")
_RES = re.compile(r"\{\{res:([A-Za-z0-9_]+(?:\.[A-Za-z0-9_-]+)*)\}\}")


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
        if do == "fetch" and raw.get("guardar") not in (None, "")                 and not _NOMBRE.fullmatch(str(raw["guardar"])):
            raise ValueError(f"Paso #{i + 1} ('fetch'): `guardar` va con letras, números o _ "
                             "(es el nombre que después se usa en {{res:nombre.ruta}}).")
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


def sustituir_res(valor: str, guardados: dict, *, url: bool = False) -> str:
    """Reemplaza `{{res:nombre.ruta.con.puntos}}` por el valor que guardó un `fetch`.

    Cada tramo es una clave, o un índice si es un número y lo que hay es una lista. Una ruta
    que no existe es un error que la nombra: es lo que cambia cuando el portal cambia su JSON.
    En una URL el valor va codificado, para que un `&` o un espacio no rompan la consulta."""
    def _rep(m: re.Match) -> str:
        ruta = m.group(1)
        nombre, *tramos = ruta.split(".")
        if nombre not in guardados:
            raise ValueError(f"La receta usa {{{{res:{ruta}}}}} y ningún fetch guardó '{nombre}'.")
        v = guardados[nombre]
        for t in tramos:
            if isinstance(v, dict) and t in v:
                v = v[t]
            elif isinstance(v, list) and t.isdigit() and int(t) < len(v):
                v = v[int(t)]
            else:
                raise ValueError(f"La ruta '{ruta}' no existe en lo que guardó el fetch '{nombre}'.")
        if isinstance(v, (dict, list)):
            raise ValueError(f"La ruta '{ruta}' es un objeto o una lista, no un valor.")
        texto = "" if v is None else str(v)
        return quote(texto, safe="") if url else texto
    return _RES.sub(_rep, str(valor))


def sustituir_variables(valor: str, variables: dict) -> str:
    """Como `sustituir`, pero deja los `{{secret:...}}` intactos. Es lo que ve la IA: un
    objetivo puede nombrar una clave por su placeholder, nunca por su valor."""
    def _rep(m: re.Match) -> str:
        if m.group(1) == "secret" or m.group(2) not in variables:
            return m.group(0)
        return str(variables[m.group(2)])
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
