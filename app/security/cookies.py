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
