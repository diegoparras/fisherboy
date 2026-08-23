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


# ---------------------------------------------------------------------------
# Puente login interactivo → yt-dlp (ADR-013): storage_state a cookies.txt
# ---------------------------------------------------------------------------
def test_storage_state_a_netscape():
    """Lo que guarda el login en pantalla tiene que servirle a yt-dlp tal cual."""
    from app.security.cookies import storage_state_to_netscape
    txt = storage_state_to_netscape({"cookies": [
        {"name": "SID", "value": "abc", "domain": ".youtube.com", "path": "/",
         "expires": 1893456000, "secure": True},
        {"name": "PREF", "value": "xyz", "domain": "www.youtube.com", "path": "/x",
         "expires": -1, "secure": False},
    ]})
    lineas = [l for l in txt.splitlines() if l and not l.startswith("#")]
    assert len(lineas) == 2
    d, sub, path, seguro, exp, nombre, valor = lineas[0].split("\t")
    assert d == ".youtube.com" and sub == "TRUE"      # dominio con punto = vale subdominios
    assert seguro == "TRUE" and nombre == "SID" and valor == "abc"
    # Sin punto → FALSE; cookie de sesión (expires -1) → 0 (no expira sola)
    d2, sub2, path2, seg2, exp2, _, _ = lineas[1].split("\t")
    assert sub2 == "FALSE" and seg2 == "FALSE" and exp2 == "0" and path2 == "/x"


def test_storage_state_vacio_o_roto():
    from app.security.cookies import storage_state_to_netscape
    assert storage_state_to_netscape(None) == ""
    assert storage_state_to_netscape({}) == ""
    assert storage_state_to_netscape({"cookies": []}) == ""
    assert storage_state_to_netscape({"cookies": "no soy lista"}) == ""
    # Cookies incompletas se saltean, no rompen
    assert storage_state_to_netscape({"cookies": [{"name": "x"}, {"domain": ".y.com"}]}) == ""
