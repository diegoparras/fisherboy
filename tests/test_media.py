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
    assert opts["format"].startswith("bestaudio")
    assert opts["format"].endswith("/b")   # cae a un progresivo si YouTube esconde el DASH
    assert opts["postprocessors"][0]["preferredcodec"] == "mp3"


def test_pot_url_reaches_extractor_args(monkeypatch, tmp_path):
    """El PO Token provider viaja en extractor_args: sin esto el plugin bgutil no sabe a
    qué sidecar pedirle el token y YouTube no entrega los formatos."""
    opts = _capture_ydl_opts(monkeypatch, tmp_path, ffmpeg=True,
                             pot_url="http://fisherboy-bgutil:4416")
    assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == \
        ["http://fisherboy-bgutil:4416"]


def test_no_pot_url_leaves_extractor_args_clean(monkeypatch, tmp_path):
    opts = _capture_ydl_opts(monkeypatch, tmp_path, ffmpeg=True)
    assert "extractor_args" not in opts


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


@pytest.mark.parametrize("msg,no_fmt", [
    ("ERROR: [youtube] x: Requested format is not available. Use --list-formats", True),
    ("ERROR: [youtube] x: No video formats found!", True),
    ("Only images are available for download. Use --list-formats", True),
    ("HTTP Error 404: Not Found", False),
    ("Sign in to confirm you're not a bot", False),   # eso es auth, no falta de formatos
])
def test_is_no_formats(msg, no_fmt):
    assert media.is_no_formats(msg) is no_fmt


# ---- Cascada de player_client: el arreglo de "Requested format is not available" (SABR).
NO_FMT = "ERROR: [youtube] x: Requested format is not available. Use --list-formats"


def _run_with_script(monkeypatch, tmp_path, script, calls=None, **kw):
    """Corre download_video con un yt_dlp falso guionado: `script[i]` es el mensaje de error
    del intento i (None = ese intento anda y deja el archivo). `calls` (lista del caller) se
    llena con las opts de cada intento — así se puede inspeccionar incluso si download_video
    termina lanzando."""
    calls = [] if calls is None else calls

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def extract_info(self, url, download=True):
            calls.append(self.opts)
            err = script[len(calls) - 1]
            if err:
                raise RuntimeError(err)
            (tmp_path / "clip.mp3").write_bytes(b"x" * 512)
            return {}

    import sys
    import types
    fake = types.ModuleType("yt_dlp")
    fake.YoutubeDL = _FakeYDL
    monkeypatch.setattr(media, "ffmpeg_available", lambda: True)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake)
    result = media.download_video("https://youtu.be/x", tmpdir=str(tmp_path),
                                  max_bytes=10**8, **kw)
    return result, calls


