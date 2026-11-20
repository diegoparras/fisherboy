"""Sesión de agente (receta + IA + humano) contra la maqueta local de ARCA, con Chromium real."""
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
        {"do": "human_if", "sel": "#captcha", "motivo": "ARCA pide resolver un captcha"},
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
        variables={"cuit_login": "20263630743", "cuit": "20263630743",
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


def test_receta_completa_baja_csv(arca_maqueta):
    ses = _abrir(arca_maqueta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(ses.archivos) == 1
    assert ses.blob(0).decode("utf-8").startswith("Fecha;Tipo")
    assert ses.archivos[0]["nombre"].endswith(".csv")


def test_secreto_no_queda_en_log(arca_maqueta):
    ses = _abrir(arca_maqueta, ia=False)
    _esperar(ses, {"terminado", "error"})
    volcado = json.dumps(ses.resumen(), ensure_ascii=False)
    assert CLAVE not in volcado
    assert "{{secret:clave_fiscal}}" in volcado


def test_clave_incorrecta_falla_con_motivo(arca_maqueta):
    ses = _abrir(arca_maqueta, clave="otra", ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "wait_url" in ses.error
    assert ses.resumen()["alerta"] == "Clave o usuario incorrecto"
    assert "otra" not in json.dumps(ses.resumen())


def test_captcha_pausa_y_humano_resuelve(arca_maqueta):
    ses = _abrir(arca_maqueta, url="login.html?captcha=1", ia=False)
    assert _esperar(ses, {"esperando_humano", "error"}) == "esperando_humano"
    assert "captcha" in ses.motivo_humano
    ses.enviar({"tipo": "click_sel", "sel": "#captcha-ok"})
    time.sleep(0.5)
    ses.devolver()
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error


def test_tomar_control_y_devolver(arca_maqueta):
    ses = _abrir(arca_maqueta, receta=[{"do": "wait", "s": 1}] + _receta(), ia=False)
    ses.tomar_control()
    assert _esperar(ses, {"humano", "error"}) == "humano"
    ses.enviar({"tipo": "tecla", "tecla": "Shift"})     # en modo humano los eventos entran
    ses.devolver()
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error


def test_evento_fuera_de_modo_humano_rechazado(arca_maqueta):
    ses = _abrir(arca_maqueta, ia=False)
    with pytest.raises(ba.AgentError):
        ses.enviar({"tipo": "click", "x": 1, "y": 1})
    _esperar(ses, {"terminado", "error"})


def test_dominio_no_permitido(arca_maqueta):
    receta = [{"do": "goto", "url": "https://example.com/"}]
    ses = _abrir(arca_maqueta, receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "dominio" in ses.error


def test_ia_repara_paso_roto(arca_maqueta):
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
    ses = _abrir(arca_maqueta, receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(llamadas) == 2
    assert any(p.get("origen") == "ia" for p in ses.log)


def test_ia_ve_cuit_enmascarado(arca_maqueta):
    vistos = []

    def falso(system, user):
        vistos.append(user)
        return '{"accion": "done"}'

    receta = [{"do": "goal", "objetivo": "Elegir el representado 20263630743"}]
    ses = _abrir(arca_maqueta, url="setear.html", receta=receta, ia=True, llm_complete=falso)
    _esperar(ses, {"terminado", "error"})
    assert vistos and "20263630743" not in vistos[0]
    assert re.search(r"«CUIT_\d»", vistos[0])


def test_objetivo_con_secreto_no_lo_revela_a_la_ia(arca_maqueta):
    vistos = []

    def falso(system, user):
        vistos.append(system + user)
        return '{"accion": "done"}'

    receta = [{"do": "goal", "objetivo": "Escribir la clave {{secret:clave_fiscal}} en Clave"},
              {"do": "click", "sel": "#noExiste", "timeout_s": 1,
               "objetivo": "Usar {{secret:clave_fiscal}} para {{var:cuit_login}}"}]
    ses = _abrir(arca_maqueta, receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(vistos) == 2
    assert all(CLAVE not in v and "{{secret:clave_fiscal}}" in v for v in vistos)


def test_captura_json(arca_maqueta):
    ses = _abrir(arca_maqueta, ia=False)
    _esperar(ses, {"terminado", "error"})
    assert len(ses.capturas) >= 1
    assert ses.capturas[0]["json"]["data"][0][1] == "Factura C"


def test_tope_de_sesiones(arca_maqueta):
    _abrir(arca_maqueta, receta=[{"do": "wait", "s": 5}], ia=False)
    _abrir(arca_maqueta, receta=[{"do": "wait", "s": 5}], ia=False)
    with pytest.raises(ba.AgentError):
        _abrir(arca_maqueta, receta=[{"do": "wait", "s": 5}], ia=False)
