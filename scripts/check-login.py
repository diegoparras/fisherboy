#!/usr/bin/env python3
"""Prueba REAL del login interactivo (ADR-013), manejando un Chromium de verdad.

Levanta una página con un formulario de login, abre una sesión de login remoto, y hace lo mismo
que haría el usuario desde la UI: pedir frames, tirar clicks en coordenadas, escribir con el
teclado, apretar Enter y guardar. Al final verifica que la cookie del login quedó en el store.

Es la prueba de que el lazo cierra:

    navegador del server → frames PNG → (clicks/teclas) → navegador del server → sesión guardada

    docker run --rm -u root -v "$PWD":/src -w /src -e PYTHONPATH=/src \\
        ghcr.io/diegoparras/fisherboy:latest python scripts/check-login.py
"""
from __future__ import annotations

import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Entrar</title>
<style>body{font:16px sans-serif;margin:0}
#usuario{position:absolute;left:100px;top:100px;width:400px;height:60px;font-size:20px}
#clave{position:absolute;left:100px;top:200px;width:400px;height:60px;font-size:20px}
#btn-entrar{position:absolute;left:100px;top:300px;width:200px;height:60px;font-size:20px}
</style>
</head><body>
<h1 id="titulo">Iniciar sesion</h1>
<input id="usuario" placeholder="usuario">
<input id="clave" type="password" placeholder="clave">
<button id="btn-entrar" type="button" onclick="entrar()">Entrar</button>
<script>
function entrar() {
  if (document.getElementById('usuario').value && document.getElementById('clave').value) {
    document.cookie = 'sesion_demo=ok-12345; path=/';
    document.getElementById('titulo').textContent = 'ADENTRO';
  }
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                        # noqa: N802
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class MemStore:
    """Store de sesiones en memoria (mismo contrato que BrowserSessionStore)."""

    def __init__(self):
        self.d = {}

    def load(self, dueno, nombre):
        return self.d.get((dueno, nombre))

    def save(self, dueno, nombre, estado):
        self.d[(dueno, nombre)] = estado
        return True


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 8779), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from app.net import browser_login as bl

    fails = []

    def check(cond, msg):
        print(("  \033[32mOK\033[0m  " if cond else "  \033[31mFALLO\033[0m ") + msg)
        if not cond:
            fails.append(msg)

    def esperar(pred, seg=25):
        fin = time.monotonic() + seg
        while time.monotonic() < fin:
            if pred():
                return True
            time.sleep(0.25)
        return False

    store = MemStore()
    print("\n\033[1m1. Abrir el navegador remoto\033[0m")
    token, ses = bl.abrir("http://127.0.0.1:8779/", nombre="demo", dueno="jti-1",
                          store=store, allow_private=True)
    check(esperar(lambda: ses.estado == "listo"), f"la sesion quedo lista (estado: {ses.estado})")
    check(esperar(lambda: bool(ses.frame)), "llego el primer frame")
    png = ses.frame
    check(png.startswith(b"\x89PNG"), f"el frame es un PNG real ({len(png)} bytes)")

    print("\n\033[1m2. Manejarlo como lo haria el usuario\033[0m")
    ses.enviar({"tipo": "click", "x": 300, "y": 130})    # centro de #usuario
    ses.enviar({"tipo": "texto", "texto": "diego"})
    ses.enviar({"tipo": "click", "x": 300, "y": 230})    # centro de #clave
    ses.enviar({"tipo": "texto", "texto": "hunter2"})
    ses.enviar({"tipo": "click", "x": 200, "y": 330})    # centro de #entrar
    entro = esperar(lambda: any(
        c.get("nombre") == "sesion_demo" for c in _cookies(ses, store)), seg=20)
    check(entro, "el login se completo escribiendo por control remoto")

    print("\n\033[1m3. Guardar la sesion\033[0m")
    check(ses.guardar(), "guardar() confirmo")
    estado = store.load("jti-1", "demo")
    check(bool(estado), "quedo guardada en el store")
    nombres = [c.get("name") for c in (estado or {}).get("cookies", [])]
    check("sesion_demo" in nombres, f"con la cookie del login: {nombres}")

    print("\n\033[1m4. Limpieza\033[0m")
    bl.cerrar(token, "jti-1")
    check(bl.obtener(token, "jti-1") is None, "la sesion se cerro y salio del registro")
    check(bl.obtener("token-falso", "jti-1") is None, "un token inventado no devuelve nada")

    srv.shutdown()
    print()
    if fails:
        print(f"\033[1;31m✗ {len(fails)} FALLARON\033[0m")
        return 1
    print("\033[1;32m✓ TODO OK — el login remoto cierra el lazo\033[0m")
    return 0


def _cookies(ses, store):
    """Las cookies actuales del navegador remoto: se piden guardando a un nombre de sondeo."""
    ses.enviar({"tipo": "guardar"})
    time.sleep(0.5)
    est = store.load(ses.dueno, ses.nombre) or {}
    return [{"nombre": c.get("name")} for c in est.get("cookies", [])]


if __name__ == "__main__":
    sys.exit(main())