def test_retries_with_next_player_client_when_no_formats(monkeypatch, tmp_path):
    """YouTube esconde los formatos con el cliente por defecto (SABR) → reintenta con el
    plan siguiente (tv/web_embedded) en vez de morir con el error críptico de yt-dlp."""
    (_path, name), calls = _run_with_script(
        monkeypatch, tmp_path, [NO_FMT, None],
        audio_only=True, pot_url="http://fisherboy-bgutil:4416")
    assert name == "clip.mp3"
    assert len(calls) == 2
    assert calls[1]["extractor_args"]["youtube"]["player_client"] == ["tv", "web_embedded"]
    # el PO token sigue viajando en el reintento
    assert calls[1]["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] \
        == ["http://fisherboy-bgutil:4416"]


def test_all_plans_exhausted_gives_actionable_error(monkeypatch, tmp_path):
    """Si ningún cliente entrega formatos, el mensaje tiene que decir qué hacer — el de
    yt-dlp manda a leer --list-formats, que al usuario de la UI no le sirve de nada."""
    calls: list[dict] = []
    with pytest.raises(RuntimeError) as ei:
        _run_with_script(monkeypatch, tmp_path, [NO_FMT] * 3, calls=calls,
                         audio_only=True, pot_url="")
    assert len(calls) == len(media._PLAYER_PLANS)   # probó todos los planes antes de rendirse
    msg = str(ei.value)
    assert "SABR" in msg and "YT_POT_URL" in msg and "cookies" in msg


# ---- El sidecar y el plugin se despliegan por separado y pueden desincronizarse: en vez de
# ---- fijar versiones a mano (que se pudren igual), se detecta y se dice.
def _fake_ping(monkeypatch, *, version=None, boom=False):
    """Simula el GET /ping del sidecar bgutil."""
    class _Resp:
        def raise_for_status(self):
            pass
        def json(self):
            return {"server_uptime": 12.3, "version": version}

    def _get(url, timeout=None):
        assert url.endswith("/ping"), url
        if boom:
            raise ConnectionError("Connection refused")
        return _Resp()

    import httpx
    monkeypatch.setattr(httpx, "get", _get)


def test_pot_probe_detects_version_mismatch(monkeypatch):
    """Sidecar en 2.x + plugin en 1.x = no se entienden. Hay que verlo, no adivinarlo."""
    _fake_ping(monkeypatch, version="2.0.0")
    monkeypatch.setattr(media, "pot_plugin_version", lambda: "1.3.1")
    p = media.pot_probe("http://fisherboy-bgutil:4416")
    assert p["ok"] is True and p["mismatch"] is True
    assert p["server"] == "2.0.0" and p["plugin"] == "1.3.1"


def test_pot_probe_same_major_is_fine(monkeypatch):
    """Dentro de la misma major se entienden: 1.4.0 contra 1.3.1 NO es un problema."""
    _fake_ping(monkeypatch, version="1.4.0")
    monkeypatch.setattr(media, "pot_plugin_version", lambda: "1.3.1")
    p = media.pot_probe("http://fisherboy-bgutil:4416")
    assert p["ok"] is True and p["mismatch"] is False


def test_pot_probe_reports_dead_sidecar(monkeypatch):
    _fake_ping(monkeypatch, boom=True)
    p = media.pot_probe("http://fisherboy-bgutil:4416")
    assert p["ok"] is False and "Connection refused" in p["error"]


def test_hint_mismatch_tells_you_to_redeploy(monkeypatch):
    monkeypatch.setattr(media, "pot_probe", lambda url, **k: {
        "url": url, "plugin": "1.3.1", "server": "2.0.0", "ok": True, "mismatch": True, "error": ""})
    msg = media.no_formats_hint("http://x:4416")
    assert "2.0.0" in msg and "1.3.1" in msg and "redeploy" in msg.lower()


def test_hint_healthy_provider_points_at_the_ip(monkeypatch):
    """Si el proveedor está sano, el que bloquea es YouTube por IP: mandá al usuario a las
    cookies/proxy, no a seguir peleando con bgutil."""
    monkeypatch.setattr(media, "pot_probe", lambda url, **k: {
        "url": url, "plugin": "1.3.1", "server": "1.3.1", "ok": True, "mismatch": False, "error": ""})
    msg = media.no_formats_hint("http://x:4416")
    assert "IP" in msg and "cookies" in msg


def test_hint_dead_provider_says_so(monkeypatch):
    monkeypatch.setattr(media, "pot_probe", lambda url, **k: {
        "url": url, "plugin": "1.3.1", "server": "", "ok": False, "mismatch": False,
        "error": "ConnectionError: refused"})
    msg = media.no_formats_hint("http://fisherboy-bgutil:4416")
    assert "no responde" in msg and "fisherboy-bgutil" in msg


def test_pot_health_endpoint(client_factory, monkeypatch):
    _as_role(monkeypatch, "dios")
    monkeypatch.setattr(media, "pot_probe", lambda url, **k: {
        "url": url, "plugin": "1.3.1", "server": "1.3.1", "ok": True, "mismatch": False, "error": ""})
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    d = c.get("/api/download/pot/health").json()
    assert d["ok"] is True and d["mismatch"] is False


def test_pot_health_endpoint_requires_capture(client_factory, monkeypatch):
    _as_role(monkeypatch, "humano")
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    assert c.get("/api/download/pot/health").status_code == 403


def test_auth_error_does_not_retry(monkeypatch, tmp_path):
    """Un pedido de sesión no se arregla cambiando de player_client: cortá y pedí cookies.
    El guion deja 2 planes exitosos detrás; si reintentara, no lanzaría y habría 2+ intentos."""
    calls: list[dict] = []
    with pytest.raises(RuntimeError) as ei:
        _run_with_script(monkeypatch, tmp_path,
                         ["ERROR: Sign in to confirm you're not a bot", None, None], calls=calls)
    assert len(calls) == 1                          # cortó en el primero
    assert media.is_auth_required(str(ei.value))    # y el motivo llega intacto al endpoint


def _poll_dl(c, token, tries=150):
    """La descarga corre en background; segui el progreso hasta done/error."""
    import time as _t
    p = {}
    for _ in range(tries):
        p = c.get(f"/api/download/video/progress/{token}").json()
        if p.get("status") in ("error", "done"):
            return p
        _t.sleep(0.02)
    return p


def test_video_endpoint_botcheck_returns_422(client_factory, monkeypatch):
    """El anti-bot de YouTube → status 'error' con needs_cookies en el progreso."""
    _as_role(monkeypatch, "dios")

    def _boom(*a, **k):
        raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies")
    monkeypatch.setattr(media, "ytdlp_available", lambda: True)
    monkeypatch.setattr(media, "download_video", _boom)
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={"url": "https://www.youtube.com/watch?v=x"})
    assert r.status_code == 200
    p = _poll_dl(c, r.json()["token"])
    assert p["status"] == "error"
    assert p.get("needs_cookies") is True and p.get("had_cookies") is False


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
        raise RuntimeError("stop")   # corta antes del archivo real
    monkeypatch.setattr(media, "ytdlp_available", lambda: True)
    monkeypatch.setattr(media, "download_video", _capture)
    c = client_factory(FILE_DOWNLOAD_MODE="both")
    r = c.post("/api/download/video", json={
        "url": "https://www.youtube.com/watch?v=x",
        "cookies": "SID=abc; HSID=def", "proxy": "http://u:p@host:8080"})
    assert r.status_code == 200
    _poll_dl(c, r.json()["token"])
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
