"""Datos de Instagram (instaloader): parseo de URL, kind, gating del endpoint."""
from __future__ import annotations

import pytest

from app.net import instagram
from app.security import auth


@pytest.mark.parametrize("url,kind", [
    ("https://www.instagram.com/p/ABC123/", "post"),
    ("https://instagram.com/reel/XYZ/", "post"),
    ("https://www.instagram.com/tv/QWE/", "post"),
    ("https://www.instagram.com/cristiano/", "profile"),
    ("https://instagram.com/cristiano", "profile"),
    ("https://www.instagram.com/explore/tags/x/", None),
    ("https://www.instagram.com/accounts/login/", None),
    ("https://example.com/p/ABC/", None),
    ("", None),
])
def test_url_kind(url, kind):
    assert instagram.url_kind(url) == kind


def test_extract_shortcode_and_username():
    assert instagram.extract_shortcode("https://instagram.com/p/ABC123/") == "ABC123"
    assert instagram.extract_shortcode("https://instagram.com/reel/ZZ/") == "ZZ"
    assert instagram.extract_shortcode("https://instagram.com/cristiano/") is None
    assert instagram.extract_username("https://instagram.com/cristiano/") == "cristiano"
    assert instagram.extract_username("https://instagram.com/p/ABC/") is None


def test_loader_requires_sessionid():
    with pytest.raises(RuntimeError):
        instagram._loader("")


def _as_role(monkeypatch, role):
    monkeypatch.setattr(auth, "identity_from_request",
                        lambda req: (role, "jti" if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_comments_endpoint_requires_dios(client_factory, monkeypatch):
    _as_role(monkeypatch, "angel")
    c = client_factory(FILE_DOWNLOAD_MODE="both", IG_SESSIONID="x")
    r = c.get("/api/instagram/comments", params={"url": "https://instagram.com/p/ABC/"})
    assert r.status_code == 403
    assert "dios" in r.json()["detail"]


def test_comments_endpoint_needs_sessionid(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")   # sin IG_SESSIONID
    r = c.get("/api/instagram/comments", params={"url": "https://instagram.com/p/ABC/"})
    assert r.status_code == 503
    assert "IG_SESSIONID" in r.json()["detail"]


def test_comments_endpoint_wrong_url_kind(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both", IG_SESSIONID="x")
    r = c.get("/api/instagram/comments", params={"url": "https://instagram.com/cristiano/"})
    assert r.status_code == 400   # es perfil, no post


def test_follows_endpoint_wrong_url_kind(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both", IG_SESSIONID="x")
    r = c.get("/api/instagram/follows", params={"url": "https://instagram.com/p/ABC/"})
    assert r.status_code == 400   # es post, no perfil


def test_me_reports_instagram_capability(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both", IG_SESSIONID="abc123")
    d = c.get("/api/me").json()
    assert d["instagram_data"] is True


def test_me_instagram_false_without_sessionid(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")   # sin sessionid
    d = c.get("/api/me").json()
    assert d["instagram_data"] is False


def test_me_instagram_false_for_angel(client_factory, monkeypatch):
    _as_role(monkeypatch, "angel")
    c = client_factory(FILE_DOWNLOAD_MODE="both", IG_SESSIONID="abc")
    d = c.get("/api/me").json()
    assert d["instagram_data"] is False
