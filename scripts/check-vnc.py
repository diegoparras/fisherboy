#!/usr/bin/env python3
"""Prueba REAL del modo VNC (ADR-013): que el escritorio del server levante de verdad.

Verifica la cadena completa del modo pesado: pantalla virtual (Xvfb) → servidor VNC (x11vnc,
solo en localhost) → Chromium CON ventana → guardar la sesión. Es lo que hace posible ver
popups y pestañas nuevas, que es donde se traba el modo liviano.

    docker run --rm -u root -v "$PWD":/src -w /src -e PYTHONPATH=/src fb-vnc \\
        python scripts/check-vnc.py
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Login VNC</title></head>
<body style="font:20px sans-serif;padding:40px">
<h1 id="t">Iniciar sesion</h1>
<button id="b" onclick="ok()">Entrar</button>
<script>function ok(){document.cookie='vnc_demo=si; path=/';document.getElementById('t').textContent='ADENTRO';}</script>
</body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):                                        # noqa: N802
        b = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class MemStore:
    def __init__(self):
        self.d = {}

    def load(self, o, n):
        return self.d.get((o, n))

    def save(self, o, n, e):
        self.d[(o, n)] = e
        return True


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 8781), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from app.net import browser_vnc as bv

    fails = []

    def check(cond, msg):
        print(("  \033[32mOK\033[0m  " if cond else "  \033[31mFALLO\033[0m ") + msg)
        if not cond:
            fails.append(msg)

    def esperar(pred, seg=40):
        fin = time.monotonic() + seg
        while time.monotonic() < fin:
            if pred():
                return True
            time.sleep(0.4)
        return False

    print("\n\033[1m1. La imagen soporta el modo pesado\033[0m")
    ok, motivo = bv.disponible()
    check(ok, f"Xvfb + x11vnc presentes {('(' + motivo + ')') if motivo else ''}")
    if not ok:
        print("\n\033[1;31m✗ sin los binarios no se puede seguir\033[0m")
        return 1

    print("\n\033[1m2. Levantar escritorio + VNC + navegador con ventana\033[0m")
    store = MemStore()
    token, ses = bv.abrir("http://127.0.0.1:8781/", nombre="vncdemo", dueno="jti-1",
                          store=store, allow_private=True)
    listo = esperar(lambda: ses.estado in ("listo", "error"))
    check(listo and ses.estado == "listo", f"estado: {ses.estado} {ses.error}")

    def vnc_escucha():
        with socket.socket() as s:
            s.settimeout(1.0)
            return s.connect_ex(("127.0.0.1", bv.VNC_PORT)) == 0

    check(esperar(vnc_escucha, seg=15), f"x11vnc escuchando en el puerto {bv.VNC_PORT}")

    print("\n\033[1m3. El VNC NO se expone al exterior\033[0m")
    # x11vnc corre con -localhost: desde otra IP del contenedor no debe atender.
    fuera = socket.socket()
    fuera.settimeout(1.5)
    try:
        ip = socket.gethostbyname(socket.gethostname())
        expuesto = fuera.connect_ex((ip, bv.VNC_PORT)) == 0
    except OSError:
        expuesto = False
    finally:
        fuera.close()
    check(not expuesto, "solo atiende en 127.0.0.1 (el puente WebSocket es la unica puerta)")

    print("\n\033[1m4. Guardar la sesion\033[0m")
    check(ses.guardar(), "guardar() confirmo")
    est = store.load("jti-1", "vncdemo")
    check(isinstance(est, dict) and "cookies" in est, "quedo un storage_state en el store")

    print("\n\033[1m5. Limpieza: no quedan procesos colgados\033[0m")
    bv.cerrar(token, "jti-1")
    check(esperar(lambda: not vnc_escucha(), seg=15), "el x11vnc se apago al cerrar")
    check(bv.obtener(token, "jti-1") is None, "la sesion salio del registro")

    srv.shutdown()
    print()
    if fails:
        print(f"\033[1;31m✗ {len(fails)} FALLARON\033[0m")
        return 1
    print("\033[1;32m✓ TODO OK — el escritorio remoto levanta y se apaga limpio\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
