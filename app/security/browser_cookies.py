"""Lee las cookies del navegador LOCAL — reemplaza a la extensión "Get cookies.txt".

La extensión exporta las cookies que tu navegador ya tiene. Esto hace lo mismo desde
adentro: lee (y descifra) el store de cookies del perfil local de Chrome/Firefox/Edge/
Brave para un dominio dado, vía `browser_cookie3`. Sin exportar nada a mano.

SEGURIDAD: lee secretos de SESIÓN del navegador del usuario. Solo tiene sentido en
standalone corriendo en la MÁQUINA del usuario, es opt-in por job y gateado a rol dios.
NUNCA en modo sidekick/servidor (ahí el "navegador" no es el del usuario). Si el store
está bloqueado (Chrome abierto / cifrado app-bound nuevo), devuelve {} y un motivo; el
fallback es Firefox o la extensión.
"""
from __future__ import annotations

import importlib.util

from ..logging import get_logger

log = get_logger("fisherboy.browser_cookies")

_SUPPORTED = ("chrome", "firefox", "edge", "brave", "chromium", "opera")


def available() -> bool:
    return importlib.util.find_spec("browser_cookie3") is not None


def read_cookies(domain: str, browser: str = "chrome") -> dict[str, str]:
    """Lee las cookies del navegador local para `domain`. {} si no se pudo (con log)."""
    if not available():  # pragma: no cover
        log.warning("browser_cookie3 no instalado")
        return {}
    browser = (browser or "chrome").lower()
    if browser not in _SUPPORTED:
        return {}
    import browser_cookie3 as bc3

    loader = getattr(bc3, browser, None)
    if loader is None:
        return {}
    try:
        jar = loader(domain_name=domain)
        out = {c.name: c.value for c in jar if c.value}
        log.info("cookies del navegador leídas", extra={"browser": browser, "domain": domain,
                                                        "n": len(out)})
        return out
    except Exception as e:  # noqa: BLE001 — store bloqueado / cifrado nuevo / sin perfil
        log.warning("no se pudieron leer cookies del navegador",
                    extra={"browser": browser, "error": type(e).__name__})
        return {}
