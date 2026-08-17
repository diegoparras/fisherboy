"""Acciones de browser (ADR-011): validación, motor, sesiones y gating del endpoint."""
from __future__ import annotations

import pytest

from app.fetchers import actions as A
from app.fetchers.session_store import BrowserSessionStore
from app.security import auth


# ---------------------------------------------------------------------------
# Validación — un error del usuario tiene que ser un 400 claro, no un timeout raro
# ---------------------------------------------------------------------------
def test_validate_ok():
    acts = A.validate([{"do": "click", "sel": "#x"}, {"do": "wait", "s": 1}])
    assert acts[0]["do"] == "click" and len(acts) == 2


@pytest.mark.parametrize("bad,msg", [
    ([{"do": "volar", "sel": "#x"}], "no existe"),
    ([{"do": "click"}], "falta `sel`"),
    ([{"do": "type", "sel": "#u"}], "falta `text`"),
    ([{"do": "press", "sel": "#u"}], "falta `key`"),
    ([{"do": "goto"}], "falta `url`"),
    (["no soy un dict"], "cada paso es un objeto"),
    ([{}], "falta `do`"),
    ("no soy lista", "tiene que ser una lista"),
])
def test_validate_rechaza(bad, msg):
    with pytest.raises(ValueError) as ei:
        A.validate(bad)
    assert msg in str(ei.value)


def test_validate_tope_de_pasos():
    with pytest.raises(ValueError, match="Demasiadas acciones"):
        A.validate([{"do": "wait", "s": 1}] * (A.MAX_ACTIONS + 1))


def test_eval_bloqueado_por_defecto():
    """`eval` ejecuta JS arbitrario: apagado salvo que el server lo habilite."""
    with pytest.raises(ValueError, match="deshabilitada"):
        A.validate([{"do": "eval", "script": "1+1"}])
    ok = A.validate([{"do": "eval", "script": "1+1"}], allow_eval=True)
    assert ok[0]["do"] == "eval"


# ---------------------------------------------------------------------------
# Redacción — una contraseña que entra por `actions` no puede salir por ningún lado
# ---------------------------------------------------------------------------
def test_redact_esconde_secretos():
    red = A.redact([
        {"do": "type", "sel": "#p", "text": "clave-secreta", "secret": True},
        {"do": "type", "sel": "#u", "text": "diego"},
        {"do": "eval", "script": "fetch('/x')"},
    ])
    assert red[0]["text"] == A.REDACTED
    assert "clave-secreta" not in str(red)
    assert red[1]["text"] == "diego"       # lo no-secreto se mantiene (sirve para depurar)
    assert "fetch" not in str(red[2])      # el script tampoco se guarda entero


def test_from_login_expande_y_marca_secreto():
    acts = A.from_login({"user_sel": "#u", "user": "diego", "pass_sel": "#p",
                         "password": "hunter2", "submit_sel": "#go", "wait_for": ".panel"})
    assert [a["do"] for a in acts] == ["type", "type", "click", "wait_for"]
    assert acts[1]["secret"] is True                       # la clave va marcada
    assert A.redact(acts)[1]["text"] == A.REDACTED         # y por lo tanto nunca sale
    # sin botón declarado, Enter en el campo de la clave
    assert A.from_login({"user_sel": "#u", "user": "d", "pass_sel": "#p",
                         "password": "x"})[-1]["do"] == "press"


def test_from_login_incompleto():
    with pytest.raises(ValueError, match="falta"):
        A.from_login({"user_sel": "#u", "user": "diego"})


