#!/usr/bin/env python3
"""Prueba REAL de la cadena de redes sociales (ADR-012), con Chromium de verdad.

Levanta una página que se comporta como X: pide su timeline a una API interna por XHR (con la
forma de GraphQL, tuits envueltos en `legacy`) y carga más al scrollear. Después corre la
captura de Fisherboy contra ella y verifica que:

  navegador → intercepta el XHR → el extractor reconoce los tuits por su forma

Lo único que NO se prueba acá es el login de X real: eso necesita una cuenta, y las cookies
las pone el usuario. Todo lo demás del camino es el mismo.

    docker run --rm -u root -v "$PWD":/src -w /src -e PYTHONPATH=/src \\
        ghcr.io/diegoparras/fisherboy:latest python scripts/check-social.py
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Timeline</title></head><body>
<div id="feed" style="height:1600px">cargando…</div>
<script>
let page = 0;
async function cargar() {
  const r = await fetch('/i/api/graphql/AbC123/UserTweets?cursor=' + page);
  const d = await r.json();
  const n = d.data.user.result.timeline.instructions[0].entries.length;
  document.getElementById('feed').insertAdjacentHTML('beforeend',
    '<div style="height:900px">TANDA-' + page + ' (' + n + ' tuits)</div>');
  page++;
}
cargar();
addEventListener('scroll', () => {
  if (page < 3 && innerHeight + scrollY >= document.body.offsetHeight - 60) cargar();
});
</script></body></html>"""


def _tuits(pagina: int, n: int = 5) -> dict:
    """Respuesta con la forma real de la GraphQL de X: tuits dentro de `legacy`, autor
    anidado en `core.user_results`, y un montón de ruido alrededor."""
    entries = []
    for i in range(n):
        tid = str(1750000000000000000 + pagina * 100 + i)
        entries.append({
            "entryId": "tweet-" + tid,
            "content": {"itemContent": {"tweet_results": {"result": {
                "rest_id": tid,
                "legacy": {
                    "full_text": "Tuit numero " + str(pagina * n + i),
                    "created_at": "Wed Oct 10 20:19:24 +0000 2018",
                    "favorite_count": 10 + i, "reply_count": i, "retweet_count": 2 * i,
                    "id_str": tid,
                },
                "views": {"count": str(1000 + i)},
                "core": {"user_results": {"result": {"legacy": {
                    "screen_name": "cuenta_demo", "name": "Cuenta Demo"}}}},
            }}}},
        })
    # Ruido: objetos con `text` que NO son tuits (botones, etiquetas). No deben colarse.
    entries.append({"entryId": "cursor", "content": {"text": "Mostrar mas"}})
    return {"data": {"user": {"result": {"timeline": {"instructions": [
        {"type": "TimelineAddEntries", "entries": entries}]}}}}}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                        # noqa: N802
        if self.path.startswith("/i/api/graphql/"):
            try:
                pagina = int(self.path.split("cursor=")[-1])
            except ValueError:
                pagina = 0
            body = json.dumps(_tuits(pagina)).encode("utf-8")
            ctype = "application/json"
        else:
            body, ctype = PAGE.encode("utf-8"), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main() -> int:
    srv = HTTPServer(("127.0.0.1", 8778), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    from app.fetchers.base import FetchContext
    from app.fetchers.capture import capture_xhr
    from app.net import social

    fails = []

    def check(cond, msg):
        print(("  \033[32mOK\033[0m  " if cond else "  \033[31mFALLO\033[0m ") + msg)
        if not cond:
            fails.append(msg)

    print("\n\033[1m1. Navegador real: scrollea y captura la API interna\033[0m")
    ctx = FetchContext(
        allow_private=True, headless=True, settle_s=1.0, scroll=False, timeout_s=30,
        actions=social.scroll_actions(15, pause_s=0.6),   # el mismo scroll que usa el job
    )
    endpoints = capture_xhr("http://127.0.0.1:8778/", ctx)
    graphql = [e for e in endpoints if "graphql" in e.get("url", "")]
    check(len(graphql) >= 2, f"interceptó {len(graphql)} respuestas de la API (paginó al scrollear)")

    print("\n\033[1m2. El extractor reconoce los tuits por su forma\033[0m")
    posts = social.extract_posts("x", endpoints, max_posts=100)
    check(len(posts) >= 10, f"extrajo {len(posts)} tuits de {len(endpoints)} endpoints")
    if posts:
        p = posts[0]
        check(p["author"] == "cuenta_demo", f"autor: {p['author']}")
        check(p["text"].startswith("Tuit numero"), f"texto: {p['text'][:30]}")
        check(p["created_at"].startswith("2018-10-10T"), f"fecha ISO: {p['created_at']}")
        check(isinstance(p["likes"], int), f"likes: {p['likes']}")
        check(p["url"].startswith("https://x.com/cuenta_demo/status/"), "URL del tuit armada")
    check(all("Mostrar mas" not in (p["text"] or "") for p in posts),
          "el ruido (botones con `text`) NO se coló")
    ids = [p["id"] for p in posts]
    check(len(ids) == len(set(ids)), "sin duplicados entre tandas")

    print("\n\033[1m3. El tope se respeta\033[0m")
    check(len(social.extract_posts("x", endpoints, max_posts=3)) == 3, "max_posts corta la lista")

    srv.shutdown()
    print()
    if fails:
        print(f"\033[1;31m✗ {len(fails)} FALLARON\033[0m")
        return 1
    print("\033[1;32m✓ TODO OK — captura + extracción por forma andan de punta a punta\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
