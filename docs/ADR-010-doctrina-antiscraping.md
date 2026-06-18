# ADR-010 — Doctrina anti-antiscraping (estrategia definitiva)

Estado: aceptado
Fecha: 2026-06-18

## Contexto

Síntesis de la investigación del estado del arte (2026) sobre detección anti-bot y
técnicas de evasión, bajada a una estrategia concreta para Fisherboy. No existe una
"bala de plata": es una carrera armamentista. Pero sí hay una DOCTRINA que maximiza la
tasa de éxito y minimiza costo, y un orden correcto de escalado.

## Cómo te detectan (4 capas simultáneas)

Un scraper tiene que parecer un browser real en las CUATRO a la vez; un mismatch entre
capas (ej. User-Agent de Chrome con handshake TLS de Python) es delación instantánea.

1. **TLS (JA3/JA4)**: el ClientHello de `requests`/`httpx` no matchea ningún browser real.
2. **HTTP/2**: orden de frames / pseudo-headers.
3. **Fingerprint JS**: canvas, WebGL, fonts, `navigator.webdriver`, audio, timezone.
4. **Comportamiento + IP**: mouse/scroll/timing + reputación de IP (datacenter flagged).

Pesos por vendor: Cloudflare → TLS + ML global; DataDome → comportamiento + IP;
PerimeterX → biometría de mouse; Akamai → fingerprint + sensor data.

## La doctrina (orden de escalado, barato → caro)

### 0. API-first — el movimiento más confiable (keystone)
Antes de pelear el HTML, **buscar el JSON/XHR que la página ya consume** y pegarle
directo. Es más estable (la API cambia menos que el DOM), más liviano (50 KB vs 2 MB) y
suele estar menos defendido. Implementado en `fetchers/capture.py` (`capture_api`):
renderiza, intercepta las responses XHR/fetch y devuelve los endpoints JSON. Para SPAs
y grids dinámicos (ML, gov) esto es lo que de verdad trae los datos.

### 1. TLS realista sin browser
`curl_cffi` impersona el TLS+HTTP2 de un Chrome real (tier 1). Pasa el grueso de los
filtros de fingerprint de red sin el costo de un browser.

### 2. Browser indetectable — Camoufox, NO CDP
Dato clave de 2026: **CDP (Chrome DevTools Protocol) es detectable** por timing,
execution-context leaks y binding exposure. Playwright/Patchright/Crawl4AI usan CDP.
**Camoufox usa el protocolo Juggler de Firefox por debajo de CDP → no filtra CDP**, y
spoofea el fingerprint a nivel C++. Por eso el tier 2 (`stealth.py`) **prefiere Camoufox**
si está instalado, y cae a Patchright. `playwright-stealth` se descarta: `Function.toString()`
expone los parches.

### 3. Comportamiento humano
Render + espera de asentado + scroll (dispara lazy-load y baja la sospecha de "bot que
no se mueve"). Mimicry de mouse/timing más fino queda como mejora incremental.

### 4. Insumos externos del usuario (cuando lo anterior no alcanza)
- **Proxies residenciales/móviles**: imprescindibles contra DataDome/IP-reputation. El
  datacenter viene flagged. Fisherboy NO los trae (nadie los trae en código); se enchufan
  por `PROXIES` o el override por job del panel Avanzado.
- **Cookies de sesión**: para gates de login/ubicación (ej. ML supermercado). Override por job.
- **Solver de CAPTCHA por API** (2captcha-style): último recurso, override por job.

## Consecuencias

Fisherboy implementa la doctrina completa **del lado del código**: API-capture, TLS
impersonation, browser indetectable (Camoufox preferente), comportamiento básico, y los
puntos de enchufe para los insumos externos (proxy residencial, cookies, solver). Lo que
NO se puede hornear en el binario —IPs residenciales y suscripción a solver— se inyecta
por job. La honestidad operativa: contra el top de anti-bot (Cloudflare Enterprise,
DataDome) sin proxy residencial, el sitio puede ganar; con proxy residencial + esta
doctrina, la tasa de éxito es la mejor alcanzable con herramientas open-source.

## Recomendación de instalación para máxima evasión

```
pip install camoufox[geoip]   # tier 2 preferente (sin leak de CDP)
camoufox fetch                 # baja el Firefox endurecido
pip install curl_cffi          # tier 1 (TLS)
# + proxies residenciales en PROXIES / panel Avanzado
```
