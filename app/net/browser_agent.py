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
from urllib.parse import urljoin, urlsplit

from ..logging import get_logger
from ..security.ssrf import resolve_and_validate
from . import agent_ai, recipe
from .irreversible import irreversible

log = get_logger("fisherboy.browser_agent")

ANCHO, ALTO = 1280, 800
MAX_SESIONES = 2
TTL_S = 20 * 60
MAX_BYTES_ARCHIVO = 20 * 1024 * 1024
MAX_CAPTURAS = 50
MAX_BYTES_CAPTURA = 512 * 1024
# Consultas en vuelo que se anotan en el log de un paso fallido (las más viejas primero).
MAX_PENDIENTES = 5
_CALIDAD_JPEG = 70
# Cada cuanto se mira la pantalla. NO se baja cuando maneja una persona, y vale explicar por
# que, porque la intuicion dice lo contrario:
#
#   Medido contra Chromium real, de aplicar el clic a tener la imagen (dos pasadas, n=15):
#     cliente preguntando cada 150 ms      135 / 165 ms de mediana   (p95 255 / 232)
#     con la espera larga de /frame         50 /  51 ms              (p95  96 /  89)
#     y encima bajando el tick a 80 ms      65 /  54 ms              (p95 109 / 144)
#
#   O sea: lo que saca la demora es la espera larga (Pantalla.esperar), no el ritmo. Con la
#   espera larga, lo que queda es el costo de sacar la captura (~44 ms, el minimo medido);
#   duplicar la cantidad de capturas solo deja al hilo del navegador mas ocupado, y un clic
#   que llega mientras hay una captura en curso espera a que termine. De ahi el p95 peor.
#
#   Lo mismo con la calidad: bajar de 70 a 55 ahorra 8% de bytes (12.1K -> 11.1K promedio en
#   las pantallas de la maqueta), que no justifica el halo de compresion sobre el texto.
#
# Un tick mas corto SI daria una pantalla mas fluida cuando cambia sola (una pagina cargando
# se veria a 12 cuadros/s en vez de 6.6). Si algun dia eso importa mas que la latencia del
# clic, el numero esta aca y la medicion esta arriba.
_TICK_S = 0.15
_TIMEOUT_DEFAULT_S = 15.0
# `objetivo` NO va: es para la IA y se resuelve aparte, sin secretos (sustituir_variables).
# El fetch de la receta, corriendo en la página. Devuelve un objeto plano y nunca tira: así
# Python decide el mensaje. `redirect: "manual"` es lo que no deja seguir una redirección.
_JS_FETCH = """async ({url, ms, max}) => {
  const ctl = new AbortController();
  const reloj = setTimeout(() => ctl.abort(), ms);
  try {
    const r = await fetch(url, {
      method: "GET", credentials: "include", redirect: "manual", signal: ctl.signal,
      headers: {"X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01"},
    });
    if (r.type === "opaqueredirect" || (r.status >= 300 && r.status < 400)) {
      return {redirigio: true, status: r.status};
    }
    const text = await r.text();
    if (text.length > max) return {grande: true, status: r.status};
    return {status: r.status, contentType: r.headers.get("content-type") || "", text};
  } catch (e) {
    return {error: e && e.name === "AbortError" ? "se cumplió el tiempo" : String(e)};
  } finally {
    clearTimeout(reloj);
  }
}"""


def _asomo(texto: str, tipo: str) -> str:
    """Qué vino en vez de JSON, en pocas palabras: el <title> si es HTML, o el primer texto."""
    if not texto.strip():
        return "cuerpo vacío"
    if "html" in tipo or texto.lstrip().startswith("<"):
        m = re.search(r"<title[^>]*>(.*?)</title>", texto, re.I | re.S)
        visible = m.group(1) if m and m.group(1).strip() else re.sub(
            r"<(script|style)\b.*?</\1>|<[^>]+>", " ", texto, flags=re.I | re.S)
        visible = " ".join(visible.split())[:120]
        return f'HTML: "{visible}"' if visible else "HTML"
    return f'"{" ".join(texto.split())[:120]}"'


_CAMPOS_SUSTITUIBLES = ("text", "value", "url", "sel", "contains", "key")

