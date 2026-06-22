"""Tests de auth por rol y gating de capacidades (decisiones del dueño, ADR-011)."""
from __future__ import annotations

from app.security import auth


def _as_role(monkeypatch, role):
    """Simula una sesión con ese rol (rol, jti) para los endpoints que usan identidad."""
    monkeypatch.setattr(auth, "identity_from_request", lambda req: (role, "jti-test" if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_login_rate_limited(client_factory, monkeypatch):
    """Anti fuerza-bruta: superar MAX_LOGINS_PER_MIN devuelve 429 (cuenta intentos fallidos)."""
    monkeypatch.setattr(auth, "_PASSWORDS", {"dios": "zeus", "angel": None, "humano": None})
    client = client_factory(MAX_LOGINS_PER_MIN=3)
    codes = [client.post("/api/login", json={"key": "mal"}).status_code for _ in range(4)]
    assert codes[:3] == [401, 401, 401]   # los 3 primeros pasan el rate-limit (clave inválida)
    assert codes[3] == 429                # el 4º se frena por rate-limit


# --------------------------------------------------------------------------- token / password
def test_token_roundtrip():
    tok = auth.make_token("angel")
    assert auth.verify_token(tok) == "angel"
    assert auth.verify_token(tok + "x") is None
    assert auth.verify_token("basura") is None


def test_role_for_password(monkeypatch):
    monkeypatch.setattr(auth, "_PASSWORDS", {"dios": "zeus", "angel": None, "humano": "hola"})
    assert auth.role_for_password("zeus") == "dios"
    assert auth.role_for_password("hola") == "humano"
    assert auth.role_for_password("nope") is None


def test_caps_shape():
    assert auth.caps_for("humano")["max_tier"] == 1
    assert auth.caps_for("humano")["capture"] is False
    assert auth.caps_for("dios")["solver"] is True
    assert auth.caps_for("angel")["solver"] is False


# --------------------------------------------------------------------------- endpoints
def test_login_sets_cookie(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "_PASSWORDS", {"dios": "zeus", "angel": None, "humano": None})
    client = client_factory()
    r = client.post("/api/login", json={"key": "zeus"})
    assert r.status_code == 200
    assert r.json()["role"] == "dios"
    assert auth.COOKIE_NAME in r.cookies
    bad = client.post("/api/login", json={"key": "mal"})
    assert bad.status_code == 401


def test_me_requires_auth(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "role_from_request", lambda req: None)
    client = client_factory()
    assert client.get("/api/me").status_code == 401


# --------------------------------------------------------------------------- gating
def test_humano_cannot_capture(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "capture_api": True})
    assert r.status_code == 403
    assert "captur" in r.json()["detail"].lower()


def test_humano_cannot_force_browser_tier(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "tier_hint": 3})
    assert r.status_code == 403


def test_no_role_escalation(client_factory, monkeypatch):
    # Sesión humano; el body pide actuar como dios → NO escala, queda humano.
    _as_role(monkeypatch, "humano")
    client = client_factory()
    # directo solo lo habilita dios; como queda humano, 403 por la matriz.
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "rol": "dios", "privacy_mode": "directo"})
    assert r.status_code == 403


def test_dios_can_capture(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "capture_api": True, "privacy_mode": "directo"})
    assert r.status_code == 202


def test_unauthenticated_rejected(client_factory, monkeypatch):
    _as_role(monkeypatch, None)
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/"})
    assert r.status_code == 401


def test_browser_cookies_only_dios(client_factory, monkeypatch):
    _as_role(monkeypatch, "angel")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "cookies_browser": "chrome"})
    assert r.status_code == 403
    assert "navegador" in r.json()["detail"].lower()


# --------------------------------------------------------------------------- fail-closed
def test_fail_closed_without_auth_or_optin(client_factory, monkeypatch):
    # Sin contraseñas, sin API_TOKEN y SIN el opt-in de modo abierto → 401 (no dios).
    monkeypatch.setattr(auth, "_PASSWORDS", {"dios": None, "angel": None, "humano": None})
    monkeypatch.setattr(auth, "API_TOKEN", None)
    monkeypatch.delenv("FISHERBOY_OPEN_GOD", raising=False)
    monkeypatch.delenv("HUMAN_OPEN", raising=False)
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/"})
    assert r.status_code == 401


def test_open_god_optin_grants_dios(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "_PASSWORDS", {"dios": None, "angel": None, "humano": None})
    monkeypatch.setattr(auth, "API_TOKEN", None)
    monkeypatch.setenv("FISHERBOY_OPEN_GOD", "1")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "privacy_mode": "directo"})
    assert r.status_code == 202


# --------------------------------------------------------------------------- gating compartido (REST/MCP)
def _req(**over):
    import types
    base = dict(tier_hint=None, capture_api=False, proxy=None, captcha_api_url=None,
                captcha_api_key=None, crawl_depth=0, paginate=False, tarantula=False,
                cookies_browser=None)
    base.update(over)
    return types.SimpleNamespace(**base)


def test_enforce_caps_sidekick_vetoes_tarantula_even_for_dios():
    # dios habilita tarántula por rol, pero NUNCA en sidekick (browser/cookies del host).
    auth.enforce_job_caps("dios", _req(tarantula=True), is_sidekick=False)  # ok standalone
    import pytest
    with pytest.raises(auth.CapDenied):
        auth.enforce_job_caps("dios", _req(tarantula=True), is_sidekick=True)
    with pytest.raises(auth.CapDenied):
        auth.enforce_job_caps("dios", _req(cookies_browser="chrome"), is_sidekick=True)


def test_enforce_caps_blocks_humano_capture():
    import pytest
    with pytest.raises(auth.CapDenied):
        auth.enforce_job_caps("humano", _req(capture_api=True))
