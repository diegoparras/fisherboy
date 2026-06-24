"""Tier de browser en la nube (Cloudflare Browser Rendering): gating + fetch + errores."""
from __future__ import annotations

import httpx
import pytest

from app.fetchers.base import BlockedError, FetchContext, FetchError
from app.fetchers.cloudflare import CloudflareBrowserFetcher


def _resp(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", "https://x"))


def test_available_gating():
    assert CloudflareBrowserFetcher().available() is False           # sin credenciales
    assert CloudflareBrowserFetcher("acct", "").available() is False  # falta el token
    assert CloudflareBrowserFetcher("acct", "tok").available() is True


def test_fetch_ok(monkeypatch):
    f = CloudflareBrowserFetcher("acct", "tok")

    def fake_post(url, **k):
        assert "/accounts/acct/browser-rendering/content" in url
        assert k["headers"]["Authorization"] == "Bearer tok"
        assert k["json"]["url"] == "https://ej.com"
        return _resp(200, {"success": True, "meta": {"status": 200, "title": "T"},
                           "result": "<html><body>hola</body></html>"})
    monkeypatch.setattr(httpx, "post", fake_post)
    r = f.fetch("https://ej.com", FetchContext())
    assert r.status_code == 200 and "hola" in r.text
    assert r.tier == 3 and r.meta["engine"] == "cloudflare-browser"


def test_fetch_empty_html_blocks(monkeypatch):
    f = CloudflareBrowserFetcher("a", "t")
    monkeypatch.setattr(httpx, "post", lambda url, **k: _resp(200, {"success": True, "result": "   "}))
    with pytest.raises(BlockedError):
        f.fetch("https://ej.com", FetchContext())


def test_fetch_success_false_raises(monkeypatch):
    f = CloudflareBrowserFetcher("a", "t")
    monkeypatch.setattr(httpx, "post",
                        lambda url, **k: _resp(200, {"success": False, "errors": [{"message": "boom"}]}))
    with pytest.raises(FetchError):
        f.fetch("https://ej.com", FetchContext())


def test_fetch_bad_credentials_raises(monkeypatch):
    f = CloudflareBrowserFetcher("a", "t")
    monkeypatch.setattr(httpx, "post", lambda url, **k: _resp(403, {"success": False}))
    with pytest.raises(FetchError):
        f.fetch("https://ej.com", FetchContext())


def test_fetch_429_blocks(monkeypatch):
    f = CloudflareBrowserFetcher("a", "t")
    monkeypatch.setattr(httpx, "post", lambda url, **k: _resp(429, {"success": False}))
    with pytest.raises(BlockedError):
        f.fetch("https://ej.com", FetchContext())


class _FakeRedis:
    def __init__(self, val: bytes | None = None) -> None:
        self._v = val

    def get(self, k):
        return self._v


def test_creds_from_redis_override_env(monkeypatch):
    """La config de la UI (Redis) gana sobre las credenciales del entorno."""
    import json
    fake = _FakeRedis(json.dumps({"enabled": True, "account_id": "ui-acct", "api_token": "ui-tok"}).encode())
    f = CloudflareBrowserFetcher("env-acct", "env-tok", redis_client=fake)
    assert f.available() is True
    cap = {}

    def fake_post(url, **k):
        cap["url"] = url
        cap["auth"] = k["headers"]["Authorization"]
        return _resp(200, {"success": True, "result": "<html>ok</html>"})
    monkeypatch.setattr(httpx, "post", fake_post)
    f.fetch("https://ej.com", FetchContext())
    assert "ui-acct" in cap["url"] and cap["auth"] == "Bearer ui-tok"


def test_redis_disabled_turns_off_even_with_env():
    """Si la UI lo apagó (enabled=false), queda off aunque haya creds en el entorno."""
    import json
    fake = _FakeRedis(json.dumps({"enabled": False, "account_id": "x", "api_token": "y"}).encode())
    f = CloudflareBrowserFetcher("env-acct", "env-tok", redis_client=fake)
    assert f.available() is False
