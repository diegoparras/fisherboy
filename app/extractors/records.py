"""Aplanado genérico de registros de cualquier JSON capturado. Ver Capa 5.

Un API de listado (productos, resultados, items, rows…) devuelve JSON con un array de
objetos enterrado entre metadata/tracking. Esto, sin saber nada del sitio (ML es solo
guía), encuentra **el array de objetos más grande** y aplana cada objeto a un registro
compacto.

El truco para no confundir campos (ej. el "text" del envío vs el del título): se busca
por PRIORIDAD de nombre de campo, no el primero que aparece. El campo de mayor prioridad
y más cercano a la raíz gana; si su valor es un objeto, se cava por un escalar.
"""
from __future__ import annotations

import json

# Prioridad de nombres por campo (índice 0 = preferido).
_TITLE_KEYS = ("title", "name", "nombre", "titulo", "título", "headline", "label", "text")
_PRICE_KEYS = ("current_price", "price", "amount", "precio", "monto", "value")
_URL_KEYS = ("permalink", "url", "link", "href", "enlace")
_ID_KEYS = ("id", "sku", "code", "codigo", "uid")

_BUDGET = 6000


def _array_score(objs) -> int:
    """Riqueza total del array: suma del tamaño serializado de sus objetos. Premia el
    array de REGISTROS (pocos objetos grandes) sobre arrays anidados de objetos chicos
    (ej. 'components'), que por cantidad ganarían pero son ruido."""
    total = 0
    for o in objs[:200]:
        try:
            total += len(json.dumps(o, default=str))
        except (TypeError, ValueError):
            total += 50
    return total


def _largest_object_array(obj):
    """El array de objetos más RICO (por tamaño total), no el de más elementos."""
    best, best_score = [], -1
    stack, n = [obj], 0
    while stack and n < 20000:
        cur = stack.pop()
        n += 1
        if isinstance(cur, list):
            objs = [x for x in cur if isinstance(x, dict)]
            if objs:
                sc = _array_score(objs)
                if sc > best_score:
                    best, best_score = objs, sc
            stack.extend(cur[:500])
        elif isinstance(cur, dict):
            stack.extend(list(cur.values())[:500])
    return best


def _scalar(v, prefer=("text", "value", "name", "label", "amount", "number")):
    """Reduce un valor a un escalar útil; si es dict/list, cava por los nombres `prefer`."""
    if isinstance(v, (str, int, float)) and str(v).strip():
        return v
    if isinstance(v, dict):
        for k in prefer:
            if k in v and isinstance(v[k], (str, int, float)) and str(v[k]).strip():
                return v[k]
        for vv in v.values():   # un nivel más
            s = _scalar(vv, prefer)
            if s is not None:
                return s
    elif isinstance(v, list):
        for x in v[:20]:
            s = _scalar(x, prefer)
            if s is not None:
                return s
    return None


def _best(obj, keys, prefer):
    """Devuelve el valor del campo de MAYOR prioridad (y más cercano a la raíz)."""
    pos = {k: i for i, k in enumerate(keys)}
    matches = []   # (prioridad, profundidad, valor)
    stack, n = [(obj, 0)], 0
    while stack and n < _BUDGET:
        cur, d = stack.pop()
        n += 1
        if isinstance(cur, dict):
            for k, v in cur.items():
                if k.lower() in pos:
                    matches.append((pos[k.lower()], d, v))
                stack.append((v, d + 1))
        elif isinstance(cur, list):
            for x in cur[:200]:
                stack.append((x, d + 1))
    if not matches:
        return None
    matches.sort(key=lambda m: (m[0], m[1]))
    return _scalar(matches[0][2], prefer)


def flatten_records(obj, *, limit: int = 200) -> list[dict]:
    """Aplana el array de objetos más grande a registros {title, price, url, id}."""
    out = []
    for item in _largest_object_array(obj)[:limit]:
        rec = {}
        title = _best(item, _TITLE_KEYS, ("text", "name", "label"))
        if title is not None:
            rec["title"] = title
        price = _best(item, _PRICE_KEYS, ("value", "amount", "number"))
        if price is not None:
            rec["price"] = price
        url = _best(item, _URL_KEYS, ("url", "href"))
        if url is not None:
            rec["url"] = str(url).split("#", 1)[0]   # sin fragmento de tracking
        idv = _best(item, _ID_KEYS, ("id",))
        if idv is not None:
            rec["id"] = idv
        if rec:
            out.append(rec)
    return out
