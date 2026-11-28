# Agente de punta — plan de implementación

> **Para quien ejecute:** SUB-SKILL REQUERIDA: superpowers:executing-plans (o subagent-driven-development). Los pasos usan `- [ ]`.

**Objetivo:** que la sesión de agente sea la más fácil de diagnosticar, la más rápida de mirar y la que menos tiene que "manejar la pantalla": graba lo que pasó, propone la API que hay detrás del portal y le muestra a la IA la página como la ve un lector de pantalla.

**Arquitectura:** cinco fases, **una versión publicada por fase** (rama → PR → CI → merge → imagen). Cada fase se sostiene sola. Casi todo vive en `app/net/browser_agent.py` (el hilo de la sesión); lo que se puede probar sin navegador va a módulos chicos aparte.

**Stack:** Python 3.14, Playwright 1.60 (sync), FastAPI, pytest con la maqueta `tests/portal_maqueta.py`.

**Spec:** este documento (surge de la revisión del 2026-11-27; no hay spec aparte).

## Restricciones globales

- Rama por fase, TDD (RED visto antes del código), suite completa en verde. **Sin push ni merge sin el OK del dueño del repo.**
- Repo público: ni datos personales ni nombres de otros proyectos, ni en código, ni en tests, ni en docs (ejemplos con `portal.example.com`).
- Ningún secreto (`{{secret:...}}`) sale de la sesión: ni en log, ni en archivos, ni en trazas.
- El log guarda placeholders, nunca valores resueltos. Los cuerpos de respuesta no van al log.
- Nada de lo nuevo amplía lo que puede hacer la IA (lista cerrada `ACCIONES_IA`) ni esquiva la allowlist + SSRF.
- Cada versión: `app/__init__.py`, `pyproject.toml`, sección en `CHANGELOG.md` y `docs/AGENT.md`.
- Un cambio de rendimiento se adopta **solo si una medición contra Chromium real lo muestra mejor** (como el tick de 80 ms, medido y descartado en la 1.11.0).

## Foco de revisión

1. Una traza que guarde la clave tipeada: Playwright registra los parámetros de `fill`/`type`. → test en Fase 1 que busca el secreto (crudo, JSON-escapado y url-encoded) en **cada** miembro del zip.
2. Cookies de sesión del portal dentro de la traza (`trace.network`): con ellas se roba la sesión. → test en Fase 1 de que `cookie`/`set-cookie`/`authorization` quedan sin valor.
3. Receta sugerida que meta un dato literal (un CUIT) en vez de `{{var:cuit}}`. → test en Fase 3 con una variable que aparece en la query.
4. Screencast que deja de mandar cuadros cuando el hilo no está adentro de una llamada a Playwright (el sync API solo despacha eventos ahí). → test en Fase 4 de que un cambio de pantalla en modo humano llega sin acción del cliente.
5. `aria-ref` vencido (la página cambió entre la foto y la acción). → test en Fase 5: la acción falla con mensaje claro y la IA reintenta con una foto nueva, sin clickear otra cosa.

---

## Fase 1 — Traza al fallar (1.13.0)

**Archivos:** crear `app/net/traza.py`; modificar `app/net/browser_agent.py` (`_SesionAgente.__init__`, `_correr`, `abrir`), `app/main.py` (`agent_start`: `traza=bool(body.get("traza"))`); tests en `tests/test_traza.py` (sin navegador), `tests/test_browser_agent.py`, `tests/test_agent_api.py`.

**Interfaces:**
- Produce `traza.limpiar_zip(datos: bytes, secretos: list[str]) -> bytes`: reescribe cada miembro reemplazando cada secreto (crudo, `json.dumps(s)[1:-1]`, `quote(s, safe="")`, `quote_plus(s)`) por `«***»`, y vacía los valores de los headers `cookie`, `set-cookie`, `authorization`, `proxy-authorization` en los `*.network` (JSON por línea).
- `abrir(..., traza: bool = False)`. Con `traza=True` la sesión corre `ctx.tracing.start(screenshots=True, snapshots=True)`. Si termina **con error**, guarda `traza.zip` limpio en `self.archivos` (tipo `application/zip`, tope `MAX_BYTES_ARCHIVO`; si se pasa, `self.alerta` lo dice y no se guarda). Si termina bien, `tracing.stop()` sin archivo.

- [ ] Unitarios de `limpiar_zip` (secreto en las 4 formas en dos miembros; header cookie vaciado; resto intacto) → RED → implementar → GREEN.
- [ ] Navegador: `falla.html` + `traza=True` y una receta que tipea el secreto antes de fallar → `traza.zip` en `archivos`, zip válido con `trace.trace`, y la clave no aparece en ningún miembro.
- [ ] Navegador: sesión que termina bien con `traza=True` → sin `traza.zip`.
- [ ] API: `traza` llega a `abrir`.
- [ ] Docs + CHANGELOG + 1.13.0. Commit.

