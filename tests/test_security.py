"""Tests de seguridad. Van primero (FISHERBOY-build §11)."""
from __future__ import annotations

import pytest

from app.security.ssrf import SSRFError, resolve_and_validate, validate_callback_url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/admin",
        "http://localhost/",          # resuelve a loopback
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # metadata de cloud
        "http://[::1]/",
        "https://0.0.0.0/",
        "ftp://example.com/file",     # esquema no permitido
        "file:///etc/passwd",
    ],
)
def test_inbound_ssrf_blocked(url):
    with pytest.raises(SSRFError):
        resolve_and_validate(url, allow_private=False)


def test_inbound_public_ip_ok():
    # 1.1.1.1 es pública: no debe bloquearse.
    assert resolve_and_validate("https://1.1.1.1/", allow_private=False) == ["1.1.1.1"]


def test_outbound_callback_ssrf_blocked_endpoint(client_factory):
    client = client_factory()
    resp = client.post(
        "/api/jobs",
        json={
            "url": "https://1.1.1.1/articulo",
            "rol": "dios",
            "privacy_mode": "opaco",
            "callback_url": "http://169.254.169.254/exfil",
        },
    )
    assert resp.status_code == 400
    assert "callback_url" in resp.json()["detail"]


def test_outbound_callback_allowlist():
    # Con allowlist, un host fuera de ella se rechaza aunque sea público.
    with pytest.raises(SSRFError):
        validate_callback_url(
            "https://evil.example.com/hook", allowlist=["hooks.miempresa.com"]
        )
