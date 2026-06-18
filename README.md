# 🎣 Fisherboy

**Sistema supremo de scraping.** Adquiere contenido web, lo convierte a markdown o
JSON, lo anonimiza según la política del job y lo entrega. El papá de todos los
scrapers.

Corre en dos modos, elegidos por una variable de entorno:

- **`sidekick`** (default) — sin interfaz. Se lo llama por REST y por MCP desde n8n,
  Claude Code o Escriba. Delega la conversión documental de PDFs y docs a Escriba.
  Vive detrás de la red interna.
- **`standalone`** — monta su propia interfaz web para cargar jobs y ver resultados.

El núcleo es **idéntico** en los dos modos. El modo solo decide si se monta el router
de UI y a quién se delega la conversión. No es un producto comercial: es
infraestructura propia.

> Estado: **v1 — núcleo liviano**, funcionando de punta a punta. Las fases siguientes
> (fetch rico, crawling, targets difíciles) están planificadas abajo.

---

## Qué hace hoy (v1)

```
URL → fetch estático (httpx) → markdown (Trafilatura) → Anonimal (opaco) → entrega
```

1. El REST recibe un job, valida el **schema**, el **rol × modo de privacidad** y el
   **callback_url** contra bloqueos SSRF, y recién entonces lo **encola**.
2. Un **worker** saca el job, hace fetch estático, extrae el texto principal a
   markdown, lo **anonimiza** con Anonimal en modo opaco y lo entrega por webhook.
3. Logs JSON estructurados desde el día uno. Esqueleto de seguridad completo.

**Toda salida pasa por Anonimal antes de salir** (rama de conversión local). Si la
anonimización falla, el job termina en error y **nunca** se devuelve contenido crudo
(_fail-closed_).

---

## Arranque rápido

### Local (dev)

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements-dev.txt

# API standalone con UI propia:
APP_MODE=standalone ANONIMAL_URL=http://localhost:8080 \
  uvicorn app.main:app --reload --port 8000
# → abrí http://localhost:8000  (UI)  y  http://localhost:8000/docs  (API)

# Worker (otra terminal):
python -m app.worker

# Tests:
pytest
```

### Docker

```bash
docker network create escriba_internal   # si todavía no existe
cp .env.example .env                      # ajustá ANONIMAL_URL, APP_MODE, etc.
docker compose up --build
```

---

## API REST

```http
POST /api/jobs            # valida schema, rol×modo y callback_url; encola → 202
GET  /api/jobs/{job_id}   # estado y resultado (el "sobre")
POST /api/revert          # rehidrata contenido pseudonimizado (modo reversible)
GET  /healthz
GET  /metrics             # métricas Prometheus
```

Campos del job: `url`, `rol`, `privacy_mode`, `output_format` (`markdown`/`llms_txt`/
`json`), `tier_hint` (0-3), `crawl_depth`, `max_pages`, `extract_schema` (para `json`),
`callback_url`.

Ejemplo:

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H 'content-type: application/json' \
  -d '{"url":"https://ejemplo.com/nota","rol":"angel","privacy_mode":"opaco"}'
# → { "job_id": "…", "status": "pendiente" }

curl http://localhost:8000/api/jobs/<job_id>
# → el sobre con content_md anonimizado cuando status == "ok"
```

### MCP

El mismo pipeline se expone como herramientas MCP (`submit_job`, `get_job`) para que
n8n, Claude Code o Escriba encolen sin hablar HTTP a mano:

```bash
python -m app.mcp_server     # requiere fastmcp
```

---

## Fetch escalonado: tiers, proxies y anti-CAPTCHA

Fisherboy escala por costo: arranca barato y sube solo cuando el sitio bloquea.

```
tier 0 httpx  →  tier 1 TLS  →  tier 2 stealth  →  tier 3 browser
   (base)        (curl_cffi)     (Camoufox)         (nodriver/Playwright)
```

- **Router de gate** — detecta bloqueo (403/429/WAF) o CAPTCHA y **escala** al
  siguiente tier. Un 404 real no escala. Cachea el tier ganador **por dominio** para
  no re-pagar el escalado en la próxima URL del mismo sitio.
- **Proxies** — pool con rotación `round_robin` / `random` / `sticky` (misma IP por
  dominio), cooldown ante proxy quemado, soporte autenticado y SOCKS5. **Funcionan
  desde el tier 0.** Se configuran con `PROXIES=...` en el `.env`.
- **Anti-CAPTCHA, prevención primero** — se detecta el desafío (Cloudflare,
  reCAPTCHA, hCaptcha, DataDome, PerimeterX, Arkose) y la defensa primaria es subir
  de tier (un browser stealth lo previene). El solver por API es un hook opcional
  (`CAPTCHA_SOLVER=external`).

Los tiers altos usan **import perezoso**: la imagen base es liviana y cada tier se
enciende instalando su lib. El router detecta qué hay y arma la cadena solo.

