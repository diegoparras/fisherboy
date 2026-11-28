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
| GET | `/api/agent/{t}/frame?desde=N` | Última foto JPEG. 204 si no cambió desde `N`, 202 si todavía no hay. Con `esperar_ms`, espera la próxima (ver abajo) |
| POST | `/api/agent/{t}/event` | Evento humano. 409 si la sesión no está en modo humano |
| POST | `/api/agent/{t}/takeover` | Tomar el control |
| POST | `/api/agent/{t}/resume` | Devolver el control a la receta / IA |
| POST | `/api/agent/{t}/ok` | Dar el OK a lo que no se deshace (409 si no está en `esperando_ok`) |
| POST | `/api/agent/{t}/no` | Negarlo: la IA no lo toca y la sesión termina con «No lo hice: quien maneja la sesión dijo que no.» |
| GET | `/api/agent/{t}/punto?x=&y=` | Qué elemento hay en ese punto; con `activo=1`, el que tiene el foco. Sólo lee |
| GET | `/api/agent/{t}/files/{n}` | Archivo descargado `n` |
| GET | `/api/agent/{t}/captures` | Respuestas JSON capturadas (el estado solo trae la cantidad) |
| GET | `/api/agent/{t}/receta-sugerida` | La receta de `fetch` que baja lo mismo que vio la sesión (ver abajo) |
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
- `traza: true`: graba una traza de Playwright y la deja como `traza.zip` si algo falla (ver
  [Cuando un paso falla](#cuando-un-paso-falla)).

El estado trae `resumen`: lo que la IA dijo al terminar (`done`), con los datos de verdad.

### Ver la pantalla sin demora: `esperar_ms`

`GET /api/agent/{t}/frame?desde=N` contesta lo que hay y corta. Si la foto no cambió, el
cliente recibe 204 y vuelve a preguntar unos milisegundos después — y ese ida y vuelta es
justo lo que se siente cuando una persona está manejando: hace clic y ve el resultado bastante
después.

Con `esperar_ms` (0–5000) el pedido **se queda esperando**: vuelve en el instante en que la
foto cambia, o con 204 al cumplirse el tiempo.

```
GET /api/agent/{t}/frame?desde=12&esperar_ms=3000
  → 200 con el JPEG en cuanto la pantalla cambia (X-Agent-Seq: 13)
  → 204 si pasaron los 3 s sin que cambiara (volvé a pedir con el mismo `desde`)
```

Sin el parámetro, el comportamiento es el de siempre. No hace falta acertarle al valor: pedir
3–5 s y volver a pedir en cuanto llega la respuesta es todo lo que necesita un cliente.

Medido contra un Chromium real, de que se aplica el clic a que la imagen nueva está lista:
**135 ms de mediana (p95 255) bajan a 50 ms (p95 96)**. Por dentro no hay bucle de consulta:
el hilo del navegador despierta a los que esperan cuando la imagen cambia. Y la espera corre
fuera del bucle de eventos de la API, así que un pedido colgado no frena a los demás — el
estado y los eventos siguen respondiendo mientras otro espera.

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
| `fetch` | `url`, `guardar?`, `name?` | GET desde la página, con su sesión; la respuesta tiene que ser JSON (ver abajo) |

Cualquier paso acepta `objetivo` (lo que la IA intenta si el paso falla), `optional`,
`timeout_s`. Tope: 60 pasos de receta y 30 acciones de IA por objetivo. 2 sesiones vivas a la
vez, TTL 20 minutos.

### `fetch`: pedirle los datos al portal en vez de leer la pantalla

Muchos portales arman sus tablas con consultas AJAX que devuelven JSON. Una vez logueado, es
más rápido y más estable pedir ese JSON que manejar calendarios, botones y paginadores:

```json
[
  {"do": "goto", "url": "https://portal.example.com/consultas/emitidos.do"},
  {"do": "fetch", "url": "ajax.do?f=generar&desde={{var:desde}}", "guardar": "consulta"},
  {"do": "fetch", "url": "ajax.do?f=resultados&id={{res:consulta.datos.id}}", "name": "emitidos.json"}
]
```

- **Corre adentro de la página** (`fetch` del navegador, `credentials: "include"`, con
  `X-Requested-With: XMLHttpRequest`): sale con las cookies, el `Origin` y el `Referer` de la
  sesión, igual que las consultas del propio portal. Solo `GET`.
- `url` puede ser relativa: se resuelve contra la URL actual de la página. El destino pasa por
  la allowlist de la sesión y el chequeo SSRF, y **las redirecciones no se siguen**: una podría
  llevar el pedido, con la sesión puesta, a un host que nadie chequeó.
- La respuesta tiene que ser 2xx y JSON. Si no, el paso falla diciendo qué vino, por ejemplo
  `la respuesta no es JSON (HTTP 200, HTML: "Sesión expirada")` — así se nota una sesión vencida
  que el portal contesta con una página de login y status 200.
- `guardar` deja el JSON en la sesión. Los pasos que siguen lo usan con
  `{{res:nombre.ruta.con.puntos}}` (un tramo numérico es un índice de lista). En una `url` el
  valor va codificado. Una ruta que no existe hace fallar el paso, nombrándola.
- `name` guarda el cuerpo como archivo de la sesión (`/files/{n}`, tope 20 MB), como `download`.
- `timeout_s` por defecto es 30.
- El log lleva la `url` de la receta (con sus placeholders), el status y los bytes: **nunca el
  cuerpo**. La IA no puede pedir un `fetch`, y el job declarativo (ACTIONS.md) tampoco lo tiene:
  ahí no hay allowlist de sesión, y sería un SSRF.

### La receta que está detrás de la pantalla: `receta-sugerida`

Una vez que una sesión recorrió la pantalla —una persona, la IA o una receta de clics—,
`GET /api/agent/{t}/receta-sugerida` propone una receta de `fetch` con las consultas JSON que
hizo la página. Es la forma rápida de pasar de "manejar el portal" a "pedirle los datos":

```json
{"receta": [
   {"do": "goto", "url": "https://portal.example.com/consultas/emitidos.do?cuit={{var:cuit}}"},
   {"do": "fetch", "url": "ajax.do?f=generar&cuit={{var:cuit}}", "name": "respuesta-1.json", "guardar": "paso_1"},
   {"do": "fetch", "url": "ajax.do?f=lista&id={{res:paso_1.datos.idConsulta}}", "name": "respuesta-2.json"}
 ],
 "omitidos": [{"url": "https://portal.example.com/consultas/ajax.do?f=borrar", "motivo": "fetch solo hace GET"}],
 "notas": []}
```

- Arranca con un `goto` a la página desde donde salieron las consultas: el `fetch` corre
  adentro de ella, con su sesión.
- Un valor que es una variable de la sesión va como `{{var:nombre}}`, **nunca literal**. Si dos
  variables valen lo mismo, gana la que se llama como el parámetro. Si el valor iba codificado
  en la URL, `notas` avisa que hay que mandarla codificada.
- Un valor que salió de una respuesta anterior (el id de una consulta) va como
  `{{res:paso_N.ruta}}`, y ese paso lleva `guardar`. Los valores de menos de 4 caracteres no
  se encadenan: "1" o "E" aparecen en cualquier JSON.
- Un secreto que viajó en una URL queda como `{{secret:nombre}}`.
- Solo se aprende de los dominios de la sesión, de hasta 50 respuestas (5 MB en total), y solo
  en memoria. Es una **propuesta para revisar**: corrérsela a ciegas puede pedir de más.

### Cuando un paso falla

En el momento del fallo se saca una foto, antes de que la IA intente nada o la sesión cierre:
`GET /frame` devuelve **esa** pantalla (también con la sesión en `error`, hasta el `DELETE`).
La entrada del paso en el log suma `pagina` (la URL en la que estaba) y `pendientes`: las
consultas XHR/fetch que salieron y no volvieron, como `GET host/ruta (12.3 s)`, sin la query.

Para ver todo lo que pasó, y no solo el último momento, abrí la sesión con `"traza": true`. Si
un paso falla (aunque la IA lo repare) o la sesión termina en `error`, queda `traza.zip` entre
los archivos (`/files/{n}`, tope 20 MB): se abre en https://trace.playwright.dev y muestra cada
acción con el DOM antes y después, la red y la consola. Si todo sale bien, no se guarda.

La traza sale **limpia**: sin valores en `Cookie`, `Set-Cookie`, `Authorization` ni en las
listas de cookies, y con los secretos tapados (también escapados o codificados en una URL). Lo
que sí trae es el contenido de las páginas y las variables tipeadas (`{{var:...}}`): es de
quien abrió la sesión y solo lo baja ella.

## Seguridad

- **Lo que no se deshace.** Antes de que la IA haga clic sobre un elemento `type=submit` o cuyo
  texto o `aria-label` dice enviar, comprar, pagar, confirmar, aceptar, transferir, borrar,
  eliminar, publicar, suscribir, contratar o finalizar (y sus formas en inglés), o antes de un
  Enter dentro de un formulario, la sesión pasa a `esperando_ok` y espera `/ok` o `/no`. Lo decide
  código (`app/net/irreversible.py`), no la IA. El estado trae `ok_pedido: {texto, motivo}`.

- **Secretos.** `{{secret:nombre}}` se resuelve al ejecutar el paso, adentro de la sesión. El
  log guarda el placeholder; los secretos viven solo en memoria y se borran al terminar. Nunca
  van a Redis ni vuelven por la API, y cualquier mensaje de error se limpia por si un valor se
  colara.
- **Dominios.** Toda navegación (receta, IA, humano, pestañas nuevas) y todo `fetch` de receta
  pasa por la allowlist de la sesión y por el chequeo SSRF; fuera de la lista, el pedido se
  aborta. Un iframe de otro dominio que trae la página sola no carga, pero no corta la sesión. La lista sale de
  `AGENT_ALLOWED_DOMAINS`, que tiene tres modos:

  | Valor | Qué pasa |
  |---|---|
  | vacío (default) | La sesión de agente está **apagada**: `/api/agent/start` devuelve 403. Se enciende a mano, porque el agente opera sitios de terceros con credenciales de quien lo instala. |
  | `dominio.com,otro.org` | Lista fija del servidor, subdominios incluidos. El pedido puede **acotarla** con su propio `dominios`, nunca ampliarla. |
  | `*` | Cada sesión **tiene que** traer su `dominios` (sin eso, 400). No es sin límite: mueve la decisión del servidor a quien abre la sesión. |
- **La IA** ve la página como lista de elementos (no capturas), con los CUIT, emails,
  tarjetas, teléfonos e IP reemplazados por marcadores (`«CUIT_1»`). Solo puede devolver una
  acción de una lista cerrada (`click`, `type`, `select`, `press`, `goto`, `wait`, `done`,
  `human`): sin JavaScript, sin descargas, sin abrir pestañas. El prompt declara que el
  contenido de la página es dato y no instrucción. Las claves las escribe por su marcador
  `{{secret:...}}`, sin conocerlas.
- **Captchas.** No se saltean: `human_if` o la propia IA (`human`) pausan y espera a una
  persona.
