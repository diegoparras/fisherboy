#!/usr/bin/env python3
"""Prueba REAL de las acciones de browser (ADR-011): Chromium de verdad, no un mock.

Levanta una página local que imita lo que uno se encuentra en la vida real —banner de cookies,
formulario de login, contenido que recién aparece al scrollear— y corre el fetcher completo
contra ella con una lista de acciones. Verifica que la página quedó operada (no solo cargada),
que el screenshot salió, y que la sesión persiste para el próximo job.

    docker run --rm -u root -v "$PWD":/src -w /src ghcr.io/diegoparras/fisherboy:latest \
        python scripts/check-actions.py
"""
from __future__ import annotations

import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Prueba</title></head><body>
<div id="banner">Usamos cookies <button id="aceptar" onclick="this.parentElement.remove()">Aceptar</button></div>
<div id="login">
  <input id="user" placeholder="usuario"><input id="pass" type="password" placeholder="clave">
  <button id="entrar" onclick="entrar()">Entrar</button>
</div>
<div id="panel" style="display:none"><h1>Panel privado</h1><p id="dato">SECRETO-42</p></div>
<!-- Alto inicial > viewport: si no, no hay barra de scroll y el evento nunca dispara
     (una página más corta que la ventana no tiene "scroll infinito" que probar). -->
<div id="feed"><div style="height:1600px">ITEM-INICIAL</div></div>
<script>
function entrar() {
  if (document.getElementById('user').value && document.getElementById('pass').value) {
    document.getElementById('login').remove();
    document.getElementById('panel').style.display = 'block';
    document.cookie = 'sesion=abc123; path=/';
  }
}
// Contenido que solo aparece al scrollear (feed infinito).
let n = 0;
addEventListener('scroll', () => {
  if (n < 3 && innerHeight + scrollY >= document.body.offsetHeight - 50) {
    n++;
    document.getElementById('feed').insertAdjacentHTML('beforeend',
      '<div style="height:800px">ITEM-LAZY-' + n + '</div>');
  }
});
</script></body></html>"""


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):                           # silencio
        pass


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 8777), partial(Handler))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:8777/"

    from app.fetchers.base import FetchContext
    from app.fetchers.browser import BrowserFetcher
    from app.fetchers.session_store import BrowserSessionStore

    class MemRedis:                       # store mínimo, sin depender de Redis real
        def __init__(self): self.d = {}
        def get(self, k): return self.d.get(k)
        def set(self, k, v, ex=None): self.d[k] = v
        def delete(self, k): return bool(self.d.pop(k, None))

    store = BrowserSessionStore(MemRedis())
    fails = []

    def check(cond, msg):
        print(("  \033[32mOK\033[0m  " if cond else "  \033[31mFALLO\033[0m ") + msg)
        if not cond:
            fails.append(msg)

    # --- 1. Operar la página: banner + login + scroll infinito + screenshot ---------
    print("\n\033[1m1. Acciones contra Chromium real\033[0m")
    ctx = FetchContext(
        allow_private=True,              # 127.0.0.1 es privada: para la prueba se permite
        headless=True, settle_s=0.5, scroll=False, timeout_s=30,
        session_name="prueba", session_owner="jti-1", session_store=store,
        actions=[
            {"do": "click", "sel": "#aceptar"},
            {"do": "type", "sel": "#user", "text": "diego"},
            {"do": "type", "sel": "#pass", "text": "hunter2", "secret": True},
            {"do": "click", "sel": "#entrar"},
            {"do": "wait_for", "sel": "#panel"},
            {"do": "scroll_until", "max_rounds": 8, "pause_s": 0.4},
            {"do": "click", "sel": "#no-existe", "optional": True},   # tiene que NO cortar
            {"do": "screenshot", "name": "final"},
        ],
    )
    res = BrowserFetcher().fetch(url, ctx)
    html, log = res.text, res.meta.get("actions", [])

    check("SECRETO-42" in html, "el login funcionó (aparece el contenido privado)")
    check("Usamos cookies" not in html, "el banner de cookies se cerró")
    check("ITEM-LAZY-3" in html, "el scroll infinito cargó todo el feed lazy")
    check(any(s["do"] == "scroll_until" and s.get("rounds", 0) >= 3 for s in log),
          "scroll_until reportó las vueltas que dio")
    opt = [s for s in log if s.get("sel") == "#no-existe"]
    check(opt and opt[0]["ok"] is False, "el paso `optional` que falló quedó registrado…")
    check(any(s["do"] == "screenshot" and s["ok"] for s in log), "…y la secuencia siguió igual")

    png = res.meta.get("artifacts", {}).get("final.png", b"")
    check(png.startswith(b"\x89PNG"), f"el screenshot es un PNG real ({len(png)} bytes)")

    # --- 2. El secreto no se filtra en el log --------------------------------------
    print("\n\033[1m2. La contraseña no se filtra\033[0m")
    check("hunter2" not in str(log), "la clave NO aparece en el log de acciones")

    # --- 3. La sesión persiste para el próximo job ---------------------------------
    print("\n\033[1m3. Sesión persistente (login que sobrevive al job)\033[0m")
    saved = store.load("jti-1", "prueba")
    check(bool(saved), "la sesión se guardó")
    cookies = [c["name"] for c in (saved or {}).get("cookies", [])]
    check("sesion" in cookies, f"guardó la cookie del login {cookies}")
    check(store.load("jti-otro", "prueba") is None, "y otro usuario NO puede leerla")

    # --- 4. Un paso obligatorio que falla, falla el fetch ---------------------------
    print("\n\033[1m4. Un selector inexistente falla claro\033[0m")
    from app.fetchers.base import FetchError
    ctx2 = FetchContext(allow_private=True, headless=True, settle_s=0.3, scroll=False,
                        timeout_s=15, actions=[{"do": "click", "sel": "#jamas", "timeout_s": 2}])
    try:
        BrowserFetcher().fetch(url, ctx2)
        check(False, "tendría que haber fallado")
    except FetchError as e:
        check("Acciones de browser" in str(e), f"error claro: {str(e)[:70]}…")

    srv.shutdown()
    print()
    if fails:
        print(f"\033[1;31m✗ {len(fails)} FALLARON\033[0m")
        return 1
    print("\033[1;32m✓ TODO OK — las acciones operan un navegador real\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