```bash
pip install curl_cffi      # tier 1
pip install camoufox       # tier 2  (o patchright)
pip install nodriver       # tier 3  (o playwright)
```

Detalle en [`docs/ADR-006`](docs/ADR-006-fetch-escalonado.md).

## Privacidad

El modo de privacidad se elige **por job** y queda **acotado por el rol**
(`privacy_matrix.yaml`, nunca hardcodeado):

| Rol      | opaco | reversible | directo |
|----------|:-----:|:----------:|:-------:|
| `humano` |   ✅  |     —      |    —    |
| `angel`  |   ✅  |     ✅     |    —    |
| `dios`   |   ✅  |     ✅     |    ✅   |

Si el rol no habilita el modo pedido, el gateway responde **403** y no encola. Nunca
se baja de modo en silencio.

- **opaco** (v1) — cada entidad se reemplaza por un marcador tipado y estable
  («PERSONA_1», «CUIT_2»). El LLM razona relacional sin ver PII; el valor real no se
  recupera.
- **reversible** (v2) — igual, pero guardando una tabla de mapeo cifrada para
  rehidratar después. Su modelo de amenaza está en [`docs/ADR-005`](docs/ADR-005-reversible-threat-model.md):
  la garantía está acotada por la _recall_ de detección, no por el cifrado.
- **directo** — sin anonimizar, solo para data no sensible (rama LLM, v2).

Además del modelo de Anonimal, corre una **pasada determinística por regla** para PII
de alto riesgo (CUIT/CUIL, email, IP, tarjeta con Luhn, teléfono).

---

## Seguridad (construida en v1, no diferida)

- **Fail-closed**: si se pidió anonimización y Anonimal falla, el job queda en error;
  jamás sale contenido crudo.
- **SSRF de entrada**: antes de hacer fetch se resuelve el DNS y se bloquean IP
  privada, loopback, link-local y metadata de cloud (169.254.169.254). Re-validación
  en cada redirect contra DNS rebinding. Tope de bytes, timeout y máximo de redirects.
- **SSRF de salida**: el `callback_url` se valida contra los mismos bloqueos, con
  allowlist opcional en producción.
- **Secretos por entorno**, nunca en logs. Los logs JSON no incluyen PII ni contenido.

Detalle en [`docs/ADR-004`](docs/ADR-004-seguridad.md).

---

## Arquitectura objetivo (8 capas)

El destino, no lo que corre el primer día:

0. **Superficie** — FastAPI REST, MCP (FastMCP), webhooks, router de UI si standalone.
1. **Orquestación** — cola Redis con workers, dedup SHA-256, cache por hash de URL,
   robots.txt, rate limit (Crawlee en v3).
2. **Discovery** — Katana, sitemap, RSS.
3. **Fetch escalonado por costo** — tier 0 httpx · tier 1 Scrapling/curl_cffi · tier 2
   stealth (Patchright/Camoufox) · tier 3 nodriver/Playwright. Router de gate, cache de
   tier por dominio, rotación de proxy.
4. **Parsing auto-reparable** — Scrapling con selectores self-healing.
5. **Conversión y extracción** — Crawl4AI (HTML→md), Trafilatura, sub-pipeline
   documental delegado a Escriba, extracción estructurada por LLM con Pydantic.
6. **Anonimización** — Anonimal, tres modos de privacidad.
7. **Salida** — markdown / JSON validado / llms.txt / vector store / Postgres / webhook.
8. **Observabilidad** — logs JSON desde el día uno; Prometheus, Loki, Grafana después.

### Plan por fases

- **v1 — núcleo liviano** ✅ — el camino de arriba, de punta a punta.
- **fetch escalonado** ✅ — router de tiers, **proxies con rotación**, detección de
  CAPTCHA y solver pluggable. Tier 0 + proxies andan hoy; tiers 1-3 se encienden
  instalando su lib.
- **conversión + extracción** ✅ — Crawl4AI (con fallback Trafilatura), **extracción
  estructurada por LLM** con validación de schema, y **modo reversible end-to-end**
  (pseudonimiza → LLM → re-hidrata). Ver [`docs/ADR-008`](docs/ADR-008-extraccion-llm-reversible.md).
- **crawling + discovery** ✅ — crawler BFS multipágina, dedup por contenido,
  sitemap/RSS/links, robots.txt. Katana como hook. Ver [`docs/ADR-007`](docs/ADR-007-crawling-discovery.md).
- **persistencia + observabilidad** ✅ — Postgres + pgvector, métricas Prometheus en
  `/metrics`, stack Loki/Grafana provisionado (`docker-compose.observability.yml`).
- **parsing auto-reparable** ✅ — selectores self-healing que relocalizan por
  fingerprint cuando el DOM cambia (`app/parsing/adaptive.py`).
