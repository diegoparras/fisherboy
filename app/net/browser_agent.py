"""Sesión de agente: un navegador vivo que corre una receta, cae a la IA y deja entrar a un humano.

Es el hermano de `browser_login` (misma vista en vivo por frames JPEG, mismo registro con
dueño y TTL), pero en vez de esperar que te loguees, trabaja solo:

    receta (recorrido fijo, sin IA)  ->  si un paso falla y la sesión lo permite, la IA intenta
                                         cumplir el objetivo de ese paso y se sigue con la receta
    human_if / la IA pide ayuda      ->  "esperando_humano": vos resolvés (captcha, 2FA) y devolvés
    "tomar el control"               ->  "humano" en cualquier momento; "devolver" y sigue

Lo que produce: log de pasos (con los placeholders, nunca los valores), archivos descargados
(en memoria, con tope), respuestas JSON capturadas y la URL final. Ver docs/AGENT.md.

Seguridad:
- Toda navegación (receta, IA o humano, incluidas pestañas nuevas) pasa por la allowlist de
  dominios de la sesión y por el chequeo SSRF. Fuera de la lista, se aborta el pedido.
- Los secretos viven solo en la memoria de esta sesión, se sustituyen al ejecutar cada paso y
  se borran al terminar. Los mensajes de error se limpian por si un valor se colara.
- La API sync de Playwright no es thread-safe: todo lo del navegador pasa en el hilo de la
  sesión; la API le habla por una cola y por eventos.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

from ..logging import get_logger
from ..security.ssrf import resolve_and_validate
from . import agent_ai, recipe

log = get_logger("fisherboy.browser_agent")

ANCHO, ALTO = 1280, 800
MAX_SESIONES = 2
TTL_S = 20 * 60
MAX_BYTES_ARCHIVO = 20 * 1024 * 1024
MAX_CAPTURAS = 50
MAX_BYTES_CAPTURA = 512 * 1024
_CALIDAD_JPEG = 70
_TICK_S = 0.15
_TIMEOUT_DEFAULT_S = 15.0
# `objetivo` NO va: es para la IA y se resuelve aparte, sin secretos (sustituir_variables).
_CAMPOS_SUSTITUIBLES = ("text", "value", "url", "sel", "contains", "key")

ESTADOS_VIVOS = ("arrancando", "corriendo", "esperando_humano", "humano")


class AgentError(Exception):
    """Pedido inválido a la sesión (tope, estado equivocado)."""


class _Cerrada(Exception):
    """Interno: alguien cerró la sesión mientras corría."""


class _PasoFallido(Exception):
    """Interno: un paso no opcional de la receta falló."""


class _SesionAgente:
    def __init__(self, url: str, *, dueno: str, receta: list, variables: dict, secretos: dict,
                 dominios: list[str], ia: bool, objetivo: str = "", llm_complete=None,
                 allow_private: bool = False, locale: str = "es-AR") -> None:
        self.url, self.dueno = url, dueno
        self.receta = recipe.validar_receta(receta)
        self.variables = {str(k): str(v) for k, v in (variables or {}).items()}
        self._secretos = {str(k): str(v) for k, v in (secretos or {}).items()}
        self.dominios = [d for d in (dominios or []) if d]
        self.ia = bool(ia and llm_complete)
        self.objetivo = objetivo or ""
        self._llm = llm_complete
        self.allow_private, self.locale = allow_private, locale

        self.estado = "arrancando"
        self.error = ""
        self.motivo_humano = ""
        self.alerta = ""
        self.url_actual = url
        self.creada = time.monotonic()
        self.log: list[dict] = []
        self.archivos: list[dict] = []
        self._blobs: list[bytes] = []
        self.capturas: list[dict] = []

        self._frame = b""
        self._huella = ""
        self.seq = 0
        self._lock = threading.Lock()
        self._cmds: queue.Queue = queue.Queue()
        self._pedido_humano = threading.Event()
        self._devuelto = threading.Event()
        self._cerrar = threading.Event()
        self._patron: re.Pattern | None = None
        self._bloqueado = ""
        self._mapeo: dict = {}
        self._page = None
        self._ctx = None
        self._hilo = threading.Thread(target=self._correr, daemon=True)
        self._hilo.start()

    # --- lo que usa la API -----------------------------------------------------
    @property
    def frame(self) -> bytes:
        with self._lock:
            return self._frame

    def blob(self, i: int) -> bytes:
        return self._blobs[i]

    def vencida(self) -> bool:
        return (time.monotonic() - self.creada) > TTL_S

    def enviar(self, cmd: dict) -> None:
        """Evento humano (click/tecla/...). Solo cuando la sesión está en manos de una persona."""
        if self.estado not in ("humano", "esperando_humano"):
            raise AgentError("La sesión no está en modo humano: tomá el control primero.")
        self._cmds.put(dict(cmd))

    def tomar_control(self) -> None:
        if self.estado not in ESTADOS_VIVOS:
            raise AgentError("La sesión ya terminó.")
        self._devuelto.clear()
        self._pedido_humano.set()

    def devolver(self) -> None:
        self._pedido_humano.clear()
        self._devuelto.set()

    def cerrar(self) -> None:
        self._cerrar.set()
        self._devuelto.set()

    def resumen(self) -> dict:
        """Lo que se le puede devolver a cualquiera con acceso: sin secretos ni bytes."""
        return {
            "estado": self.estado, "error": self.error, "motivo_humano": self.motivo_humano,
            "alerta": self.alerta, "url": self.url_actual, "seq": self.seq, "ia": self.ia,
            "log": list(self.log), "archivos": list(self.archivos),
            "capturas": list(self.capturas),
        }

    # --- utilidades del hilo ---------------------------------------------------
    def _limpiar(self, s: str) -> str:
        for v in self._secretos.values():
            if v:
                s = s.replace(v, recipe_redactado())
        return s

    def _resolver(self, paso: dict) -> dict:
        out = dict(paso)
        for k in _CAMPOS_SUSTITUIBLES:
            if isinstance(out.get(k), str):
                out[k] = recipe.sustituir(out[k], self.variables, self._secretos)
        return out

    def _anotar(self, entrada: dict) -> None:
        self.log.append({k: (self._limpiar(v) if isinstance(v, str) else v)
                         for k, v in entrada.items()})

    def _vivo(self) -> None:
        if self._cerrar.is_set() or self.vencida():
            raise _Cerrada()

    def _foto(self) -> None:
        page = self._page
        if page is None:
            return
        try:
            img = page.screenshot(timeout=8_000, type="jpeg", quality=_CALIDAD_JPEG)
            huella = hashlib.blake2b(img, digest_size=16).hexdigest()
            with self._lock:
                if huella != self._huella:
                    self._frame, self._huella = img, huella
                    self.seq += 1
            self.url_actual = page.url
        except Exception:  # noqa: BLE001 — navegando: la próxima sale
            pass

    def _bombear(self, segundos: float | None = None, hasta: threading.Event | None = None) -> None:
        """Procesa eventos humanos y saca fotos hasta que pase el tiempo o se dé el evento."""
        fin = time.monotonic() + segundos if segundos is not None else None
        while True:
            self._vivo()
            if hasta is not None and hasta.is_set():
                return
            if fin is not None and time.monotonic() >= fin:
                return
            try:
                cmd = self._cmds.get(timeout=_TICK_S)
            except queue.Empty:
                cmd = None
            if cmd is not None and self.estado in ("humano", "esperando_humano"):
                try:
                    self._aplicar_humano(cmd)
                except Exception as e:  # noqa: BLE001 — un click fallido no corta nada
                    log.info("agente: evento humano falló", extra={"error": type(e).__name__})
            self._foto()

    def _aplicar_humano(self, cmd: dict) -> None:
        page, t = self._page, cmd.get("tipo")
        if t == "click":
            page.mouse.click(float(cmd.get("x", 0)), float(cmd.get("y", 0)))
        elif t == "doble":
            page.mouse.dblclick(float(cmd.get("x", 0)), float(cmd.get("y", 0)))
        elif t == "click_sel":
            page.click(str(cmd.get("sel", "")), timeout=5_000)
        elif t == "texto":
            page.keyboard.type(str(cmd.get("texto", ""))[:500])
        elif t == "tecla":
            page.keyboard.press(str(cmd.get("tecla", ""))[:24])
        elif t == "scroll":
            page.mouse.wheel(0, float(cmd.get("dy", 0)))
        elif t == "navegar":
            destino = str(cmd.get("url", ""))
            self._chequear_destino(destino)
            page.goto(destino, timeout=45_000, wait_until="domcontentloaded")
        elif t == "atras":
            page.go_back(timeout=30_000)
        elif t == "recargar":
            page.reload(timeout=45_000)
        self._adoptar_pestana_nueva()

    def _chequear_destino(self, url: str) -> None:
        if not recipe.dominio_permitido(url, self.dominios):
            host = urlsplit(url).hostname or url
            raise _PasoFallido(f"dominio no permitido en esta sesión: {host}")
        if not url.startswith("about:"):
            resolve_and_validate(url, allow_private=self.allow_private)

    def _guardia(self, route, request) -> None:
        """Toda navegación pasa por acá: allowlist de dominios + SSRF."""
        try:
            if request.is_navigation_request():
                self._chequear_destino(request.url)
            route.continue_()
        except Exception:  # noqa: BLE001
            self._bloqueado = request.url
            try:
                route.abort()
            except Exception:  # noqa: BLE001
                pass

    def _al_responder(self, resp) -> None:
        if self._patron is None or len(self.capturas) >= MAX_CAPTURAS:
            return
        try:
            if not self._patron.search(resp.url):
                return
            if "json" not in (resp.headers.get("content-type") or ""):
                return
            cuerpo = resp.body()
            if len(cuerpo) > MAX_BYTES_CAPTURA:
                return
            self.capturas.append({"url": resp.url[:500], "json": json.loads(cuerpo)})
        except Exception:  # noqa: BLE001
            pass

    def _adoptar_pestana_nueva(self) -> bool:
        paginas = [p for p in self._ctx.pages if not p.is_closed()]
        if self._page not in paginas:
            if paginas:
                self._page = paginas[-1]
                return True
            return False
        nuevas = paginas[paginas.index(self._page) + 1:]
        if not nuevas:
            return False
        self._page = nuevas[-1]
        try:
            self._page.wait_for_load_state("domcontentloaded", timeout=30_000)
            self._page.bring_to_front()
        except Exception:  # noqa: BLE001
            pass
        return True

    def _modo_humano(self, estado: str, motivo: str) -> None:
        self.estado, self.motivo_humano = estado, motivo
        self._anotar({"do": "humano", "motivo": motivo, "origen": "humano"})
        self._devuelto.clear()
        self._bombear(hasta=self._devuelto)
        self._vivo()
        self._pedido_humano.clear()
        self._adoptar_pestana_nueva()
        self.estado, self.motivo_humano = "corriendo", ""

    def _si_piden_control(self) -> None:
        if self._pedido_humano.is_set():
            self._modo_humano("humano", "Control tomado por una persona")

    def _alerta_visible(self) -> str:
        try:
            for el in self._page.query_selector_all("[role=alert], .alert-danger, .error"):
                txt = (el.inner_text() or "").strip()
                if txt and el.is_visible():
                    return self._limpiar(txt[:200])
        except Exception:  # noqa: BLE001
            pass
        return ""

    # --- pasos -----------------------------------------------------------------
    def _ms(self, paso: dict, default_s: float = _TIMEOUT_DEFAULT_S) -> int:
        try:
            return int(float(paso.get("timeout_s", default_s)) * 1000)
        except (TypeError, ValueError):
            return int(default_s * 1000)

    def _ejecutar(self, paso: dict) -> dict:
        """Corre UN paso ya resuelto. Devuelve datos extra para el log. Lanza ante falla."""
        do, page = paso["do"], self._page
        extra: dict = {}
        if do == "wait":
            self._bombear(segundos=float(paso.get("s", 1) or 0))
        elif do == "wait_url":
            fin = time.monotonic() + self._ms(paso, 20.0) / 1000
            while paso["contains"] not in self._page.url:
                if time.monotonic() >= fin:
                    raise _PasoFallido(f"la URL no llegó a contener '{paso['contains']}'")
                self._bombear(segundos=0.2)
                self._adoptar_pestana_nueva()
        elif do == "switch_tab":
            fin = time.monotonic() + self._ms(paso, 10.0) / 1000
            while not self._adoptar_pestana_nueva():
                if time.monotonic() >= fin:
                    raise _PasoFallido("no se abrió ninguna pestaña nueva")
                self._bombear(segundos=0.2)
            self._chequear_destino(self._page.url)
            extra["url"] = self._page.url
        elif do == "download":
            with page.expect_download(timeout=self._ms(paso, 60.0)) as info:
                page.click(paso["sel"], timeout=self._ms(paso))
            d = info.value
            ruta = d.path()
            tam = os.path.getsize(ruta)
            if tam > MAX_BYTES_ARCHIVO:
                raise _PasoFallido(f"la descarga pesa {tam} bytes (tope {MAX_BYTES_ARCHIVO})")
            with open(ruta, "rb") as fh:
                datos = fh.read()
            nombre = paso.get("name") or d.suggested_filename or f"descarga-{len(self._blobs) + 1}"
            tipo = mimetypes.guess_type(nombre)[0] or "application/octet-stream"
            self._blobs.append(datos)
            self.archivos.append({"n": len(self._blobs) - 1, "nombre": nombre, "tipo": tipo,
                                  "bytes": tam})
            extra.update(nombre=nombre, bytes=tam)
        elif do == "capture":
            self._patron = re.compile(str(paso["pattern"]))
        elif do == "human_if":
            try:
                visible = page.locator(paso["sel"]).first.is_visible()
            except Exception:  # noqa: BLE001
                visible = False
            extra["visible"] = visible
            if visible:
                self._modo_humano("esperando_humano",
                                  str(paso.get("motivo") or "La página pide a una persona."))
        elif do == "goal":
            if not self.ia:
                raise _PasoFallido("el paso necesita IA y la sesión no la tiene habilitada")
            if not self._agente(paso["objetivo"]):
                raise _PasoFallido("la IA no pudo cumplir el objetivo")
        elif do == "goto":
            self._chequear_destino(paso["url"])
            page.goto(paso["url"], timeout=self._ms(paso, 45.0),
                      wait_until=str(paso.get("wait_until") or "domcontentloaded"))
        else:
            from ..fetchers.actions import run_actions
            out = run_actions(page, [paso], allow_private=self.allow_private,
                              default_timeout_s=_TIMEOUT_DEFAULT_S,
                              max_artifact_bytes=MAX_BYTES_ARCHIVO)
            for nombre, blob in out.artifacts.items():
                self._blobs.append(blob)
                self.archivos.append({"n": len(self._blobs) - 1, "nombre": nombre,
                                      "tipo": mimetypes.guess_type(nombre)[0] or "image/png",
                                      "bytes": len(blob)})
            if out.failed:
                raise _PasoFallido(out.log[-1].get("error", out.failed) if out.log else out.failed)
        self._adoptar_pestana_nueva()
        if self._bloqueado:
            host = urlsplit(self._bloqueado).hostname or self._bloqueado
            self._bloqueado = ""
            raise _PasoFallido(f"dominio no permitido en esta sesión: {host}")
        return extra

    # --- la IA -----------------------------------------------------------------
    def _agente(self, objetivo: str) -> bool:
        """Bucle observar -> decidir -> actuar. True si la IA declara el objetivo cumplido."""
        objetivo_m, self._mapeo = agent_ai.enmascarar(
            recipe.sustituir_variables(objetivo, self.variables), self._mapeo)
        historial: list[dict] = []
        for _ in range(agent_ai.MAX_PASOS_IA):
            self._vivo()
            self._si_piden_control()
            try:
                elementos = self._page.evaluate(agent_ai.OBSERVAR_JS)
            except Exception:  # noqa: BLE001 — navegando
                self._bombear(segundos=0.5)
                continue
            for el in elementos:
                for k in ("texto", "valor", "placeholder"):
                    if el.get(k):
                        el[k], self._mapeo = agent_ai.enmascarar(str(el[k]), self._mapeo)
            url_m, self._mapeo = agent_ai.enmascarar(self._page.url, self._mapeo)
            system, user = agent_ai.armar_prompt(objetivo_m, url_m, elementos, historial,
                                                 sorted(self._secretos))
            try:
                accion = agent_ai.parsear_accion(self._llm(system, user))
            except Exception as e:  # noqa: BLE001 — LLMError o red: se anota y se reintenta
                historial.append({"error": str(e)[:160]})
                self._anotar({"do": "ia", "ok": False, "error": str(e)[:160], "origen": "ia"})
                continue
            entrada = {"do": f"ia:{accion['accion']}", "origen": "ia",
                       **{k: v for k, v in accion.items() if k != "accion"}}
            if accion["accion"] == "done":
                self._anotar({**entrada, "ok": True})
                return True
            if accion["accion"] == "human":
                self._anotar({**entrada, "ok": True})
                self._modo_humano("esperando_humano", str(accion.get("motivo") or
                                                          "La IA pide ayuda de una persona."))
                historial.append({**accion, "resultado": "resolvió una persona"})
                continue
            try:
                self._accion_ia(accion)
                entrada["ok"] = True
            except Exception as e:  # noqa: BLE001
                entrada.update(ok=False, error=self._limpiar(str(e).splitlines()[0][:160]))
            self._anotar(entrada)
            historial.append({k: v for k, v in entrada.items() if k != "origen"})
            self._foto()
        return False

    def _accion_ia(self, a: dict) -> None:
        page, acc = self._page, a["accion"]
        sel = f'[data-fb-ref="{a["ref"]}"]' if "ref" in a else ""
        if acc == "click":
            page.click(sel, timeout=8_000)
        elif acc == "type":
            texto = recipe.sustituir(agent_ai.restaurar(str(a["texto"]), self._mapeo),
                                     self.variables, self._secretos)
            page.fill(sel, texto, timeout=8_000)
        elif acc == "select":
            page.select_option(sel, agent_ai.restaurar(str(a["valor"]), self._mapeo), timeout=8_000)
        elif acc == "press":
            page.keyboard.press(str(a["tecla"])[:24])
        elif acc == "goto":
            destino = agent_ai.restaurar(str(a["url"]), self._mapeo)
            self._chequear_destino(destino)
            page.goto(destino, timeout=45_000, wait_until="domcontentloaded")
        elif acc == "wait":
            self._bombear(segundos=1.5)
        self._bombear(segundos=0.3)
        self._adoptar_pestana_nueva()
        if self._bloqueado:
            host = urlsplit(self._bloqueado).hostname or self._bloqueado
            self._bloqueado = ""
            raise _PasoFallido(f"dominio no permitido en esta sesión: {host}")

    # --- el hilo ---------------------------------------------------------------
    def _correr_receta(self) -> None:
        for i, paso in enumerate(self.receta):
            self._vivo()
            self._si_piden_control()
            entrada = {"n": i + 1, **recipe.redactar(paso), "origen": "receta"}
            try:
                extra = self._ejecutar(self._resolver(paso))
                entrada.update(extra, ok=True)
                self._anotar(entrada)
            except _Cerrada:
                raise
            except Exception as e:  # noqa: BLE001
                motivo = self._limpiar(str(e).splitlines()[0][:200] if str(e) else type(e).__name__)
                entrada.update(ok=False, error=motivo)
                self._anotar(entrada)
                if paso.get("optional"):
                    continue
                objetivo = paso.get("objetivo") or self.objetivo
                if self.ia and objetivo and self._agente(objetivo):
                    continue
                self.alerta = self._alerta_visible()
                raise _PasoFallido(f"paso #{i + 1} ({paso['do']}): {motivo}") from None
            self._foto()

    def _correr(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:  # pragma: no cover
            self.estado, self.error = "error", "Playwright no está instalado en el servidor."
            return
        try:
            self._chequear_destino(self.url)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                try:
                    self._ctx = browser.new_context(
                        viewport={"width": ANCHO, "height": ALTO}, locale=self.locale,
                        accept_downloads=True)
                    self._ctx.route("**/*", self._guardia)
                    self._ctx.on("response", self._al_responder)
                    self._page = self._ctx.new_page()
                    self._page.goto(self.url, timeout=45_000, wait_until="domcontentloaded")
                    self.estado = "corriendo"
                    self._foto()
                    if self.receta:
                        self._correr_receta()
                    elif self.objetivo:
                        if not self.ia:
                            raise _PasoFallido("sin receta ni IA no hay nada que hacer")
                        if not self._agente(self.objetivo):
                            raise _PasoFallido("la IA no pudo cumplir el objetivo")
                    self._foto()
                    self.estado = "terminado"
                finally:
                    browser.close()
        except _Cerrada:
            self.estado = "cerrado"
        except Exception as e:  # noqa: BLE001
            self.estado = "error"
            self.error = self._limpiar(str(e).splitlines()[0][:300] if str(e) else type(e).__name__)
        finally:
            self._secretos.clear()
            self._page = None


def recipe_redactado() -> str:
    from ..fetchers.actions import REDACTED
    return REDACTED


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------
_SESIONES: dict[str, _SesionAgente] = {}
_REG_LOCK = threading.Lock()


def _limpiar_registro() -> None:
    """Saca las vencidas. Las terminadas se quedan hasta el TTL: hay que poder bajar archivos."""
    for tok, s in list(_SESIONES.items()):
        if s.vencida():
            s.cerrar()
            _SESIONES.pop(tok, None)


def abrir(url: str, *, dueno: str, receta: list, variables: dict | None = None,
          secretos: dict | None = None, dominios: list[str] | None = None, ia: bool = False,
          objetivo: str = "", llm_complete=None, allow_private: bool = False,
          locale: str = "es-AR") -> tuple[str, _SesionAgente]:
    with _REG_LOCK:
        _limpiar_registro()
        vivas = [s for s in _SESIONES.values() if s.estado in ESTADOS_VIVOS]
        if len(vivas) >= MAX_SESIONES:
            raise AgentError(f"Ya hay {MAX_SESIONES} sesiones de agente corriendo. "
                             "Esperá a que termine una.")
        try:
            ses = _SesionAgente(url, dueno=dueno, receta=receta, variables=variables or {},
                                secretos=secretos or {}, dominios=list(dominios or []), ia=ia,
                                objetivo=objetivo, llm_complete=llm_complete,
                                allow_private=allow_private, locale=locale)
        except ValueError as e:
            raise AgentError(str(e)) from e
        token = uuid.uuid4().hex
        _SESIONES[token] = ses
        return token, ses


def obtener(token: str, dueno: str) -> _SesionAgente | None:
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
