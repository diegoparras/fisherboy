"""Login por VNC — el escritorio del server, no solo la página. Ver ADR-013.

Es el hermano pesado de `browser_login`. Los dos sirven para lo mismo (que te loguees vos en el
navegador del servidor), pero cubren cosas distintas:

  browser_login (liviano)  captura la PÁGINA y reinyecta eventos. Sin dependencias nuevas.
                           Limitación real: muestra UNA página. Si el login abre un POPUP
                           ("Entrar con Google", OAuth), el popup no se ve y quedás trabado.

  browser_vnc (este)       Xvfb + x11vnc + un Chromium de verdad con ventana. Se ve TODO:
                           popups, pestañas nuevas, la barra de direcciones, descargas.
                           Cuesta ~100 MB de imagen y bastante más RAM, por eso es opt-in y
                           de a UNA sesión por vez.

El VNC escucha SOLO en 127.0.0.1 y nunca se expone al exterior: el navegador del usuario llega
por un WebSocket de la propia API (que ya exige sesión y rol), y ese endpoint hace de puente
hasta el puerto local. Así no hay que abrir otro puerto en el deploy ni pelearse con EasyPanel.

Al terminar se guarda el storage_state igual que en el modo liviano, así los jobs no saben ni
les importa por cuál de los dos te logueaste.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import uuid

from ..logging import get_logger

log = get_logger("fisherboy.browser_vnc")

ANCHO, ALTO = 1280, 800
DISPLAY = ":99"            # una sola sesión por vez → display y puerto fijos
VNC_PORT = 5900
TTL_S = 20 * 60            # el VNC es caro: se cierra solo


class VncError(Exception):
    """No se pudo levantar el escritorio remoto."""


def disponible() -> tuple[bool, str]:
    """¿Están Xvfb y x11vnc en la imagen? Devuelve (ok, motivo si no)."""
    faltan = [b for b in ("Xvfb", "x11vnc") if not shutil.which(b)]
    if faltan:
        return False, "faltan en la imagen: " + ", ".join(faltan)
    return True, ""


def _puerto_libre(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class _SesionVnc:
    """Xvfb + x11vnc + Chromium con ventana. Vive hasta que guardás o se vence."""

    def __init__(self, url: str, *, nombre: str, dueno: str, store, proxy: str = "",
                 allow_private: bool = False, locale: str = "es-AR") -> None:
        self.url, self.nombre, self.dueno = url, nombre, dueno
        self.store, self.proxy = store, proxy
        self.allow_private, self.locale = allow_private, locale

        self.estado = "arrancando"      # arrancando | listo | guardado | error | cerrado
        self.error = ""
        self.creada = time.monotonic()
        self.guardada = False

        self._procs: list[subprocess.Popen] = []
        self._guardar = threading.Event()
        self._cerrar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    def vencida(self) -> bool:
        return (time.monotonic() - self.creada) > TTL_S

    def guardar(self) -> bool:
        self._guardar.set()
        for _ in range(80):             # hasta ~8 s
            if self.guardada or self.estado in ("error", "cerrado"):
                break
            time.sleep(0.1)
        return self.guardada

    def cerrar(self) -> None:
        self._cerrar.set()

    def _matar_procesos(self) -> None:
        for p in self._procs:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    p.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._procs.clear()

    def _correr(self) -> None:
        from ..security.ssrf import resolve_and_validate
        try:
            resolve_and_validate(self.url, allow_private=self.allow_private)
        except Exception as e:  # noqa: BLE001
            self.estado, self.error = "error", f"URL rechazada: {e}"
            return
        ok, motivo = disponible()
        if not ok:
            self.estado, self.error = "error", f"El modo VNC no está disponible ({motivo})."
            return

        try:
            # 1. Pantalla virtual. -nolisten tcp: nadie de afuera habla con el X.
            self._procs.append(subprocess.Popen(
                ["Xvfb", DISPLAY, "-screen", "0", f"{ANCHO}x{ALTO}x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(1.2)

            # 2. Servidor VNC sobre esa pantalla, SOLO en localhost y sin clave: el control de
            #    acceso lo hace la API (sesión + rol) antes de tender el puente WebSocket.
            self._procs.append(subprocess.Popen(
                ["x11vnc", "-display", DISPLAY, "-rfbport", str(VNC_PORT), "-localhost",
                 "-nopw", "-forever", "-shared", "-noxdamage", "-quiet"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            time.sleep(0.8)

            # 3. Chromium CON ventana sobre la pantalla virtual.
            from playwright.sync_api import sync_playwright
            entorno = dict(os.environ, DISPLAY=DISPLAY)
            with sync_playwright() as p:
                lanzar = {
                    "headless": False,
                    "env": entorno,
                    "args": [f"--window-size={ANCHO},{ALTO}", "--window-position=0,0",
                             "--disable-blink-features=AutomationControlled", "--no-first-run"],
                }
                if self.proxy:
                    lanzar["proxy"] = {"server": self.proxy}
                browser = p.chromium.launch(**lanzar)
                opts = {"viewport": None, "locale": self.locale}
                previo = None
                if self.store is not None:
                    try:
                        previo = self.store.load(self.dueno, self.nombre)
                    except Exception:  # noqa: BLE001
                        previo = None
                if previo:
                    opts["storage_state"] = previo
                context = browser.new_context(**opts)
                page = context.new_page()
                page.goto(self.url, timeout=45_000, wait_until="domcontentloaded")
                self.estado = "listo"

                while not self._cerrar.is_set() and not self.vencida():
                    if self._guardar.wait(timeout=0.5):
                        self._guardar.clear()
                        if self.store is not None:
                            # storage_state del CONTEXTO: junta las cookies de todas las
                            # pestañas y popups que hayas abierto durante el login.
                            self.guardada = bool(self.store.save(
                                self.dueno, self.nombre, context.storage_state()))
                        self.estado = "guardado" if self.guardada else "listo"
                        if not self.guardada:
                            self.error = "No se pudo guardar la sesión (¿Redis?)."
                browser.close()
        except Exception as e:  # noqa: BLE001
            self.estado, self.error = "error", f"{type(e).__name__}: {e}"[:200]
        finally:
            self._matar_procesos()
            if self.estado != "error":
                self.estado = "cerrado"


# ---------------------------------------------------------------------------
# Registro: una sola sesión VNC a la vez (Xvfb + Chromium con ventana es caro)
# ---------------------------------------------------------------------------
_SESIONES: dict[str, _SesionVnc] = {}
_LOCK = threading.Lock()


def _limpiar() -> None:
    for tok, s in list(_SESIONES.items()):
        if s.vencida() or s.estado in ("cerrado", "error"):
            s.cerrar()
            _SESIONES.pop(tok, None)


def abrir(url: str, *, nombre: str, dueno: str, store, **kw) -> tuple[str, _SesionVnc]:
    with _LOCK:
        _limpiar()
        if _SESIONES:
            raise VncError("Ya hay una ventana VNC abierta. Cerrá esa y volvé a probar.")
        if not _puerto_libre(VNC_PORT):
            raise VncError("El puerto de VNC está ocupado; probá de nuevo en unos segundos.")
        ses = _SesionVnc(url, nombre=nombre, dueno=dueno, store=store, **kw)
        token = uuid.uuid4().hex
        _SESIONES[token] = ses
        return token, ses


def obtener(token: str, dueno: str) -> _SesionVnc | None:
    s = _SESIONES.get(token)
    if s is None or s.dueno != dueno:
        return None
    return s


def cerrar(token: str, dueno: str) -> bool:
    s = obtener(token, dueno)
    if s is None:
        return False
    s.cerrar()
    _SESIONES.pop(token, None)
    return True
