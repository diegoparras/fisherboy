"""La receta que está detrás de la pantalla: de las consultas JSON que hizo la página, una
receta de `fetch` que baja lo mismo sin manejar la pantalla (sin navegador)."""
from __future__ import annotations

import json

from app.net.sugerir import sugerir_receta

PAGINA = "https://portal.example.com/consultas/emitidos.do?tipo=E"


def _x(url, json_, metodo="GET", pagina=PAGINA):
    return {"metodo": metodo, "url": url, "status": 200, "json": json_, "pagina": pagina}


def test_empieza_en_la_pagina_y_pide_lo_mismo():
    out = sugerir_receta([_x("https://portal.example.com/consultas/ajax.do?f=lista", {"a": 1})], {})
    assert out["receta"] == [
        {"do": "goto", "url": PAGINA},
        {"do": "fetch", "url": "ajax.do?f=lista", "name": "respuesta-1.json"},
    ]


def test_encadena_el_id_de_una_respuesta_con_la_siguiente():
    out = sugerir_receta([
        _x("https://portal.example.com/consultas/ajax.do?f=generar", {"datos": {"idConsulta": "231754"}}),
        _x("https://portal.example.com/consultas/ajax.do?f=lista&id=231754", {"data": [[1]]}),
    ], {})
    generar, lista = out["receta"][1], out["receta"][2]
    assert generar["guardar"] == "paso_1"
    assert lista["url"] == "ajax.do?f=lista&id={{res:paso_1.datos.idConsulta}}"


def test_un_valor_que_es_una_variable_va_como_placeholder_y_nunca_literal():
    out = sugerir_receta(
        [_x("https://portal.example.com/consultas/ajax.do?f=lista&cuit=20111111112&t=E", {"a": 1})],
        {"cuit": "20111111112", "tipo": "E"})
    assert out["receta"][1]["url"] == "ajax.do?f=lista&cuit={{var:cuit}}&t=E"
    assert "20111111112" not in json.dumps(out)


def test_una_variable_codificada_en_la_url_tambien_se_reconoce_y_se_avisa():
    out = sugerir_receta(
        [_x("https://portal.example.com/consultas/ajax.do?desde=01%2F11%2F2026+-+18%2F11%2F2026",
            {"a": 1})],
        {"rango": "01/11/2026 - 18/11/2026"})
    assert out["receta"][1]["url"] == "ajax.do?desde={{var:rango}}"
    assert any("rango" in n and "codificad" in n for n in out["notas"])


def test_post_va_a_omitidos():
    out = sugerir_receta([_x("https://portal.example.com/consultas/ajax.do?f=x", {"a": 1}, metodo="POST")], {})
    assert out["receta"] == []
    assert out["omitidos"] == [{"url": "https://portal.example.com/consultas/ajax.do?f=x",
                                "motivo": "fetch solo hace GET"}]


def test_la_misma_consulta_dos_veces_va_una_sola_vez():
    x = _x("https://portal.example.com/consultas/ajax.do?f=lista", {"a": 1})
    out = sugerir_receta([x, dict(x)], {})
    assert [p["do"] for p in out["receta"]] == ["goto", "fetch"]


def test_otro_origen_va_con_la_url_entera_y_otra_carpeta_desde_la_raiz():
    out = sugerir_receta([
        _x("https://portal.example.com/api/v1/datos?x=1", {"a": 1}),
        _x("https://api.example.com/datos", {"b": 2}),
    ], {})
    assert out["receta"][1]["url"] == "/api/v1/datos?x=1"
    assert out["receta"][2]["url"] == "https://api.example.com/datos"


def test_valores_cortos_no_se_toman_por_ids():
    # "1" o "E" aparecen en cualquier JSON: encadenarlos sería inventar una relación.
    out = sugerir_receta([
        _x("https://portal.example.com/consultas/ajax.do?f=a", {"n": "1", "t": "E"}),
        _x("https://portal.example.com/consultas/ajax.do?f=b&p=1&t=E", {"c": 3}),
    ], {})
    assert out["receta"][2]["url"] == "ajax.do?f=b&p=1&t=E"
    assert "guardar" not in out["receta"][1]


def test_sin_consultas_no_hay_receta():
    out = sugerir_receta([], {})
    assert out["receta"] == [] and out["omitidos"] == []
    assert out["notas"]


def test_si_dos_variables_valen_lo_mismo_gana_la_del_mismo_nombre_que_el_parametro():
    out = sugerir_receta(
        [_x("https://portal.example.com/consultas/ajax.do?cuit=20111111112", {"a": 1})],
        {"cuit_login": "20111111112", "cuit": "20111111112"})
    assert out["receta"][1]["url"] == "ajax.do?cuit={{var:cuit}}"
