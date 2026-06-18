"""Tests del filtro de telemetría y el ranking 'cuán dato es' del capturador."""
from __future__ import annotations

from app.fetchers.capture import _data_score, _is_telemetry, _reg_domain


def test_telemetry_detection():
    assert _is_telemetry("https://api.mercadolibre.com/melidata/tracks")
    assert _is_telemetry("https://o11y-proxy-otel-frontend.meli.com/v1/metrics")
    assert _is_telemetry("https://www.google-analytics.com/collect")
    assert _is_telemetry("https://x.com/v1/traces")
    # un endpoint de datos real NO es telemetría
    assert not _is_telemetry("https://www.mercadolibre.com.ar/adn/api?page=home")
    assert not _is_telemetry("https://api.tienda.com/products?q=notebook")


def test_reg_domain():
    assert _reg_domain("api.mercadolibre.com") == "mercadolibre.com"
    assert _reg_domain("www.tienda.com.ar") == "com.ar"  # aproximación por 2 labels


def test_data_score_ranks_real_data_first():
    target = "tienda.com"
    data = {"url": "https://api.tienda.com/products", "bytes": 4000,
            "json": {"results": [{"id": i} for i in range(50)]}}
    noise = {"url": "https://tienda.com/ping", "bytes": 80, "json": {"ok": True}}
    third = {"url": "https://cdn.otra.com/config.json", "bytes": 3000,
             "json": {"a": 1, "b": 2}}
    assert _data_score(data, target) > _data_score(third, target)
    assert _data_score(data, target) > _data_score(noise, target)


def test_data_score_zero_without_json():
    assert _data_score({"url": "https://x.com/a", "bytes": 999, "text": "<html>"}, "x.com") == 0
