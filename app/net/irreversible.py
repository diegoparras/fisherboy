"""Qué no se deshace: enviar, comprar, pagar, borrar, publicar.

Lo decide código, antes de tocar, mirando el elemento: no lo decide la IA. La misma lista vive en
SecretarIA (`packages/core/src/navegador/irreversible.ts`): si cambia una, cambia la otra.
"""
from __future__ import annotations

import re
import unicodedata

PALABRAS = (
    "enviar", "envia", "mandar", "manda", "comprar", "compra", "pagar", "paga", "confirmar", "confirma",
    "aceptar", "acepta", "transferir", "transferi", "borrar", "borra", "eliminar", "elimina",
    "publicar", "publica", "suscribir", "suscribi", "contratar", "contrata", "finalizar", "finaliza",
    "submit", "send", "buy", "pay", "confirm", "delete", "place order",
)


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "")).lower()
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


_PATRON = re.compile(r"\b(" + "|".join(re.escape(p) for p in PALABRAS) + r")\b")


def irreversible(el: dict, accion: str) -> bool:
    """`el` = {tag, type, texto, ariaLabel, enFormulario}; `accion` = 'click' | 'enter'."""
    if accion == "enter":
        return bool(el.get("enFormulario"))
    if str(el.get("type") or "").lower() == "submit":
        return True
    texto = _plano(f"{el.get('texto') or ''} {el.get('ariaLabel') or ''}")
    return bool(_PATRON.search(texto))
