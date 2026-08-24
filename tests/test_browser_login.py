"""Login interactivo (ADR-013): registro de sesiones, ownership y gating de los endpoints."""
from __future__ import annotations

import pytest

from app.net import browser_login as bl
from app.security import auth


@pytest.fixture(autouse=True)
def _limpiar_registro():
    """Cada test arranca sin sesiones vivas (el registro es global)."""
    bl._SESIONES.clear()
    yield
    bl._SESIONES.clear()


class _SesionFalsa:
    """Se hace pasar por una sesión sin levantar un navegador de verdad."""

    def __init__(self, dueno="jti-1", nombre="s", vencida=False, estado="listo"):
        self.dueno, self.nombre, self.estado = dueno, nombre, estado
        self.frame, self.error, self.url_actual = b"JPG", "", "https://ej.com/"
        self.seq = 1
        self.guardada, self.cerrada = False, False
        self._vencida = vencida
        self.enviados = []

    def vencida(self):
        return self._vencida

    def cerrar(self):
        self.cerrada = True
        self.estado = "cerrado"

    def enviar(self, cmd):
        self.enviados.append(cmd)

    def guardar(self):
        self.guardada = True
        return True


def test_obtener_respeta_al_dueno():
    """Adivinar el token de otro no da acceso a su navegador logueado."""
    bl._SESIONES["tok"] = _SesionFalsa(dueno="jti-ana")
    assert bl.obtener("tok", "jti-ana") is not None
    assert bl.obtener("tok", "jti-beto") is None
    assert bl.obtener("inexistente", "jti-ana") is None


def test_cerrar_ajeno_no_hace_nada():
    ses = _SesionFalsa(dueno="jti-ana")
    bl._SESIONES["tok"] = ses
    assert bl.cerrar("tok", "jti-beto") is False
    assert ses.cerrada is False and "tok" in bl._SESIONES
    assert bl.cerrar("tok", "jti-ana") is True
    assert ses.cerrada is True and "tok" not in bl._SESIONES


def test_limpia_las_vencidas():
    """Una ventana olvidada no puede quedarse con un Chromium para siempre."""
    bl._SESIONES["viva"] = _SesionFalsa()
    bl._SESIONES["vieja"] = _SesionFalsa(vencida=True)
    assert bl.activas() == 1
    assert "vieja" not in bl._SESIONES


def test_limpia_las_muertas():
    bl._SESIONES["rota"] = _SesionFalsa(estado="error")
    assert bl.activas() == 0


def test_tope_de_sesiones_concurrentes(monkeypatch):
    """Cada sesión es un navegador vivo: hay tope para no comerse la RAM del server."""
    monkeypatch.setattr(bl, "_Sesion", lambda *a, **k: _SesionFalsa())
    for _ in range(bl.MAX_SESIONES):
        bl.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)
    with pytest.raises(bl.LoginError, match="ventanas de login"):
        bl.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)


def test_una_vencida_libera_lugar(monkeypatch):
    monkeypatch.setattr(bl, "_Sesion", lambda *a, **k: _SesionFalsa())
    for _ in range(bl.MAX_SESIONES):
        bl.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)
    for s in bl._SESIONES.values():
        s._vencida = True
    tok, _ = bl.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)   # no lanza
    assert tok in bl._SESIONES


def test_url_privada_se_rechaza():
    """El navegador de login no puede ser una ventana a la red interna del server."""
    ses = bl._Sesion("http://169.254.169.254/latest/", nombre="n", dueno="jti", store=None)
    for _ in range(50):
        if ses.estado in ("error", "listo"):
            break
        import time
        time.sleep(0.05)
    assert ses.estado == "error"
    assert "rechazada" in ses.error.lower() or "ssrf" in ses.error.lower()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
