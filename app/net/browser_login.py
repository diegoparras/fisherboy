"""Login interactivo — el navegador del server, manejado desde tu pantalla. Ver ADR-013.

El problema: X/LinkedIn/Facebook exigen sesión iniciada, y el servidor no tiene tu login. Las
salidas que había eran malas: exportar cookies a mano (feo y se vencen) o automatizar usuario y
clave (se rompe con 2FA, con un captcha, o con cualquier pantalla rara que metan ese día).

La salida buena es que te loguees VOS, con tus manos, en el navegador del server:

    navegador del server  --fotos-->  tu pantalla
                          <--clicks/teclas--  vos

Sin VNC a propósito. Un x11vnc + noVNC serían ~100 MB más en la imagen, otro puerto y más
piezas que mantener. Acá se reusa el Chromium que ya está: se le saca una captura por frame y
se le reinyectan los eventos con la misma API de Playwright que usan las acciones. El resultado
es más liviano y no expone nada nuevo hacia afuera.

Cuando terminás de loguearte, se guarda el `storage_state` (cookies + localStorage) en el store
de sesiones, con el nombre que elegiste. De ahí en más, los jobs con ese nombre entran solos.

Cada sesión es UN navegador vivo (RAM): por eso hay tope de sesiones concurrentes y un TTL que
las cierra solas si te olvidás. La API sync de Playwright no es thread-safe, así que cada sesión
vive en su propio hilo y se le habla por una cola.
"""
from __future__ import annotations

import queue
import threading
import time
import uuid

from ..logging import get_logger
from ..security.ssrf import resolve_and_validate

log = get_logger("fisherboy.browser_login")

# Tamaño del navegador remoto. Fijo a propósito: la UI mapea tus clicks 1:1 contra estas
# medidas, así que si cambia acá tiene que cambiar allá.
ANCHO, ALTO = 1280, 800

MAX_SESIONES = 2          # cada una es un Chromium vivo; no es gratis
TTL_S = 15 * 60           # si te olvidás abierta, se cierra sola
_FRAME_CADA_S = 0.35      # ritmo de captura (suficiente para tipear cómodo)


class LoginError(Exception):
    """No se pudo abrir/manejar el navegador de login."""


