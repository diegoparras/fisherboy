"""La traza de Playwright de una sesión de agente, limpia para que pueda salir de la sesión.

Una traza (`trace.zip`) se abre en https://trace.playwright.dev y muestra todo lo que pasó:
cada acción con sus parámetros, una foto del DOM antes y después, la red con sus tiempos y la
consola. Es lo que hace falta para entender por qué un paso falló.

También guarda lo que no puede salir: medido contra Playwright 1.60, la clave tipeada con
`fill` y el valor de las cookies quedan en `trace.trace` (los parámetros de cada llamada) y en
`trace.network` (headers y cookies de cada pedido). Con la cookie de sesión de un portal se
entra sin clave. Por eso, antes de guardarla:

- en cada línea JSON se vacían los headers de credenciales y toda lista `cookies`, estén
  donde estén (como `{"name", "value"}` o como un dict de headers);
- en cada miembro, texto o no, se reemplazan los secretos de la sesión en sus formas
  habituales (crudo, escapado en JSON, codificado en una URL).
"""
from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import quote, quote_plus

TAPADO = "«***»"
HEADERS_SENSIBLES = frozenset({"cookie", "set-cookie", "authorization", "proxy-authorization"})


def _formas(secreto: str) -> list[bytes]:
    """Cómo puede aparecer un secreto adentro de la traza. Las más largas primero, para que
    reemplazar una no deje restos de otra."""
    formas = {secreto, json.dumps(secreto)[1:-1], quote(secreto, safe=""), quote_plus(secreto)}
    return sorted((f.encode() for f in formas if f), key=len, reverse=True)


def _tapar(obj):
    """Recorre el JSON vaciando credenciales. Devuelve el objeto limpio."""
    if isinstance(obj, list):
        return [_tapar(x) for x in obj]
    if not isinstance(obj, dict):
        return obj
    nombre = obj.get("name")
    if isinstance(nombre, str) and nombre.lower() in HEADERS_SENSIBLES and "value" in obj:
        return {**obj, "value": TAPADO}
    out = {}
    for k, v in obj.items():
        if isinstance(k, str) and k.lower() in HEADERS_SENSIBLES and isinstance(v, str):
            out[k] = TAPADO
        elif k == "cookies" and isinstance(v, list):
            out[k] = [{**c, "value": TAPADO} if isinstance(c, dict) and "value" in c else TAPADO
                      for c in v]
        else:
            out[k] = _tapar(v)
    return out


def _limpiar_lineas(datos: bytes) -> bytes:
    lineas = []
    for linea in datos.split(b"\n"):
        try:
            obj = json.loads(linea)
        except ValueError:
            lineas.append(linea)   # no es JSON: queda para el reemplazo de secretos
            continue
        lineas.append(json.dumps(_tapar(obj), ensure_ascii=False).encode())
    return b"\n".join(lineas)


def limpiar_zip(datos: bytes, secretos: list) -> bytes:
    """La misma traza, sin credenciales ni secretos de la sesión."""
    formas = [f for s in secretos if s for f in _formas(str(s))]
    tapado = TAPADO.encode()
    salida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(datos)) as entrada, \
            zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for info in entrada.infolist():
            contenido = entrada.read(info.filename)
            if info.filename.endswith((".trace", ".network")):
                contenido = _limpiar_lineas(contenido)
            for forma in formas:
                contenido = contenido.replace(forma, tapado)
            z.writestr(info.filename, contenido)
    return salida.getvalue()
