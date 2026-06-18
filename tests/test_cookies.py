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
