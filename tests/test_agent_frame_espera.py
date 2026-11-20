"""GET /api/agent/{t}/frame con espera larga (`esperar_ms`).

Sin el parámetro, el endpoint contesta lo que hay y listo — el cliente pregunta de nuevo en
unos milisegundos, y entre el clic de una persona y ver el resultado pasa medio segundo largo.
Con el parámetro, el pedido se queda esperando y vuelve en el instante en que la foto cambia.

La sesión falsa de estos tests lleva una `Pantalla` de verdad adentro: lo que se prueba es el
mecanismo real atravesando el endpoint, no un simulacro de él.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.net import browser_agent as ba


@pytest.fixture(autouse=True)
def _registro_limpio():
    ba._SESIONES.clear()
    yield
    ba._SESIONES.clear()


class _SesionConPantalla:
    """Una sesión mínima con la pantalla real: el endpoint no distingue."""

    def __init__(self, dueno="dios", estado="humano"):
        self.dueno, self.estado = dueno, estado
        self.url_actual = "https://portal.example.com/"
        self.pantalla = ba.Pantalla()
        self.eventos: list[dict] = []

    # --- lo que mira el endpoint ---
    @property
    def seq(self):
        return self.pantalla.seq

    @property
    def frame(self):
        return self.pantalla.img

    def esperar_frame(self, desde: int, timeout_s: float) -> int:
        return self.pantalla.esperar(desde, timeout_s)

    def vencida(self):
        return False

    def resumen(self):
        return {"estado": self.estado, "seq": self.seq, "log": [], "archivos": []}

    def enviar(self, cmd):
        self.eventos.append(cmd)

    def cerrar(self):
        self.estado = "cerrado"
        self.pantalla.cerrar()


@pytest.fixture
def sesion():
    ses = _SesionConPantalla()
    ba._SESIONES["tok"] = ses
    return ses


@pytest.fixture
def c(client_factory):
    return client_factory(AGENT_ALLOWED_DOMAINS="portal.example.com")


# --- sin el parámetro, todo igual que siempre ---------------------------------------
def test_sin_esperar_ms_contesta_al_instante(c, sesion):
    sesion.pantalla.poner(b"foto-1")
    arranque = time.monotonic()
    r = c.get("/api/agent/tok/frame?desde=1")
    assert r.status_code == 204
    assert time.monotonic() - arranque < 0.3, "sin esperar_ms no tiene que esperar nada"


def test_sin_esperar_ms_devuelve_la_foto_si_hay_una_nueva(c, sesion):
    sesion.pantalla.poner(b"foto-1")
    r = c.get("/api/agent/tok/frame?desde=0")
    assert r.status_code == 200
    assert r.content == b"foto-1"
    assert r.headers["X-Agent-Seq"] == "1"


# --- con el parámetro ---------------------------------------------------------------
def test_con_esperar_ms_vuelve_enseguida_si_ya_hay_algo_nuevo(c, sesion):
    sesion.pantalla.poner(b"foto-1")
    arranque = time.monotonic()
    r = c.get("/api/agent/tok/frame?desde=0&esperar_ms=3000")
    assert r.status_code == 200
    assert r.content == b"foto-1"
    assert time.monotonic() - arranque < 0.3, "había novedad: no tenía nada que esperar"


def test_con_esperar_ms_entrega_la_foto_que_llega_durante_la_espera(c, sesion):
    """El caso que motiva todo: el pedido ya está colgado cuando la pantalla cambia."""
    sesion.pantalla.poner(b"foto-1")

    def sacar_foto_despues():
        time.sleep(0.25)
        sesion.pantalla.poner(b"foto-2")

    threading.Thread(target=sacar_foto_despues, daemon=True).start()
    arranque = time.monotonic()
    r = c.get("/api/agent/tok/frame?desde=1&esperar_ms=4000")
    tardo = time.monotonic() - arranque
    assert r.status_code == 200
    assert r.content == b"foto-2"
    assert tardo < 1.5, f"tardó {tardo:.2f}s: tendría que haber vuelto al cambiar la foto"
    assert tardo >= 0.2, "volvió antes de que existiera la foto nueva"


def test_con_esperar_ms_da_204_al_cumplirse_el_tiempo(c, sesion):
    sesion.pantalla.poner(b"foto-1")
    arranque = time.monotonic()
    r = c.get("/api/agent/tok/frame?desde=1&esperar_ms=400")
    tardo = time.monotonic() - arranque
    assert r.status_code == 204
    assert 0.3 <= tardo < 2.0, f"tardó {tardo:.2f}s, se pidieron 0.4s"


def test_el_tope_es_cinco_segundos(c, sesion):
    """Un cliente no puede pedir que el servidor le retenga un pedido indefinidamente."""
    sesion.pantalla.poner(b"foto-1")
    arranque = time.monotonic()
    r = c.get("/api/agent/tok/frame?desde=1&esperar_ms=600000")
    tardo = time.monotonic() - arranque
    assert r.status_code == 204
    assert tardo < 7.0, f"esperó {tardo:.1f}s: el tope de 5 s no se aplicó"


def test_esperar_ms_negativo_no_espera(c, sesion):
    sesion.pantalla.poner(b"foto-1")
    arranque = time.monotonic()
    r = c.get("/api/agent/tok/frame?desde=1&esperar_ms=-50")
    assert r.status_code == 204
    assert time.monotonic() - arranque < 0.3


# --- que no trabe el servidor -------------------------------------------------------
def test_el_estado_responde_mientras_un_frame_espera(c, sesion):
    """Si la espera bloqueara el bucle de eventos, esto se quedaría colgado."""
    sesion.pantalla.poner(b"foto-1")
    esperando = threading.Thread(
        target=lambda: c.get("/api/agent/tok/frame?desde=1&esperar_ms=3000"), daemon=True)
    esperando.start()
    time.sleep(0.2)

    arranque = time.monotonic()
    r = c.get("/api/agent/tok")
    assert r.status_code == 200
    assert time.monotonic() - arranque < 1.5, "el estado quedó detrás de la espera"

    r = c.post("/api/agent/tok/event", json={"tipo": "click", "x": 1, "y": 2})
    assert r.status_code == 200, r.text
    assert sesion.eventos == [{"tipo": "click", "x": 1.0, "y": 2.0}]
    sesion.pantalla.poner(b"foto-2")
    esperando.join(timeout=3)
    assert not esperando.is_alive()


def test_dos_frames_esperando_a_la_vez_vuelven_los_dos(c, sesion):
    sesion.pantalla.poner(b"foto-1")
    codigos: list[int] = []

    def mirar():
        codigos.append(c.get("/api/agent/tok/frame?desde=1&esperar_ms=4000").status_code)

    hilos = [threading.Thread(target=mirar, daemon=True) for _ in range(2)]
    for h in hilos:
        h.start()
    time.sleep(0.25)
    sesion.pantalla.poner(b"foto-2")
    for h in hilos:
        h.join(timeout=3)
    assert codigos == [200, 200], f"alguno no volvió: {codigos}"
