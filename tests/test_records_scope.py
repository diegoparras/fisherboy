"""Tests del aplanado genérico de registros y del foco de sección del spider."""
from __future__ import annotations

from app.crawl.discovery import extract_links
from app.extractors.records import flatten_records


# --------------------------------------------------------------------------- flatten genérico
def test_flatten_ml_shaped():
    # Forma tipo ML (items con title/price anidados + ruido de tracking).
    data = {
        "component_name": "CarouselDynamic",
        "items": [
            {"metadata": {"id": "MLA1", "url": "articulo.com/MLA-1"},
             "components": [{"title": {"text": "Buzo Negro"}},
                            {"price": {"current_price": {"value": 144990}}}]},
            {"metadata": {"id": "MLA2", "url": "articulo.com/MLA-2"},
             "components": [{"title": {"text": "Campera"}},
                            {"price": {"current_price": {"value": 88701}}}]},
        ],
    }
    recs = flatten_records(data)
    assert len(recs) == 2
    assert recs[0]["title"] == "Buzo Negro"
    assert recs[0]["id"] == "MLA1"
    assert any(str(r.get("price")) in ("144990", "88701") for r in recs)


def test_flatten_generic_results():
    data = {"page": 1, "results": [
        {"name": "Item A", "price": 10, "permalink": "https://x.com/a"},
        {"name": "Item B", "price": 20, "permalink": "https://x.com/b"},
        {"name": "Item C", "price": 30, "permalink": "https://x.com/c"},
    ]}
    recs = flatten_records(data)
    assert len(recs) == 3
    assert recs[0]["title"] == "Item A"
    assert recs[0]["url"] == "https://x.com/a"
    assert recs[2]["price"] == 30


def test_flatten_no_array():
    assert flatten_records({"a": 1, "b": "x"}) == []


# --------------------------------------------------------------------------- foco de sección
def test_drop_chrome_filters_nav():
    html = ("<a href='/login'>login</a><a href='/carrito'>cart</a>"
            "<a href='/terminos'>tos</a><a href='/c/ropa/producto-1'>prod</a>")
    got = extract_links(html, "https://x.com/c/ropa", drop_chrome=True)
    assert got == ["https://x.com/c/ropa/producto-1"]   # nav descartada


def test_scope_path_stays_in_section():
    html = ("<a href='/c/ropa/sub'>dentro</a><a href='/c/vehiculos'>fuera</a>"
            "<a href='/ofertas'>fuera2</a>")
    got = extract_links(html, "https://x.com/c/ropa", scope_path="/c/ropa")
    assert got == ["https://x.com/c/ropa/sub"]          # solo bajo la sección


def test_flatten_picks_items_not_nested_components():
    # Regresión: 'components' tiene más objetos que 'items', pero 'items' es el registro.
    data = {"items": [
        {"metadata": {"id": "A1", "url": "x.com/a#t=1"},
         "components": [
             {"type": "title", "id": "title", "title": {"text": "Producto A"}},
             {"type": "price", "price": {"current_price": {"value": 999}}},
             {"type": "shipping", "shipping": {"text": "Llega gratis"}},
         ]},
        {"metadata": {"id": "B2", "url": "x.com/b"},
         "components": [
             {"type": "title", "title": {"text": "Producto B"}},
             {"type": "price", "price": {"current_price": {"value": 500}}},
             {"type": "shipping", "shipping": {"text": "Envío gratis"}},
         ]},
    ]}
    recs = flatten_records(data)
    assert len(recs) == 2                       # items, no los 6 components
    assert recs[0]["title"] == "Producto A"     # título real, no "Llega gratis"
    assert recs[0]["price"] == 999              # current_price, no cuotas
    assert recs[0]["id"] == "A1"
    assert "#" not in recs[0]["url"]            # tracking removido
