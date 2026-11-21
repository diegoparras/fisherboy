"""La pantalla de la sesión de agente: la última foto, su número de orden, y la espera.

Vive aparte de la sesión a propósito. El que escribe las fotos (el hilo del navegador) y los
que las miran (los pedidos HTTP) no se conocen, y separarlo deja probar la espera sin levantar
un Chromium.
"""
from __future__ import annotations

import threading
import time

from app.net.browser_agent import Pantalla


def test_arranca_vacia():
    p = Pantalla()
    assert p.seq == 0
    assert p.img == b""


def test_poner_una_foto_sube_el_numero():
    p = Pantalla()
    assert p.poner(b"foto-1") is True
    assert p.seq == 1
    assert p.img == b"foto-1"


def test_la_misma_foto_no_sube_el_numero():
    """Pantalla quieta = nada que mandar. Es lo que hace que el tráfico baje a cero mientras
    la persona lee un formulario."""
    p = Pantalla()
    p.poner(b"igual")
    assert p.poner(b"igual") is False
    assert p.seq == 1


def test_esperar_vuelve_enseguida_si_ya_hay_algo_mas_nuevo():
    p = Pantalla()
    p.poner(b"foto-1")
    arranque = time.monotonic()
    assert p.esperar(desde=0, timeout_s=5) == 1
    assert time.monotonic() - arranque < 0.2, "no tendría que haber esperado nada"


def test_esperar_se_despierta_cuando_llega_una_foto():
    """El punto de todo esto: el que mira no pregunta en bucle, se lo despierta."""
    p = Pantalla()
    p.poner(b"foto-1")
    visto = []

    def mirar():
        visto.append(p.esperar(desde=1, timeout_s=5))

    t = threading.Thread(target=mirar, daemon=True)
    t.start()
    time.sleep(0.1)
    assert not visto, "todavía no hay nada nuevo: tiene que seguir esperando"
    p.poner(b"foto-2")
    t.join(timeout=2)
    assert visto == [2]


def test_esperar_se_rinde_al_cumplirse_el_tiempo():
    p = Pantalla()
    p.poner(b"foto-1")
    arranque = time.monotonic()
    assert p.esperar(desde=1, timeout_s=0.3) == 1   # devuelve el seq que hay, sin novedad
    tardo = time.monotonic() - arranque
    assert 0.25 <= tardo < 1.5, f"tardó {tardo:.2f}s"


def test_dos_que_miran_se_despiertan_los_dos():
    """Dos pedidos a la vez no se traban entre ellos."""
    p = Pantalla()
    p.poner(b"foto-1")
    vistos = []
    hilos = [threading.Thread(target=lambda: vistos.append(p.esperar(desde=1, timeout_s=5)),
                              daemon=True) for _ in range(2)]
    for h in hilos:
        h.start()
    time.sleep(0.1)
    p.poner(b"foto-2")
    for h in hilos:
        h.join(timeout=2)
    assert sorted(vistos) == [2, 2]


def test_cerrar_despierta_a_los_que_estan_mirando():
    """Si la sesión se cierra, el que espera no se queda colgado hasta que venza el tiempo."""
    p = Pantalla()
    p.poner(b"foto-1")
    listo = threading.Event()

    def mirar():
        p.esperar(desde=1, timeout_s=10)
        listo.set()

    threading.Thread(target=mirar, daemon=True).start()
    time.sleep(0.1)
    assert not listo.is_set()
    p.cerrar()
    assert listo.wait(timeout=2), "quedó esperando después de cerrar"


def test_esperar_sin_tiempo_no_espera():
    """timeout_s=0 es «decime lo que hay ahora», que es el comportamiento de siempre."""
    p = Pantalla()
    p.poner(b"foto-1")
    arranque = time.monotonic()
    assert p.esperar(desde=1, timeout_s=0) == 1
    assert time.monotonic() - arranque < 0.1
