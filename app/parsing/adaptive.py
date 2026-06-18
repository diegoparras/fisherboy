"""Parsing auto-reparable estilo Scrapling. Ver Capa 4.

Los scrapers se rompen cuando el sitio cambia el DOM y un selector deja de matchear.
La idea de Scrapling: además del selector, guardar un FINGERPRINT del elemento
(tag, id, clases, atributos, texto). Si el selector falla en una corrida futura, se
RELOCALIZA el elemento por similitud con el fingerprint y se sugiere un selector nuevo.

Implementación sobre lxml (ya viene con Trafilatura). Scrapling como hook: si está
instalado se podría delegar; acá tenemos un self-healing propio, testeable y sin deps
extra. Los fingerprints se persisten por perfil (Redis/memoria) entre corridas.
"""
from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field


def _lxml_available() -> bool:
    return importlib.util.find_spec("lxml") is not None


@dataclass
class Fingerprint:
    tag: str = ""
    el_id: str = ""
    classes: list[str] = field(default_factory=list)
    text: str = ""
    attrs: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"tag": self.tag, "el_id": self.el_id, "classes": self.classes,
                "text": self.text, "attrs": self.attrs}

    @classmethod
    def from_dict(cls, d: dict) -> "Fingerprint":
        return cls(tag=d.get("tag", ""), el_id=d.get("el_id", ""),
                   classes=list(d.get("classes") or []), text=d.get("text", ""),
                   attrs=dict(d.get("attrs") or {}))


def _fingerprint_of(el) -> Fingerprint:
    classes = (el.get("class") or "").split()
    text = (el.text_content() if hasattr(el, "text_content") else (el.text or "")) or ""
    attrs = {k: v for k, v in el.attrib.items() if k != "class"}
    return Fingerprint(tag=el.tag, el_id=el.get("id", ""), classes=classes,
                       text=text.strip()[:120], attrs=attrs)


def _similarity(fp: Fingerprint, el) -> float:
    """Score 0..1 de cuán parecido es `el` al fingerprint guardado."""
    cand = _fingerprint_of(el)
    score = 0.0
    if cand.tag == fp.tag:
        score += 0.25
    if fp.el_id and cand.el_id == fp.el_id:
        score += 0.35
    # Jaccard de clases.
    a, b = set(fp.classes), set(cand.classes)
    if a or b:
        score += 0.25 * (len(a & b) / len(a | b))
    # Similitud de texto (prefijo compartido normalizado).
    if fp.text and cand.text:
        common = len(_common_prefix(fp.text, cand.text))
        score += 0.15 * (common / max(len(fp.text), 1))
    return score


def _common_prefix(a: str, b: str) -> str:
    out = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return "".join(out)


def _css_to_elements(tree, selector: str):
    """Devuelve elementos por CSS (cssselect si está) o XPath (prefijo 'xpath:')."""
    if selector.startswith("xpath:"):
        return tree.xpath(selector[len("xpath:"):])
    try:
        return tree.cssselect(selector)   # requiere cssselect
    except Exception:  # noqa: BLE001 — sin cssselect o selector inválido
        return []


@dataclass
class HealResult:
    value: str | None
    healed: bool          # True si hubo que relocalizar por fingerprint
    selector: str         # selector usado/sugerido
    score: float = 1.0


class SelfHealingExtractor:
    """Extrae campos con selectores que se auto-reparan ante cambios de DOM.

    `store` es un dict-like {clave: fingerprint_dict} persistente (Redis/memoria).
    `profile` namespacéa los fingerprints (p. ej. el dominio).
    """

    def __init__(self, store: dict | None = None, *, profile: str = "default",
                 min_score: float = 0.45) -> None:
        self.store = store if store is not None else {}
        self.profile = profile
        self.min_score = min_score

    def _key(self, field_name: str) -> str:
        return f"{self.profile}::{field_name}"

    def extract(self, html: str, field_name: str, selector: str) -> HealResult:
        if not _lxml_available():
            raise RuntimeError("El parsing auto-reparable necesita lxml.")
        from lxml import html as lxml_html

        tree = lxml_html.fromstring(html)
        matches = _css_to_elements(tree, selector)

        if matches:
            el = matches[0]
            self.store[self._key(field_name)] = _fingerprint_of(el).to_dict()
            return HealResult(value=_text(el), healed=False, selector=selector, score=1.0)

        # El selector falló: relocalizar por fingerprint guardado.
        saved = self.store.get(self._key(field_name))
        if not saved:
            return HealResult(value=None, healed=False, selector=selector, score=0.0)

        fp = Fingerprint.from_dict(saved)
        best, best_score = None, 0.0
        for el in tree.iter():
            if not isinstance(el.tag, str):  # comentarios/PI
                continue
            s = _similarity(fp, el)
            if s > best_score:
                best, best_score = el, s

        if best is not None and best_score >= self.min_score:
            new_sel = _suggest_selector(best)
            self.store[self._key(field_name)] = _fingerprint_of(best).to_dict()
            return HealResult(value=_text(best), healed=True, selector=new_sel, score=best_score)

        return HealResult(value=None, healed=False, selector=selector, score=best_score)


def _text(el) -> str:
    t = el.text_content() if hasattr(el, "text_content") else (el.text or "")
    return (t or "").strip()


def _suggest_selector(el) -> str:
    """Sugiere un selector estable para el elemento relocalizado."""
    if el.get("id"):
        return f"#{el.get('id')}"
    classes = (el.get("class") or "").split()
    if classes:
        return el.tag + "." + ".".join(classes)
    return el.tag


def scrapling_available() -> bool:
    """Scrapling como hook alternativo (si el usuario lo instala)."""
    return importlib.util.find_spec("scrapling") is not None
