"""Descarga de video (yt-dlp): allowlist de hosts, selección de formato, gating del endpoint."""
from __future__ import annotations

import pytest

from app.net import media
from app.security import auth


@pytest.mark.parametrize("url,ok", [
    ("https://www.youtube.com/watch?v=abc", True),
    ("https://youtu.be/abc", True),
    ("https://player.vimeo.com/video/123", True),
    ("https://m.tiktok.com/@x/video/1", True),
    ("https://x.com/u/status/1", True),
    ("https://evil.com/video.mp4", False),
    ("http://169.254.169.254/latest/", False),
    ("https://notyoutube.com.evil.com/x", False),   # no se cuela por substring
    ("", False),
])
def test_host_allowed(url, ok):
    assert media.host_allowed(url) is ok


def test_format_selector_progressive_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: False)
    fmt = media._format_selector(1080)
    assert "acodec!=none" in fmt and "vcodec!=none" in fmt   # un solo archivo, sin mux
    assert "+ba" not in fmt


def test_format_selector_muxes_with_ffmpeg(monkeypatch):
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    fmt = media._format_selector(720)
    assert "height<=720" in fmt and "+ba" in fmt            # muxea video+audio


def _capture_ydl_opts(monkeypatch, tmp_path, *, ffmpeg, **kw) -> dict:
    """Corre download_video con un yt_dlp falso (no baja nada) y devuelve las opts que armó.
    tmp_path queda vacío → download_video lanza RuntimeError al no hallar archivo (esperado)."""
    captured: dict = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download):
            return {}

    import sys
    import types
    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = _FakeYDL
    monkeypatch.setattr(media, "ffmpeg_available", lambda: ffmpeg)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    try:
        media.download_video("https://youtu.be/x", tmpdir=str(tmp_path), max_bytes=1, **kw)
    except Exception:  # noqa: BLE001 — sin archivo real, RuntimeError; solo queremos las opts
        pass
    return captured


def test_quality_capped_by_server_max(monkeypatch, tmp_path):
    opts = _capture_ydl_opts(monkeypatch, tmp_path, ffmpeg=True, max_height=480, quality="1080")
    assert "height<=480" in opts["format"]   # capado al tope del server, no 1080


def test_audio_only_uses_mp3_postprocessor(monkeypatch, tmp_path):
    opts = _capture_ydl_opts(monkeypatch, tmp_path, ffmpeg=True, audio_only=True)
    assert opts["format"] == "bestaudio/best"
    assert opts["postprocessors"][0]["preferredcodec"] == "mp3"


def test_audio_only_no_ffmpeg_skips_postprocessor(monkeypatch, tmp_path):
    opts = _capture_ydl_opts(monkeypatch, tmp_path, ffmpeg=False, audio_only=True)
    assert "postprocessors" not in opts   # sin ffmpeg, baja el audio nativo


def _as_role(monkeypatch, role):
    monkeypatch.setattr(auth, "identity_from_request",
                        lambda req: (role, "jti-test" if role else None))
    monkeypatch.setattr(auth, "role_from_request", lambda req: role)


def test_video_endpoint_humano_forbidden(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={"url": "https://www.youtube.com/watch?v=x"})
    assert r.status_code == 403
    assert "no habilita" in r.json()["detail"]


def test_video_endpoint_unauthenticated(client_factory, monkeypatch):
    _as_role(monkeypatch, None)
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={"url": "https://www.youtube.com/watch?v=x"})
    assert r.status_code == 401


def test_video_endpoint_disabled_mode(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="direct")   # solo link directo → proxy/video off
    r = c.post("/api/download/video", json={"url": "https://www.youtube.com/watch?v=x"})
    assert r.status_code == 403


def test_video_endpoint_bad_host(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={"url": "https://evil.com/x.mp4"})
    assert r.status_code == 400
    assert "plataformas conocidas" in r.json()["detail"]


def test_video_endpoint_ssrf_host(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={"url": "http://169.254.169.254/latest/"})
    assert r.status_code == 400   # no está en la allowlist de plataformas


