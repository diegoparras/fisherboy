"""Cliente hacia Anonimal + armado de la salida opaca. Ver ADR-002, ADR-003, ADR-005.

Anonimal hoy expone `POST /anonymize {text}` → `{detected_spans, redacted_text,
summary}` en modo typed, sin auth (contrato real confirmado en Fase 0 leyendo
markitdown-web/anonimal/app.py). "Detecta, no decide": devuelve spans tipados con
`placeholder`, y el llamador arma el reemplazo.

Modo OPACO (v1): se reemplaza cada entidad por un marcador tipado y ESTABLE dentro
del documento («PERSONA_1», «CUIT_2»…). Mismo valor → mismo marcador. NO se guarda
el mapeo (eso lo diferencia de reversible, v2). Se combinan los spans de OPF con la
pasada determinística de detectors.py (ADR-005 punto 4).

FALLA CERRADO (ADR-004 punto 1): si Anonimal falla, da timeout o responde algo
inválido, se lanza AnonimalError y el llamador NUNCA devuelve el texto crudo.

NOTA (2026-06): Anonimal YA tiene auth de servicio (require_auth: `X-Anonimal-Token`
o Bearer) — este cliente la usa cuando hay `ANONIMAL_TOKEN`. Los endpoints reversibles
dedicados (`/privacy/process` y `/privacy/revert`) siguen sin existir en Anonimal; el
reversible se arma client-side (`build_reversible`) desde los spans de `/anonymize`.
"""
from __future__ import annotations

import httpx

from . import detectors

# placeholder de OPF → nombre de tipo legible (espejo de markitdown-web/app/anonimal.py).
_TYPE_FROM_PLACEHOLDER = {
    "<PRIVATE_PERSON>": "PERSONA",
    "<PRIVATE_ADDRESS>": "DOMICILIO",
    "<PRIVATE_EMAIL>": "EMAIL",
    "<PRIVATE_PHONE>": "TEL",
    "<ACCOUNT_NUMBER>": "ID",
    "<PRIVATE_DATE>": "FECHA",
    "<PRIVATE_URL>": "URL",
    "<SECRET>": "SECRETO",
    "<REDACTED>": "DATO",
}


class AnonimalError(Exception):
    """Falla de anonimización. El llamador NO debe devolver el texto original."""


def _locate(text: str, span: dict) -> tuple[int, int] | None:
    """Resuelve (start, end) de un span de OPF; verifica offsets y cae a buscar
    el fragmento si no cuadran (mismo criterio que Escriba)."""
    n = len(text)
    frag = span.get("text")
    try:
        st, en = int(span["start"]), int(span["end"])
    except (KeyError, TypeError, ValueError):
        st = en = -1
    if 0 <= st < en <= n and (frag is None or text[st:en] == frag):
        return st, en
    if frag:
        i = text.find(frag)
        if i >= 0:
            return i, i + len(frag)
    return None


def _collect_opf(text: str, opf_spans: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in opf_spans or []:
        rng = _locate(text, s)
        if rng:
            tipo = _TYPE_FROM_PLACEHOLDER.get(s.get("placeholder"), "DATO")
            out.append({"start": rng[0], "end": rng[1], "tipo": tipo})
    return out


def _merge(spans: list[dict]) -> list[dict]:
    """Ordena y fusiona spans solapados, conservando el primer tipo."""
    spans = sorted(spans, key=lambda s: (s["start"], s["end"]))
    merged: list[dict] = []
    for s in spans:
        if merged and s["start"] < merged[-1]["end"]:
            if s["end"] > merged[-1]["end"]:
                merged[-1]["end"] = s["end"]
        else:
            merged.append(dict(s))
    return merged


def _apply_markers(text: str, opf_spans: list[dict]) -> tuple[str, dict[str, str]]:
    """Core compartido: combina spans OPF + determinísticos y reemplaza cada entidad
    por un marcador tipado ESTABLE («TIPO_N»). Devuelve (texto, token→original).

    Mismo valor → mismo marcador, así el LLM razona relacional sin ver PII (ADR-002).
    El opaco descarta el mapa; el reversible lo guarda cifrado para rehidratar.
    """
    spans = _collect_opf(text, opf_spans) + detectors.run(text)
    merged = _merge(spans)

    counters: dict[str, int] = {}
    token_for: dict[str, str] = {}   # original → token
    token_to_original: dict[str, str] = {}

    parts: list[str] = []
    cur = 0
    for s in merged:
        st, en = s["start"], s["end"]
        if en <= cur:
            continue
        if st < cur:
            st = cur
        parts.append(text[cur:st])
        frag = text[st:en]
        if frag not in token_for:
            tipo = s["tipo"]
            counters[tipo] = counters.get(tipo, 0) + 1
            tok = f"«{tipo}_{counters[tipo]}»"
            token_for[frag] = tok
            token_to_original[tok] = frag
        parts.append(token_for[frag])
        cur = en
    parts.append(text[cur:])
    return "".join(parts), token_to_original


def build_opaco(text: str, opf_spans: list[dict]) -> tuple[str, int]:
    """Texto opaco con marcadores estables, sin guardar el mapeo. Pura y testeable."""
    out, mapping = _apply_markers(text, opf_spans)
    return out, len(mapping)


def build_reversible(text: str, opf_spans: list[dict]) -> tuple[str, dict[str, str], int]:
    """Como opaco, pero DEVUELVE el mapa token→original para rehidratar (reversible)."""
    out, mapping = _apply_markers(text, opf_spans)
    return out, mapping, len(mapping)


class AnonimalClient:
    """Llama a Anonimal y arma la salida. Falla cerrado ante cualquier problema."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 180.0,
        service_token: str = "",
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout_s = timeout_s
        self.service_token = service_token

    def _detect(self, text: str) -> list[dict]:
        if not self.base_url:
            raise AnonimalError("Anonimización no configurada (ANONIMAL_URL vacío).")
        headers = {}
        if self.service_token:  # token de servicio de Anonimal (require_auth: X-Anonimal-Token / Bearer)
            headers["X-Anonimal-Token"] = self.service_token
        try:
            resp = httpx.post(
                f"{self.base_url}/anonymize",
                json={"text": text},
                timeout=httpx.Timeout(self.timeout_s, connect=5.0),
                headers=headers,
            )
        except httpx.HTTPError as e:
            raise AnonimalError("No se pudo contactar al anonimizador.") from e
        if resp.status_code == 503:
            raise AnonimalError("El anonimizador se está iniciando.")
        if resp.status_code == 413:
            raise AnonimalError("El texto es demasiado largo para anonimizar.")
        if not resp.is_success:
            raise AnonimalError(f"El anonimizador respondió {resp.status_code}.")
        try:
            data = resp.json()
        except ValueError as e:
            raise AnonimalError("Respuesta inválida del anonimizador.") from e
        return data.get("detected_spans") or []

    def detect_spans(self, text: str) -> list[dict]:
        """Detección cruda de Anonimal (público; lo usan opaco y reversible)."""
        if not text or not text.strip():
            return []
        return self._detect(text)

    def process_opaco(self, text: str) -> tuple[str, int]:
        """Detecta con Anonimal y arma la salida opaca. Falla cerrado."""
        if not text or not text.strip():
            return text, 0
        opf_spans = self._detect(text)
        return build_opaco(text, opf_spans)
