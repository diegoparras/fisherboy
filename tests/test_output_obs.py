"""Tests de formatos de salida, conversión (fallback) y métricas."""
from __future__ import annotations

from app.extractors.convert import html_to_markdown_rich
from app.obs.metrics import get_metrics
from app.output.formats import bundle_pages, title_from_markdown, to_llms_txt
from app.store.postgres import PostgresStore


def test_to_llms_txt_header():
    out = to_llms_txt("Cuerpo del doc.", title="Mi Título", source_url="https://x.com/a")
    assert out.startswith("# Mi Título")
    assert "Fuente: https://x.com/a" in out
    assert "Cuerpo del doc." in out


def test_bundle_pages():
    out = bundle_pages([("https://x.com/1", "uno"), ("https://x.com/2", "dos")])
    assert "## https://x.com/1" in out and "## https://x.com/2" in out
    assert "---" in out


def test_title_from_markdown():
    assert title_from_markdown("# Hola\n\ntexto") == "Hola"
    assert title_from_markdown("sin encabezado") is None


def test_convert_fallback_to_trafilatura():
    html = "<html><body><article><h1>T</h1><p>" + "contenido real largo. " * 20 + "</p></article></body></html>"
    md, engine = html_to_markdown_rich(html, url="https://x.com/a")
    assert md.strip()
    assert engine in ("crawl4ai", "trafilatura")


def test_metrics_noop_safe():
    m = get_metrics()
    m.inc_job("ok")
    m.inc_tier(1)
    m.inc_captcha("cloudflare")
    body, ctype = m.render()
    assert isinstance(body, bytes)
    assert "text" in ctype


def test_postgres_unavailable_without_dsn():
    store = PostgresStore("")
    assert store.available() is False
    # save_sobre debe degradar a no-op sin romper.
    from app.models import PrivacyMode, Rol, Sobre
    s = Sobre(job_id="x", source_url="https://x.com/a", privacy_mode=PrivacyMode.OPACO, rol=Rol.DIOS)
    assert store.save_sobre(s) is False


class _FakeCur:
    def __init__(self):
        self.params = None
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def execute(self, sql, params):
        self.params = params


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def cursor(self):
        return self._cur
    def commit(self):
        pass


def test_postgres_no_persiste_secretos_por_job(monkeypatch):
    """El store durable NUNCA debe guardar proxy-creds/captcha-key/cookies (auditoría 2026-06)."""
    from app.models import PrivacyMode, Rol, Sobre
    store = PostgresStore("postgres://x")
    cur = _FakeCur()
    monkeypatch.setattr(store, "available", lambda: True)
    store._checked = True  # saltear ensure_schema
    monkeypatch.setattr(store, "_connect", lambda: _FakeConn(cur))

    s = Sobre(job_id="j1", source_url="https://x.com/a", privacy_mode=PrivacyMode.OPACO, rol=Rol.DIOS)
    s.meta.update({
        "proxy": "http://user:s3cr3t@1.2.3.4:8080",
        "captcha_api_key": "CAPTCHA-SECRET-KEY",
        "cookies": {"sid": "abc123"},
        "max_tier": 3,          # control NO sensible: se conserva
    })
    assert store.save_sobre(s) is True
    meta_json = store and cur.params[-1]   # último param = json.dumps(safe_meta)
    assert "s3cr3t" not in meta_json
    assert "CAPTCHA-SECRET-KEY" not in meta_json
    assert "captcha_api_key" not in meta_json
    assert "cookies" not in meta_json
    assert "max_tier" in meta_json         # la metadata de control no-secreta se mantiene
