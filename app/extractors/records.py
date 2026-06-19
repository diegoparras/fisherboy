"""Aplanado genérico de registros de cualquier JSON capturado. Ver Capa 5.

Un API de listado (productos, resultados, items, rows…) devuelve JSON con un array de
objetos enterrado entre metadata/tracking. Esto, sin saber nada del sitio (ML es solo
guía), encuentra **el array de objetos más grande** del JSON y aplana cada objeto a un
registro compacto buscando campos comunes por nombre, en cualquier nivel:

- título  → title/name/text/label/titulo/nombre
- precio  → price/value/amount/precio (toma el número más representativo)
- link    → url/permalink/link/href
- id      → id/sku/code

Best-effort: si no encuentra un campo, lo omite. Devuelve (registros, ruta_del_array).
"""
from __future__ import annotations

_TITLE_KEYS = ("title", "name", "text", "label", "titulo", "título", "nombre", "headline")
_PRICE_KEYS = ("price", "value", "amount", "precio", "current_price", "monto")
_URL_KEYS = ("url", "permalink", "link", "href", "enlace")
_ID_KEYS = ("id", "sku", "code", "codigo", "uid")


def _largest_object_array(obj, _depth=0, _budget=[20000]):
    """Devuelve el array de objetos (dicts) más grande del JSON, recursivo."""
    best = []
    stack = [obj]
    while stack and _budget[0] > 0:
        cur = stack.pop()
        _budget[0] -= 1
        if isinstance(cur, list):
            objs = [x for x in cur if isinstance(x, dict)]
            if len(objs) > len(best):
                best = objs
            stack.extend(cur[:500])
        elif isinstance(cur, dict):
            stack.extend(list(cur.values())[:500])
    return best


def _find(obj, keys, _budget=2000):
    """Busca el primer valor escalar para alguna de `keys` en cualquier nivel."""
    stack = [obj]
    n = 0
    while stack and n < _budget:
        cur = stack.pop()
        n += 1
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in keys and isinstance(v, (str, int, float)) and str(v).strip():
                    return v
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur[:200])
    return None


def flatten_records(obj, *, limit: int = 200) -> list[dict]:
    """Aplana el array de objetos más grande a registros {title, price, url, id}."""
    rows = _largest_object_array(obj, _budget=[20000])
    out = []
    for item in rows[:limit]:
        rec = {}
        for field, keys in (("title", _TITLE_KEYS), ("price", _PRICE_KEYS),
                            ("url", _URL_KEYS), ("id", _ID_KEYS)):
            val = _find(item, keys)
            if val is not None:
                rec[field] = val
        if rec:
            out.append(rec)
    return out
