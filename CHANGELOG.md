# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).
Versionado semántico. Cada versión publicada tiene su sección: sin ella, el CI no publica.

## [1.15.0] - 2026-11-28

### Cambiado
- **La IA mira la página como árbol de accesibilidad.** En vez de una lista de elementos
  armada a mano, recibe la página como la lee un lector de pantalla: roles y nombres
  (`button "Buscar" [ref=e12]`), con el texto alrededor. No depende de ids ni clases del portal,
  y pesa menos: **37 % menos** en las páginas de la maqueta (2288 → 1433 caracteres).
  - El `ref` que devuelve la IA pasa de número a `eN`, y solo puede ser uno del árbol: va
    directo a un selector, y cualquier otra cosa sería un selector elegido por el modelo.
  - Si el elemento ya no está (la página cambió), la acción falla enseguida diciéndolo, en
    vez de esperar 8 s; la IA lo ve y mira la página de nuevo.
  - Requiere Playwright 1.60 o más nuevo.

### Seguridad
- **Una clave tipeada a mano no llega a la IA.** El árbol de accesibilidad muestra el valor de
  cada campo, también el de un campo de clave (medido antes de cambiar nada). Las claves de la
  sesión (`{{secret:}}`) ya se tapaban; una que tipeó una persona en modo humano no era
  conocida. Ahora se tapa el valor de todo campo de clave, mirando el campo y no el valor.
- Los tests de enmascarado usan los datos de prueba de la maqueta, como el resto.

## [1.14.1] - 2026-11-27

### Corregido
- **Tomar el control ya no espera a que termine el paso.** El pedido se miraba solo entre un
  paso y el siguiente: durante un `wait` largo, quien pedía el control miraba una pantalla que
  no podía tocar hasta que el paso terminaba. Ahora se atiende enseguida, y al devolverlo el
  paso sigue donde estaba.

### Medido y descartado
- **Vista en vivo por screencast de Chromium (CDP) en vez de capturas.** Medido contra Chromium
  real, 4 rondas de 25 clics, del clic a la imagen nueva: capturas 49–52 ms de mediana (p95
  55–83); screencast 51–64 ms (p95 67–86). No mejora: el piso de ~45 ms es lo que tarda Chromium
  en dibujar y codificar el cuadro después del clic, no el costo de pedirle la captura.

## [1.14.0] - 2026-11-27

### Nuevo
- **La receta que está detrás de la pantalla.** Después de que una sesión recorre un portal
  —manejada por una persona, por la IA o con una receta de clics—, `GET
  /api/agent/{t}/receta-sugerida` propone una receta de `fetch` con las consultas JSON que hizo
  la página: lo que se hacía con calendario, botón, tabla y exportación pasa a ser una o dos
  consultas, y deja de romperse cuando el portal mueve un botón. Probado de punta a punta: la
  receta sugerida, corrida en una sesión nueva, baja los mismos datos sin un clic.
  - Las variables de la sesión van como `{{var:...}}`, nunca literales: la receta sugerida no
    se lleva un CUIT ni un rango de fechas escritos a mano. Un id que sale de una respuesta y
    se usa en la siguiente queda encadenado con `{{res:...}}`.
  - Solo aprende de los dominios que la sesión permite y lo guarda en memoria. Los POST
    quedan aparte (`fetch` solo hace GET). Nuevo módulo `app/net/sugerir.py`.

## [1.13.1] - 2026-11-27

### Interno
- **El CI revisa el código además de correr los tests.** `ruff` (pyflakes, bugbear, imports y
  sintaxis moderna; reglas y excepciones en `pyproject.toml`) corre antes que pytest, y la
  cobertura tiene piso: 65 %, uno menos que lo medido al agregarlo.
- De lo que encontró: imports sin usar, y `zip()` sin `strict` en la máscara de archivos — ahí
  las tres listas tienen que medir lo mismo, y ahora si no miden lo mismo falla en vez de
  dejar archivos sin enmascarar.

## [1.13.0] - 2026-11-27

