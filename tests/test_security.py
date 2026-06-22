"""Tests de seguridad. Van primero (FISHERBOY-build §11)."""
from __future__ import annotations

import pytest

from app.security.ssrf import (
    SSRFError,
    resolve_and_validate,
    validate_callback_url,
    validate_proxy_url,
)


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


def test_ssrf_transport_pins_validated_ip(monkeypatch):
    """Anti DNS-rebinding: el transport conecta a la IP validada y mantiene Host+SNI por hostname."""
    import httpx

    from app.security import ssrf

    captured = {}

    def fake_super(self, request):
        captured["host"] = request.url.host
        captured["sni"] = request.extensions.get("sni_hostname")
        captured["hosthdr"] = request.headers.get("host")
        return httpx.Response(200, request=request)

    # Resolución determinística (sin red) + interceptar la conexión real.
    monkeypatch.setattr(ssrf, "resolve_and_validate",
                        lambda url, allow_private=False: ["93.184.216.34"])
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", fake_super)

    t = ssrf.SSRFGuardTransport()
    resp = t.handle_request(httpx.Request("GET", "https://example.com/path"))
    assert resp.status_code == 200
    assert captured["host"] == "93.184.216.34"   # conecta a la IP ya validada (no re-resuelve)
    assert captured["sni"] == "example.com"       # TLS SNI + verificación de cert por hostname
    assert captured["hosthdr"] == "example.com"   # Host header preservado (vhost correcto)


def test_capture_resolver_pin_arg():
    """Tier browser: pin del host de entrada a su IP validada (anti DNS-rebinding)."""
    from app.fetchers.capture import _resolver_pin_arg
    assert _resolver_pin_arg("https://example.com/x", ["93.184.216.34"]) == \
        "--host-resolver-rules=MAP example.com 93.184.216.34"
    assert _resolver_pin_arg("https://1.2.3.4/x", ["1.2.3.4"]) is None   # IP literal: no se mapea
    assert _resolver_pin_arg("https://example.com/x", []) is None        # sin IPs: nada que pinear


def test_ssrf_transport_blocks_internal_target():
    """El transport falla cerrado ante un destino interno (metadata de cloud)."""
    import httpx

    from app.security.ssrf import SSRFGuardTransport

    t = SSRFGuardTransport()
    with pytest.raises(SSRFError):
        t.handle_request(httpx.Request("GET", "http://169.254.169.254/latest/meta-data/"))


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


# --------------------------------------------------------------------------- proxy SSRF
@pytest.mark.parametrize("proxy", [
    "http://127.0.0.1:8080",
    "http://10.0.0.5:3128",
    "socks5://169.254.169.254:1080",
    "gopher://1.2.3.4:70",          # esquema no permitido
])
def test_proxy_to_internal_blocked(proxy):
    with pytest.raises(SSRFError):
        validate_proxy_url(proxy, allow_private=False)


def test_proxy_public_ok():
    validate_proxy_url("http://1.1.1.1:3128", allow_private=False)   # no lanza


def test_job_with_internal_proxy_rejected(client_factory):
    client = client_factory()
    resp = client.post("/api/jobs", json={
        "url": "https://1.1.1.1/", "rol": "dios", "privacy_mode": "directo",
        "proxy": "http://127.0.0.1:6379",
    })
    assert resp.status_code == 400
    assert "proxy" in resp.json()["detail"].lower()


# --------------------------------------------------------------------------- DoS / límites
def test_rate_limit_returns_429(client_factory):
    client = client_factory(MAX_JOBS_PER_MIN=3)
    body = {"url": "https://1.1.1.1/", "rol": "dios", "privacy_mode": "directo"}
    codes = [client.post("/api/jobs", json=body).status_code for _ in range(5)]
    assert codes[:3] == [202, 202, 202]
    assert 429 in codes[3:]                      # el 4º/5º ya pasan el tope por minuto


def test_max_pages_hard_capped(client_factory, fake_queue):
    client = client_factory(CRAWL_MAX_PAGES=10)
    r = client.post("/api/jobs", json={
        "url": "https://1.1.1.1/", "rol": "dios", "privacy_mode": "directo",
        "crawl_depth": 2, "max_pages": 200,
    })
    job_id = r.json()["job_id"]
    sobre = fake_queue.get(job_id)
    assert sobre.meta["max_pages"] == 10         # clampeado al tope duro, no 200


# --------------------------------------------------------------------------- logs sin PII
def test_log_redacts_url_querystring():
    import logging as _logging
    from app.logging import JsonFormatter
    rec = _logging.makeLogRecord({"msg": "x", "url": "https://s.com/perfil?email=juan@x.com&token=abc"})
    out = JsonFormatter().format(rec)
    assert "juan@x.com" not in out and "token=abc" not in out
    assert "https://s.com/perfil" in out


# --------------------------------------------------------------------------- proxy test endpoint
def test_proxy_test_requires_proxy_cap(client_factory, monkeypatch):
    from app.security import auth
    monkeypatch.setattr(auth, "identity_from_request", lambda req: ("humano", None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: "humano")
    client = client_factory()
    r = client.post("/api/proxy/test", json={"proxy": "http://1.1.1.1:3128"})
    assert r.status_code == 403


def test_proxy_test_rejects_internal_proxy(client_factory, monkeypatch):
    from app.security import auth
    monkeypatch.setattr(auth, "identity_from_request", lambda req: ("dios", None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: "dios")
    client = client_factory()
    r = client.post("/api/proxy/test", json={"proxy": "http://127.0.0.1:6379"})
    assert r.status_code == 400
    assert "proxy" in r.json()["detail"].lower()


def test_proxy_test_needs_auth(client_factory, monkeypatch):
    from app.security import auth
    monkeypatch.setattr(auth, "identity_from_request", lambda req: (None, None))
    client = client_factory()
    r = client.post("/api/proxy/test", json={"proxy": "http://1.1.1.1:3128"})
    assert r.status_code == 401
