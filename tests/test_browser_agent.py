"""Sesión de agente (receta + IA + humano) contra la maqueta local de un portal, con Chromium real."""
from __future__ import annotations

import json
import re
import time

import pytest

from app.net import browser_agent as ba

pytestmark = pytest.mark.browser

CLAVE = "clave-de-prueba"


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:  # noqa: BLE001
        return False


if not _chromium_ok():  # pragma: no cover
    pytest.skip("Chromium de Playwright no disponible", allow_module_level=True)


@pytest.fixture(autouse=True)
def _limpiar():
    yield
    for tok, s in list(ba._SESIONES.items()):
        s.cerrar()
    ba._SESIONES.clear()


def _receta(buscar_sel="#buscarComprobantes", objetivo_buscar=""):
    paso_buscar = {"do": "click", "sel": buscar_sel}
    if objetivo_buscar:
        paso_buscar.update(timeout_s=1, objetivo=objetivo_buscar)
    return [
        {"do": "type", "sel": "[id='F1:username']", "text": "{{var:cuit_login}}"},
        {"do": "click", "sel": "[id='F1:btnSiguiente']"},
        {"do": "human_if", "sel": "#captcha", "motivo": "el portal pide resolver un captcha"},
        {"do": "type", "sel": "[id='F1:password']", "text": "{{secret:clave_fiscal}}"},
        {"do": "click", "sel": "[id='F1:btnIngresar']"},
        {"do": "wait_url", "contains": "portal", "timeout_s": 4},
        {"do": "click", "sel": "#servicio-mcmp"},
        {"do": "switch_tab"},
        {"do": "click", "sel": ".btn-representado[data-cuit='{{var:cuit}}']"},
        {"do": "click", "sel": "#btnEmitidos"},
        {"do": "capture", "pattern": "listaResultados"},
        {"do": "type", "sel": "#fechaEmision", "text": "{{var:rango}}"},
        paso_buscar,
        {"do": "wait_for", "sel": "#tablaDataTables"},
        {"do": "download", "sel": ".buttons-csv"},
    ]


def _abrir(m, *, url="login.html", receta=None, clave=CLAVE, dominios=("127.0.0.1",), **kw):
    _, ses = ba.abrir(
        m.base + url, dueno="t", receta=receta if receta is not None else _receta(),
        variables={"cuit_login": "20111111112", "cuit": "20111111112",
                   "rango": "01/11/2026 - 18/11/2026"},
        secretos={"clave_fiscal": clave}, dominios=list(dominios), allow_private=True, **kw)
    return ses


def _esperar(ses, estados, timeout=40):
    fin = time.monotonic() + timeout
    while time.monotonic() < fin:
        if ses.estado in estados:
            return ses.estado
        time.sleep(0.1)
    raise AssertionError(f"la sesión quedó en {ses.estado}: {ses.error} {ses.log[-3:]}")