### Nuevo
- **Traza de Playwright cuando algo falla.** Con `"traza": true` en `/api/agent/start`, la
  sesión graba todo lo que pasa y, si un paso falla (aunque la IA lo repare) o la sesión
  termina con error, deja `traza.zip` entre sus archivos. Se abre en
  https://trace.playwright.dev: cada acción con una foto del antes y el después, el DOM de cada
  momento, la red con sus tiempos y la consola. Si todo sale bien, no se guarda nada.
  - **Sale limpia.** Medido contra Playwright 1.60: la clave tipeada y el valor de las cookies
    quedan adentro de la traza, y con la cookie de sesión de un portal se entra sin clave.
    Antes de guardarla se vacían los headers de credenciales (`Cookie`, `Set-Cookie`,
    `Authorization`) y las cookies estén donde estén, y se tapan los secretos de la sesión
    también escapados o codificados en una URL. Nuevo módulo `app/net/traza.py`.
  - Las cookies de un login guardado (`session`) no entran: la grabación arranca después de
    cargarlas.

### Corregido
- **"Terminado" ahora quiere decir que ya está todo guardado.** La sesión avisaba que había
  terminado y recién después guardaba el login: quien abría la próxima sesión con ese mismo
  login apenas veía terminar la anterior podía arrancar sin él y tener que volver a ingresar.
  Ahora primero guarda (el login y la traza) y después avisa. Era también la causa de un test
  que fallaba una de cada tres veces.

## [1.12.1] - 2026-11-26

### Seguridad
- **Una redirección ya no saca la sesión de agente de los dominios permitidos.** El guardia de
  dominios revisaba cada navegación que se pedía, pero no los saltos de una redirección HTTP
  (301/302): si un host permitido redirigía a otro, el navegador lo seguía y la sesión, ya
  logueada en el portal, quedaba en un dominio ajeno. Hallazgo P-02 de una auditoría externa.
  - Ahora se vigila cada navegación de la pestaña (también la que llega por una redirección y la
    de pestañas nuevas), y la URL se revisa otra vez al terminar cada paso.
  - En una receta o con la IA, la sesión corta con «dominio no permitido».
  - Si la URL inicial redirige afuera, la sesión no arranca.
  - En modo humano, la pestaña vuelve a una página en blanco y queda el aviso; la sesión sigue
    en manos de la persona.

## [1.12.0] - 2026-11-25

### Nuevo
- **Verbo de receta `fetch`: pedirle el JSON al portal en vez de manejar la pantalla.** Una vez
  logueada, la sesión puede hacer un GET desde adentro de la página, con sus cookies, y recibir
  los datos que el portal usa para armar sus tablas. Un recorrido de calendario, botón, tabla y
  exportación pasa a ser una o dos consultas de menos de un segundo.
  - `guardar` deja la respuesta para los pasos que siguen, que la usan con
    `{{res:nombre.ruta}}` (por ejemplo, el id de una consulta que hay que pedir después).
    `name` la guarda como archivo de la sesión.
  - Si la respuesta no es JSON, el paso falla diciendo qué vino: una sesión vencida que el
    portal contesta con su página de login y status 200 se ve como tal, no como datos vacíos.
  - Pasa por la misma allowlist de dominios y el mismo chequeo SSRF que la navegación, y no
    sigue redirecciones. El cuerpo no va al log. Es solo de receta: la IA no puede pedirlo.
- **Cuando un paso falla, la foto es la del fallo.** Antes, la última imagen de la sesión era la
  del paso anterior —el que salió bien— y el momento del error no se veía. Ahora se saca una
  foto apenas falla, y se puede bajar también con la sesión en `error`, hasta cerrarla.
  - La entrada del paso en el log suma `pagina` (en qué URL estaba) y `pendientes`: las
    consultas que salieron y no volvieron, con cuánto llevaban esperando. Un paso que espera
    una tabla y vence porque la página quedó colgada de una consulta ahora lo dice.

## [1.11.1] - 2026-11-25

### Corregido
- **Un video incrustado en la página ya no tumba la sesión de agente.** Si la página traía un
  iframe de otro dominio (un video, un mapa, un widget de chat), la sesión cortaba con
  «dominio no permitido» y el paso que estuviera corriendo en ese momento fallaba — un paso
  que ni siquiera navegaba a ningún lado, como escribir en un campo. El iframe ajeno se sigue
  bloqueando, pero en silencio: no carga y la sesión sigue.
  - Pasaba porque el guardia de dominios trata toda «navegación» igual, y cargar un iframe es
    una navegación para el navegador. Ahora distingue la pestaña del iframe incrustado: lo que
    pide la receta frena la sesión, lo que trae la página sola no.
  - La pestaña yéndose a otro dominio **sigue frenando** la sesión, como antes. Si no se puede
    determinar de qué frame viene el pedido, se lo trata como la pestaña (lo conservador).

