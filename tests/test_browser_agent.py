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
    for s in list(ba._SESIONES.values()):
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
        {"do": "type", "sel": "[id='F1:password']", "text": "{{secret:clave_portal}}"},
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
        secretos={"clave_portal": clave}, dominios=list(dominios), allow_private=True, **kw)
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
    assert "{{secret:clave_portal}}" in volcado


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

    receta = [{"do": "goal", "objetivo": "Escribir la clave {{secret:clave_portal}} en Clave"},
              {"do": "click", "sel": "#noExiste", "timeout_s": 1,
               "objetivo": "Usar {{secret:clave_portal}} para {{var:cuit_login}}"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=True, llm_complete=falso)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert len(vistos) == 2
    assert all(CLAVE not in v and "{{secret:clave_portal}}" in v for v in vistos)


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
            return json.dumps({"accion": "type", "ref": ref, "texto": "{{secret:clave_portal}}"})
        return '{"accion": "done"}'

    receta = [{"do": "goal", "objetivo": "Escribir la clave {{secret:clave_portal}}"}]
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

    receta = [{"do": "goto", "url": portal_maqueta.base + "portal.html?q={{secret:clave_portal}}"},
              {"do": "goal", "objetivo": "mirar"}]
    ses = _abrir(portal_maqueta, receta=receta, ia=True, llm_complete=falso)
    _esperar(ses, {"terminado", "error"})
    assert vistos and all(CLAVE not in v for v in vistos)
    assert CLAVE not in ses.url_actual


def test_receta_no_puede_escribir_el_secreto_en_un_campo_de_texto(portal_maqueta):
    receta = [{"do": "type", "sel": "[id='F1:username']", "text": "{{secret:clave_portal}}"}]
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
    assert ses.resumen()["resumen"] == "No lo hice: quien maneja la sesión dijo que no."


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


def test_iframe_de_otro_dominio_se_bloquea_sin_tumbar_la_sesion(portal_maqueta):
    # El portal real incrusta un video de YouTube en un iframe. Cargar ese iframe es una
    # "navegación" para Playwright, pero no la decide la receta: se aborta en silencio y el
    # paso siguiente no falla por eso.
    receta = [{"do": "wait", "s": 1.5}, {"do": "click", "sel": "#boton"}]
    ses = _abrir(portal_maqueta, url="con_iframe.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert all(paso.get("ok") for paso in ses.log), ses.log


def test_la_pagina_principal_sigue_sin_poder_ir_a_otro_dominio(portal_maqueta):
    receta = [{"do": "click", "sel": "#salir"}, {"do": "wait", "s": 1}]
    ses = _abrir(portal_maqueta, url="con_iframe.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "dominio no permitido" in ses.error and "localhost" in ses.error


# --- la foto del fallo ------------------------------------------------------------------
# Cuando un paso falla, quien diagnostica necesita ver la pantalla de ESE momento. Antes, la
# última foto era la del paso anterior (la que salió bien), y el fallo quedaba invisible.
_RECETA_QUE_FALLA = [
    {"do": "click", "sel": "#buscar"},
    {"do": "wait_for", "sel": "#tabla", "timeout_s": 1.5},
]


def _color_al_centro(jpeg: bytes) -> tuple[int, int, int]:
    """Decodifica la foto con el propio Chromium: así no hace falta sumar Pillow."""
    import base64

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            pagina = browser.new_page()
            rgb = pagina.evaluate("""async (src) => {
                const img = new Image(); img.src = src; await img.decode();
                const c = document.createElement('canvas');
                c.width = img.width; c.height = img.height;
                const g = c.getContext('2d'); g.drawImage(img, 0, 0);
                const d = g.getImageData(img.width >> 1, img.height >> 1, 1, 1).data;
                return [d[0], d[1], d[2]];
            }""", "data:image/jpeg;base64," + base64.b64encode(jpeg).decode())
        finally:
            browser.close()
    return tuple(rgb)


def test_paso_fallido_deja_la_foto_del_momento_del_fallo(portal_maqueta):
    ses = _abrir(portal_maqueta, url="falla.html", receta=_RECETA_QUE_FALLA, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    # El rojo aparece 300 ms después del clic: la foto del clic (paso ok) todavía es blanca.
    r, g, b = _color_al_centro(ses.frame)
    assert r > 200 and g < 80 and b < 80, (r, g, b)


def test_paso_fallido_anota_la_url_y_las_consultas_colgadas(portal_maqueta):
    ses = _abrir(portal_maqueta, url="falla.html", receta=_RECETA_QUE_FALLA, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    fallido = ses.log[-1]
    assert fallido["do"] == "wait_for" and fallido["ok"] is False
    assert "falla.html" in fallido["pagina"]
    assert any("colgado.do" in p for p in fallido["pendientes"]), fallido
    # Sin la query: puede traer datos de la persona (acá, un CUIT).
    assert "20111111112" not in json.dumps(fallido)


def test_la_foto_del_fallo_se_puede_bajar_hasta_cerrar_la_sesion(portal_maqueta, client_factory):
    _, ses = ba.abrir(portal_maqueta.base + "falla.html", dueno="dios", receta=_RECETA_QUE_FALLA,
                      dominios=["127.0.0.1"], allow_private=True, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    token = next(t for t, s in ba._SESIONES.items() if s is ses)
    c = client_factory()
    fr = c.get(f"/api/agent/{token}/frame?desde=0")
    assert fr.status_code == 200 and fr.headers["x-agent-estado"] == "error"
    assert fr.content == ses.frame
    assert c.delete(f"/api/agent/{token}").json() == {"ok": True}
    assert c.get(f"/api/agent/{token}/frame?desde=0").status_code == 404


# --- fetch: pedirle JSON al portal con la sesión de la página -----------------------------
def test_fetch_guarda_y_encadena_al_siguiente(portal_maqueta):
    receta = [
        {"do": "fetch", "url": "api/consulta.json", "guardar": "consulta"},
        {"do": "fetch", "url": "api/resultados.json?id={{res:consulta.datos.idConsulta}}",
         "name": "resultados.json"},
    ]
    ses = _abrir(portal_maqueta, url="menu.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", (ses.error, ses.log)
    assert all(p["ok"] for p in ses.log), ses.log
    assert ses.log[1]["status"] == 200 and ses.log[1]["bytes"] > 0
    archivo = ses.archivos[-1]
    assert archivo["nombre"] == "resultados.json" and archivo["tipo"] == "application/json"
    assert json.loads(ses.blob(archivo["n"]))["datos"]["data"][0][1] == "Factura C"


def test_fetch_no_deja_el_cuerpo_en_el_log(portal_maqueta):
    receta = [{"do": "fetch", "url": "api/consulta.json", "guardar": "consulta"}]
    ses = _abrir(portal_maqueta, url="menu.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert "idConsulta" not in json.dumps(ses.log)


def test_fetch_que_recibe_html_falla_diciendo_que_vino(portal_maqueta):
    receta = [{"do": "fetch", "url": "api/expirada"}]
    ses = _abrir(portal_maqueta, url="menu.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "no es JSON" in ses.error and "HTTP 200" in ses.error
    assert "Sesión expirada" in ses.error


def test_fetch_con_error_http_falla(portal_maqueta):
    receta = [{"do": "fetch", "url": "api/resultados.json?id=7"}]
    ses = _abrir(portal_maqueta, url="menu.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "HTTP 400" in ses.error


def test_fetch_fuera_de_la_allowlist_falla(portal_maqueta):
    ajeno = portal_maqueta.base.replace("127.0.0.1", "localhost") + "api/consulta.json"
    ses = _abrir(portal_maqueta, url="menu.html", receta=[{"do": "fetch", "url": ajeno}], ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "dominio no permitido" in ses.error and "localhost" in ses.error


def test_fetch_no_sigue_redirecciones(portal_maqueta):
    # Una redirección podría llevar el pedido a un host que nadie chequeó (SSRF): se corta.
    ses = _abrir(portal_maqueta, url="menu.html", receta=[{"do": "fetch", "url": "api/redirige"}],
                 ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "redirig" in ses.error


def test_res_con_ruta_inexistente_falla_nombrandola(portal_maqueta):
    receta = [
        {"do": "fetch", "url": "api/consulta.json", "guardar": "consulta"},
        {"do": "fetch", "url": "api/resultados.json?id={{res:consulta.datos.noHay}}"},
    ]
    ses = _abrir(portal_maqueta, url="menu.html", receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "consulta.datos.noHay" in ses.error


# --- redirecciones (auditoria externa 2026-11-26, P-02) ------------------------------------
# El guardia de `context.route` no ve los saltos de un 301/302: el navegador seguia la
# redireccion y quedaba en un host fuera de la lista, con la sesion del portal abierta.

def test_una_redireccion_a_otro_dominio_corta_la_receta(portal_maqueta):
    receta = [{"do": "goto", "url": portal_maqueta.base + "redirige"}, {"do": "wait", "s": 0.5}]
    ses = _abrir(portal_maqueta, receta=receta, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "dominio no permitido" in ses.error and "localhost" in ses.error


def test_si_la_url_inicial_redirige_afuera_la_sesion_no_arranca(portal_maqueta):
    ses = _abrir(portal_maqueta, url="redirige", receta=[{"do": "wait", "s": 0.5}], ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert "dominio no permitido" in ses.error and "localhost" in ses.error


def test_en_modo_humano_una_redireccion_afuera_no_deja_la_pagina_ajena(portal_maqueta):
    ses = _abrir(portal_maqueta, receta=[{"do": "wait", "s": 1}] + _receta(), ia=False)
    ses.tomar_control()
    assert _esperar(ses, {"humano", "error"}) == "humano"
    ses.enviar({"tipo": "navegar", "url": portal_maqueta.base + "redirige"})
    fin = time.monotonic() + 10
    while time.monotonic() < fin and "localhost" not in (ses.alerta or ""):
        time.sleep(0.1)
    assert "localhost" not in (ses.url_actual or ""), ses.url_actual
    assert "dominio no permitido" in (ses.alerta or ""), ses.alerta
    ses.cerrar()


# --- traza de Playwright ----------------------------------------------------------------
def _miembros_zip(datos: bytes) -> dict[str, bytes]:
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_traza_se_guarda_al_fallar_y_sin_la_clave(portal_maqueta):
    receta = [
        {"do": "type", "sel": "[id='F1:username']", "text": "{{var:cuit_login}}"},
        {"do": "click", "sel": "[id='F1:btnSiguiente']"},
        {"do": "type", "sel": "[id='F1:password']", "text": "{{secret:clave_portal}}"},
        {"do": "wait_for", "sel": "#no-existe", "timeout_s": 1},
    ]
    ses = _abrir(portal_maqueta, receta=receta, ia=False, traza=True)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    trazas = [a for a in ses.archivos if a["nombre"] == "traza.zip"]
    assert len(trazas) == 1 and trazas[0]["tipo"] == "application/zip"
    miembros = _miembros_zip(ses.blob(trazas[0]["n"]))
    assert "trace.trace" in miembros
    # La clave se tipeó adentro de la traza (fill) y no puede quedar en ningún lado.
    for nombre, datos in miembros.items():
        assert CLAVE.encode() not in datos, nombre
    assert b"F1:password" in miembros["trace.trace"]   # la acción sí quedó registrada


def test_traza_no_se_guarda_si_todo_sale_bien(portal_maqueta):
    ses = _abrir(portal_maqueta, url="con_iframe.html", receta=[{"do": "click", "sel": "#boton"}],
                 ia=False, traza=True)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert not [a for a in ses.archivos if a["nombre"] == "traza.zip"]


def test_sin_pedirla_no_hay_traza(portal_maqueta):
    ses = _abrir(portal_maqueta, url="falla.html", receta=_RECETA_QUE_FALLA, ia=False)
    assert _esperar(ses, {"terminado", "error"}) == "error"
    assert not [a for a in ses.archivos if a["nombre"] == "traza.zip"]


# --- "terminado" quiere decir que ya está todo -------------------------------------------
class _StoreLento(_StoreEnMemoria):
    """Guarda tarde, como un Redis lejos. Hace visible la carrera sin depender del azar."""

    def save(self, dueno, nombre, estado):
        time.sleep(0.8)
        return super().save(dueno, nombre, estado)


def test_terminado_llega_con_el_login_ya_guardado(portal_maqueta):
    # Antes, la sesión decía "terminado" y DESPUÉS guardaba el login: quien abría la próxima
    # sesión apenas la veía terminar, arrancaba sin él (y el test de arriba fallaba 1 de 3).
    store = _StoreLento()
    ses = _abrir(portal_maqueta, url="galleta.html?poner=1", receta=[{"do": "wait", "s": 0.3}],
                 ia=False, session="sesion-guardada", store=store)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert store.load("t", "sesion-guardada") is not None


def test_terminado_llega_con_la_traza_ya_guardada(portal_maqueta):
    # Un paso opcional que falla también deja traza; tiene que estar al decir "terminado".
    receta = [{"do": "wait_url", "contains": "nunca", "timeout_s": 0.5, "optional": True}]
    ses = _abrir(portal_maqueta, url="con_iframe.html", receta=receta, ia=False, traza=True)
    assert _esperar(ses, {"terminado", "error"}) == "terminado", ses.error
    assert ses.log[0]["ok"] is False   # falló de verdad, y la sesión siguió
    assert [a for a in ses.archivos if a["nombre"] == "traza.zip"]