## Fase 2 — CI que no miente (1.13.1)

**Archivos:** `pyproject.toml` (`[tool.ruff]`), `requirements-dev.txt` (`ruff`, `pytest-cov`), `.github/workflows/tests.yml`, `tests/test_browser_agent.py::test_sesion_guardada_se_usa_y_se_guarda`.

- [x] ~~Arreglar el test inestable~~ — hecho en la 1.13.0: era una carrera real (la sesión decía "terminado" antes de guardar el login). Test determinístico con un store lento.
- [ ] `ruff check` con un set chico (`E`, `F`, `I`, `B`, `UP`) y los arreglos que pida; paso de CI antes de pytest.
- [ ] `pytest --cov=app --cov-fail-under=<cobertura medida − 1>`.
- [ ] Commit.

## Fase 3 — La receta que está detrás de la pantalla (1.14.0)

**Archivos:** crear `app/net/sugerir.py`; modificar `browser_agent.py` (registrar intercambios JSON), `app/main.py` (`GET /api/agent/{t}/receta-sugerida`); tests `tests/test_sugerir.py` y uno de navegador.

**Interfaces:**
- La sesión guarda `self.intercambios: list[dict]` = `{"metodo", "url", "status", "json"}` de cada respuesta JSON de un dominio permitido (tope 50, tope de bytes `MAX_BYTES_CAPTURA`), solo en memoria.
- `sugerir.sugerir_receta(intercambios: list[dict], variables: dict, url_inicio: str) -> dict` → `{"receta": [...], "omitidos": [{"url", "motivo"}]}`:
  - `goto` a `url_inicio`, después un `fetch` por cada GET (un POST va a `omitidos`: "fetch solo hace GET").
  - Valor de query igual al de una variable → `{{var:nombre}}`.
  - Valor de query que aparece en un JSON anterior → `guardar` en ese paso y `{{res:paso_N.ruta}}` acá.
  - URL relativa a la página cuando comparten origen.
- [ ] Unitarios: encadenado (id de la 1.ª respuesta en la 2.ª URL), variable reemplazada (y el valor literal ausente del resultado), POST omitido, duplicados colapsados.
- [ ] Navegador: corrida "de pantalla" de la maqueta → la receta sugerida, corrida en una sesión nueva, baja los mismos datos.
- [ ] Endpoint con ownership (404 a otro dueño). Docs, CHANGELOG, 1.14.0. Commit.

## Fase 4 — Vista en vivo por screencast (1.15.0, sujeta a medición)

**Archivos:** `browser_agent.py` (`_foto`, `_bombear`), nuevo `tests/test_agent_screencast.py`.

- Con CDP (`ctx.new_cdp_session(page)`, `Page.startScreencast` jpeg con `_CALIDAD_JPEG`, ack con `Page.screencastFrameAck`) cada cuadro va a `pantalla.poner`. Al cambiar de pestaña se re-ata. Si CDP falla, queda el método actual.
- El sync API solo despacha eventos dentro de llamadas a Playwright: en `_bombear` la espera pasa a `page.wait_for_timeout(ms)` en vez de `queue.get(timeout)`, sin perder la respuesta inmediata a eventos humanos.
- [ ] Medir primero con el script de la 1.11.0 (clic → imagen, mediana y p95). Adoptar solo si mejoran mediana y p95; si no, anotarlo en "Medido y descartado".
- [ ] Test: en modo humano, un cambio de pantalla hecho por la página sola llega a `/frame` con `esperar_ms` sin acción del cliente.
- [ ] Docs, CHANGELOG, 1.15.0. Commit.

## Fase 5 — La IA ve el árbol de accesibilidad (1.16.0)

**Archivos:** `app/net/agent_ai.py` (lista de elementos → snapshot), `browser_agent.py` (`_accion_ia`, resolución de `ref`), tests `tests/test_agent_ai.py`, `tests/test_browser_agent.py`.

- Observación: `page.locator("body").aria_snapshot(mode="ai")` (verificado en Playwright 1.60: devuelve `[ref=eN]`, y `page.locator("aria-ref=eN")` actúa sobre ese elemento). El enmascarado de CUIT/email/tarjeta/teléfono/IP se aplica al texto del snapshot, como hoy.
- `ref` de la IA pasa de `int` a `str` con patrón `e\d+`; `parsear_accion` lo valida.
- El chequeo de lo irreversible (`irreversible.py`) sigue mirando el elemento real (atributos vía el locator), no lo que diga la IA.
- [ ] Tests: snapshot enmascarado (sin CUIT literal); ref inválido rechazado; ref vencido → falla clara y foto nueva; los tests de IA existentes siguen verdes con el formato nuevo.
- [ ] Comparar tokens del prompt (viejo vs. nuevo) en las páginas de la maqueta y anotarlo en el CHANGELOG.
- [ ] Docs, CHANGELOG, 1.16.0. Commit.