# ---------------------------------------------------------------------------
# Motor — con una `page` de mentira que imita la API sync de Playwright
# ---------------------------------------------------------------------------
class FakePage:
    def __init__(self, *, fail_on=(), heights=None):
        self.calls: list[tuple] = []
        self.fail_on = set(fail_on)
        self.url = "https://ej.com/"
        self._heights = list(heights or [])
        self.mouse = self          # mouse.wheel cae en este mismo objeto
        self.keyboard = self

    def _rec(self, name, *a):
        self.calls.append((name, *a))
        if name in self.fail_on:
            raise RuntimeError(f"boom en {name}")

    def click(self, sel, timeout=None): self._rec("click", sel)
    def fill(self, sel, text, timeout=None): self._rec("fill", sel, text)
    def press(self, sel, key=None, timeout=None): self._rec("press", sel, key)
    def hover(self, sel, timeout=None): self._rec("hover", sel)
    def select_option(self, sel, value, timeout=None): self._rec("select", sel, value)
    def wait_for_timeout(self, ms): self._rec("wait", ms)
    def wait_for_selector(self, sel, state=None, timeout=None): self._rec("wait_for", sel, state)
    def wheel(self, x, y): self._rec("wheel", y)
    def goto(self, url, timeout=None, wait_until=None): self._rec("goto", url); self.url = url
    def screenshot(self, full_page=True): self._rec("screenshot"); return b"\x89PNG" + b"0" * 100
    def pdf(self): self._rec("pdf"); return b"%PDF-1.4" + b"0" * 100
    def evaluate(self, script):
        self._rec("evaluate", script)
        if script == "document.body.scrollHeight":
            return self._heights.pop(0) if self._heights else 1000
        return "resultado-js"


def test_motor_corre_la_secuencia():
    page = FakePage()
    out = A.run_actions(page, A.validate([
        {"do": "click", "sel": "#ok"},
        {"do": "type", "sel": "#u", "text": "diego"},
        {"do": "wait_for", "sel": ".listo"},
    ]))
    assert out.failed == ""
    assert [c[0] for c in page.calls] == ["click", "fill", "wait_for"]
    assert all(s["ok"] for s in out.log)


def test_paso_optional_no_corta():
    """El banner de cookies a veces no está: `optional` deja seguir."""
    page = FakePage(fail_on={"click"})
    out = A.run_actions(page, A.validate([
        {"do": "click", "sel": "#banner", "optional": True},
        {"do": "type", "sel": "#u", "text": "diego"},
    ]))
    assert out.failed == ""                     # no cortó
    assert out.log[0]["ok"] is False            # pero quedó registrado
    assert ("fill", "#u", "diego") in page.calls   # y siguió con lo demás


def test_paso_obligatorio_corta_la_secuencia():
    page = FakePage(fail_on={"click"})
    out = A.run_actions(page, A.validate([
        {"do": "click", "sel": "#comprar"},
        {"do": "type", "sel": "#u", "text": "diego"},
    ]))
    assert "acción #1" in out.failed and "click" in out.failed
    assert not any(c[0] == "fill" for c in page.calls)   # NO siguió


def test_scroll_until_corta_cuando_deja_de_crecer():
    """Scroll infinito: para solo cuando la altura se estabiliza, sin gastar las 10 vueltas."""
    page = FakePage(heights=[1000, 2000, 3000, 3000, 3000])
    out = A.run_actions(page, A.validate([{"do": "scroll_until", "max_rounds": 10}]))
    assert out.log[0]["ok"] is True
    assert out.log[0]["rounds"] == 5           # 3 de crecimiento + 2 estables → corta
    assert out.log[0]["rounds"] < 10


def test_scroll_until_respeta_el_tope():
    page = FakePage(heights=[i * 100 for i in range(1, 60)])   # crece siempre
    out = A.run_actions(page, A.validate([{"do": "scroll_until", "max_rounds": 999}]))
    assert out.log[0]["rounds"] <= A.MAX_SCROLL_ROUNDS


def test_screenshot_y_pdf_generan_artefactos():
    page = FakePage()
    out = A.run_actions(page, A.validate([
        {"do": "screenshot", "name": "antes"},
        {"do": "pdf", "name": "informe"},
    ]))
    assert set(out.artifacts) == {"antes.png", "informe.pdf"}
    assert out.artifacts["antes.png"].startswith(b"\x89PNG")


