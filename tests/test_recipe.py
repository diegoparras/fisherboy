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
    assert recipe.dominio_permitido("https://auth.afip.gob.ar/x", ["afip.gob.ar"])
    assert recipe.dominio_permitido("https://afip.gob.ar/", ["afip.gob.ar"])
    assert not recipe.dominio_permitido("https://afip.gob.ar.evil.com", ["afip.gob.ar"])
    assert not recipe.dominio_permitido("https://example.com", ["afip.gob.ar"])
    assert recipe.dominio_permitido("about:blank", ["afip.gob.ar"])
    assert recipe.dominio_permitido("https://example.com", [])
