"""Tests de la superficie: modos APP_MODE, health, contrato del sobre."""
from __future__ import annotations

from app.models import PrivacyMode, Rol, Sobre


def test_app_mode_sidekick_no_ui(client_factory):
    client = client_factory(APP_MODE="sidekick")
    # En sidekick no se monta el router de UI: la raíz no existe.
    assert client.get("/").status_code == 404
    # Pero el REST sigue vivo.
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json()["app_mode"] == "sidekick"


def test_app_mode_standalone_serves_ui(client_factory):
    client = client_factory(APP_MODE="standalone")
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Fisherboy" in resp.text


def test_healthz(client_factory):
    client = client_factory()
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"


def test_get_unknown_job_404(client_factory):
    client = client_factory()
    assert client.get("/api/jobs/nope").status_code == 404


def test_sobre_contract_roundtrip():
    sobre = Sobre(
        job_id="abc",
        source_url="https://example.com/x",
        privacy_mode=PrivacyMode.OPACO,
        rol=Rol.ANGEL,
        content_md="hola «EMAIL_1»",
        anonimizado=True,
        meta={"entidades_anonimizadas": 1},
    )
    raw = sobre.model_dump_json()
    back = Sobre.model_validate_json(raw)
    assert back == sobre
    assert back.privacy_mode is PrivacyMode.OPACO
    assert back.rol is Rol.ANGEL


def test_job_enqueue_then_fetch_roundtrip(client_factory, fake_queue):
    client = client_factory()
    resp = client.post(
        "/api/jobs", json={"url": "https://1.1.1.1/", "rol": "dios", "privacy_mode": "opaco"}
    )
    job_id = resp.json()["job_id"]
    got = client.get(f"/api/jobs/{job_id}")
    assert got.status_code == 200
    assert got.json()["job_id"] == job_id
    assert got.json()["status"] == "pendiente"
