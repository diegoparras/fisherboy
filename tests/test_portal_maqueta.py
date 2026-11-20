"""La maqueta del portal levanta y sirve el recorrido que usan los tests de la sesión de agente."""
from __future__ import annotations

import io
import zipfile

import httpx


def test_maqueta_sirve_paginas(portal_maqueta):
    r = httpx.get(portal_maqueta.base + "login.html")
    assert r.status_code == 200 and "F1:username" in r.text
    csv = httpx.get(portal_maqueta.base + "export.csv")
    assert csv.status_code == 200
    assert csv.text.startswith("Fecha;Tipo")
    assert "attachment" in csv.headers["content-disposition"]


def test_maqueta_variantes_zip_y_vacio(portal_maqueta):
    portal_maqueta.modo["zip"] = True
    z = zipfile.ZipFile(io.BytesIO(httpx.get(portal_maqueta.base + "export.csv").content))
    assert z.namelist() == ["Mis Documentos Emitidos.csv"]
    portal_maqueta.modo.update(zip=False, vacio=True)
    assert httpx.get(portal_maqueta.base + "export.csv").text.count("\n") == 1
