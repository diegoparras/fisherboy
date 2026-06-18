"""Tests de privacidad: rol×modo y fail-closed."""
from __future__ import annotations

from app.privacy.anonimal_client import build_opaco


def test_privacy_role_denied_returns_403(client_factory):
    client = client_factory()
    # humano solo habilita opaco; reversible debe dar 403 y no encolar.
    resp = client.post(
        "/api/jobs",
        json={"url": "https://1.1.1.1/", "rol": "humano", "privacy_mode": "reversible"},
    )
    assert resp.status_code == 403
    assert "humano" in resp.json()["detail"]


def test_role_allowed_mode_enqueues(client_factory):
    client = client_factory()
    resp = client.post(
        "/api/jobs",
        json={"url": "https://1.1.1.1/", "rol": "humano", "privacy_mode": "opaco"},
    )
    assert resp.status_code == 202
    assert resp.json()["status"] == "pendiente"


def test_default_mode_validated_against_role(client_factory):
    # Sin privacy_mode → default de la matriz (opaco), permitido para humano.
    client = client_factory()
    resp = client.post("/api/jobs", json={"url": "https://1.1.1.1/", "rol": "humano"})
    assert resp.status_code == 202


def test_build_opaco_stable_typed_markers():
    text = "Escribí a juan@mail.com. El CUIT 20-12345678-9 es de juan@mail.com."
    out, n = build_opaco(text, opf_spans=[])
    # mismo valor → mismo marcador (estabilidad); tipos preservados.
    assert "«EMAIL_1»" in out
    assert "«CUIT_1»" in out
    assert out.count("«EMAIL_1»") == 2   # las dos apariciones del mismo email
    assert "juan@mail.com" not in out    # PII enmascarada
    assert n == 2                        # dos entidades únicas
