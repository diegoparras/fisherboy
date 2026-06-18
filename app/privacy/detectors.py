"""Pasada determinística previa para PII de alto riesgo atrapable por regla.

ADR-005 punto 4: corre ADEMÁS del modelo de Anonimal, no en su lugar. El modelo
tiene recall limitado en identificadores estructurados sueltos (CUIT/CUIL/CBU);
estas reglas garantizan su enmascarado. Sesgo conservador: ante duda, marcar.

Cada detector devuelve spans {start, end, tipo} sobre el texto recibido tal cual.
"""
from __future__ import annotations

import re

# tipo → (regex, validador opcional). El validador recibe el match y decide si
# realmente es PII (evita falsos positivos tipo montos de dinero).
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")
# CUIT/CUIL: 11 dígitos, con o sin guiones. Prefijos válidos de persona/empresa.
_CUIT = re.compile(r"\b(20|23|24|27|30|33|34)[-\s]?\d{8}[-\s]?\d\b")
# Tarjeta: 13-19 dígitos, agrupados o no. Se valida con Luhn para no marcar
# cualquier número largo (ej. un id de transacción).
_CARD = re.compile(r"\b(?:\d[ \-]?){13,19}\b")
# Teléfono: conservador. Requiere prefijo internacional + o formato con separadores
# y al menos 8 dígitos, para no pisar montos ($1.200.000) ni años.
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}[\s\-]?)?(?:\(?\d{2,4}\)?[\s\-]){1,3}\d{3,4}(?![\w])"
)


def _luhn_ok(digits: str) -> bool:
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _phone_ok(frag: str) -> bool:
    digits = re.sub(r"\D", "", frag)
    # 8 a 15 dígitos: descarta años (4) y montos cortos; topa en E.164.
    return 8 <= len(digits) <= 15


def run(text: str) -> list[dict]:
    """Devuelve los spans determinísticos sobre `text`. Sin fusionar todavía."""
    spans: list[dict] = []

    for m in _EMAIL.finditer(text):
        spans.append({"start": m.start(), "end": m.end(), "tipo": "EMAIL"})
    for m in _IPV4.finditer(text):
        spans.append({"start": m.start(), "end": m.end(), "tipo": "IP"})
    for m in _CUIT.finditer(text):
        spans.append({"start": m.start(), "end": m.end(), "tipo": "CUIT"})
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if _luhn_ok(digits):
            spans.append({"start": m.start(), "end": m.end(), "tipo": "TARJETA"})
    for m in _PHONE.finditer(text):
        if _phone_ok(m.group()):
            spans.append({"start": m.start(), "end": m.end(), "tipo": "TEL"})

    return spans
