"""Agente IA de la sesión: datos enmascarados, prompt seguro y acciones de una lista cerrada."""
from __future__ import annotations

import pytest

from app.extractors.llm_extract import LLMError
from app.net import agent_ai


def test_enmascara_cuit_y_restaura():
    original = "Representar a CUIT 20-11111111-2 y avisar a persona@ejemplo.com"
    texto, mapeo = agent_ai.enmascarar(original)
    assert "«CUIT_1»" in texto and "«EMAIL_1»" in texto
    assert "11111111" not in texto and "diego@" not in texto
    assert agent_ai.restaurar(texto, mapeo) == original


def test_mismo_valor_mismo_marcador_y_mapeo_compartido():
    t1, mapeo = agent_ai.enmascarar("CUIT 20111111112")
    t2, mapeo = agent_ai.enmascarar("otra vez 20111111112 y 30711111118", mapeo)
    assert "«CUIT_1»" in t1 and "«CUIT_1»" in t2 and "«CUIT_2»" in t2


def test_prompt_no_incluye_valor_de_secreto():
    elementos = [{"ref": 1, "tag": "input", "tipo": "password", "id": "F1:password", "valor": ""}]
    system, user = agent_ai.armar_prompt("Ingresar a el portal", "https://auth.portal.example.com/",
                                         elementos, [], ["clave_fiscal"])
    assert "dato" in system.lower()
    assert "{{secret:clave_fiscal}}" in system + user
    assert "F1:password" in user


def test_parsea_accion_valida():
    a = agent_ai.parsear_accion('```json\n{"accion": "click", "ref": 3}\n```')
    assert a == {"accion": "click", "ref": 3}
    assert agent_ai.parsear_accion('{"accion":"done","motivo":"listo"}')["accion"] == "done"


def test_rechaza_accion_fuera_de_lista():
    with pytest.raises(LLMError):
        agent_ai.parsear_accion('{"accion": "eval", "script": "fetch(1)"}')


def test_rechaza_click_sin_ref():
    with pytest.raises(LLMError):
        agent_ai.parsear_accion('{"accion": "click"}')
    with pytest.raises(LLMError):
        agent_ai.parsear_accion('{"accion": "type", "ref": 2}')
    with pytest.raises(LLMError):
        agent_ai.parsear_accion('no es json')
