"""Tests de auth por rol y gating de capacidades (decisiones del dueño, ADR-011)."""
from __future__ import annotations

from app.security import auth


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
    monkeypatch.setattr(auth, "role_from_request", lambda req: "humano")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "capture_api": True})
    assert r.status_code == 403
    assert "captur" in r.json()["detail"].lower()


def test_humano_cannot_force_browser_tier(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "role_from_request", lambda req: "humano")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "tier_hint": 3})
    assert r.status_code == 403


def test_no_role_escalation(client_factory, monkeypatch):
    # Sesión humano; el body pide actuar como dios → NO escala, queda humano.
    monkeypatch.setattr(auth, "role_from_request", lambda req: "humano")
    client = client_factory()
    # directo solo lo habilita dios; como queda humano, 403 por la matriz.
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "rol": "dios", "privacy_mode": "directo"})
    assert r.status_code == 403


def test_dios_can_capture(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "role_from_request", lambda req: "dios")
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "capture_api": True, "privacy_mode": "directo"})
    assert r.status_code == 202


def test_unauthenticated_rejected(client_factory, monkeypatch):
    monkeypatch.setattr(auth, "role_from_request", lambda req: None)
    client = client_factory()
    r = client.post("/api/jobs", json={"url": "https://1.1.1.1/"})
    assert r.status_code == 401
