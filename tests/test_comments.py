"""Comentarios multi-plataforma: detección, reliability, gating, Reddit parse."""
from __future__ import annotations

import pytest

from app.net import comments as cmod
from app.security import auth


@pytest.mark.parametrize("url,plat", [
    ("https://www.youtube.com/watch?v=x", "youtube"),
    ("https://youtu.be/x", "youtube"),
    ("https://www.reddit.com/r/x/comments/1/", "reddit"),
    ("https://x.com/u/status/1", "twitter"),
    ("https://www.tiktok.com/@x/video/1", "tiktok"),
    ("https://example.com/x", None),
])
def test_comment_platform(url, plat):
    assert cmod.comment_platform(url) == plat


def test_experimental_flag():
    assert cmod.is_experimental("https://x.com/u/status/1") is True
    assert cmod.is_experimental("https://tiktok.com/@x/video/1") is True
    assert cmod.is_experimental("https://reddit.com/r/x/") is True   # Reddit 403 desde server
    assert cmod.is_experimental("https://youtube.com/watch?v=x") is False  # yt-dlp confiable


def test_reddit_parse(monkeypatch):
    # listing estilo Reddit: [post, comments]; t1 con replies anidadas.
    fake = [
        {"kind": "Listing", "data": {"children": []}},
        {"kind": "Listing", "data": {"children": [
            {"kind": "t1", "data": {"author": "ana", "body": "hola", "score": 5,
                                    "created_utc": 1700000000,
                                    "replies": {"kind": "Listing", "data": {"children": [
                                        {"kind": "t1", "data": {"author": "leo", "body": "respuesta",
                                                                "score": 2, "created_utc": 1700000100,
                                                                "replies": ""}}]}}}},
            {"kind": "more", "data": {}},
        ]}},
    ]

    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}
        def json(self): return fake

    class _Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, u): return _Resp()

    import sys
    import types
    fake_httpx = types.ModuleType("httpx")
    fake_httpx.Client = _Client
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    out = cmod._reddit_comments("https://reddit.com/r/x/comments/1/", max_items=50, timeout_s=5)
    assert [c["author"] for c in out] == ["ana", "leo"]   # aplana anidados
    assert out[0]["text"] == "hola" and out[0]["score"] == 5
    assert out[1]["created_at"].startswith("2023-11-14")  # iso del unix ts


def _as_role(monkeypatch, role):
    monkeypatch.setattr(auth, "identity_from_request",
                        lambda req: (role, "jti" if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_comments_endpoint_humano_forbidden(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/comments", json={"url": "https://youtube.com/watch?v=x"})
    assert r.status_code == 403


def test_comments_endpoint_unknown_platform(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/comments", json={"url": "https://example.com/x"})
    assert r.status_code == 400


def test_me_reports_comments_capability(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    d = c.get("/api/me").json()
    assert d["comments_download"] is True


def test_me_comments_false_for_humano(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    d = c.get("/api/me").json()
    assert d["comments_download"] is False


def test_auth_wall_detection():
    assert cmod._is_auth_wall("ERROR: Sign in to confirm you're not a bot. Use --cookies") is True
    assert cmod._is_auth_wall("This video is age-restricted") is True
    assert cmod._is_auth_wall("HTTP Error 404: Not Found") is False
    assert cmod._is_auth_wall("") is False
    assert issubclass(cmod.CommentsAuthRequired, RuntimeError)


def test_cookies_to_netscape():
    from app.security.cookies import to_netscape
    # header "k=v; k2=v2" -> dos líneas con el dominio dado
    ns = to_netscape("SID=abc; HSID=def", ".youtube.com")
    assert ns.startswith("# Netscape")
    assert ns.count(".youtube.com\tTRUE") == 2 and "SID\tabc" in ns
    # JSON array
    assert "tok\tXYZ" in to_netscape('[{"name":"tok","value":"XYZ"}]')
    # ya-Netscape: verbatim, conserva el dominio real
    raw = ".example.com\tTRUE\t/\tTRUE\t99\tLOGIN\tzzz"
    out = to_netscape(raw)
    assert "LOGIN\tzzz" in out and ".example.com" in out
    # vacío
    assert to_netscape("") == "" and to_netscape(None) == ""
