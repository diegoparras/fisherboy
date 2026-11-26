"""Recetas de la sesión de agente: validación, variables/secretos y allowlist de dominios."""
from __future__ import annotations

import pytest

from app.net import recipe


def test_valida_verbos_nuevos():
    pasos = recipe.validar_receta([{"do": "switch_tab"}, {"do": "wait_url", "contains": "portal"}])
    assert [p["do"] for p in pasos] == ["switch_tab", "wait_url"]
    with pytest.raises(ValueError):
        recipe.validar_receta([{"do": "eval", "script": "1"}])


def test_requisitos_por_verbo():
    with pytest.raises(ValueError, match="sel"):
        recipe.validar_receta([{"do": "download"}])
    with pytest.raises(ValueError, match="pattern"):
        recipe.validar_receta([{"do": "capture"}])
    with pytest.raises(ValueError, match="objetivo"):
        recipe.validar_receta([{"do": "goal"}])
    with pytest.raises(ValueError, match="contains"):
        recipe.validar_receta([{"do": "wait_url"}])
    with pytest.raises(ValueError, match="sel"):
        recipe.validar_receta([{"do": "human_if"}])


def test_tope_de_pasos():
    with pytest.raises(ValueError):
        recipe.validar_receta([{"do": "wait", "s": 0}] * (recipe.MAX_PASOS_RECETA + 1))


def test_sustituye_variables_y_secretos():
    assert recipe.sustituir("{{var:a}}-{{secret:b}}", {"a": "1"}, {"b": "x"}) == "1-x"
    assert recipe.sustituir("sin nada", {}, {}) == "sin nada"


def test_placeholder_desconocido():
    with pytest.raises(ValueError):
        recipe.sustituir("{{secret:otra}}", {}, {"b": "x"})


def test_redactar_no_expone_secreto():
    paso = {"do": "type", "sel": "#p", "text": "{{secret:clave}}"}
    red = recipe.redactar(paso)
    assert red["text"] == "{{secret:clave}}"
    assert red is not paso


def test_dominio_permitido():
    assert recipe.dominio_permitido("https://portal.example.com/x", ["portal.example.com"])
    assert recipe.dominio_permitido("https://portal.example.com/", ["portal.example.com"])
    assert not recipe.dominio_permitido("https://portal.example.com.evil.com", ["portal.example.com"])
    assert not recipe.dominio_permitido("https://example.com", ["portal.example.com"])
    assert recipe.dominio_permitido("about:blank", ["portal.example.com"])
    assert recipe.dominio_permitido("https://example.com", [])


def test_fetch_es_verbo_de_receta_y_pide_url():
    pasos = recipe.validar_receta([{"do": "fetch", "url": "ajax.do?f=x", "guardar": "consulta"}])
    assert pasos[0]["do"] == "fetch"
    with pytest.raises(ValueError, match="url"):
        recipe.validar_receta([{"do": "fetch"}])


def test_fetch_valida_el_nombre_de_guardar():
    with pytest.raises(ValueError, match="guardar"):
        recipe.validar_receta([{"do": "fetch", "url": "a", "guardar": "no vale.esto"}])


def test_fetch_no_es_del_job_declarativo():
    # El job declarativo no tiene la allowlist de la sesión: ahí un fetch sería un SSRF.
    from app.fetchers import actions
    with pytest.raises(ValueError):
        actions.validate([{"do": "fetch", "url": "http://10.0.0.1/"}])


def test_res_resuelve_rutas_con_puntos_e_indices():
    guardados = {"consulta": {"datos": {"idConsulta": "42", "filas": [["a", "b"]]}}}
    assert recipe.sustituir_res("id={{res:consulta.datos.idConsulta}}", guardados) == "id=42"
    assert recipe.sustituir_res("{{res:consulta.datos.filas.0.1}}", guardados) == "b"
    with pytest.raises(ValueError, match="consulta.datos.noHay"):
        recipe.sustituir_res("{{res:consulta.datos.noHay}}", guardados)


def test_res_en_una_url_va_codificado():
    guardados = {"c": {"v": "a b&c=d"}}
    assert recipe.sustituir_res("x?id={{res:c.v}}", guardados, url=True) == "x?id=a%20b%26c%3Dd"
