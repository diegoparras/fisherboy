"""Tests del motor de paginado: ASP.NET postback, links 'siguiente', ?page= y barrido."""
from __future__ import annotations

from app.crawl.pagination import (
    build_postback,
    find_next_link,
    find_postback_pagers,
    is_aspnet,
    paginate,
    parse_form_state,
)

ASPNET_P1 = """
<html><body>
<form method="post" action="/Listar.aspx">
  <input type="hidden" name="__VIEWSTATE" value="VS_PAGE_1" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="ABC123" />
  <table id="grid">
    <tr><td>fila 1</td></tr>
    <tr><td>fila 2</td></tr>
  </table>
  <a href="javascript:__doPostBack(&#39;ctl00$grid&#39;,&#39;Page$2&#39;)">2</a>
  <a href="javascript:__doPostBack(&#39;ctl00$grid&#39;,&#39;Page$3&#39;)">3</a>
</form>
</body></html>
"""

ASPNET_P2 = ASPNET_P1.replace("VS_PAGE_1", "VS_PAGE_2").replace("fila 1", "fila 3").replace("fila 2", "fila 4")


def test_detects_aspnet_and_parses_viewstate():
    assert is_aspnet(ASPNET_P1)
    state = parse_form_state(ASPNET_P1)
    assert state["action"] == "/Listar.aspx"
    assert state["fields"]["__VIEWSTATE"] == "VS_PAGE_1"
    assert state["fields"]["__VIEWSTATEGENERATOR"] == "ABC123"


def test_finds_postback_pagers():
    pagers = find_postback_pagers(ASPNET_P1)
    assert ("ctl00$grid", "Page$2") in pagers
    assert ("ctl00$grid", "Page$3") in pagers


def test_build_postback_body():
    state = parse_form_state(ASPNET_P1)
    url, data = build_postback(state, "ctl00$grid", "Page$2", "https://x.gob.ar/Listar.aspx")
    assert url == "https://x.gob.ar/Listar.aspx"
    assert data["__EVENTTARGET"] == "ctl00$grid"
    assert data["__EVENTARGUMENT"] == "Page$2"
    assert data["__VIEWSTATE"] == "VS_PAGE_1"      # manda el viewstate de la página actual


def test_paginate_sweeps_aspnet_postback():
    posts = []

    def post_text(url, data):
        posts.append((data["__EVENTTARGET"], data["__EVENTARGUMENT"], data["__VIEWSTATE"]))
        return ASPNET_P2 if data["__EVENTARGUMENT"] == "Page$2" else "<html>fin</html>"

    pages = paginate(ASPNET_P1, "https://x.gob.ar/Listar.aspx",
                     get_text=lambda u: "", post_text=post_text, max_pages=3)
    # barrió 3 páginas (P1 + 2 postbacks) y mandó el viewstate correcto en cada POST
    assert len(pages) == 3
    assert posts[0] == ("ctl00$grid", "Page$2", "VS_PAGE_1")
    assert "fila 3" in pages[1][1]


def test_find_next_link_siguiente():
    html = '<a href="/lista?p=2">Siguiente ›</a>'
    assert find_next_link(html, "https://x.com/lista?p=1") == "https://x.com/lista?p=2"


def test_find_next_link_query_param_bump():
    # Sin link explícito, incrementa el ?page=.
    assert find_next_link("<html>sin links</html>", "https://x.com/l?page=3") == "https://x.com/l?page=4"


def test_paginate_links_until_dry():
    pages_html = {
        "https://x.com/l?page=1": '<a href="/l?page=2">siguiente</a> contenido 1',
        "https://x.com/l?page=2": '<a href="/l?page=3">siguiente</a> contenido 2',
        "https://x.com/l?page=3": "contenido 3 sin siguiente",
    }
    got = paginate(pages_html["https://x.com/l?page=1"], "https://x.com/l?page=1",
                   get_text=lambda u: pages_html.get(u, "vacío"), max_pages=10)
    # corta cuando no hay más "siguiente" y el ?page= devuelve contenido nuevo agotado
    assert len(got) >= 3
    assert any("contenido 2" in h for _, h in got)
