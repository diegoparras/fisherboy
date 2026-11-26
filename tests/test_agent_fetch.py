"""Lo que dice un `fetch` de receta cuando no recibe JSON (sin navegador)."""
from __future__ import annotations

from app.net.browser_agent import _asomo


def test_asomo_usa_el_titulo_del_html():
    html = "<html><head><title>Sesión expirada</title></head><body>x</body></html>"
    assert _asomo(html, "text/html") == 'HTML: "Sesión expirada"'


def test_asomo_sin_titulo_usa_el_texto_visible_y_no_el_script():
    html = "<html><body><script>var clave = 1;</script><p>No se encuentra  logueado</p></body></html>"
    assert _asomo(html, "text/html") == 'HTML: "No se encuentra logueado"'


def test_asomo_de_texto_plano_y_vacio():
    assert _asomo("error raro", "text/plain") == '"error raro"'
    assert _asomo("  ", "text/plain") == "cuerpo vacío"
