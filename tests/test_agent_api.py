"""API de la sesión de agente: auth, ownership, secretos fuera de las respuestas y allowlist."""
from __future__ import annotations

import pytest

from app.net import browser_agent as ba
from app.security import auth


@pytest.fixture(autouse=True)
def _registro_limpio():
    ba._SESIONES.clear()
    yield
    ba._SESIONES.clear()


class _Falsa:
    def __init__(self, dueno="dios", estado="corriendo", **kw):
        self.dueno, self.estado = dueno, estado
        self.seq, self.frame, self.url_actual = 1, b"JPG", "https://portal.example.com/"
        self.eventos, self.kw = [], kw
        self.archivos = [{"n": 0, "nombre": "c.csv", "tipo": "text/csv", "bytes": 3}]

    def vencida(self):
        return False

    def resumen(self):
        return {"estado": self.estado, "log": [], "archivos": self.archivos}

    def enviar(self, cmd):
        if self.estado not in ("humano", "esperando_humano"):
            raise ba.AgentError("no humano")
        self.eventos.append(cmd)

    def tomar_control(self):
        self.estado = "humano"

    def devolver(self):
        self.estado = "corriendo"

    def blob(self, i):
        return b"a;b"

    def cerrar(self):
        self.estado = "cerrado"


def _abrir_falso(monkeypatch):
    capturado = {}

    def falso(url, **kw):
        capturado.update(url=url, **kw)
        ses = _Falsa(dueno=kw["dueno"])
        ba._SESIONES["tok"] = ses
        return "tok", ses

    monkeypatch.setattr(ba, "abrir", falso)
    return capturado


def test_start_requiere_auth(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "identity_from_request", lambda r: (None, None))
    c = client_factory()
    r = c.post("/api/agent/start", json={"url": "https://portal.example.com/", "receta": []})
    assert r.status_code == 401


def test_resumen_no_devuelve_secretos(client_factory, monkeypatch):
    capturado = _abrir_falso(monkeypatch)
    c = client_factory(AGENT_ALLOWED_DOMAINS="portal.example.com")
    r = c.post("/api/agent/start", json={
        "url": "https://portal.example.com/", "receta": [{"do": "wait", "s": 0}],
        "secretos": {"clave_portal": "super-secreta"}})
    assert r.status_code == 200, r.text
    assert "super-secreta" not in r.text
    assert capturado["secretos"] == {"clave_portal": "super-secreta"}
    estado = c.get("/api/agent/tok")
    assert estado.status_code == 200 and "super-secreta" not in estado.text


def test_dueno_ajeno_404(client_factory):
    ba._SESIONES["tok"] = _Falsa(dueno="otro")
    c = client_factory()
    assert c.get("/api/agent/tok").status_code == 404
    assert c.get("/api/agent/tok/frame").status_code == 404


def test_event_fuera_de_modo_humano_409(client_factory):
    ba._SESIONES["tok"] = _Falsa()
    c = client_factory()
    assert c.post("/api/agent/tok/event", json={"tipo": "click", "x": 1, "y": 1}).status_code == 409
    assert c.post("/api/agent/tok/takeover").status_code == 200
    assert c.post("/api/agent/tok/event", json={"tipo": "click", "x": 1, "y": 1}).status_code == 200
    assert c.post("/api/agent/tok/resume").status_code == 200


def test_dominios_no_amplian_allowlist(client_factory, monkeypatch):
    capturado = _abrir_falso(monkeypatch)
    c = client_factory(AGENT_ALLOWED_DOMAINS="portal.example.com")
    r = c.post("/api/agent/start", json={
        "url": "https://portal.example.com/", "receta": [],
        "dominios": ["portal.example.com", "evil.com"]})
    assert r.status_code == 200
    assert capturado["dominios"] == ["portal.example.com"]
    r = c.post("/api/agent/start", json={"url": "https://evil.com/", "receta": []})
    assert r.status_code == 400


def test_capturas_van_por_su_propio_endpoint(client_factory):
    ses = _Falsa()
    ses.capturas = [{"url": "https://x/listaResultados", "json": {"data": [1]}}]
    ba._SESIONES["tok"] = ses
    c = client_factory()
    r = c.get("/api/agent/tok/captures")
    assert r.status_code == 200 and r.json() == {"capturas": ses.capturas}


def test_archivo_y_frame(client_factory):
    ba._SESIONES["tok"] = _Falsa()
    c = client_factory()
    f = c.get("/api/agent/tok/files/0")
    assert f.status_code == 200 and f.content == b"a;b"
    assert c.get("/api/agent/tok/files/9").status_code == 404
    fr = c.get("/api/agent/tok/frame?desde=0")
    assert fr.status_code == 200 and fr.headers["x-agent-estado"] == "corriendo"
    assert c.get("/api/agent/tok/frame?desde=1").status_code == 204
    assert c.delete("/api/agent/tok").json() == {"ok": True}
