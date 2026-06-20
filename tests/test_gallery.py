"""Descarga de galerías (gallery-dl): allowlist, comando, gating del endpoint, seed."""
from __future__ import annotations

import pytest

from app.net import gallery
from app.security import auth


@pytest.mark.parametrize("url,ok", [
    ("https://www.instagram.com/p/abc/", True),
    ("https://x.com/user/status/1", True),
    ("https://twitter.com/user/status/1", True),
    ("https://www.reddit.com/r/x/comments/1/", True),
    ("https://www.pinterest.com/pin/1/", True),
    ("https://imgur.com/gallery/abc", True),
    ("https://evil.com/gallery", False),
    ("http://169.254.169.254/", False),
    ("https://notinstagram.com.evil.com/x", False),
    ("", False),
])
def test_gallery_host_allowed(url, ok):
    assert gallery.gallery_host_allowed(url) is ok


@pytest.mark.parametrize("url,prov", [
    ("https://www.instagram.com/p/abc/", "instagram"),
    ("https://x.com/u/status/1", "twitter"),
    ("https://reddit.com/r/x/", "reddit"),
    ("https://example.com/page", None),
])
def test_gallery_provider(url, prov):
    assert gallery.gallery_provider(url) == prov


def test_download_gallery_builds_command(monkeypatch, tmp_path):
    """Arma el comando correcto y, si no hay archivos, lanza con el motivo de stderr."""
    captured = {}

    class _Proc:
        stderr = "error: cuenta privada"
        stdout = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _Proc()

    monkeypatch.setattr(gallery.subprocess, "run", _fake_run)
    with pytest.raises(RuntimeError) as ei:
        gallery.download_gallery("https://instagram.com/p/x/", tmpdir=str(tmp_path),
                                 max_files=20, proxy="http://p:1", cookiefile="c.txt")
    cmd = captured["cmd"]
    assert "-m" in cmd and "gallery_dl" in cmd
    assert "-D" in cmd and str(tmp_path) in cmd
    assert "1-20" in cmd                       # --range
    assert "--proxy" in cmd and "http://p:1" in cmd
    assert "-C" in cmd and "c.txt" in cmd
    assert "cuenta privada" in str(ei.value)   # motivo del stderr propagado


def _as_role(monkeypatch, role):
    monkeypatch.setattr(auth, "identity_from_request",
                        lambda req: (role, "jti" if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_gallery_endpoint_humano_forbidden(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.get("/api/download/gallery", params={"url": "https://instagram.com/p/x/"})
    assert r.status_code == 403


def test_gallery_endpoint_bad_host(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.get("/api/download/gallery", params={"url": "https://evil.com/x"})
    assert r.status_code == 400
    assert "plataformas conocidas" in r.json()["detail"]


def test_gallery_endpoint_disabled_mode(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="direct")
    r = c.get("/api/download/gallery", params={"url": "https://instagram.com/p/x/"})
    assert r.status_code == 403


def test_me_reports_gallery_capability(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    d = c.get("/api/me").json()
    assert "gallery_download" in d
    assert d["gallery_download"] is True   # dios + gallery-dl instalado + modo both


def test_pipeline_injects_seed_gallery():
    """Pegar un link de Instagram directo debe ofrecer la galería."""
    from app.fetchers.base import FetchResult
    from app.models import PrivacyMode, Rol, Sobre
    from app.pipeline import PipelineDeps, _harvest_files

    deps = PipelineDeps(fetch=None, extract=None,
                        anonymize_opaco=lambda t: (t, 0), file_download_mode="both")
    s = Sobre(job_id="g", source_url="https://www.instagram.com/p/ABC123/",
              privacy_mode=PrivacyMode.DIRECTO, rol=Rol.DIOS)
    page = FetchResult(url="https://www.instagram.com/p/ABC123/", status_code=200,
                       content=b"<html><body>ig</body></html>", text="<html><body>ig</body></html>",
                       content_type="text/html", tier=0)
    _harvest_files(deps, s, [page])
    embeds = s.meta["files"]["embed"]
    gal = [e for e in embeds if e.get("dl_gallery")]
    assert gal and gal[0]["provider"] == "instagram"
