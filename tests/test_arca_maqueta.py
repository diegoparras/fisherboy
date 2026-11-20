"""La maqueta de ARCA levanta y sirve el recorrido que usan los tests de la sesión de agente."""
from __future__ import annotations

import io
import zipfile

import httpx


def test_maqueta_sirve_paginas(arca_maqueta):
    r = httpx.get(arca_maqueta.base + "login.html")
    assert r.status_code == 200 and "F1:username" in r.text
    csv = httpx.get(arca_maqueta.base + "export.csv")
    assert csv.status_code == 200
    assert csv.text.startswith("Fecha;Tipo")
    assert "attachment" in csv.headers["content-disposition"]


def test_maqueta_variantes_zip_y_vacio(arca_maqueta):
    arca_maqueta.modo["zip"] = True
    z = zipfile.ZipFile(io.BytesIO(httpx.get(arca_maqueta.base + "export.csv").content))
    assert z.namelist() == ["Mis Comprobantes Emitidos.csv"]
    arca_maqueta.modo.update(zip=False, vacio=True)
    assert httpx.get(arca_maqueta.base + "export.csv").text.count("\n") == 1