def test_artefacto_gigante_se_rechaza():
    page = FakePage()
    out = A.run_actions(page, A.validate([{"do": "screenshot"}]), max_artifact_bytes=10)
    assert "screenshot" in out.failed and not out.artifacts


def test_goto_revalida_ssrf():
    """Una acción no puede llevar el browser a la red interna esquivando el chequeo inicial."""
    page = FakePage()
    out = A.run_actions(page, A.validate([{"do": "goto", "url": "http://169.254.169.254/latest/"}]))
    assert out.failed, "un goto a metadata interna tiene que fallar"
    assert not any(c[0] == "goto" for c in page.calls)   # ni siquiera navegó


def test_goto_valido_navega():
    page = FakePage()
    out = A.run_actions(page, A.validate([{"do": "goto", "url": "https://example.com/2"}]))
    assert out.failed == "" and page.url == "https://example.com/2"


# ---------------------------------------------------------------------------
# Sesiones de browser — el login que sobrevive al job
# ---------------------------------------------------------------------------
def test_sesion_ida_y_vuelta(fake_redis):
    st = BrowserSessionStore(fake_redis)
    assert st.save("jti-1", "mi-sitio", {"cookies": [{"name": "sid", "value": "abc"}]})
    assert st.load("jti-1", "mi-sitio")["cookies"][0]["value"] == "abc"


def test_sesion_esta_namespaceada_por_dueno(fake_redis):
    """Adivinar el nombre de la sesión de otro NO da acceso a su login."""
    st = BrowserSessionStore(fake_redis)
    st.save("jti-de-ana", "compartida", {"cookies": [{"name": "sid", "value": "de-ana"}]})
    assert st.load("jti-de-ana", "compartida") is not None
    assert st.load("jti-de-beto", "compartida") is None


def test_sesion_rechaza_estado_gigante(fake_redis):
    st = BrowserSessionStore(fake_redis)
    assert st.save("jti", "x", {"basura": "z" * (A.MAX_ACTIONS and 600_000)}) is False


def test_sesion_sin_redis_no_explota():
    st = BrowserSessionStore(None)
    assert st.save("jti", "x", {"a": 1}) is False
    assert st.load("jti", "x") is None


def test_helpers_de_sesion_toleran_todo():
    """session_state/persist_session son una optimización: nunca deben tumbar un fetch."""
    class Ctx:
        session_store, session_name, session_owner = None, "", ""
    assert A.session_state(Ctx()) is None
    assert A.persist_session(None, Ctx()) is False


# ---------------------------------------------------------------------------
# Router — un click no se puede hacer con httpx: hay que subir de tier
# ---------------------------------------------------------------------------
def _router(tiers_disponibles=(0, 2, 3), max_tier=3):
    from app.fetchers.base import FetchResult
    from app.fetchers.router import TierRouter

    usados = []

    class F:
        def __init__(self, tier):
            self.tier, self.name = tier, f"t{tier}"
        def available(self):
            return self.tier in tiers_disponibles
        def fetch(self, url, ctx):
            usados.append((self.tier, list(ctx.actions), ctx.session_name))
            return FetchResult(url=url, status_code=200, content=b"ok", text="ok",
                               content_type="text/html", tier=self.tier)

    return TierRouter([F(t) for t in (0, 1, 2, 3)], max_tier=max_tier), usados


def test_router_sube_a_browser_si_hay_acciones():
    r, usados = _router()
    r.fetch("https://ej.com/", actions=[{"do": "click", "sel": "#x"}])
    assert usados[0][0] >= 2, "con acciones no puede resolverse en tier 0/1"
    assert usados[0][1] == [{"do": "click", "sel": "#x"}]


def test_router_sin_acciones_arranca_barato():
    r, usados = _router()
    r.fetch("https://ej.com/")
    assert usados[0][0] == 0