def test_receta_completa_baja_csv(portal_maqueta):
    ses = _abrir(portal_maqueta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(ses.archivos) == 1
    assert ses.blob(0).decode("utf-8").startswith("Fecha;Tipo")
    assert ses.archivos[0]["nombre"].endswith(".csv")


def test_secreto_no_queda_en_log(portal_maqueta):
    ses = _abrir(portal_maqueta, ia=False)
    _esperar(ses, {"terminado", "error"})
    volcado = json.dumps(ses.resumen(), ensure_ascii=False)
    assert CLAVE not in volcado
    assert "{{secret:clave_fiscal}}" in volcado


def test_clave_incorrecta_falla_con_motivo(portal_maqueta):
    ses = _abrir(portal_maqueta, clave="otra", ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "wait_url" in ses.error
    assert ses.resumen()["alerta"] == "Clave o usuario incorrecto"
    assert "otra" not in json.dumps(ses.resumen())


def test_captcha_pausa_y_humano_resuelve(portal_maqueta):
    ses = _abrir(portal_maqueta, url="login.html?captcha=1", ia=False)
    assert _esperar(ses, {"esperando_humano", "error"}) == "esperando_humano"
    assert "captcha" in ses.motivo_humano
    ses.enviar({"tipo": "click_sel", "sel": "#captcha-ok"})
    time.sleep(0.5)
    ses.devolver()
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error


def test_tomar_control_y_devolver(portal_maqueta):
    ses = _abrir(portal_maqueta, receta=[{"do": "wait", "s": 1}] + _receta(), ia=False)
    ses.tomar_control()
    assert _esperar(ses, {"humano", "error"}) == "humano"
    ses.enviar({"tipo": "tecla", "tecla": "Shift"})     # en modo humano los eventos entran
    ses.devolver()
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error


def test_evento_fuera_de_modo_humano_rechazado(portal_maqueta):
    ses = _abrir(portal_maqueta, ia=False)
    with pytest.raises(ba.AgentError):
        ses.enviar({"tipo": "click", "x": 1, "y": 1})
    _esperar(ses, {"terminado", "error"})


def test_dominio_no_permitido(portal_maqueta):
    receta = [{"do": "goto", "url": "https://example.com/"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "dominio" in ses.error


def test_ia_repara_paso_roto(portal_maqueta):
    llamadas = []

    def falso(system, user):
        llamadas.append(user)
        assert CLAVE not in system + user
        if len(llamadas) == 1:
            ref = next(json.loads(linea)["ref"] for linea in user.splitlines()
                       if linea.startswith("{") and '"buscarComprobantes"' in linea)
            return json.dumps({"accion": "click", "ref": ref})
        return '{"accion": "done", "motivo": "busque"}'

    receta = _receta(buscar_sel="#botonQueNoExiste", objetivo_buscar="Apretar el boton Buscar")
    ses = _abrir(portal_maqueta, receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(llamadas) == 2
    assert any(p.get("origen") == "ia" for p in ses.log)


def test_ia_ve_cuit_enmascarado(portal_maqueta):
    vistos = []

    def falso(system, user):
        vistos.append(user)
        return '{"accion": "done"}'

    receta = [{"do": "goal", "objetivo": "Elegir el representado 20111111112"}]
    ses = _abrir(portal_maqueta, url="setear.html", receta=receta, ia=True, llm_complete=falso)
    _esperar(ses, {"terminado", "error"})
    assert vistos and "20111111112" not in vistos[0]
    assert re.search(r"«CUIT_\d»", vistos[0])


def test_objetivo_con_secreto_no_lo_revela_a_la_ia(portal_maqueta):
    vistos = []

    def falso(system, user):
        vistos.append(system + user)
        return '{"accion": "done"}'

    receta = [{"do": "goal", "objetivo": "Escribir la clave {{secret:clave_fiscal}} en Clave"},
              {"do": "click", "sel": "#noExiste", "timeout_s": 1,
               "objetivo": "Usar {{secret:clave_fiscal}} para {{var:cuit_login}}"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(vistos) == 2
    assert all(CLAVE not in v and "{{secret:clave_fiscal}}" in v for v in vistos)


def test_captura_json(portal_maqueta):
    ses = _abrir(portal_maqueta, ia=False)
    _esperar(ses, {"terminado", "error"})
    assert len(ses.capturas) >= 1
    assert ses.capturas[0]["json"]["data"][0][1] == "Factura C"
    # El resumen (que se sondea cada 2 s) lleva solo la cantidad, no los JSON.
    assert ses.resumen()["capturas"] == len(ses.capturas)


def test_tope_de_sesiones(portal_maqueta):
    _abrir(portal_maqueta, receta=[{"do": "wait", "s": 5}], ia=False)
    _abrir(portal_maqueta, receta=[{"do": "wait", "s": 5}], ia=False)
    with pytest.raises(ba.AgentError):
        _abrir(portal_maqueta, receta=[{"do": "wait", "s": 5}], ia=False)


def test_ia_no_puede_tipear_el_secreto_fuera_de_un_campo_de_clave(portal_maqueta):
    vistos = []

    def falso(system, user):
        vistos.append(system + user)
        if len(vistos) == 1:
            ref = next(json.loads(linea)["ref"] for linea in user.splitlines()
                       if linea.startswith("{") and '"F1:username"' in linea)
            return json.dumps({"accion": "type", "ref": ref, "texto": "{{secret:clave_fiscal}}"})
        return '{"accion": "done"}'

    receta = [{"do": "goal", "objetivo": "Escribir la clave {{secret:clave_fiscal}}"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=True, llm_complete=falso)
    _esperar(ses, {"terminado", "error"})
    assert len(vistos) == 2
    assert all(CLAVE not in v for v in vistos)
    assert any(p.get("ok") is False and "clave" in p.get("error", "") for p in ses.log if p.get("origen") == "ia")
    assert CLAVE not in json.dumps(ses.resumen()) and CLAVE not in ses.url_actual


def test_secreto_en_la_url_no_llega_a_la_ia(portal_maqueta):
    vistos = []

    def falso(system, user):
        vistos.append(user)
        return '{"accion": "done"}'

    receta = [{"do": "goto", "url": portal_maqueta.base + "portal.html?q={{secret:clave_fiscal}}"},
              {"do": "goal", "objetivo": "mirar"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=True, llm_complete=falso)
    _esperar(ses, {"terminado", "error"})
    assert vistos and all(CLAVE not in v for v in vistos)
    assert CLAVE not in ses.url_actual


def test_receta_no_puede_escribir_el_secreto_en_un_campo_de_texto(portal_maqueta):
    receta = [{"do": "type", "sel": "[id='F1:username']", "text": "{{secret:clave_fiscal}}"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "campo que no es de clave" in ses.error


class _StoreEnMemoria:
    def __init__(self):
        self.guardado = {}

    def load(self, dueno, nombre):
        return self.guardado.get((dueno, nombre))

    def save(self, dueno, nombre, estado):
        self.guardado[(dueno, nombre)] = estado
        return True


def test_sesion_guardada_se_usa_y_se_guarda(portal_maqueta):
    store = _StoreEnMemoria()
    s1 = _abrir(portal_maqueta, url="galleta.html?poner=1", receta=[{"do": "wait", "s": 0.3}],
                ia=False, session="sesion-guardada", store=store)
    assert _esperar(s1, {"terminado", "error"}) == "terminado", s1.error
    guardado = store.load("t", "sesion-guardada")
    assert guardado and any(c["name"] == "visto" for c in guardado["cookies"])
    # La segunda arranca con ese login: la página ve la galleta sin volver a ponerla.
    s2 = _abrir(portal_maqueta, url="galleta.html",
                receta=[{"do": "wait_for", "sel": "#con-galleta", "timeout_s": 3}],
                ia=False, session="sesion-guardada", store=store)
    assert _esperar(s2, {"terminado", "error"}) == "terminado", s2.error


def test_mantener_pasa_a_esperando_humano(portal_maqueta):
    receta = [{"do": "goal", "objetivo": "Hacer algo imposible"}]
    ses = _abrir(portal_maqueta, url="setear.html", receta=receta, ia=True,
                 llm_complete=lambda system, user: "no sé", mantener=True)
    assert _esperar(ses, {"esperando_humano", "error", "terminado"}) == "esperando_humano", ses.error
    assert ses.motivo_humano.startswith("La IA no pudo: ")
    ses.cerrar()


def test_sin_mantener_la_falla_sigue_siendo_error(portal_maqueta):
    receta = [{"do": "goal", "objetivo": "Hacer algo imposible"}]
    ses = _abrir(portal_maqueta, url="setear.html", receta=receta, ia=True,
                 llm_complete=lambda system, user: "no sé")
    assert _esperar(ses, {"esperando_humano", "error", "terminado"}) == "error"


def test_done_trae_resumen_con_los_datos_restaurados(portal_maqueta):
    def falso(system, user):
        assert "20111111112" not in user
        return '{"accion": "done", "motivo": "Está el representado «CUIT_1»"}'

    receta = [{"do": "goal", "objetivo": "Fijate qué representado aparece primero"}]
    ses = _abrir(portal_maqueta, url="setear.html", receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert ses.resumen()["resumen"] == "Está el representado 20111111112"


def _ref_de(user, texto):
    return next(json.loads(linea)["ref"] for linea in user.splitlines()
                if linea.startswith("{") and f'"{texto}"' in linea)


def _clics(ses):
    return ses.consultar_js("() => document.getElementById('clics').textContent")


def test_una_pagina_no_saltea_el_ok(portal_maqueta):
    llamadas = []

    def falso(system, user):
        llamadas.append(user)
        if len(llamadas) == 1:
            return json.dumps({"accion": "click", "ref": _ref_de(user, "Confirmar"),
                               "motivo": "la página lo pide"})
        return '{"accion": "done", "motivo": "listo"}'

    receta = [{"do": "goal", "objetivo": "Hacer lo que dice la página"}]
    ses = _abrir(portal_maqueta, url="confirmar.html", receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"esperando_ok", "terminado", "error"}) == "esperando_ok", ses.error
    assert ses.resumen()["ok_pedido"]["texto"] == "Confirmar"
    time.sleep(0.5)
    assert _clics(ses) == "0"
    ses.ok()
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error


def test_con_no_no_toca_y_termina_diciendolo(portal_maqueta):
    def falso(system, user):
        return json.dumps({"accion": "click", "ref": _ref_de(user, "Confirmar")})

    receta = [{"do": "goal", "objetivo": "Confirmar"}]
    ses = _abrir(portal_maqueta, url="confirmar.html", receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"esperando_ok", "terminado", "error"}) == "esperando_ok", ses.error
    ses.no()
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert ses.resumen()["resumen"] == "No lo hice: Diego dijo que no."


def test_ok_fuera_de_esperando_ok_es_error(portal_maqueta):
    ses = _abrir(portal_maqueta, url="confirmar.html", receta=[{"do": "wait", "s": 3}], ia=False)
    _esperar(ses, {"corriendo"})
    with pytest.raises(ba.AgentError):
        ses.ok()
    ses.cerrar()


def test_punto(portal_maqueta):
    ses = _abrir(portal_maqueta, url="confirmar.html", receta=[{"do": "wait", "s": 4}], ia=False)
    _esperar(ses, {"corriendo"})
    time.sleep(0.5)
    p = ses.punto(100, 100)
    assert p["texto"] == "Ver detalle" and p["tag"] == "button" and p["enFormulario"] is False
    a = ses.punto(activo=True)
    assert a["tag"] == "input" and a["enFormulario"] is True
    ses.cerrar()