ESTADOS_VIVOS = ("arrancando", "corriendo", "esperando_humano", "humano", "esperando_ok")

# Lo que dice un elemento, para decidir si tocarlo no se deshace (ver irreversible.py).
_INFO_JS = r"""
(el) => {
  if (!el) return {tag: "", type: "", texto: "", ariaLabel: "", enFormulario: false};
  const c = el.closest("button,a,[role=button],input,select,textarea,label") || el;
  return {tag: c.tagName.toLowerCase(), type: (c.getAttribute("type") || "").toLowerCase(),
          texto: (c.innerText || c.value || "").trim().slice(0, 120),
          ariaLabel: (c.getAttribute("aria-label") || "").slice(0, 120), enFormulario: !!c.closest("form")};
}
"""
_PUNTO_JS = "({x, y, activo}) => (" + _INFO_JS + ")(activo ? document.activeElement : document.elementFromPoint(x, y))"


class AgentError(Exception):
    """Pedido inválido a la sesión (tope, estado equivocado)."""


class _Cerrada(Exception):
    """Interno: alguien cerró la sesión mientras corría."""


class _PasoFallido(Exception):
    """Interno: un paso no opcional de la receta falló."""


class _DijoQueNo(Exception):
    """Interno: la persona no dio el OK para algo que no se deshace."""


class Pantalla:
    """La última foto del navegador, su número de orden, y la espera de quien la mira.

    Vive aparte de la sesión porque el que escribe las fotos (el hilo del navegador) y los que
    las miran (los pedidos HTTP) no se conocen entre ellos.

    `esperar` es lo que evita que el cliente pregunte en bucle: en vez de pedir cada 150 ms y
    recibir «no cambió nada», se queda esperando y se lo despierta en el momento en que la foto
    cambia. Para una persona manejando el navegador, esa diferencia es la que se siente.
    """

    def __init__(self) -> None:
        self._img = b""
        self._huella = ""
        self._seq = 0
        self._cerrada = False
        # Una Condition y no un Event: hay que despertar a TODOS los que miran, y cada uno
        # compara contra su propio `desde`.
        self._cond = threading.Condition()

    @property
    def seq(self) -> int:
        with self._cond:
            return self._seq

    @property
    def img(self) -> bytes:
        with self._cond:
            return self._img

    def poner(self, img: bytes) -> bool:
        """Guarda la foto SI cambió, y despierta a los que esperan. Devuelve si cambió.

        La huella es lo que hace que una pantalla quieta no genere tráfico: misma imagen,
        mismo número, nadie se despierta."""
        huella = hashlib.blake2b(img, digest_size=16).hexdigest()
        with self._cond:
            if huella == self._huella:
                return False
            self._img, self._huella = img, huella
            self._seq += 1
            self._cond.notify_all()
            return True

    def esperar(self, desde: int, timeout_s: float) -> int:
        """Espera a que haya una foto más nueva que `desde`. Devuelve el número que hay.

        `timeout_s=0` no espera (es el comportamiento de siempre: decime qué hay ahora).
        Cerrar la pantalla despierta a todos, para que nadie quede colgado hasta que venza."""
        fin = time.monotonic() + max(0.0, timeout_s)
        with self._cond:
            while self._seq <= desde and not self._cerrada:
                restante = fin - time.monotonic()
                if restante <= 0:
                    break
                self._cond.wait(restante)
            return self._seq

    def cerrar(self) -> None:
        with self._cond:
            self._cerrada = True
            self._cond.notify_all()


