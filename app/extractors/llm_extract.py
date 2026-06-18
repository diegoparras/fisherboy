"""Extracción estructurada por LLM con validación. Ver Capa 5.

Toma markdown + un JSON Schema y le pide a un LLM (proveedor OpenAI-compatible por
API) que extraiga los datos que cumplen ese schema. Devuelve JSON validado.

PRIVACIDAD: este módulo es agnóstico de privacidad a propósito. Recibe el texto YA
tratado (pseudonimizado en reversible/opaco, o crudo en directo) y devuelve la
extracción tal cual. El pipeline decide el tratamiento según privacy_mode y, en
reversible, re-hidrata la salida. Así la regla "el modo solo importa en la rama LLM"
(ADR-002) queda en un solo lugar y este extractor no ve PII que no deba.
"""
from __future__ import annotations

import json
import re
from typing import Callable

# Máximo de markdown que mandamos al LLM (corta costos y context overflow).
_MAX_CHARS = 60_000


class LLMError(Exception):
    """Falla del proveedor LLM o de la validación de la salida."""


class LLMClient:
    """Cliente mínimo OpenAI-compatible (chat completions). Inyectable en test."""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout_s: float = 120.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def complete(self, system: str, user: str) -> str:
        if not self.base_url or not self.api_key:
            raise LLMError("LLM no configurado (LLM_API_BASE_URL / LLM_API_KEY vacíos).")
        import httpx

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            resp = httpx.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"No se pudo contactar al LLM: {type(e).__name__}.") from e
        if not resp.is_success:
            raise LLMError(f"El LLM respondió {resp.status_code}.")
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as e:
            raise LLMError("Respuesta inválida del LLM.") from e


def _parse_json(raw: str) -> dict:
    """Extrae el objeto JSON de la respuesta, tolerando ```json ... ``` y ruido."""
    s = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", s, re.S)
    if fence:
        s = fence.group(1).strip()
    try:
        obj = json.loads(s)
    except ValueError:
        # Último intento: el primer {...} balanceado.
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise LLMError("El LLM no devolvió JSON parseable.")
        try:
            obj = json.loads(m.group(0))
        except ValueError as e:
            raise LLMError("El LLM no devolvió JSON parseable.") from e
    if not isinstance(obj, dict):
        raise LLMError("La extracción no es un objeto JSON.")
    return obj


def _validate(obj: dict, schema: dict) -> None:
    """Valida contra el JSON Schema si jsonschema está instalado (best-effort si no)."""
    import importlib.util

    if importlib.util.find_spec("jsonschema") is None:
        return
    import jsonschema

    try:
        jsonschema.validate(obj, schema)
    except jsonschema.ValidationError as e:
        raise LLMError(f"La extracción no cumple el schema: {e.message}.") from e


def extract_structured(
    markdown: str,
    schema: dict,
    *,
    complete: Callable[[str, str], str],
    instructions: str | None = None,
) -> dict:
    """Extrae datos de `markdown` que cumplan `schema`. `complete(system, user)->str`.

    `complete` se inyecta (LLMClient.complete o un fake en test). Devuelve el dict
    validado o levanta LLMError.
    """
    if not schema:
        raise LLMError("Falta extract_schema para la extracción estructurada.")
    system = (
        "Sos un extractor de datos. Devolvés SOLO un objeto JSON válido que cumpla "
        "EXACTAMENTE este JSON Schema, sin texto adicional. No inventes datos: si un "
        "campo no está en el contenido, omitilo o usá null.\n\nSchema:\n"
        + json.dumps(schema, ensure_ascii=False)
    )
    if instructions:
        system += f"\n\nInstrucciones extra: {instructions}"
    user = "Contenido del que extraer:\n\n" + markdown[:_MAX_CHARS]

    raw = complete(system, user)
    obj = _parse_json(raw)
    _validate(obj, schema)
    return obj
