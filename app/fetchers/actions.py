"""Acciones de browser — operar la página antes de extraerla. Ver ADR-011.

Fisherboy extrae; con esto además *opera*: cerrar el banner de cookies, loguearse, apretar
"cargar más", scrollear un feed infinito, sacar un screenshot. Es la puerta a lo que se hacía
solo escribiendo Playwright a mano, pero declarativa: una lista de pasos en el job, sin código.

    "actions": [
      {"do": "click", "sel": "#aceptar", "optional": true},
      {"do": "type",  "sel": "#user", "text": "diego"},
      {"do": "type",  "sel": "#pass", "text": "…", "secret": true},
      {"do": "click", "sel": "button[type=submit]"},
      {"do": "wait_for", "sel": ".dashboard"},
      {"do": "scroll_until"},
      {"do": "screenshot", "name": "final", "full_page": true}
    ]

Un solo motor para todos los tiers de browser: Camoufox, Patchright y Playwright comparten la
API sync de Playwright, así que `run_actions(page, ...)` sirve para el tier 2 y el 3 igual.
(nodriver NO la comparte: cuando hay acciones, el tier 3 prefiere Playwright.)

Seguridad — tres cosas que este módulo se toma en serio:
- `goto` revalida SSRF en cada salto: una acción no puede llevar el browser a la red interna.
- `eval` (JS crudo) está APAGADO por defecto. Enciende `allow_eval` solo el rol dios con la
  config puesta a mano, porque un script en la página puede pedirle cosas a la red del server.
- los `text` marcados `secret` nunca vuelven en el log ni en el Sobre: se reemplazan por «***».
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Verbos soportados. `eval` va aparte: es el único que ejecuta código arbitrario.
ACTIONS = frozenset({
    "click", "type", "fill", "press", "hover", "select",
    "wait", "wait_for", "scroll", "scroll_until",
    "goto", "screenshot", "pdf", "eval",
})
SAFE_ACTIONS = ACTIONS - {"eval"}

MAX_ACTIONS = 40             # tope de pasos por job (acota el tiempo del browser)
MAX_SCROLL_ROUNDS = 50       # tope de vueltas del scroll infinito
_DEFAULT_TIMEOUT_S = 10.0
REDACTED = "«***»"


class ActionError(Exception):
    """Una acción falló y no era `optional`. Corta la secuencia."""


@dataclass
class ActionOutcome:
    """Qué pasó al correr la secuencia. `log` es apto para mostrar (sin secretos)."""

    log: list[dict] = field(default_factory=list)
    artifacts: dict[str, bytes] = field(default_factory=dict)   # name -> png/pdf
    failed: str = ""                                            # vacío = todo ok


def redact(actions: list[dict]) -> list[dict]:
    """Copia de las acciones apta para logs/Sobre: sin los textos marcados `secret`.

    Se usa antes de guardar nada en meta. Una contraseña que entra por `actions` no puede
    salir por GET /api/jobs ni por el callback."""
    out = []
    for a in actions or []:
        if not isinstance(a, dict):
            continue
        c = dict(a)
        if c.get("secret") and "text" in c:
            c["text"] = REDACTED
        if c.get("do") == "eval":
            c["script"] = f"<{len(str(c.get('script', '')))} chars>"
        out.append(c)
    return out


def validate(actions, *, allow_eval: bool = False) -> list[dict]:
    """Valida y normaliza la lista. Lanza ValueError con un mensaje que se le puede mostrar
    al usuario (va a un 400, no a un 500)."""
    if actions is None:
        return []
    if not isinstance(actions, list):
        raise ValueError("`actions` tiene que ser una lista de pasos.")
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f"Demasiadas acciones ({len(actions)}); el tope es {MAX_ACTIONS}.")

    allowed = ACTIONS if allow_eval else SAFE_ACTIONS
    out: list[dict] = []
    for i, raw in enumerate(actions):
        if not isinstance(raw, dict):
            raise ValueError(f"Acción #{i + 1}: cada paso es un objeto, no {type(raw).__name__}.")
        do = str(raw.get("do") or "").strip().lower()
        if not do:
            raise ValueError(f"Acción #{i + 1}: falta `do` (qué hacer).")
        if do not in ACTIONS:
            raise ValueError(
                f"Acción #{i + 1}: '{do}' no existe. Disponibles: {', '.join(sorted(ACTIONS))}."
            )
        if do not in allowed:
            raise ValueError(
                f"Acción #{i + 1}: '{do}' está deshabilitada en este servidor "
                "(ejecuta código arbitrario; se habilita con BROWSER_ALLOW_EVAL=1 y rol dios)."
            )
        # Requisitos por verbo: mejor un 400 claro acá que un timeout raro adentro del browser.
        if do in ("click", "type", "fill", "hover", "select", "wait_for") and not raw.get("sel"):
            raise ValueError(f"Acción #{i + 1} ('{do}'): falta `sel` (el selector CSS).")
        if do in ("type", "fill") and raw.get("text") is None:
            raise ValueError(f"Acción #{i + 1} ('{do}'): falta `text`.")
        if do == "select" and raw.get("value") is None:
            raise ValueError(f"Acción #{i + 1} ('select'): falta `value`.")
        if do == "press" and not raw.get("key"):
            raise ValueError(f"Acción #{i + 1} ('press'): falta `key` (ej. 'Enter').")
        if do == "goto" and not raw.get("url"):
            raise ValueError(f"Acción #{i + 1} ('goto'): falta `url`.")
        if do == "eval" and not raw.get("script"):
            raise ValueError(f"Acción #{i + 1} ('eval'): falta `script`.")
        out.append(dict(raw, do=do))
    return out


def from_login(login: dict | None) -> list[dict]:
    """Azúcar para el caso más pedido: loguearse. Expande {user_sel, pass_sel, …} a acciones.

    No es un verbo aparte a propósito: un login ES una secuencia de acciones, y dejarlo así
    permite retocarla (agregar un paso de 2FA, un banner previo) sin tocar el motor."""
    if not login:
        return []
    for k in ("user_sel", "user", "pass_sel", "password"):
        if not login.get(k):
            raise ValueError(f"`login`: falta '{k}'.")
    acts = [
        {"do": "type", "sel": login["user_sel"], "text": login["user"]},
        {"do": "type", "sel": login["pass_sel"], "text": login["password"], "secret": True},
    ]
    if login.get("submit_sel"):
        acts.append({"do": "click", "sel": login["submit_sel"]})
    else:   # sin botón declarado, Enter en el campo de la clave es lo que hace un humano
        acts.append({"do": "press", "sel": login["pass_sel"], "key": "Enter"})
    if login.get("wait_for"):
        acts.append({"do": "wait_for", "sel": login["wait_for"]})
    return acts


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------
def _ms(v, default_s: float) -> int:
    try:
        return int(float(v) * 1000)
    except (TypeError, ValueError):
        return int(default_s * 1000)


def _scroll_until(page, a: dict) -> dict:
    """Scroll infinito: baja hasta que la página deja de crecer (o se agotan las vueltas).

    Es el patrón de los feeds/grids que cargan al scrollear. Se corta por altura estable en vez
    de por un número fijo de vueltas para no gastar tiempo cuando ya no hay más nada."""
    rounds = min(int(a.get("max_rounds", 10) or 10), MAX_SCROLL_ROUNDS)
    pause = _ms(a.get("pause_s"), 1.0)
    last_h, stable, done = -1, 0, 0
    for _ in range(rounds):
        try:
            h = int(page.evaluate("document.body.scrollHeight") or 0)
        except Exception:  # noqa: BLE001 — sin JS o página rara: seguimos a ciegas
            h = -1
        page.mouse.wheel(0, 20000)
        page.wait_for_timeout(pause)
        done += 1
        if h >= 0 and h == last_h:
            stable += 1
            if stable >= 2:      # dos vueltas sin crecer = ya cargó todo
                break
        else:
            stable = 0
        last_h = h
    return {"rounds": done, "height": last_h}


def session_state(ctx) -> dict | None:
    """El `storage_state` guardado de la sesión que pidió el job, si existe.

    Va en `new_context(storage_state=…)`: hay que resolverlo ANTES de crear el contexto, por eso
    es una función aparte y no un paso de la secuencia."""
    store = getattr(ctx, "session_store", None)
    name = getattr(ctx, "session_name", "")
    if store is None or not name:
        return None
    try:
        return store.load(getattr(ctx, "session_owner", ""), name)
    except Exception:  # noqa: BLE001 — la sesión es una optimización, nunca un bloqueante
        return None


def persist_session(context, ctx) -> bool:
    """Guarda el estado (cookies + localStorage) al terminar, si el job pidió una sesión.

    Se llama SIEMPRE que haya `session`, no solo tras un login: así el estado se refresca
    (las cookies rotan) y la sesión no se vence sola a los pocos días."""
    store = getattr(ctx, "session_store", None)
    name = getattr(ctx, "session_name", "")
    if store is None or not name:
        return False
    try:
        return bool(store.save(getattr(ctx, "session_owner", ""), name, context.storage_state()))
    except Exception:  # noqa: BLE001
        return False


def run_actions(
    page,
    actions: list[dict],
    *,
    allow_private: bool = False,
    default_timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_artifact_bytes: int = 8 * 1024 * 1024,
) -> ActionOutcome:
    """Corre la secuencia contra una `page` de Playwright (o compatible: patchright/camoufox).

    Cada paso puede marcarse `optional: true` → si falla, se anota y se sigue. Es lo correcto
    para cosas como el banner de cookies, que a veces está y a veces no. Un paso NO opcional
    que falla corta la secuencia (y el fetcher decide qué hacer con lo que haya).
    """
    out = ActionOutcome()
    for i, a in enumerate(actions or []):
        do = a["do"]
        sel = a.get("sel")
        timeout = _ms(a.get("timeout_s"), default_timeout_s)
        step = {"n": i + 1, "do": do}
        if sel:
            step["sel"] = sel
        try:
            if do == "click":
                page.click(sel, timeout=timeout)
            elif do in ("type", "fill"):
                page.fill(sel, str(a.get("text", "")), timeout=timeout)
                if a.get("enter"):
                    page.press(sel, "Enter")
            elif do == "press":
                if sel:
                    page.press(sel, str(a["key"]), timeout=timeout)
                else:
                    page.keyboard.press(str(a["key"]))
            elif do == "hover":
                page.hover(sel, timeout=timeout)
            elif do == "select":
                page.select_option(sel, str(a["value"]), timeout=timeout)
            elif do == "wait":
                page.wait_for_timeout(_ms(a.get("s"), 1.0))
            elif do == "wait_for":
                state = str(a.get("state") or "visible")
                page.wait_for_selector(sel, state=state, timeout=timeout)
            elif do == "scroll":
                for _ in range(int(a.get("times", 1) or 1)):
                    page.mouse.wheel(0, int(a.get("amount", 600) or 600))
                    page.wait_for_timeout(400)
            elif do == "scroll_until":
                step.update(_scroll_until(page, a))
            elif do == "goto":
                # Un salto de navegación es una URL nueva: revalidar SSRF es obligatorio, si no
                # `actions` sería un agujero para llegar a la red interna esquivando el chequeo
                # que hizo el fetcher sobre la URL original.
                from ..security.ssrf import resolve_and_validate
                target = str(a["url"])
                resolve_and_validate(target, allow_private=allow_private)
                page.goto(target, timeout=_ms(a.get("timeout_s"), 30.0),
                          wait_until=str(a.get("wait_until") or "domcontentloaded"))
                step["url"] = target
            elif do in ("screenshot", "pdf"):
                name = str(a.get("name") or f"{do}-{i + 1}")
                if do == "screenshot":
                    blob = page.screenshot(full_page=bool(a.get("full_page", True)))
                else:
                    blob = page.pdf()          # solo Chromium headless; en Firefox lanza
                if len(blob) > max_artifact_bytes:
                    raise ActionError(f"{do} '{name}' pesa {len(blob)} bytes (tope "
                                      f"{max_artifact_bytes}).")
                ext = "png" if do == "screenshot" else "pdf"
                out.artifacts[f"{name}.{ext}"] = blob
                step.update({"name": f"{name}.{ext}", "bytes": len(blob)})
            elif do == "eval":
                # Solo llega acá si validate() lo dejó pasar (config + rol).
                step["result"] = str(page.evaluate(str(a["script"])))[:500]
            step["ok"] = True
        except Exception as e:  # noqa: BLE001 — Playwright lanza tipos varios
            step["ok"] = False
            step["error"] = f"{type(e).__name__}: {e}".splitlines()[0][:200]
            out.log.append(step)
            if a.get("optional"):
                continue      # paso opcional (banner que no estaba): seguimos
            out.failed = f"acción #{i + 1} ({do}): {step['error']}"
            return out
        out.log.append(step)
    return out
