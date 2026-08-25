"""Reparación asistida por IA cuando una red cambia de formato. Ver ADR-014.

El costo oculto de todo scraper es el mantenimiento: las plataformas mueven campos cada pocas
semanas y el extractor deja de sacar nada. Esto convierte ese mantenimiento en algo que se
arregla solo:

    extractor de siempre  →  ¿sacó posts?  → sí: listo, gratis, instantáneo
                                           → no: la IA mira una MUESTRA y dice dónde quedaron

Tres decisiones que hacen que esto sea seguro y barato:

1. La IA NUNCA produce los datos. Devuelve solo NOMBRES DE CAMPO ("el texto está en full_text,
   el autor en screen_name"). Con esos nombres, el extractor de siempre saca los valores del
   JSON real. Un modelo que alucine puede, como mucho, dar un nombre que no existe — y eso se
   detecta al validar. Nunca puede inventar un post.
2. Se VALIDA antes de aceptar: las anclas propuestas se prueban contra el JSON capturado y
   solo se guardan si de verdad producen posts.
3. Se CACHEA lo aprendido. Se paga una llamada la primera vez que la plataforma cambia; los
   jobs siguientes vuelven a ser deterministas y gratis.

Y no se manda la respuesta entera al modelo: esas APIs devuelven megas. Se arma una muestra
chica con los objetos candidatos, que es lo único que hace falta para reconocer la forma.
"""
from __future__ import annotations

import json

from ..logging import get_logger
from . import social

log = get_logger("fisherboy.social_ai")

MAX_MUESTRA = 12_000       # caracteres que se le mandan al modelo
_MIN_CAMPOS = 3            # un objeto con menos campos no parece un post
_TTL_ANCLAS = 30 * 24 * 3600


def _candidatos(endpoints: list[dict], tope: int = 6) -> list[dict]:
    """Objetos que PARECEN un post, para mostrarle al modelo.

    Heurística deliberadamente boba (tiene texto largo + varios campos): no necesita acertar,
    solo acotar. Mandar la respuesta entera sería carísimo y encima peor: el modelo se pierde
    entre megas de menús y telemetría."""
    vistos: set[str] = set()
    out: list[dict] = []
    for ep in endpoints or []:
        data = ep.get("json")
        if data is None:
            continue
        for nodo in social._walk(data, budget=60_000):
            if len(nodo) < _MIN_CAMPOS:
                continue
            # Umbral bajo a proposito: los posts cortos ("gracias!") son posts igual, y dejar
            # afuera el unico ejemplo por corto haria que la reparacion no tenga que mirar.
            # El ruido que entre se acota solo, porque abajo se guarda UNA muestra por forma.
            textos = [v for v in nodo.values() if isinstance(v, str) and len(v.strip()) > 8]
            if not textos:
                continue
            firma = ",".join(sorted(nodo.keys()))[:200]
            if firma in vistos:          # una sola muestra por "forma" de objeto
                continue
            vistos.add(firma)
            # Recortar: el modelo necesita ver los NOMBRES y el tipo de valor, no textos largos.
            recorte = {}
            for k, v in list(nodo.items())[:40]:
                if isinstance(v, str):
                    recorte[k] = v[:120]
                elif isinstance(v, (int, float, bool)) or v is None:
                    recorte[k] = v
                elif isinstance(v, dict):
                    recorte[k] = {kk: (str(vv)[:60] if not isinstance(vv, (dict, list)) else "…")
                                  for kk, vv in list(v.items())[:8]}
                elif isinstance(v, list):
                    recorte[k] = f"[lista de {len(v)}]"
            out.append(recorte)
            if len(out) >= tope:
                return out
    return out


def muestra(endpoints: list[dict], *, max_chars: int = MAX_MUESTRA) -> str:
    """La muestra en texto que se le manda al modelo (o que podés mirar vos para depurar)."""
    cands = _candidatos(endpoints)
    if not cands:
        return ""
    txt = json.dumps(cands, ensure_ascii=False, indent=1)
    return txt[:max_chars]


