# ADR-006 — Fetch escalonado por costo, proxies y anti-CAPTCHA

Estado: aceptado
Fecha: 2026-06-18

## Contexto

La Capa 3 del documento de construcción define un fetch en cuatro escalones por
costo (tier 0 httpx → tier 1 TLS → tier 2 stealth → tier 3 browser), con router de
gate, cache de tier por dominio, rotación de proxy y CAPTCHA con prevención primero.
v1 solo tenía el tier 0. Este ADR fija cómo se construye el resto sin romper la
disciplina de fases ni sumar dependencias obligatorias pesadas.

## Decisión

### 1. Una interfaz, cuatro tiers

Todos los fetchers implementan el mismo `Fetcher` (`app/fetchers/base.py`): reciben
URL + `FetchContext` y devuelven `FetchResult`, o levantan `BlockedError` /
`CaptchaError` para pedir escalado. El router es ciego al tier concreto.

- **tier 0 — `static`** (httpx): siempre disponible, es la dependencia base.
- **tier 1 — `tls`** (curl_cffi): fingerprint TLS realista (JA3/JA4) sin browser.
- **tier 2 — `stealth`** (Camoufox / Patchright): browser indetectable con JS.
- **tier 3 — `browser`** (nodriver / Playwright): Chrome real, último recurso.

Los tiers 1-3 usan **import perezoso**: si su librería no está instalada,
`available()` devuelve False y el router los **salta**. La bestia entera está
cableada; cada tier se enciende instalando su `extra` (ver requirements). Esto
mantiene la imagen base liviana y honesta: no se promete lo que no está instalado.

### 2. Router de gate con escalado

`TierRouter` (`app/fetchers/router.py`) arranca en el tier más barato disponible y
sube solo cuando hay señal de bloqueo:

- **Bloqueo** (403/429/WAF por header): puede ser por IP. Rota proxy dentro del
  mismo tier hasta `PROXY_ATTEMPTS`; si persiste, sube de tier.
- **CAPTCHA**: un proxy no lo arregla. Sube de tier directo (un browser previene, o
  en último recurso el solver resuelve).
- **Error real** (404, tamaño, redirects): subir no lo arregla. No escala; con proxy
  reintenta una vez por si el proxy está muerto, si no falla.

El `tier_hint` del job fuerza el tier mínimo de arranque (0-3, topeado por
`MAX_FETCH_TIER`).

### 3. Cache de tier por dominio

El tier que gana se cachea por dominio (`RedisTierCache` con TTL, compartida entre
workers; `InMemoryTierCache` si no hay Redis). La próxima URL del mismo sitio arranca
directo en el tier que ya sabemos que funciona, sin re-pagar el escalado.

### 4. Proxies

`ProxyPool` (`app/net/proxies.py`) con estrategias `round_robin`, `random` y
`sticky` (misma IP de salida por dominio, para coherencia de sesión/fingerprint).
Ante fallo, el proxy entra en **cooldown** progresivo; si todos están quemados, se
sale directo (mejor intentar que no intentar). Soporta proxies autenticados
(`scheme://user:pass@host:port`) y SOCKS5. Funciona **desde el tier 0**.

### 5. Anti-CAPTCHA: prevención primero

`app/net/captcha.py` detecta desafíos por marcadores (Cloudflare, reCAPTCHA,
hCaptcha, DataDome, PerimeterX, Arkose) y por headers de WAF. La detección corre en
cada respuesta de cualquier tier. La defensa primaria es **escalar** (un browser
stealth bien hecho previene el CAPTCHA). El **solver** es pluggable y opcional:
`none` (default, solo escala) o `external` (delega a un servicio por API, hook
cableado en los tiers con browser). La PII nunca interviene acá: esto es pre-fetch.

## Consecuencias

La imagen base sigue liviana (solo httpx); los tiers caros se instalan a demanda. El
escalado más la cache de tier minimizan el costo: la mayoría de los sitios se
resuelven en tier 0/1, y solo los hostiles llegan al browser. El sesgo del router es
no malgastar: nunca sube de tier por un 404, y rota proxy antes de escalar ante un
bloqueo por IP. La garantía del solver externo queda explícitamente acotada y
opcional; la apuesta fuerte es la prevención por stealth, no la resolución.

## Extras de instalación

```
pip install curl_cffi          # tier 1 (TLS)
pip install camoufox           # tier 2 (stealth, Firefox); o: pip install patchright
pip install nodriver           # tier 3 (browser); o: pip install playwright
```
