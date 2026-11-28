"""Limpieza de la traza de Playwright antes de que salga de la sesión (sin navegador).

Una traza es un zip con lo que pasó: cada acción con sus parámetros, la red con sus headers y
fotos del DOM. Medido contra Playwright 1.60: la clave tipeada con `fill` y el valor de una
cookie quedan en `trace.trace`, no solo en `trace.network`. Con la cookie de sesión de un
portal se entra sin clave: tiene que salir vacía, esté donde esté.
"""
from __future__ import annotations

import io
import json
import zipfile
from urllib.parse import quote, quote_plus

from app.net import traza

CLAVE = "clave+de prueba&x"


def _zip(miembros: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for nombre, datos in miembros.items():
            z.writestr(nombre, datos)
    return buf.getvalue()


def _miembros(datos: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        return {n: z.read(n) for n in z.namelist()}


def _lineas(*objs) -> bytes:
    return "\n".join(json.dumps(o) for o in objs).encode()


def test_la_clave_no_queda_en_ninguna_de_sus_formas():
    formas = [CLAVE, json.dumps(CLAVE)[1:-1], quote(CLAVE, safe=""), quote_plus(CLAVE)]
    original = _zip({
        "trace.trace": _lineas({"type": "before", "method": "fill",
                                "params": {"selector": "#c", "value": CLAVE}}),
        "trace.network": _lineas({"type": "resource-snapshot",
                                  "snapshot": {"request": {"url": "https://p.example/?k=" + quote_plus(CLAVE)}}}),
        "resources/abc.html": f'<input value="{quote(CLAVE, safe="")}">'.encode(),
    })
    limpio = _miembros(traza.limpiar_zip(original, [CLAVE]))
    for nombre, datos in limpio.items():
        for forma in formas:
            assert forma.encode() not in datos, (nombre, forma)
    assert "«***»".encode() in limpio["trace.trace"]


def test_las_cookies_y_la_autorizacion_salen_vacias():
    original = _zip({
        "trace.trace": _lineas({"type": "before", "method": "fulfill",
                                "params": {"headers": {"Set-Cookie": "sid=SECRETO1"}}}),
        "trace.network": _lineas({"type": "resource-snapshot", "snapshot": {
            "request": {"headers": [{"name": "Cookie", "value": "sid=SECRETO2"},
                                    {"name": "Authorization", "value": "Bearer SECRETO3"},
                                    {"name": "Accept", "value": "text/html"}],
                        "cookies": [{"name": "sid", "value": "SECRETO4"}]},
            "response": {"headers": [{"name": "set-cookie", "value": "sid=SECRETO5"}],
                         "cookies": [{"name": "sid", "value": "SECRETO6"}]}}}),
    })
    limpio = _miembros(traza.limpiar_zip(original, []))
    todo = b"".join(limpio.values())
    for n in range(1, 7):
        assert f"SECRETO{n}".encode() not in todo, n
    red = json.loads(limpio["trace.network"])
    assert {"name": "Accept", "value": "text/html"} in red["snapshot"]["request"]["headers"]
    assert red["snapshot"]["request"]["cookies"] == [{"name": "sid", "value": "«***»"}]


def test_lo_demas_queda_igual():
    foto = bytes(range(256)) * 4
    original = _zip({"resources/page@1.jpeg": foto,
                     "trace.trace": _lineas({"type": "before", "method": "click"}),
                     "trace.stacks": b'{"files":[]}'})
    limpio = _miembros(traza.limpiar_zip(original, ["no-aparece"]))
    assert limpio["resources/page@1.jpeg"] == foto
    assert json.loads(limpio["trace.trace"]) == {"type": "before", "method": "click"}
    assert set(limpio) == {"resources/page@1.jpeg", "trace.trace", "trace.stacks"}


def test_una_linea_que_no_es_json_no_rompe_y_se_limpia_igual():
    original = _zip({"trace.trace": b'no es json sid=' + CLAVE.encode()})
    limpio = _miembros(traza.limpiar_zip(original, [CLAVE]))
    assert CLAVE.encode() not in limpio["trace.trace"]


def test_un_secreto_vacio_no_borra_todo():
    original = _zip({"trace.trace": _lineas({"a": "hola"})})
    limpio = _miembros(traza.limpiar_zip(original, ["", None]))
    assert json.loads(limpio["trace.trace"]) == {"a": "hola"}
