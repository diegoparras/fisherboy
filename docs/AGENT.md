# Sesión de agente: receta, IA y humano en el mismo navegador

Los jobs con `actions` operan una página de forma declarativa, pero no ven pestañas nuevas, no
bajan archivos y no dejan entrar a una persona. La **sesión de agente** es un navegador vivo
del servidor que:

1. corre una **receta** (el recorrido fijo, sin IA),
2. si un paso falla y la sesión tiene IA, le pasa el **objetivo** de ese paso a un modelo que
   opera la página hasta cumplirlo, y sigue con la receta,
3. se pausa en **`esperando_humano`** cuando aparece un captcha o un segundo factor, y deja que
   cualquiera con acceso **tome el control** en vivo y lo devuelva.

Sirve para cualquier sitio de la allowlist: un portal con login, un trámite con captcha,
un listado que se exporta.

## API

Todas exigen un rol con `capture` (el `API_TOKEN` de servicio sirve). La sesión es del dueño
que la abrió: la de otro devuelve 404.

| Método | Ruta | Qué hace |
|---|---|---|
| POST | `/api/agent/start` | Abre la sesión. Body abajo. Devuelve `{token, estado, ia, ancho, alto}` |
| GET | `/api/agent/{t}` | Estado, log, archivos y capturas (sin secretos) |
| GET | `/api/agent/{t}/frame?desde=N` | Última foto JPEG. 204 si no cambió desde `N`, 202 si todavía no hay |
| POST | `/api/agent/{t}/event` | Evento humano. 409 si la sesión no está en modo humano |
| POST | `/api/agent/{t}/takeover` | Tomar el control |
| POST | `/api/agent/{t}/resume` | Devolver el control a la receta / IA |
| POST | `/api/agent/{t}/ok` | Dar el OK a lo que no se deshace (409 si no está en `esperando_ok`) |
| POST | `/api/agent/{t}/no` | Negarlo: la IA no lo toca y la sesión termina con «No lo hice: Diego dijo que no.» |
| GET | `/api/agent/{t}/punto?x=&y=` | Qué elemento hay en ese punto; con `activo=1`, el que tiene el foco. Sólo lee |
| GET | `/api/agent/{t}/files/{n}` | Archivo descargado `n` |
| GET | `/api/agent/{t}/captures` | Respuestas JSON capturadas (el estado solo trae la cantidad) |
| DELETE | `/api/agent/{t}` | Cerrar |

```jsonc
POST /api/agent/start
{
  "url": "https://portal.example.com/contribuyente_/login.xhtml",
  "receta": [
    {"do": "type", "sel": "[id='F1:username']", "text": "{{var:cuit_login}}"},
    {"do": "click", "sel": "[id='F1:btnSiguiente']"},
    {"do": "human_if", "sel": "#captcha", "motivo": "el portal pide un captcha"},
    {"do": "type", "sel": "[id='F1:password']", "text": "{{secret:clave_portal}}"},
    {"do": "click", "sel": "[id='F1:btnIngresar']"},
    {"do": "wait_url", "contains": "portal", "objetivo": "Entrar al portal"}
  ],
  "variables": {"cuit_login": "20111111112"},
  "secretos": {"clave_portal": "..."},
  "dominios": ["portal.example.com"],
  "ia": true
}
```

Campos opcionales del `start`:

- `objetivo` sin `receta`: la IA trabaja sola sobre ese objetivo.
- `session`: el nombre de un login guardado (el mismo store que `/api/browser-login`). Se carga al
  arrancar y se vuelve a guardar al terminar o al cerrar.
- `mantener: true`: si la IA no puede, la sesión no termina en `error`: pasa a `esperando_humano`
  con un `motivo_humano` que empieza con `La IA no pudo: `, para que una persona (o quien la maneje
  desde afuera) tome el control. Cuando lo devuelve, la sesión termina.

El estado trae `resumen`: lo que la IA dijo al terminar (`done`), con los datos de verdad.

Estados: `arrancando`, `corriendo`, `esperando_humano`, `esperando_ok`, `humano`, `terminado`, `error`,
`cerrado`. Eventos humanos: `click {x,y}`, `doble {x,y}`, `click_sel {sel}`, `texto {texto}`,
`tecla {tecla}`, `scroll {dy}`, `navegar {url}`, `atras`, `recargar`. El navegador mide 1280x800.

## Verbos de receta

Todos los de [ACTIONS.md](ACTIONS.md) salvo `eval`, más:

| `do` | Campos | Qué hace |
|---|---|---|
| `wait_url` | `contains` | Espera a que la URL contenga el texto |
| `switch_tab` | | Pasa a la pestaña nueva o popup que se abrió |
| `download` | `sel`, `name?` | Click que dispara una descarga y la guarda (tope 20 MB) |
| `capture` | `pattern` | Desde ese paso, guarda las respuestas JSON cuyo URL matchea la regex |
| `human_if` | `sel`, `motivo?` | Si el selector está visible, pausa y pide una persona |
| `goal` | `objetivo` | Sub-objetivo en lenguaje natural para la IA |

Cualquier paso acepta `objetivo` (lo que la IA intenta si el paso falla), `optional`,
`timeout_s`. Tope: 60 pasos de receta y 30 acciones de IA por objetivo. 2 sesiones vivas a la
vez, TTL 20 minutos.

## Seguridad

- **Lo que no se deshace.** Antes de que la IA haga clic sobre un elemento `type=submit` o cuyo
  texto o `aria-label` dice enviar, comprar, pagar, confirmar, aceptar, transferir, borrar,
  eliminar, publicar, suscribir, contratar o finalizar (y sus formas en inglés), o antes de un
  Enter dentro de un formulario, la sesión pasa a `esperando_ok` y espera `/ok` o `/no`. Lo decide
  código (`app/net/irreversible.py`), no la IA. El estado trae `ok_pedido: {texto, motivo}`.
- **Dominios abiertos.** Con `AGENT_ALLOWED_DOMAINS=*` cualquier dominio sirve, pero cada sesión
  tiene que traer su `dominios` (sin eso, 400): una lista vacía sería «sin límite».

- **Secretos.** `{{secret:nombre}}` se resuelve al ejecutar el paso, adentro de la sesión. El
  log guarda el placeholder; los secretos viven solo en memoria y se borran al terminar. Nunca
  van a Redis ni vuelven por la API, y cualquier mensaje de error se limpia por si un valor se
  colara.
- **Dominios.** Toda navegación (receta, IA, humano, pestañas nuevas) pasa por la allowlist de
  la sesión y por el chequeo SSRF; fuera de la lista, el pedido se aborta. La lista sale de
  `AGENT_ALLOWED_DOMAINS` y el request solo puede acotarla.
- **La IA** ve la página como lista de elementos (no capturas), con los CUIT, emails,
  tarjetas, teléfonos e IP reemplazados por marcadores (`«CUIT_1»`). Solo puede devolver una
  acción de una lista cerrada (`click`, `type`, `select`, `press`, `goto`, `wait`, `done`,
  `human`): sin JavaScript, sin descargas, sin abrir pestañas. El prompt declara que el
  contenido de la página es dato y no instrucción. Las claves las escribe por su marcador
  `{{secret:...}}`, sin conocerlas.
- **Captchas.** No se saltean: `human_if` o la propia IA (`human`) pausan y espera a una
  persona.