## [1.11.0] - 2026-11-20

### Nuevo
- **La vista en vivo de la sesión de agente responde al instante.** `GET
  /api/agent/{t}/frame` acepta `esperar_ms` (0–5000): si la pantalla no cambió, el pedido se
  queda esperando y vuelve en el momento en que cambia, en vez de contestar «nada nuevo» y
  hacer que el cliente pregunte de nuevo. Medido contra un Chromium real, del clic a tener la
  imagen: **135–165 ms de mediana (p95 255) bajan a 50 ms (p95 96)**. Sin el parámetro, todo
  funciona como antes.
  - No hay bucle de consulta por dentro: el hilo del navegador despierta a los que esperan
    cuando la imagen cambia, y la espera corre fuera del bucle de eventos de la API, así que
    un pedido esperando no frena al resto.
  - Al cerrar la sesión, el que esté esperando vuelve enseguida en vez de quedar colgado
    hasta que se cumpla el tiempo.

### Corregido
- El mensaje de cuando se niega una acción irreversible llevaba **un nombre propio escrito en
  el código**, así que cualquiera que instalara Fisherboy veía el nombre de otra persona en su
  propia sesión. Ahora dice «quien maneja la sesión».

### Medido y descartado
- **Bajar el ritmo de captura a 80 ms con una persona al mando no sirve**, aunque la intuición
  diga lo contrario: no mejora la mediana y empeora la cola (p95 de 89 a 144 ms). Con la
  espera larga, lo que queda es el costo de sacar la captura (~44 ms); duplicar las capturas
  solo ocupa más el hilo del navegador, y un clic que llega mientras hay una captura en curso
  espera a que termine. Los números quedaron anotados en `browser_agent.py`.
- **Bajar la calidad JPEG de 70 a 55** ahorra 8% de bytes (12.1K → 11.1K por pantalla), que no
  paga el halo de compresión sobre el texto.

## [1.10.0] - 2026-11-20

### Nuevo
- **La sesión de agente puede guardar el login.** Con `session` y `mantener`, las cookies y el
  almacenamiento quedan guardados al terminar y se reutilizan en la siguiente: el segundo
  ingreso al mismo portal ya no pide clave ni segundo factor.
- **Si la IA falla, la sesión no muere.** Con `mantener`, un paso que la IA no logra deja la
  sesión en `esperando_humano` en vez de cortarla con error: la persona resuelve y la sesión
  sigue desde ahí.
- **Resumen al terminar.** `done` devuelve en lenguaje natural qué encontró, con los datos
  reales restaurados (la IA trabaja con marcadores, el resumen final los rehidrata).
- **Freno antes de lo que no se deshace.** Un paso que parece irreversible (confirmar, enviar,
  pagar, borrar) pausa y pide un OK explícito antes de tocarlo, en vez de ejecutarlo y avisar
  después. Nuevo módulo `app/net/irreversible.py`; `POST /api/agent/{t}/ok` y `/no`.
- **Preguntar qué hay en un punto de la pantalla**, para cuando el recorrido necesita leer algo
  que no está en el selector esperado.
- **Dominios por sesión.** Cada sesión puede declarar su propia allowlist; sigue sin poder
  ampliar la de `AGENT_ALLOWED_DOMAINS`, solo acotarla. Con `AGENT_ALLOWED_DOMAINS=*` el
  pedido está obligado a decir en qué dominios trabaja.

## [1.9.1] - 2026-11-20

### Nuevo
- **Cada deploy dice qué versión es y qué cambia**: lo primero que escriben la API y el worker
  en los registros del contenedor (EasyPanel, Resumen) es un cartel con la versión, la fecha,
  qué proceso es (API o worker, y en qué modo) y la lista de cambios.
- La interfaz avisa **Novedades** al entrar después de una actualización, con lo que cambió
  desde la última versión que viste en ese navegador. El historial queda en el menú
  (**Novedades**) y en **Acerca de** ("Qué cambió"). Por API: `GET /api/novedades?desde=X.Y.Z`.
- El CI etiqueta la imagen también con la versión (`ghcr.io/diegoparras/fisherboy:X.Y.Z`),
  crea el Release vX.Y.Z en GitHub con estas notas y las muestra en el resumen de la corrida.
