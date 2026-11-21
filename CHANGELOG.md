# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).
Versionado semántico. Cada versión publicada tiene su sección: sin ella, el CI no publica.

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
