"""Tests del parser de cookies multi-formato (header / cookies.txt / JSON)."""
from __future__ import annotations

from app.security.cookies import parse_cookies


def test_header_format():
    assert parse_cookies("sid=abc; loc=ar; x=1") == {"sid": "abc", "loc": "ar", "x": "1"}


def test_netscape_cookies_txt():
    raw = (
        "# Netscape HTTP Cookie File\n"
        ".mercadolibre.com.ar\tTRUE\t/\tTRUE\t1799999999\tsessid\tXYZ\n"
        "#HttpOnly_.mercadolibre.com.ar\tTRUE\t/\tTRUE\t0\tss0\tHHH\n"
    )
    jar = parse_cookies(raw)
    assert jar == {"sessid": "XYZ", "ss0": "HHH"}


def test_json_array():
    raw = '[{"name":"sid","value":"abc","domain":".x.com"},{"name":"t","value":"9"}]'
    assert parse_cookies(raw) == {"sid": "abc", "t": "9"}


def test_json_dict():
    assert parse_cookies('{"sid":"abc","n":2}') == {"sid": "abc", "n": "2"}


def test_empty():
    assert parse_cookies("") == {}
    assert parse_cookies(None) == {}


def test_job_overrides_reads_browser_cookies(monkeypatch):
    import app.security.browser_cookies as bc
    monkeypatch.setattr(bc, "read_cookies", lambda host, browser: {"sess": "abc"})
    from app.models import PrivacyMode, Rol, Sobre
    from app.pipeline import _job_overrides
    s = Sobre(job_id="x", source_url="https://tienda.com/p", privacy_mode=PrivacyMode.DIRECTO, rol=Rol.DIOS)
    s.meta["cookies_browser"] = "chrome"
    s.meta["cookies"] = "extra=1"   # lo pegado pisa/se suma
    kw = _job_overrides(s)
    assert kw["cookies"]["sess"] == "abc"
    assert kw["cookies"]["extra"] == "1"
