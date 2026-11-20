"""Maqueta local de un portal (login, portal, Mis Documentos) para probar la sesión de agente.

Imita los ids y el recorrido reales (login en dos pasos, servicio que se abre en otra pestaña,
representado, emitidos/recibidos, búsqueda por AJAX y exportación CSV) sin tocar el sitio real ni usar
una clave fiscal real. La clave válida de la maqueta es `clave-de-prueba`.

`modo` se cambia desde el test: `zip=True` exporta un ZIP con el CSV adentro, `vacio=True`
exporta solo el encabezado (rango sin comprobantes).
"""
from __future__ import annotations

import io
import json
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DIR = Path(__file__).parent / "fixtures" / "portal"
CLAVE_VALIDA = "clave-de-prueba"


def _csv(vacio: bool) -> bytes:
    texto = (DIR / "emitidos.csv").read_text(encoding="utf-8")
    if vacio:
        texto = texto.splitlines()[0] + "\n"
    return texto.encode("utf-8")


class _Handler(SimpleHTTPRequestHandler):
    modo: dict = {}

    def log_message(self, *args):  # silencio en los tests
        pass

    def _responder(self, cuerpo: bytes, ctype: str, adjunto: str = "") -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(cuerpo)))
        if adjunto:
            self.send_header("Content-Disposition", f'attachment; filename="{adjunto}"')
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):  # noqa: N802
        ruta = self.path.split("?", 1)[0]
        if ruta == "/ajax.do":
            filas = [] if self.modo.get("vacio") else [
                ["02/11/2026", "Factura C", "1", "125", "150000,00"],
                ["05/11/2026", "Nota de Credito C", "1", "12", "20000,50"],
                ["10/11/2026", "Factura B", "2", "48", "1.210,00"],
            ]
            return self._responder(json.dumps({"data": filas}).encode(), "application/json")
        if ruta == "/export.csv":
            datos = _csv(bool(self.modo.get("vacio")))
            if self.modo.get("zip"):
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    z.writestr("Mis Documentos Emitidos.csv", datos)
                return self._responder(buf.getvalue(), "application/zip", "comprobantes.zip")
            return self._responder(datos, "text/csv; charset=utf-8", "comprobantes.csv")
        return super().do_GET()


class Maqueta:
    """Servidor en un hilo. `base` termina en `/`."""

    def __init__(self) -> None:
        handler = type("H", (_Handler,), {"modo": {}})
        self.modo = handler.modo
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(handler, directory=str(DIR)))
        self.base = f"http://127.0.0.1:{self._srv.server_address[1]}/"
        self._hilo = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def __enter__(self) -> "Maqueta":
        self._hilo.start()
        return self

    def __exit__(self, *exc) -> None:
        self._srv.shutdown()
        self._srv.server_close()