@pytest.mark.parametrize("msg,auth_req", [
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies", True),
    ("ERROR: [youtube] x: Sign in to confirm you’re not a bot.", True),   # apóstrofe tipográfico
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", True),
    ("HTTP Error 404: Not Found", False),
    ("Requested format is not available", False),
])
def test_is_auth_required(msg, auth_req):
    assert media.is_auth_required(msg) is auth_req


def test_video_endpoint_botcheck_returns_422(client_factory, monkeypatch):
    """El anti-bot de YouTube → 422 needs_cookies (no 502, que el gateway se come)."""
    _as_role(monkeypatch, "dios")

    def _boom(*a, **k):
        raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies")
    monkeypatch.setattr(media, "ytdlp_available", lambda: True)
    monkeypatch.setattr(media, "download_video", _boom)
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={"url": "https://www.youtube.com/watch?v=x"})
    assert r.status_code == 422
    d = r.json()
    assert d["needs_cookies"] is True and d["had_cookies"] is False


def test_video_endpoint_passes_ui_cookies_and_proxy(client_factory, monkeypatch):
    """Las cookies y el proxy del form (este job) llegan a yt-dlp: el cookiefile temporal
    contiene las cookies de la UI y el proxy se pasa tal cual."""
    _as_role(monkeypatch, "dios")
    seen: dict = {}

    def _capture(url, *, tmpdir, cookiefile, proxy, **k):
        seen["proxy"] = proxy
        if cookiefile:
            with open(cookiefile, encoding="utf-8") as fh:
                seen["cookie_text"] = fh.read()
        raise RuntimeError("stop")   # no seguir a streaming; "stop" no es bot-check → 400
    monkeypatch.setattr(media, "ytdlp_available", lambda: True)
    monkeypatch.setattr(media, "download_video", _capture)
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={
        "url": "https://www.youtube.com/watch?v=x",
        "cookies": "SID=abc; HSID=def", "proxy": "http://u:p@host:8080"})
    assert r.status_code == 400
    assert seen["proxy"] == "http://u:p@host:8080"
    assert "SID" in seen["cookie_text"] and "abc" in seen["cookie_text"]


def test_me_reports_video_capability(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    d = c.get("/api/me").json()
    assert "video_download" in d and "ffmpeg" in d
    assert d["video_download"] is True   # dios + yt-dlp instalado + modo both


@pytest.mark.parametrize("url,prov", [
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://vimeo.com/123", "vimeo"),
    ("https://www.tiktok.com/@x/video/1", "tiktok"),
    ("https://example.com/page", None),
])
def test_video_provider(url, prov):
    assert media.video_provider(url) == prov


def test_pipeline_injects_seed_video():
    """Pegar un link de YouTube directo (no embebido) debe ofrecer el video."""
    from app.fetchers.base import FetchResult
    from app.models import PrivacyMode, Rol, Sobre
    from app.pipeline import PipelineDeps, _harvest_files

    deps = PipelineDeps(fetch=None, extract=None,
                        anonymize_opaco=lambda t: (t, 0), file_download_mode="both")
    s = Sobre(job_id="v", source_url="https://www.youtube.com/watch?v=rQ22TEyGp4s",
              privacy_mode=PrivacyMode.DIRECTO, rol=Rol.DIOS)
    page = FetchResult(url="https://www.youtube.com/watch?v=rQ22TEyGp4s", status_code=200,
                       content=b"<html><body>yt</body></html>", text="<html><body>yt</body></html>",
                       content_type="text/html", tier=0)
    _harvest_files(deps, s, [page])
    embeds = s.meta["files"]["embed"]
    assert embeds and embeds[0]["provider"] == "youtube"
    assert embeds[0]["dl_video"] is True
    assert embeds[0]["url"].endswith("rQ22TEyGp4s")


def test_me_video_false_for_humano(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    d = c.get("/api/me").json()
    assert d["video_download"] is False   # humano no habilita capture
