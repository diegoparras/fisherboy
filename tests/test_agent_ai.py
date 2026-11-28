"""Agente IA de la sesión: datos enmascarados, prompt seguro y acciones de una lista cerrada."""
from __future__ import annotations

import json

import pytest

from app.extractors.llm_extract import LLMError
from app.net import agent_ai


def test_enmascara_cuit_y_restaura():
    original = "Representar a CUIT 20-11111111-2 y avisar a persona@ejemplo.com"
    texto, mapeo = agent_ai.enmascarar(original)
    assert "«CUIT_1»" in texto and "«EMAIL_1»" in texto
    assert "11111111" not in texto and "persona@" not in texto
    assert agent_ai.restaurar(texto, mapeo) == original


def test_mismo_valor_mismo_marcador_y_mapeo_compartido():
    t1, mapeo = agent_ai.enmascarar("CUIT 20111111112")
    t2, mapeo = agent_ai.enmascarar("otra vez 20111111112 y 30711111118", mapeo)
    assert "«CUIT_1»" in t1 and "«CUIT_1»" in t2 and "«CUIT_2»" in t2


def test_prompt_lleva_el_arbol_y_los_marcadores_de_clave():
    pagina = '- textbox "Clave" [ref=e4]\n- button "Ingresar" [ref=e5]'
    system, user = agent_ai.armar_prompt("Ingresar al portal", "https://portal.example.com/",
                                         pagina, [], ["clave_portal"])
    assert "dato" in system.lower() and "ref=" in system
    assert "{{secret:clave_portal}}" in system + user
    assert 'button "Ingresar" [ref=e5]' in user


def test_prompt_recorta_una_pagina_enorme():
    pagina = "\n".join(f'- link "Item {i}" [ref=e{i}]' for i in range(5000))
    _, user = agent_ai.armar_prompt("x", "https://portal.example.com/", pagina, [], [])
    assert len(user) < agent_ai.MAX_PAGINA + 2000
    assert "recortada" in user


def test_parsea_accion_valida():
    a = agent_ai.parsear_accion('```json\n{"accion": "click", "ref": "e3"}\n```')
    assert a == {"accion": "click", "ref": "e3"}
    assert agent_ai.parsear_accion('{"accion": "click", "ref": "f1e12"}')["ref"] == "f1e12"
    assert agent_ai.parsear_accion('{"accion":"done","motivo":"listo"}')["accion"] == "done"


def test_rechaza_un_ref_que_no_es_del_arbol():
    # El ref va directo a un selector: solo puede ser lo que el árbol asigna (eN o fNeM).
    for malo in (3, "#boton", "e3, css=body", "aria-ref=e3", "e"):
        with pytest.raises(LLMError):
            agent_ai.parsear_accion(json.dumps({"accion": "click", "ref": malo}))


def test_tapa_lo_tipeado_en_un_campo_de_clave():
    pagina = ('- textbox "Usuario" [ref=e3]: juan\n'
              '- textbox "Clave" [active] [ref=e4]: mi-clave-1\n'
              '- paragraph [ref=e9]: mi-clave-1 es un texto que también está en la página')
    tapada = agent_ai.tapar_claves(pagina, ["mi-clave-1"])
    assert 'textbox "Clave" [active] [ref=e4]: «***»' in tapada
    assert 'textbox "Usuario" [ref=e3]: juan' in tapada
    # Solo se tapa el valor de un campo, no cualquier aparición del texto (que sí es dato).
    assert "mi-clave-1 es un texto" in tapada
    assert agent_ai.tapar_claves(pagina, []) == pagina


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


def test_la_ia_no_puede_pedir_fetch():
    # `fetch` es solo de receta: la IA no elige a qué URL pedirle datos con la sesión puesta.
    with pytest.raises(LLMError):
        agent_ai.parsear_accion('{"accion": "fetch", "url": "https://otro.example/"}')