- **vector store** ✅ — embeddings (OpenAI-compat) + búsqueda coseno/pgvector.
- **crawl persistente** ✅ — frontera resumible por `crawl_id` + sesiones por dominio
  (`app/crawl/frontier.py`).
- **sub-pipeline documental** ✅ — PDFs/docs delegados a Escriba (`POST /api/convert`);
  pendiente solo la auth de servicio del lado de Escriba (ADR-001).
- **profundización futura** — runtime de Crawlee a escala, embeddings en batch,
  dashboards más finos. Todas las capas del build doc ya tienen implementación o hook.

---

## Licencias (comunicación clara)

No hay restricción de uso propio: el proyecto **no se comercializa**. Esta sección
existe para quien forkee.

- **Permisivas** (sin obligación): Crawl4AI (Apache 2.0), Scrapling (BSD-3),
  Trafilatura (Apache 2.0), Crawlee (Apache 2.0), Katana (MIT), MarkItDown/Docling
  (MIT), curl_cffi (MIT), httpx (BSD).
- **Copyleft de red (AGPL-3.0)**: nodriver, Firecrawl. Para uso propio no comercial no
  imponen nada; **quien lo ofrezca como servicio comercial debe liberar sus
  modificaciones**.
- **A verificar al integrar**: ScrapeGraphAI, Patchright, Camoufox, Marker.

El código propio de Fisherboy es MIT.

---

## Estructura

```txt
app/
  main.py              # superficie REST + montaje de UI según APP_MODE
  models.py            # el Sobre y los enums (contrato compartido)
  config.py            # lee APP_MODE y el entorno
  privacy_policy.py    # matriz rol×modo desde YAML
  queue.py             # cola Redis + store del sobre
  worker.py            # saca jobs, corre el pipeline, hace callback
  pipeline.py          # fetch → extract → anonimizar (inyectable, testeable)
  mcp_server.py        # herramientas MCP (sidekick)
  callbacks.py         # webhook de retorno (SSRF de salida)
  logging.py           # logs JSON sin PII
  security/ssrf.py     # SSRF de entrada y de callback
  fetchers/
    base.py            # Fetcher protocol, FetchResult, errores de escalado
    router.py          # TierRouter: gate, escalado, cache de tier por dominio
    static.py          # tier 0 httpx (+ proxy)
    tls.py             # tier 1 curl_cffi (fingerprint TLS)
    stealth.py         # tier 2 Camoufox / Patchright
    browser.py         # tier 3 nodriver / Playwright
  net/
    proxies.py         # pool de proxies: rotación, cooldown, sticky
    captcha.py         # detección de CAPTCHA + solver pluggable
  crawl/
    crawler.py         # BFS multipágina + dedup por contenido
    discovery.py       # links / sitemap / RSS (+ hook Katana)
    robots.py          # respeto de robots.txt cacheado
  extractors/
    text_main.py       # Trafilatura
    convert.py         # Crawl4AI con fallback a Trafilatura
    llm_extract.py     # extracción estructurada por LLM + validación
  privacy/
    anonimal_client.py # cliente + armado opaco «TIPO_N»
    reversible.py      # pseudonimización cifrada + revert (modo reversible)
    detectors.py       # pasada determinística de PII de alto riesgo
  output/formats.py    # llms.txt, bundle multipágina
  store/postgres.py    # persistencia Postgres + pgvector (opcional)
  obs/metrics.py       # métricas Prometheus
  ui/                  # interfaz propia (solo standalone)
tests/                 # seguridad y privacidad primero
docs/                  # ADR-001..008 + documento de construcción
```

---

## Documentación de diseño

- [`docs/FISHERBOY-build.md`](docs/FISHERBOY-build.md) — documento de construcción.
- [`docs/ADR-001`](docs/ADR-001-arquitectura-y-modos.md) — arquitectura y modos.
- [`docs/ADR-002`](docs/ADR-002-modos-privacidad.md) — modos de privacidad y mapeo a Anonimal.
- [`docs/ADR-003`](docs/ADR-003-contrato-anonimal.md) — contrato de Anonimal y auth.
- [`docs/ADR-004`](docs/ADR-004-seguridad.md) — modelo de seguridad.
- [`docs/ADR-005`](docs/ADR-005-reversible-threat-model.md) — threat model del modo reversible.
- [`docs/ADR-006`](docs/ADR-006-fetch-escalonado.md) — fetch escalonado, proxies y anti-CAPTCHA.
- [`docs/ADR-007`](docs/ADR-007-crawling-discovery.md) — crawling, discovery y persistencia.
- [`docs/ADR-008`](docs/ADR-008-extraccion-llm-reversible.md) — extracción por LLM y reversible end-to-end.
- [`docs/ADR-009`](docs/ADR-009-observabilidad-y-cierre.md) — observabilidad, parsing auto-reparable, vectores y frontera.

Autoría: Diego Parras.
