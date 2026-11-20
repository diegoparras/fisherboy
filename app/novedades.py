"""Novedades por versión, leídas del CHANGELOG.md (una sola fuente de verdad).

Se muestran en tres lugares: el cartel de arranque de cada proceso (lo primero que se ve en los
registros de EasyPanel), la UI (aviso "Novedades" al entrar después de una actualización) y el
Release de GitHub que crea el CI. Formato esperado (Keep a Changelog):

    ## [X.Y.Z] - AAAA-MM-DD
    ### Sección
    - ítem (las líneas siguientes con sangría continúan el ítem)

Solo stdlib a propósito: el CI lo corre (`python -m app.novedades`) sin instalar dependencias.

    python -m app.novedades            -> notas en markdown de la versión actual (error si faltan)
    python -m app.novedades --version  -> solo el número
"""
from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

_RE_VERSION = re.compile(r"^## \[(\d+\.\d+\.\d+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?")
_RE_SECCION = re.compile(r"^### (.+)")
_RE_ITEM = re.compile(r"^- (.*)")
_RE_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass
class Seccion:
    titulo: str
    items: list[str] = field(default_factory=list)


@dataclass
class Version:
    version: str
    fecha: str | None
    secciones: list[Seccion] = field(default_factory=list)

    def a_dict(self) -> dict:
        return asdict(self)


def parsear(texto: str | None) -> list[Version]:
    versiones: list[Version] = []
    v: Version | None = None
    sec: Seccion | None = None
    for linea in str(texto or "").splitlines():
        m = _RE_VERSION.match(linea)
        if m:
            v = Version(m.group(1), m.group(2))
            versiones.append(v)
            sec = None
            continue
        if v is None:
            continue
        if linea.startswith("## "):          # otra sección de nivel 2 ([Unreleased], etc.)
            v = sec = None
            continue
        t = _RE_SECCION.match(linea)
        if t:
            sec = Seccion(t.group(1).strip())
            v.secciones.append(sec)
            continue
        if sec is None:
            continue
        item = _RE_ITEM.match(linea)
        if item:
            sec.items.append(item.group(1).strip())
        elif linea[:1].isspace() and linea.strip() and sec.items:
            sec.items[-1] += " " + linea.strip()
    for x in versiones:
        x.secciones = [s for s in x.secciones if s.items]
    return versiones


def es_version(s: str | None) -> bool:
    return bool(s) and bool(_RE_SEMVER.match(s))


def _clave(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def desde(versiones: list[Version], vista: str | None, actual: str) -> list[Version]:
    """Versiones posteriores a `vista` y hasta `actual` inclusive (sin `vista`: solo la actual)."""
    if not vista:
        return [x for x in versiones if x.version == actual]
    return [x for x in versiones if _clave(vista) < _clave(x.version) <= _clave(actual)]


def hasta(versiones: list[Version], actual: str) -> list[Version]:
    """Todas las versiones hasta la que corre (nunca las de un CHANGELOG más nuevo)."""
    return [x for x in versiones if _clave(x.version) <= _clave(actual)]


def _plano(s: str) -> str:
    return re.sub(r"`([^`]+)`", r"\1", re.sub(r"\*\*(.+?)\*\*", r"\1", s))


def lineas_log(v: Version) -> list[str]:
    """Una línea por ítem, sin markdown, para el log del contenedor."""
    return [f"{s.titulo}: {_plano(i)}" for s in v.secciones for i in s.items]


def _cortar(texto: str, ancho: int, pre: str, sangria: str) -> list[str]:
    """Corta un texto en renglones de hasta `ancho`; el primero lleva `pre`, los demás `sangria`."""
    out: list[str] = []
    linea, vacia = pre, True
    for palabra in texto.split():
        if not vacia and len(linea) + 1 + len(palabra) > ancho:
            out.append(linea)
            linea, vacia = sangria, True
        linea += palabra if vacia else " " + palabra
        vacia = False
    out.append(linea)
    return out


def cartel_arranque(version: str, versiones: list[Version], proceso: str | None = None,
                    ancho: int = 80) -> list[str]:
    """Cartel que se imprime PRIMERO al levantar cada proceso (lo que se ve en los registros de
    EasyPanel): versión, fecha, qué proceso es y qué cambia, cortado al ancho para leerlo sin
    scroll lateral."""
    v = next((x for x in versiones if x.version == version), None)
    raya = "=" * min(ancho, 60)
    lineas = [raya, f"FISHERBOY v{version}" + (f"  ({v.fecha})" if v and v.fecha else "")]
    if proceso:
        lineas.append(f"Proceso: {proceso}")
    if v is None or not v.secciones:
        lineas.append("Sin notas para esta version en el CHANGELOG.")
    else:
        lineas.append("Que cambia en esta version:")
        for item in lineas_log(v):
            lineas.extend(_cortar(item, ancho, "- ", "    "))
    lineas.append(raya)
    return lineas


def notas_markdown(v: Version) -> str:
    """Notas en markdown para el Release de GitHub."""
    return "\n\n".join(
        f"### {s.titulo}\n" + "\n".join(f"- {i}" for i in s.items) for s in v.secciones
    ) + "\n"


def todas() -> list[Version]:
    """CHANGELOG.md de la instalación (viene en la imagen). Sin archivo: lista vacía, nunca tira."""
    try:
        return parsear(CHANGELOG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _modo() -> str | None:
    try:
        from .config import get_settings

        return get_settings().app_mode.value
    except Exception:  # noqa: BLE001 — una config rota no tapa el cartel
        return None


def imprimir_cartel(proceso: str) -> None:
    """Imprime el cartel con el prefijo de log del proceso (`[fisherboy.api]`,
    `[fisherboy.worker]`). Va por stdout en texto plano, antes que cualquier log JSON. Nunca
    rompe el arranque: si algo falla, no imprime y sigue."""
    try:
        modo = _modo()
        etiqueta = {"api": "API", "worker": "worker"}.get(proceso, proceso)
        lineas = cartel_arranque(__version__, todas(),
                                 proceso=f"{etiqueta}, modo {modo}" if modo else etiqueta)
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        texto = "".join(f"[fisherboy.{proceso}] {x}\n" for x in lineas)
        sys.stdout.write(texto.encode(enc, "replace").decode(enc))
        sys.stdout.flush()
    except Exception:  # noqa: BLE001
        pass


def _salida_utf8(texto: str) -> None:
    """Las notas van al Release en UTF-8 sea cual sea el locale de la consola."""
    buf = getattr(sys.stdout, "buffer", None)
    if buf is None:
        sys.stdout.write(texto)
        return
    sys.stdout.flush()
    buf.write(texto.encode("utf-8"))
    buf.flush()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv:
        sys.stdout.write(__version__)
        return 0
    v = next((x for x in todas() if x.version == __version__), None)
    if v is None or not v.secciones:
        sys.stderr.write(f'CHANGELOG.md no tiene la seccion "## [{__version__}]" (o esta vacia): '
                         "agregala antes de publicar.\n")
        return 1
    _salida_utf8(notas_markdown(v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
