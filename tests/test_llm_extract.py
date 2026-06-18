"""Tests de la extracción estructurada por LLM (cliente inyectado)."""
from __future__ import annotations

import pytest

from app.extractors.llm_extract import LLMError, extract_structured

_SCHEMA = {
    "type": "object",
    "properties": {"nombre": {"type": "string"}, "monto": {"type": "integer"}},
    "required": ["nombre"],
}


def test_extract_plain_json():
    out = extract_structured(
        "ACME facturó 100", _SCHEMA, complete=lambda s, u: '{"nombre": "ACME", "monto": 100}'
    )
    assert out == {"nombre": "ACME", "monto": 100}


def test_extract_json_in_code_fence():
    out = extract_structured(
        "x", _SCHEMA, complete=lambda s, u: '```json\n{"nombre": "X"}\n```'
    )
    assert out["nombre"] == "X"


def test_extract_unparseable_raises():
    with pytest.raises(LLMError):
        extract_structured("x", _SCHEMA, complete=lambda s, u: "no soy json")


def test_extract_schema_violation_raises():
    # 'monto' debe ser entero; el LLM manda string → falla la validación.
    with pytest.raises(LLMError):
        extract_structured(
            "x", _SCHEMA, complete=lambda s, u: '{"nombre": "X", "monto": "mil"}'
        )


def test_extract_requires_schema():
    with pytest.raises(LLMError):
        extract_structured("x", {}, complete=lambda s, u: "{}")
