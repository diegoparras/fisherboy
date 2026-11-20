"""Qué no se deshace: lo decide código, antes de tocar, no la IA."""
from __future__ import annotations

import pytest

from app.net.irreversible import irreversible


def el(texto="", tipo="", aria="", tag="button", form=False):
    return {"tag": tag, "type": tipo, "texto": texto, "ariaLabel": aria, "enFormulario": form}


@pytest.mark.parametrize("e", [
    el("Pagar"), el("Confirmar compra"), el("ELIMINAR"), el("Place order"), el("Pagar ahora"),
    el("Confirmá tu pedido"), el("", aria="borrar"), el("Enviar mensaje"), el("Transferí"),
    el("", tipo="submit"),
])
def test_cuenta_como_que_no_se_deshace(e):
    assert irreversible(e, "click")


@pytest.mark.parametrize("e", [el("Ver detalle"), el("Siguiente"), el("Buscar"), el("Mandarina", tag="a")])
def test_lo_demas_no(e):
    assert not irreversible(e, "click")


def test_enter_solo_cuenta_dentro_de_un_formulario():
    assert irreversible(el("", tag="input", form=True), "enter")
    assert not irreversible(el("", tag="input", form=False), "enter")
