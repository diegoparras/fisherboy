"""Tests del modo reversible: roundtrip, control de rol, un solo uso."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.models import Rol
from app.privacy.reversible import ReversibleAnonymizer, ReversibleError, ReversibleStore
from app.privacy_policy import load_policy

from pathlib import Path

MATRIX = Path(__file__).resolve().parent.parent / "privacy_matrix.yaml"


class _FakeAnon:
    """No agrega spans propios: la pasada determinística (detectors) hace el trabajo."""

    def detect_spans(self, text):
        return []


def _rev():
    store = ReversibleStore(Fernet(Fernet.generate_key()))
    return ReversibleAnonymizer(_FakeAnon(), store, policy=load_policy(str(MATRIX)))


def test_reversible_roundtrip():
    rev = _rev()
    text = "Escribir a juan@x.com, CUIT 20-12345678-9."
    pseudo, ref, n = rev.process(text, Rol.ANGEL)
    assert "juan@x.com" not in pseudo
    assert "«EMAIL_1»" in pseudo and "«CUIT_1»" in pseudo
    assert ref and n == 2

    back = rev.revert(pseudo, ref, Rol.ANGEL)
    assert "juan@x.com" in back
    assert "20-12345678-9" in back


def test_reversible_role_denied_by_policy():
    rev = _rev()
    pseudo, ref, _ = rev.process("mail a@b.com", Rol.ANGEL)
    # humano no habilita reversible → no puede revertir (chequeo previo al pop).
    with pytest.raises(ReversibleError):
        rev.revert(pseudo, ref, Rol.HUMANO)


def test_reversible_owner_mismatch():
    rev = _rev()
    pseudo, ref, _ = rev.process("mail a@b.com", Rol.ANGEL)
    # dios habilita reversible, pero no es el dueño del mapeo → denegado.
    with pytest.raises(ReversibleError):
        rev.revert(pseudo, ref, Rol.DIOS)


def test_reversible_single_use():
    rev = _rev()
    pseudo, ref, _ = rev.process("mail a@b.com", Rol.ANGEL)
    rev.revert(pseudo, ref, Rol.ANGEL)            # consume el mapeo
    with pytest.raises(ReversibleError):          # segundo intento: ya no existe
        rev.revert(pseudo, ref, Rol.ANGEL)


def test_reversible_no_pii_no_ref():
    rev = _rev()
    pseudo, ref, n = rev.process("Texto sin datos personales.", Rol.ANGEL)
    assert ref is None and n == 0
