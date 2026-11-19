"""Que un fallo de DNS no se disfrace de negativa de seguridad.

Los dos casos viajaban como el mismo SSRFError y el que los recibía no tenía
forma de separarlos. Medido el 2026-08-25 contra Oculum: su colector del
Boletín Oficial figuraba caído con "Error inesperado: SSRFError." y nada más.
La URL era `https://www.boletinoficial.gob.ar/seccion/primera`, pública y sana;
lo que fallaba era la resolución de nombres adentro del contenedor.

Un error de seguridad y una caída de DNS piden acciones opuestas: una es
"revisá lo que pediste", la otra "revisá la red de este despliegue".
"""

import socket

import pytest

from app.security.ssrf import (
    DestinoBloqueado,
    NoSeResuelve,
    SSRFError,
    resolve_and_validate,
)


def test_un_destino_privado_es_una_negativa_de_seguridad():
    """127.0.0.1 se rechaza, y con la clase que dice que fue por seguridad."""
    with pytest.raises(DestinoBloqueado) as e:
        resolve_and_validate("http://127.0.0.1/x")
    assert "bloqueado" in str(e.value).lower()


def test_un_host_que_no_resuelve_no_es_una_negativa_de_seguridad(monkeypatch):
    """El caso del Boletín: el DNS no responde y eso NO es un bloqueo."""

    def sin_dns(*_a, **_k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", sin_dns)

    with pytest.raises(NoSeResuelve) as e:
        resolve_and_validate("https://www.boletinoficial.gob.ar/seccion/primera")

    # NO puede ser un DestinoBloqueado: mandaría a revisar la URL, que está bien.
    assert not isinstance(e.value, DestinoBloqueado)
    # Y el mensaje tiene que llevar el host Y el motivo del sistema: sin eso,
    # quien lo lea sigue sin saber si es DNS, red o firewall.
    assert "boletinoficial.gob.ar" in str(e.value)
    assert "Name or service not known" in str(e.value)


def test_las_dos_siguen_siendo_SSRFError():
    """Compatibilidad: el código que ya escribía `except SSRFError` sigue andando.

    Sin esto, separar las clases rompería en silencio a todos los llamadores que
    hoy atrapan la base — que es peor que el problema que se vino a arreglar.
    """
    assert issubclass(DestinoBloqueado, SSRFError)
    assert issubclass(NoSeResuelve, SSRFError)


def test_una_url_malformada_sigue_siendo_la_clase_base():
    """Ni bloqueo ni DNS: la URL está mal escrita y eso ya se distinguía solo."""
    with pytest.raises(SSRFError) as e:
        resolve_and_validate("ftp://ejemplo.com/x")
    assert not isinstance(e.value, (DestinoBloqueado, NoSeResuelve))