_SISTEMA = (
    "Sos un asistente que identifica la ESTRUCTURA de respuestas JSON de redes sociales. "
    "NO extraés datos: solo decís en qué campo vive cada cosa. Respondés únicamente con JSON."
)

_PEDIDO = """Abajo hay objetos de la respuesta de una API interna de {plataforma}.
Identificá qué NOMBRE DE CAMPO contiene cada dato de una publicación.

Respondé SOLO un objeto JSON con estas claves (usá null si no encontrás el campo):
{{"texto": "...", "autor": "...", "autor_nombre": "...", "id": "...",
  "fecha": "...", "likes": "...", "respuestas": "...", "compartidos": "..."}}

Los valores deben ser NOMBRES DE CAMPO que aparezcan en los objetos, no contenido.
"texto" es obligatorio: es el campo con el cuerpo de la publicación.

Objetos:
{muestra}"""


def _parsear(respuesta: str) -> dict:
    """Saca el objeto JSON de la respuesta del modelo, tolerando que venga con adornos."""
    s = (respuesta or "").strip()
    if "```" in s:                      # a veces lo envuelve en un bloque de código
        partes = s.split("```")
        s = max(partes, key=len).lstrip("json").strip()
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j <= i:
        return {}
    try:
        datos = json.loads(s[i:j + 1])
    except ValueError:
        return {}
    if not isinstance(datos, dict):
        return {}
    # Solo las claves conocidas, y solo cadenas: nada de estructuras raras hacia adentro.
    return {k: str(v) for k, v in datos.items()
            if k in social.ANCLAS and isinstance(v, str) and v.strip()}


def descubrir(endpoints: list[dict], plataforma: str, complete, *,
              max_posts: int = 200) -> tuple[dict, list[dict]]:
    """Le pide al modelo las anclas, las VALIDA contra el JSON real y devuelve (anclas, posts).

    Si las anclas propuestas no producen ni un post, se descartan: se prefiere no entregar
    nada antes que entregar basura inventada. Devuelve ({}, []) si no se pudo."""
    m = muestra(endpoints)
    if not m:
        return {}, []
    try:
        cruda = complete(_SISTEMA, _PEDIDO.format(plataforma=plataforma or "una red social",
                                                  muestra=m))
    except Exception as e:  # noqa: BLE001 — el LLM es opcional: sin él el job igual termina
        log.info("social_ai: el modelo no respondió", extra={"error": type(e).__name__})
        return {}, []

    anclas = _parsear(cruda)
    if not anclas.get("texto"):
        return {}, []
    # VALIDACIÓN: las anclas valen solo si de verdad sacan posts del JSON capturado.
    posts = social.extract_with_anchors(endpoints, anclas, max_posts=max_posts,
                                        platform=plataforma)
    if not posts:
        log.info("social_ai: las anclas propuestas no extrajeron nada", extra={"anclas": anclas})
        return {}, []
    log.info("social_ai: anclas descubiertas", extra={"plataforma": plataforma, "anclas": anclas,
                                                      "posts": len(posts)})
    return anclas, posts


# ---------------------------------------------------------------------------
# Cache: se paga UNA vez por cambio de formato
# ---------------------------------------------------------------------------
def _clave(plataforma: str) -> str:
    return f"fisherboy:social-anclas:{(plataforma or 'desconocida')[:32]}"


def anclas_guardadas(redis_client, plataforma: str) -> dict:
    if redis_client is None:
        return {}
    try:
        crudo = redis_client.get(_clave(plataforma))
        return json.loads(crudo) if crudo else {}
    except Exception:  # noqa: BLE001
        return {}


def guardar_anclas(redis_client, plataforma: str, anclas: dict) -> bool:
    if redis_client is None or not anclas:
        return False
    try:
        redis_client.set(_clave(plataforma), json.dumps(anclas), ex=_TTL_ANCLAS)
        return True
    except Exception:  # noqa: BLE001
        return False
