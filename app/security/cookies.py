"""Parser de cookies de sesión multi-formato.

El usuario pega/importa sus cookies para entrar a páginas tras login o ubicación
(igual que el flujo de YouTube en Escriba). Acepta los tres formatos que escupen las
herramientas comunes, así reusás la extensión que ya tenés ("Get cookies.txt LOCALLY"):

1. Netscape `cookies.txt`  — líneas TAB-separadas de 7 campos (export de la extensión).
2. JSON                    — array `[{"name","value",...}]` o dict `{name: value}`.
3. Header                  — `nombre=valor; otra=valor` (copiar del header Cookie).

Devuelve siempre un dict {name: value}. Nunca lanza.
"""
from __future__ import annotations

import json


def parse_cookies(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    raw = raw.strip()
    if not raw:
        return {}

    # 1) JSON (array de objetos o dict plano)
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if isinstance(data, list):
            jar = {}
            for c in data:
                if isinstance(c, dict) and c.get("name"):
                    jar[str(c["name"])] = str(c.get("value", ""))
            if jar:
                return jar
        elif isinstance(data, dict):
            if "cookies" in data and isinstance(data["cookies"], list):
                return parse_cookies(json.dumps(data["cookies"]))
            return {str(k): str(v) for k, v in data.items() if isinstance(v, (str, int, float))}

    # 2) Netscape cookies.txt (tabs / multilínea)
    if "\t" in raw or "\n" in raw:
        jar = {}
        for line in raw.splitlines():
            s = line.rstrip("\r")
            if not s.strip():
                continue
            if s.startswith("#HttpOnly_"):
                s = s[len("#HttpOnly_"):]
            elif s.startswith("#"):
                continue
            parts = s.split("\t")
            if len(parts) >= 7 and parts[5]:
                jar[parts[5]] = parts[6]
        if jar:
            return jar

    # 3) Header "k=v; k2=v2"
    jar = {}
    for part in raw.split(";"):
        if "=" in part:
            k, _, v = part.strip().partition("=")
            if k:
                jar[k] = v
    return jar


def to_netscape(raw: str | None, default_domain: str = ".youtube.com") -> str:
    """Convierte cookies (cualquiera de los 3 formatos) a texto Netscape cookies.txt, que es
    lo que come yt-dlp vía `cookiefile`. Si YA vienen en Netscape se respetan tal cual (así se
    conservan los dominios reales). Si vienen como header/JSON, se les asigna `default_domain`.
    Devuelve "" si no hay cookies. Nunca lanza.
    """
    if not raw or not raw.strip():
        return ""
    s = raw.strip()
    # ¿Ya es Netscape? (alguna línea no comentada con ≥7 campos TAB). Verbatim: conserva dominios.
    if "\t" in s and any(
        len(ln.split("\t")) >= 7 for ln in s.splitlines() if ln.strip() and not ln.startswith("#")
    ):
        head = "" if s.lstrip().startswith("#") else "# Netscape HTTP Cookie File\n"
        return head + s + ("" if s.endswith("\n") else "\n")
    jar = parse_cookies(s)
    if not jar:
        return ""
    dom = default_domain if default_domain.startswith(".") else "." + default_domain
    # incl_subdomains=TRUE, path=/, secure=TRUE, expira lejos (2038). 7 campos TAB.
    lines = ["# Netscape HTTP Cookie File"]
    lines += ["\t".join([dom, "TRUE", "/", "TRUE", "2147483647", name, value])
              for name, value in jar.items()]
    return "\n".join(lines) + "\n"


def storage_state_to_netscape(state: dict | None) -> str:
    """Convierte el `storage_state` de una sesión de browser al cookies.txt que come yt-dlp.

    Es el puente entre las dos mitades del sistema (ADR-013): te logueás UNA vez en el navegador
    del server y esa misma sesión sirve para bajar videos, sin exportar cookies a mano nunca más.

    A diferencia de `to_netscape`, acá cada cookie ya trae su dominio, path, expiración y flag
    secure reales — no hay que inventarles un dominio por defecto, así que el archivo resultante
    es fiel a lo que tiene el navegador. Nunca lanza: sin cookies devuelve "".
    """
    galletas = (state or {}).get("cookies") or []
    if not isinstance(galletas, list):
        return ""
    lineas = ["# Netscape HTTP Cookie File"]
    for c in galletas:
        if not isinstance(c, dict):
            continue
        nombre, valor = str(c.get("name") or ""), str(c.get("value") or "")
        dominio = str(c.get("domain") or "")
        if not nombre or not dominio:
            continue
        # Netscape: el primer campo TRUE/FALSE indica "vale para subdominios", que es
        # justamente lo que significa un dominio que arranca con punto.
        subdominios = "TRUE" if dominio.startswith(".") else "FALSE"
        path = str(c.get("path") or "/")
        seguro = "TRUE" if c.get("secure") else "FALSE"
        try:    # -1 (cookie de sesión) → 0, que en este formato significa "no expira sola"
            expira = int(float(c.get("expires", 0) or 0))
        except (TypeError, ValueError):
            expira = 0
        if expira < 0:
            expira = 0
        lineas.append("\t".join([dominio, subdominios, path, seguro, str(expira), nombre, valor]))
    return "\n".join(lineas) + "\n" if len(lineas) > 1 else ""