def test_router_sube_a_browser_si_hay_sesion():
    r, usados = _router()
    r.fetch("https://ej.com/", session_name="mi-sitio", session_owner="jti-1")
    assert usados[0][0] >= 2 and usados[0][2] == "mi-sitio"


def test_router_falla_claro_si_el_rol_capa_el_tier():
    from app.fetchers.base import FetchError
    r, _ = _router(max_tier=1)
    with pytest.raises(FetchError, match="necesitan tier 2"):
        r.fetch("https://ej.com/", actions=[{"do": "click", "sel": "#x"}])


def test_acciones_no_ensucian_la_cache_de_tier():
    """Un job con clicks NO debe dejar todo el dominio arrancando en browser para siempre."""
    r, _ = _router()
    r.fetch("https://ej.com/", actions=[{"do": "click", "sel": "#x"}])
    assert r.cache.get("ej.com") is None
    r.fetch("https://ej.com/")                     # un fetch normal sí cachea
    assert r.cache.get("ej.com") == 0


# ---------------------------------------------------------------------------
# API — validación temprana, gating de `eval` y ownership de los artefactos
# ---------------------------------------------------------------------------
def _as_role(monkeypatch, role, jti="jti-test"):
    monkeypatch.setattr(auth, "identity_from_request", lambda req: (role, jti if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_api_rechaza_acciones_invalidas(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    r = c.post("/api/jobs", json={"url": "https://ej.com/", "actions": [{"do": "click"}]})
    assert r.status_code == 400 and "falta `sel`" in r.json()["detail"]


def test_api_eval_rechazado_sin_config(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()   # BROWSER_ALLOW_EVAL no seteado
    r = c.post("/api/jobs", json={"url": "https://ej.com/",
                                  "actions": [{"do": "eval", "script": "1+1"}]})
    assert r.status_code == 400 and "deshabilitada" in r.json()["detail"]


def test_api_eval_rechazado_para_no_dios_aun_con_config(client_factory, monkeypatch):
    """Las dos llaves: config del server Y rol dios. Con una sola no alcanza."""
    _as_role(monkeypatch, "angel")
    c = client_factory(BROWSER_ALLOW_EVAL="1")
    r = c.post("/api/jobs", json={"url": "https://ej.com/",
                                  "actions": [{"do": "eval", "script": "1+1"}]})
    assert r.status_code == 400 and "deshabilitada" in r.json()["detail"]


def test_api_eval_aceptado_con_config_y_dios(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(BROWSER_ALLOW_EVAL="1")
    r = c.post("/api/jobs", json={"url": "https://ej.com/",
                                  "actions": [{"do": "eval", "script": "1+1"}]})
    assert r.status_code == 202


def test_api_acepta_acciones_validas(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    r = c.post("/api/jobs", json={
        "url": "https://ej.com/",
        "actions": [{"do": "click", "sel": "#ok", "optional": True},
                    {"do": "scroll_until"}, {"do": "screenshot", "name": "final"}],
        "session": "mi-sitio"})
    assert r.status_code == 202


def test_la_clave_del_login_no_sale_por_la_api(client_factory, monkeypatch):
    """El secreto entra por el job pero NO puede volver por GET /api/jobs."""
    _as_role(monkeypatch, "dios")
    c = client_factory()
    r = c.post("/api/jobs", json={
        "url": "https://ej.com/",
        "login": {"user_sel": "#u", "user": "diego", "pass_sel": "#p", "password": "hunter2"}})
    assert r.status_code == 202
    body = c.get(f"/api/jobs/{r.json()['job_id']}").text
    assert "hunter2" not in body


def test_artefacto_404_si_no_es_de_ese_job(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory()
    jid = c.post("/api/jobs", json={"url": "https://ej.com/"}).json()["job_id"]
    assert c.get(f"/api/jobs/{jid}/artifacts/cualquiera.png").status_code == 404