def _as_role(monkeypatch, role, jti="jti-test"):
    monkeypatch.setattr(auth, "identity_from_request", lambda req: (role, jti if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_start_requiere_login(client_factory, monkeypatch):
    _as_role(monkeypatch, None)
    c = client_factory()
    assert c.post("/api/browser-login/start", json={"url": "https://x.com/", "session": "s"}
                  ).status_code == 401


def test_start_requiere_rol_con_capture(client_factory, monkeypatch):
    """Abrir un navegador es caro: humano no puede."""
    _as_role(monkeypatch, "humano")
    c = client_factory()
    r = c.post("/api/browser-login/start", json={"url": "https://x.com/", "session": "s"})
    assert r.status_code == 403 and "no habilita" in r.json()["detail"]


def test_start_pide_url_y_nombre(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    assert c.post("/api/browser-login/start", json={"url": "https://x.com/"}).status_code == 400
    assert c.post("/api/browser-login/start", json={"session": "s"}).status_code == 400


def test_frame_404_si_no_es_tuyo(client_factory, monkeypatch):
    bl._SESIONES["tok"] = _SesionFalsa(dueno="jti-otro")
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    assert c.get("/api/browser-login/tok/frame").status_code == 404


def test_frame_devuelve_jpeg(client_factory, monkeypatch):
    """JPEG y no PNG: la mitad de bytes por el mismo dibujo (medido: 54 vs 117 KB)."""
    bl._SESIONES["tok"] = _SesionFalsa(dueno="jti-mio")
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    r = c.get("/api/browser-login/tok/frame")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["X-Login-Estado"] == "listo"


def test_frame_204_si_la_pantalla_no_cambio(client_factory, monkeypatch):
    """Pantalla quieta = cero trafico. El cliente dice hasta que cuadro tiene y no se le
    remandan los mismos bytes."""
    bl._SESIONES["tok"] = _SesionFalsa(dueno="jti-mio")     # seq = 1
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    assert c.get("/api/browser-login/tok/frame?desde=1").status_code == 204
    assert c.get("/api/browser-login/tok/frame?desde=0").status_code == 200


def test_frame_202_mientras_arranca(client_factory, monkeypatch):
    ses = _SesionFalsa(dueno="jti-mio")
    ses.frame, ses.seq = b"", 0        # todavia no hubo primer cuadro
    bl._SESIONES["tok"] = ses
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    assert c.get("/api/browser-login/tok/frame").status_code == 202


def test_evento_llega_a_la_sesion(client_factory, monkeypatch):
    ses = _SesionFalsa(dueno="jti-mio")
    bl._SESIONES["tok"] = ses
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    r = c.post("/api/browser-login/tok/event", json={"tipo": "click", "x": 10, "y": 20})
    assert r.status_code == 200
    assert ses.enviados == [{"tipo": "click", "x": 10, "y": 20}]


def test_evento_guardar_va_por_su_endpoint(client_factory, monkeypatch):
    bl._SESIONES["tok"] = _SesionFalsa(dueno="jti-mio")
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    r = c.post("/api/browser-login/tok/event", json={"tipo": "guardar"})
    assert r.status_code == 400


def test_save_guarda_y_cierra(client_factory, monkeypatch):
    ses = _SesionFalsa(dueno="jti-mio", nombre="mi-x")
    bl._SESIONES["tok"] = ses
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    r = c.post("/api/browser-login/tok/save")
    assert r.status_code == 200 and r.json()["session"] == "mi-x"
    assert ses.guardada is True
    assert "tok" not in bl._SESIONES        # se cierra sola al guardar


# ---------------------------------------------------------------------------
# Modo VNC (el pesado): registro, ownership y que sea de a una
# ---------------------------------------------------------------------------
from app.net import browser_vnc as bv   # noqa: E402


@pytest.fixture(autouse=True)
def _limpiar_vnc():
    bv._SESIONES.clear()
    yield
    bv._SESIONES.clear()


class _VncFalsa:
    def __init__(self, dueno="jti-1", nombre="s", vencida=False, estado="listo"):
        self.dueno, self.nombre, self.estado = dueno, nombre, estado
        self.error, self.guardada, self.cerrada = "", False, False
        self._v = vencida

    def vencida(self):
        return self._v

    def cerrar(self):
        self.cerrada = True

    def guardar(self):
        self.guardada = True
        return True


def test_vnc_es_de_a_una(monkeypatch):
    """Xvfb + Chromium con ventana es caro: no se permiten dos a la vez."""
    monkeypatch.setattr(bv, "_SesionVnc", lambda *a, **k: _VncFalsa())
    monkeypatch.setattr(bv, "_puerto_libre", lambda p: True)
    bv.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)
    with pytest.raises(bv.VncError, match="Ya hay una ventana VNC"):
        bv.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)


def test_vnc_libera_cuando_vence(monkeypatch):
    monkeypatch.setattr(bv, "_SesionVnc", lambda *a, **k: _VncFalsa())
    monkeypatch.setattr(bv, "_puerto_libre", lambda p: True)
    bv.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)
    for s in bv._SESIONES.values():
        s._v = True
    bv.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)   # no lanza


def test_vnc_respeta_al_dueno():
    bv._SESIONES["tok"] = _VncFalsa(dueno="jti-ana")
    assert bv.obtener("tok", "jti-ana") is not None
    assert bv.obtener("tok", "jti-beto") is None
    assert bv.cerrar("tok", "jti-beto") is False


def test_vnc_rechaza_si_el_puerto_esta_ocupado(monkeypatch):
    monkeypatch.setattr(bv, "_puerto_libre", lambda p: False)
    with pytest.raises(bv.VncError, match="puerto"):
        bv.abrir("https://ej.com/", nombre="n", dueno="jti", store=None)


def test_vnc_available_reporta_lo_que_hay(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    d = c.get("/api/browser-login/vnc/available").json()
    assert "ok" in d and isinstance(d["ok"], bool)


def test_vnc_start_pide_url_y_nombre(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    assert c.post("/api/browser-login/vnc/start", json={"url": "https://x.com/"}).status_code == 400


def test_vnc_start_gateado_por_rol(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory()
    r = c.post("/api/browser-login/vnc/start", json={"url": "https://x.com/", "session": "s"})
    assert r.status_code == 403


def test_vnc_save_404_si_no_es_tuyo(client_factory, monkeypatch):
    bv._SESIONES["tok"] = _VncFalsa(dueno="jti-otro")
    _as_role(monkeypatch, "dios", jti="jti-mio")
    c = client_factory()
    assert c.post("/api/browser-login/vnc/tok/save").status_code == 404