class _SesionAgente:
    def __init__(self, url: str, *, dueno: str, receta: list, variables: dict, secretos: dict,
                 dominios: list[str], ia: bool, objetivo: str = "", llm_complete=None,
                 allow_private: bool = False, locale: str = "es-AR", session: str = "",
                 mantener: bool = False, store=None) -> None:
        self.url, self.dueno = url, dueno
        self.receta = recipe.validar_receta(receta)
        self.variables = {str(k): str(v) for k, v in (variables or {}).items()}
        self._secretos = {str(k): str(v) for k, v in (secretos or {}).items()}
        self.dominios = [d for d in (dominios or []) if d]
        self.ia = bool(ia and llm_complete)
        self.objetivo = objetivo or ""
        self._llm = llm_complete
        self.allow_private, self.locale = allow_private, locale
        # `session`: el login guardado con ese nombre (el mismo store de browser_login).
        # `mantener`: si la IA no puede, la sesión no se cierra: espera a una persona (o a
        # quien la maneje desde afuera) en vez de terminar en error.
        self.session, self.mantener, self._store = session or "", bool(mantener), store
        self.resumen_texto = ""
        # El OK antes de lo que no se deshace: lo pide el código (irreversible.py), no la IA.
        self.ok_pedido: dict | None = None
        self._decision: str | None = None
        self._ok_evento = threading.Event()
        # Preguntas a la página desde la API (`punto`): se responden en el hilo de la sesión.
        self._consultas: queue.Queue = queue.Queue()

        self.estado = "arrancando"
        self.error = ""
        self.motivo_humano = ""
        self.alerta = ""
        self.url_actual = url
        # XHR/fetch que salieron y no volvieron: si un paso falla esperando algo, lo primero
        # que hay que saber es si la página quedó colgada de una consulta.
        self._en_vuelo: dict = {}
        self.creada = time.monotonic()
        self.log: list[dict] = []
        self.archivos: list[dict] = []
        self._blobs: list[bytes] = []
        # Lo que guardó cada `fetch` con `guardar`, para los `{{res:...}}` de los pasos que siguen.
        self._res: dict[str, object] = {}
        self.capturas: list[dict] = []

        self.pantalla = Pantalla()
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
        return self.pantalla.img

    @property
    def seq(self) -> int:
        return self.pantalla.seq

    def esperar_frame(self, desde: int, timeout_s: float) -> int:
        """Espera a que haya una foto mas nueva que `desde`. Lo usa la espera larga de
        /frame: bloquea un hilo, nunca el bucle de eventos de la API."""
        return self.pantalla.esperar(desde, timeout_s)

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
        self.pantalla.cerrar()   # el que este esperando una foto vuelve ya, no en 5 s
        self._devuelto.set()
        self._ok_evento.set()

    def ok(self) -> None:
        self._decidir("si")

    def no(self) -> None:
        self._decidir("no")

    def _decidir(self, decision: str) -> None:
        if self.estado != "esperando_ok":
            raise AgentError("La sesión no está esperando un OK.")
        self._decision = decision
        self._ok_evento.set()

    def consultar_js(self, js: str, args=None, timeout: float = 3.0):
        """Evalúa `js` en la página, en el hilo de la sesión. Para leer, nunca para actuar."""
        if self.estado not in ESTADOS_VIVOS:
            raise AgentError("La sesión ya terminó.")
        caja: dict = {"ev": threading.Event()}
        self._consultas.put((js, args, caja))
        if not caja["ev"].wait(timeout):
            raise AgentError("La página no respondió a tiempo.")
        if "e" in caja:
            raise AgentError(caja["e"])
        return caja.get("r")

    def punto(self, x: float = 0, y: float = 0, activo: bool = False) -> dict:
        """Qué elemento hay en (x, y), o el que tiene el foco: {tag, type, texto, ariaLabel, enFormulario}."""
        return self.consultar_js(_PUNTO_JS, {"x": float(x), "y": float(y), "activo": bool(activo)})

    def resumen(self) -> dict:
        """Lo que se le puede devolver a cualquiera con acceso: sin secretos ni bytes."""
        return {
            "estado": self.estado, "error": self.error, "motivo_humano": self.motivo_humano,
            "alerta": self.alerta, "url": self.url_actual, "seq": self.seq, "ia": self.ia,
            "log": list(self.log), "archivos": list(self.archivos), "resumen": self.resumen_texto,
            "ok_pedido": dict(self.ok_pedido) if self.ok_pedido else None,
            # Solo la cantidad: los JSON van por /api/agent/{t}/captures (el resumen se sondea).
            "capturas": len(self.capturas),
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
                v = recipe.sustituir_res(out[k], self._res, url=(k == "url"))
                out[k] = recipe.sustituir(v, self.variables, self._secretos)
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
            self.pantalla.poner(img)   # decide si cambio y despierta a los que esperan
            self.url_actual = self._limpiar(page.url)
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
            self._atender_consultas()
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

    @staticmethod
    def _es_subframe(request) -> bool:
        """¿La navegación es de un iframe incrustado y no de la pestaña?

        Playwright marca la carga de un iframe como navegación, igual que la de la pestaña.
        Pero un iframe ajeno no lo pidió la receta: lo trae la página (un video incrustado,
        un mapa, un widget de chat). Tratarlo como un intento de salir del dominio tumbaba
        la sesión por algo que la persona ni pidió ni ve.

        Si no se puede saber de qué frame viene —`request.frame` puede tirar, p. ej. con
        service workers— se lo trata como la pestaña, que es lo conservador: se bloquea.
        """
        try:
            return request.frame.parent_frame is not None
        except Exception:  # noqa: BLE001
            return False

    def _guardia(self, route, request) -> None:
        """Toda navegación pasa por acá: allowlist de dominios + SSRF."""
        try:
            if request.is_navigation_request():
                self._chequear_destino(request.url)
            route.continue_()
        except Exception:  # noqa: BLE001
            # El iframe ajeno se corta igual, pero en silencio: sin `_bloqueado` no hay paso
            # que falle. La pestaña yéndose a otro dominio sí tiene que frenar la sesión.
            if not self._es_subframe(request):
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

    def _salio(self, req) -> None:
        try:
            if req.resource_type in ("xhr", "fetch"):
                if len(self._en_vuelo) >= 200:   # una página que nunca cierra las suyas
                    self._en_vuelo.pop(next(iter(self._en_vuelo)))
                self._en_vuelo[req] = time.monotonic()
        except Exception:  # noqa: BLE001
            pass

    def _volvio(self, req) -> None:
        self._en_vuelo.pop(req, None)

    def _pendientes(self) -> list[str]:
        """Las consultas colgadas, como `GET host/ruta (3.2 s)`. Sin la query: puede traer
        datos de la persona, y para diagnosticar alcanza con saber a dónde fue."""
        ahora, out = time.monotonic(), []
        for req, desde in sorted(self._en_vuelo.items(), key=lambda kv: kv[1])[:MAX_PENDIENTES]:
            try:
                u = urlsplit(req.url)
                out.append(self._limpiar(f"{req.method} {u.netloc}{u.path} ({ahora - desde:.1f} s)"))
            except Exception:  # noqa: BLE001
                pass
        return out

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

    def _atender_consultas(self) -> None:
        while True:
            try:
                js, args, caja = self._consultas.get_nowait()
            except queue.Empty:
                return
            try:
                caja["r"] = self._page.evaluate(js, args) if args is not None else self._page.evaluate(js)
            except Exception as e:  # noqa: BLE001
                caja["e"] = self._limpiar(str(e).splitlines()[0][:160] if str(e) else type(e).__name__)
            finally:
                caja["ev"].set()

    def _modo_ok(self, texto: str, motivo: str) -> bool:
        """Frena hasta que una persona diga sí o no. True = sí."""
        self.estado, self.ok_pedido, self._decision = "esperando_ok", {"texto": texto, "motivo": motivo}, None
        self._ok_evento.clear()
        self._anotar({"do": "ok", "texto": texto, "motivo": motivo, "origen": "humano"})
        self._bombear(hasta=self._ok_evento)
        self._vivo()
        decision = self._decision
        self.estado, self.ok_pedido = "corriendo", None
        return decision == "si"

    def _frenar_si_no_se_deshace(self, info: dict, accion: str, motivo: str) -> None:
        if irreversible(info or {}, accion):
            texto = (info or {}).get("texto") or (info or {}).get("ariaLabel") or (
                "Enter en el formulario" if accion == "enter" else "el botón")
            if not self._modo_ok(str(texto)[:120], motivo):
                raise _DijoQueNo()

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

    def _cargar_sesion(self):
        if not (self.session and self._store is not None):
            return None
        try:
            return self._store.load(self.dueno, self.session)
        except Exception:  # noqa: BLE001 — sin store, se arranca sin login
            return None

    def _guardar_sesion(self) -> None:
        if not (self.session and self._store is not None and self._ctx is not None):
            return
        try:
            self._store.save(self.dueno, self.session, self._ctx.storage_state())
        except Exception as e:  # noqa: BLE001 — no guardar no corta la tarea
            log.info("agente: no se pudo guardar la sesión", extra={"error": type(e).__name__})

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

    def _solo_campo_de_clave(self, sel: str) -> None:
        tipo = self._page.eval_on_selector(sel, "e => (e.type || '').toLowerCase()")
        if tipo != "password":
            raise _PasoFallido("la receta intentó escribir una clave en un campo que no es de clave")

    def _ejecutar(self, paso: dict, original: dict | None = None) -> dict:
        """Corre UN paso ya resuelto. Devuelve datos extra para el log. Lanza ante falla."""
        do, page = paso["do"], self._page
        if do in ("type", "fill") and "{{secret:" in str((original or paso).get("text", "")):
            self._solo_campo_de_clave(paso["sel"])
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
        elif do == "fetch":
            extra.update(self._fetch(paso))
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

    def _fetch(self, paso: dict) -> dict:
        """GET a `url` desde ADENTRO de la página: sale con las cookies, el Origin y el Referer
        de la sesión, como las consultas AJAX del propio portal.

        El guardia de dominios solo mira navegaciones, y esto no lo es: por eso el destino se
        chequea acá (allowlist + SSRF) y las redirecciones no se siguen — una podría llevar el
        pedido, con la sesión puesta, a un host que nadie chequeó."""
        url = urljoin(self._page.url, paso["url"])
        if urlsplit(url).scheme not in ("http", "https"):
            raise _PasoFallido("fetch solo pide http(s)")
        self._chequear_destino(url)
        r = self._page.evaluate(_JS_FETCH, {"url": url, "ms": self._ms(paso, 30.0),
                                            "max": MAX_BYTES_ARCHIVO})
        if r.get("error"):
            raise _PasoFallido(f"el fetch no salió: {r['error'][:150]}")
        if r.get("redirigio"):
            raise _PasoFallido("la respuesta redirigió a otro lado y un fetch no sigue "
                               "redirecciones (puede ser la sesión vencida)")
        if r.get("grande"):
            raise _PasoFallido(f"la respuesta pesa más de {MAX_BYTES_ARCHIVO} bytes")
        texto, status = r.get("text") or "", int(r.get("status") or 0)
        cuerpo = texto.encode("utf-8")
        datos = None
        try:
            datos = json.loads(texto)
        except ValueError:
            pass
        if not 200 <= status < 300 or datos is None:
            que = "la respuesta no es JSON" if datos is None else "el pedido falló"
            asoma = _asomo(texto, r.get("contentType") or "")
            raise _PasoFallido(f"{que} (HTTP {status}{', ' + asoma if asoma else ''})")
        if paso.get("guardar"):
            self._res[str(paso["guardar"])] = datos
        extra = {"status": status, "bytes": len(cuerpo)}
        if paso.get("name"):
            nombre = str(paso["name"])
            self._blobs.append(cuerpo)
            self.archivos.append({"n": len(self._blobs) - 1, "nombre": nombre,
                                  "tipo": "application/json", "bytes": len(cuerpo)})
            extra["nombre"] = nombre
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
            # Todo lo que va al modelo pasa por _limpiar ANTES de enmascarar: si un secreto
            # quedo en un campo de texto o en la URL, el modelo ve «***» y no el valor.
            for el in elementos:
                for k in ("texto", "valor", "placeholder", "id", "nombre"):
                    if el.get(k):
                        el[k], self._mapeo = agent_ai.enmascarar(self._limpiar(str(el[k])), self._mapeo)
            url_m, self._mapeo = agent_ai.enmascarar(self._limpiar(self._page.url), self._mapeo)
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
                # Lo que encontró, para quien pidió la tarea: con los datos de verdad.
                self.resumen_texto = agent_ai.restaurar(
                    self._limpiar(str(accion.get("motivo") or ""))[:2000], self._mapeo)
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
            except _Cerrada:
                raise
            except _DijoQueNo:
                self._anotar({**entrada, "ok": False, "error": "lo negaron desde la sesión"})
                self.resumen_texto = "No lo hice: quien maneja la sesión dijo que no."
                return True
            except Exception as e:  # noqa: BLE001
                entrada.update(ok=False, error=self._limpiar(str(e).splitlines()[0][:160]))
            self._anotar(entrada)
            historial.append({k: v for k, v in entrada.items() if k != "origen"})
            self._foto()
        return False

    def _accion_ia(self, a: dict) -> None:
        page, acc = self._page, a["accion"]
        sel = f'[data-fb-ref="{a["ref"]}"]' if "ref" in a else ""
        motivo = agent_ai.restaurar(str(a.get("motivo") or ""), self._mapeo)[:300]
        if acc == "click":
            self._frenar_si_no_se_deshace(page.eval_on_selector(sel, _INFO_JS), "click", motivo)
            page.click(sel, timeout=8_000)
        elif acc == "type":
            crudo = agent_ai.restaurar(str(a["texto"]), self._mapeo)
            if "{{secret:" in crudo:
                # Un secreto solo va a un campo de clave: si la IA se equivoca de campo (o el
                # texto de la página la empuja a otro), el valor terminaría a la vista.
                tipo = page.eval_on_selector(sel, "e => (e.type || '').toLowerCase()")
                if tipo != "password":
                    raise _PasoFallido("la IA intentó escribir una clave en un campo que no es de clave")
            page.fill(sel, recipe.sustituir(crudo, self.variables, self._secretos), timeout=8_000)
        elif acc == "select":
            page.select_option(sel, agent_ai.restaurar(str(a["valor"]), self._mapeo), timeout=8_000)
        elif acc == "press":
            tecla = str(a["tecla"])[:24]
            if tecla.lower() == "enter":
                activo = page.evaluate("() => (" + _INFO_JS + ")(document.activeElement)")
                self._frenar_si_no_se_deshace(activo, "enter", motivo)
            page.keyboard.press(tecla)
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
                extra = self._ejecutar(self._resolver(paso), paso)
                entrada.update(extra, ok=True)
                self._anotar(entrada)
            except _Cerrada:
                raise
            except Exception as e:  # noqa: BLE001
                motivo = self._limpiar(str(e).splitlines()[0][:200] if str(e) else type(e).__name__)
                # La foto del momento del fallo, antes de que la IA toque nada o la sesión
                # cierre: sin esto, la última imagen es la del paso anterior, que salió bien.
                self._foto()
                entrada.update(ok=False, error=motivo, pagina=self.url_actual,
                               pendientes=self._pendientes())
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
                    opts = {"viewport": {"width": ANCHO, "height": ALTO}, "locale": self.locale,
                            "accept_downloads": True}
                    previo = self._cargar_sesion()
                    if previo:
                        opts["storage_state"] = previo
                    self._ctx = browser.new_context(**opts)
                    self._ctx.route("**/*", self._guardia)
                    self._ctx.on("response", self._al_responder)
                    self._ctx.on("request", self._salio)
                    self._ctx.on("requestfinished", self._volvio)
                    self._ctx.on("requestfailed", self._volvio)
                    self._page = self._ctx.new_page()
                    self._page.goto(self.url, timeout=45_000, wait_until="domcontentloaded")
                    self.estado = "corriendo"
                    self._foto()
                    try:
                        try:
                            if self.receta:
                                self._correr_receta()
                            elif self.objetivo:
                                if not self.ia:
                                    raise _PasoFallido("sin receta ni IA no hay nada que hacer")
                                if not self._agente(self.objetivo):
                                    raise _PasoFallido("la IA no pudo cumplir el objetivo")
                        except _PasoFallido as e:
                            if not self.mantener:
                                raise
                            # Sigue viva: quien la maneja desde afuera (o una persona) toma
                            # el control; cuando lo devuelve, la sesión termina.
                            motivo = self._limpiar(str(e).splitlines()[0][:200] if str(e) else "")
                            self._modo_humano("esperando_humano", "La IA no pudo: " + motivo)
                        self._foto()
                        self.estado = "terminado"
                        self._guardar_sesion()
                    except _Cerrada:
                        self._guardar_sesion()
                        raise
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
          locale: str = "es-AR", session: str = "", mantener: bool = False,
          store=None) -> tuple[str, _SesionAgente]:
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
                                allow_private=allow_private, locale=locale, session=session,
                                mantener=mantener, store=store)
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