class _Sesion:
    """Un navegador vivo esperando que te loguees. Habla por cola, responde con frames."""

    def __init__(self, url: str, *, nombre: str, dueno: str, store, proxy: str = "",
                 allow_private: bool = False, locale: str = "es-AR", user_agent: str = "") -> None:
        self.url = url
        self.nombre = nombre
        self.dueno = dueno
        self.store = store
        self.proxy = proxy
        self.allow_private = allow_private
        self.locale = locale
        self.user_agent = user_agent

        self.estado = "arrancando"        # arrancando | listo | guardado | error | cerrado
        self.error = ""
        self.url_actual = url
        self.creada = time.monotonic()
        self.guardada = False

        self._frame: bytes = b""
        self._lock = threading.Lock()
        self._cmds: queue.Queue = queue.Queue()
        self._cerrar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    # --- lo que ve el cliente -------------------------------------------------
    @property
    def frame(self) -> bytes:
        with self._lock:
            return self._frame

    def enviar(self, cmd: dict) -> None:
        """Encola un evento (click/tecla/scroll/navegar). No bloquea."""
        if self.estado in ("cerrado", "error"):
            raise LoginError("La sesión de login ya no está activa.")
        self._cmds.put(cmd)

    def guardar(self) -> bool:
        """Pide guardar el estado y esperar a que el hilo confirme."""
        self.enviar({"tipo": "guardar"})
        for _ in range(60):                       # hasta ~6 s
            if self.guardada or self.estado in ("error", "cerrado"):
                break
            time.sleep(0.1)
        return self.guardada

    def cerrar(self) -> None:
        self._cerrar.set()

    def vencida(self) -> bool:
        return (time.monotonic() - self.creada) > TTL_S

    # --- el hilo del navegador ------------------------------------------------
    def _aplicar(self, page, cmd: dict) -> None:
        t = cmd.get("tipo")
        if t == "click":
            page.mouse.click(float(cmd.get("x", 0)), float(cmd.get("y", 0)))
        elif t == "doble":
            page.mouse.dblclick(float(cmd.get("x", 0)), float(cmd.get("y", 0)))
        elif t == "texto":
            page.keyboard.type(str(cmd.get("texto", ""))[:500])
        elif t == "tecla":
            page.keyboard.press(str(cmd.get("tecla", ""))[:24])
        elif t == "scroll":
            page.mouse.wheel(0, float(cmd.get("dy", 0)))
        elif t == "navegar":
            destino = str(cmd.get("url", ""))
            # Igual que en las acciones: cada navegación revalida SSRF. Sin esto, el login
            # interactivo sería una ventana para pasear por la red interna del server.
            resolve_and_validate(destino, allow_private=self.allow_private)
            page.goto(destino, timeout=45_000, wait_until="domcontentloaded")
        elif t == "atras":
            page.go_back(timeout=30_000)
        elif t == "recargar":
            page.reload(timeout=45_000)

    def _correr(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover
            self.estado, self.error = "error", "Playwright no está instalado en el servidor."
            return
        try:
            resolve_and_validate(self.url, allow_private=self.allow_private)
        except Exception as e:  # noqa: BLE001
            self.estado, self.error = "error", f"URL rechazada: {e}"
            return

        try:
            with sync_playwright() as p:
                lanzar = {"headless": True}
                if self.proxy:
                    lanzar["proxy"] = {"server": self.proxy}
                browser = p.chromium.launch(**lanzar)
                opts = {"viewport": {"width": ANCHO, "height": ALTO}, "locale": self.locale}
                if self.user_agent:
                    opts["user_agent"] = self.user_agent
                # Si ya había una sesión con ese nombre, se arranca desde ahí: sirve para
                # renovar un login vencido sin empezar de cero.
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
                    try:
                        cmd = self._cmds.get(timeout=_FRAME_CADA_S)
                    except queue.Empty:
                        cmd = None
                    if cmd is not None:
                        if cmd.get("tipo") == "guardar":
                            if self.store is not None:
                                self.guardada = bool(
                                    self.store.save(self.dueno, self.nombre,
                                                    context.storage_state()))
                            self.estado = "guardado" if self.guardada else "listo"
                            if not self.guardada:
                                self.error = "No se pudo guardar la sesión (¿Redis?)."
                        else:
                            try:
                                self._aplicar(page, cmd)
                            except Exception as e:  # noqa: BLE001 — un click fallido no mata todo
                                log.info("login: evento falló", extra={"error": type(e).__name__})
                    try:
                        png = page.screenshot(timeout=8_000)
                        with self._lock:
                            self._frame = png
                        self.url_actual = page.url
                    except Exception:  # noqa: BLE001 — página navegando: se reintenta al toque
                        pass
                browser.close()
        except Exception as e:  # noqa: BLE001
            self.estado, self.error = "error", f"{type(e).__name__}: {e}"[:200]
            return
        if self.estado != "error":
            self.estado = "cerrado"


# ---------------------------------------------------------------------------
# Registro de sesiones vivas
# ---------------------------------------------------------------------------
_SESIONES: dict[str, _Sesion] = {}
_REG_LOCK = threading.Lock()


def _limpiar() -> None:
    """Cierra las vencidas/muertas. Se llama en cada operación: no hace falta un reaper."""
    for tok, s in list(_SESIONES.items()):
        if s.vencida() or s.estado in ("cerrado", "error"):
            s.cerrar()
            if s.vencida() or s.estado in ("cerrado", "error"):
                _SESIONES.pop(tok, None)


def abrir(url: str, *, nombre: str, dueno: str, store, **kw) -> tuple[str, _Sesion]:
    """Abre un navegador para que el usuario se loguee. Devuelve (token, sesión)."""
    with _REG_LOCK:
        _limpiar()
        if len(_SESIONES) >= MAX_SESIONES:
            raise LoginError(
                f"Ya hay {MAX_SESIONES} ventanas de login abiertas. Cerrá una y volvé a probar."
            )
        ses = _Sesion(url, nombre=nombre, dueno=dueno, store=store, **kw)
        token = uuid.uuid4().hex
        _SESIONES[token] = ses
        return token, ses


def obtener(token: str, dueno: str) -> _Sesion | None:
    """La sesión de ESE dueño. Ajeno o inexistente devuelven None (no se filtra cuál es)."""
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


def activas() -> int:
    _limpiar()
    return len(_SESIONES)
