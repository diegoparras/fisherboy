# Fisherboy — sistema supremo de scraping. Documento de construcción

Brief de construcción para Claude Code. El objetivo es arrancar a construir ya, por fases, con la arquitectura completa como destino. No es un documento de discusión; es un plan ejecutable. Donde dice "Fase 0" y "Empezá por esto", ahí arranca el trabajo.

Fisherboy corre en dos modos, elegidos por variable de entorno: como sidekick de Escriba sin interfaz, o standalone con interfaz propia. El núcleo es el mismo; el modo decide qué se monta encima.

---

## 0. Identidad y modos

Fisherboy adquiere contenido web, lo convierte a markdown o JSON, lo anonimiza según la política del job, y lo entrega. Repo propio, independiente de Escriba.

`APP_MODE` define el modo:

- `sidekick` (default): no sirve interfaz. Se lo llama por REST y por MCP desde n8n, desde Claude Code o desde Escriba. Delega la conversión documental de PDFs y docs linkeados a la API de Escriba. Usa Anonimal por red interna. Pensado para vivir detrás de Escriba.
- `standalone`: monta interfaz web propia para cargar jobs y ver resultados, con su propio gateway. Puede correr conversión propia o seguir llamando a Escriba si `ESCRIBA_URL` está seteada. La interfaz es lo único que cambia; el pipeline es idéntico.

En los dos modos quedan disponibles el REST y el MCP. La diferencia operativa es si se mucha el router de UI y a quién se delega la conversión.

---

## 1. Arquitectura objetivo

Ocho capas. Es el destino, no lo que se construye el primer día.

0. Superficie: FastAPI REST, servidor MCP con FastMCP, webhooks, y router de UI solo si `APP_MODE=standalone`.
1. Orquestación: cola sobre Redis con workers, Crawlee para cola persistente y sesiones, dedup por SHA-256, cache por hash de URL con TTL, robots.txt y rate limit.
2. Discovery: Katana para mapeo de URLs y endpoints, sitemap y RSS.
3. Fetch escalonado por costo: tier 0 httpx, tier 1 Scrapling Fetcher con curl_cffi, tier 2 StealthyFetcher Patchright y Camoufox, tier 3 nodriver o Playwright. Router de gate, cache de tier por dominio, rotación de proxy, CAPTCHA con prevención primero.
4. Parsing auto-reparable: Scrapling con selectores self-healing, parsers base.
5. Conversión y extracción: Crawl4AI HTML a markdown, Trafilatura para texto principal, sub-pipeline documental delegado a Escriba, extracción estructurada por LLM con validación Pydantic.
6. Anonimización: Anonimal, con los tres modos de privacidad.
7. Salida: markdown o JSON validado, llms.txt, vector store, Postgres, webhook de retorno.
8. Observabilidad: logs JSON desde el día uno, Prometheus, Loki y Grafana después.

---

## 2. Plan por fases

Cada fase termina en algo que funciona end to end. La bestia completa es la suma de las cuatro.

### v1 — núcleo liviano
Camino mínimo real: REST recibe un job, valida, encola; worker hace fetch estático con httpx, convierte con Trafilatura, anonimiza con Anonimal en modo opaco, entrega por webhook. Sin browser, sin discovery, sin LLM. Logs JSON estructurados. Esqueleto de seguridad ya puesto.

### v2 — fetch rico y extracción
Tiers de fetch con Scrapling y curl_cffi, Crawl4AI como conversor, router de tier con cache por dominio, parsing auto-reparable, extracción estructurada por LLM por API, modo de privacidad reversible.

### v3 — crawling, persistencia, observabilidad
Crawlee para multipágina, Katana para discovery, Postgres y pgvector, Prometheus Loki Grafana.

### v4 — targets difíciles
Camoufox y Playwright, nodriver como tier de último recurso, CAPTCHA con Whisper local y solver externo en reserva, sub-pipeline documental completo.

---

## 3. Contrato del sobre

Modelo Pydantic compartido. Todo lo importa. Definirlo primero.

```python
from enum import Enum, IntEnum
from datetime import datetime
from pydantic import BaseModel, Field, HttpUrl


class PrivacyMode(str, Enum):
    REVERSIBLE = "reversible"
    OPACO = "opaco"
    DIRECTO = "directo"


class Rol(str, Enum):
    DIOS = "dios"
    ANGEL = "angel"
    HUMANO = "humano"


class FetchTier(IntEnum):
    ESTATICO = 0
    TLS = 1
    STEALTH = 2
    BROWSER = 3


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    LLMS_TXT = "llms_txt"


class JobStatus(str, Enum):
    PENDIENTE = "pendiente"
    EN_PROCESO = "en_proceso"
    OK = "ok"
    ERROR = "error"


class JobRequest(BaseModel):
    url: HttpUrl
    rol: Rol
    privacy_mode: PrivacyMode
    output_format: OutputFormat = OutputFormat.MARKDOWN
    extract_schema: dict | None = None
    crawl_depth: int = 0
    max_pages: int = 1
    tier_hint: FetchTier | None = None
    callback_url: HttpUrl | None = None


class Sobre(BaseModel):
    job_id: str
    source_url: HttpUrl
    privacy_mode: PrivacyMode
    rol: Rol
    status: JobStatus = JobStatus.PENDIENTE
    tier_usado: FetchTier | None = None
    content_md: str | None = None
    content_json: dict | None = None
    mapping_ref: str | None = None
    anonimizado: bool = False
    error: str | None = None
    fetched_at: datetime | None = None
    meta: dict = Field(default_factory=dict)
```

---

## 4. Privacidad

El modo de privacidad solo importa en la rama de extracción por LLM, que sale a un proveedor externo. La rama de conversión local siempre pasa por Anonimal antes de salir.

Reglas:

1. Validar el rol contra el modo pedido antes de encolar. Si el rol no lo habilita, responder 403 con mensaje claro. No bajar de modo en silencio.
2. Rama local: contenido, Anonimal anonimiza, salida.
3. LLM reversible: Anonimal pseudonimiza con tabla de mapeo cifrada local, el LLM ve pseudónimos, devuelve JSON con pseudónimos, Anonimal revierte local.
4. LLM opaco: Anonimal anonimiza one-way, el LLM ve tokens, no se revierte.
5. LLM directo: contenido crudo al LLM, sale dato real. Solo para data no sensible.

Matriz rol por modo en config, no hardcodeada. Piso a confirmar: humano solo opaco, angel opaco y reversible, dios los tres.

Amenaza primaria del modo reversible: la garantía está acotada por la recall de detección de spans de Anonimal, por encima del cifrado de la tabla. Un span no detectado viaja al LLM sin enmascarar. Ver ADR-005. La capa de privacidad incluye una pasada determinística previa para PII de alto riesgo atrapable por regla: CUIT, CUIL, email, IP, tarjeta, teléfono. Fallar cerrado: si la cobertura cae bajo umbral, el job no va al LLM en reversible.

---

## 5. Seguridad

No opcional. Se construye en v1.

1. Fallar cerrado: si el job pidió anonimización y Anonimal falla, no se devuelve contenido crudo. El job queda en error.
2. SSRF de entrada: bloquear fetch a IP privada, loopback, link-local, metadata de cloud. Controlar redirects, límite de bytes, timeout.
3. SSRF de salida: el `callback_url` lo provee el usuario y el worker le hace POST. Validar contra los mismos bloques, o restringir a allowlist en producción.
4. Anonimal hoy no tiene auth propia y fue pensado solo detrás de Escriba. Al sumar Fisherboy como segundo llamador, decidir en Fase 0 si Anonimal suma auth de servicio o si alcanza con política de red estricta.

---

## 6. Licencias, comunicación clara

No hay restricción de uso propio porque el proyecto no se comercializa. Esta sección existe para que cualquiera que forkee sepa qué puede hacer con cada pieza. Comunicarla en el README.

Permisivas, sin obligación para nadie: Crawl4AI Apache 2.0, Scrapling BSD-3, Trafilatura Apache 2.0 desde v1.8, Crawlee Apache 2.0, Katana MIT, MarkItDown MIT, Docling MIT, curl_cffi MIT, httpx BSD.

Con copyleft de red, AGPL-3.0: nodriver y Firecrawl. Para uso propio no comercial no imponen nada. Quien forkee y lo ofrezca como servicio comercial debe liberar sus modificaciones. Decirlo explícito en el README, al lado de la dependencia.

A verificar al integrar, porque cambian o son ambiguas: ScrapeGraphAI, Patchright, Camoufox, Marker.

Como no hay límite comercial, nodriver entra como un tier más sin necesidad de aislarlo por motivo legal. Si se lo aísla como subproceso, que sea por razón técnica, no de licencia.

---

## 7. Estructura de repo

```txt
app/
  main.py
  models.py            # el Sobre y los enums
  config.py            # lee APP_MODE y el resto del entorno
  privacy_policy.py    # matriz rol por modo, desde YAML/env
  queue.py
  worker.py
  security/
    ssrf.py            # validacion de entrada y de callback
  fetchers/
    static.py          # httpx, v1
  extractors/
    text_main.py       # Trafilatura, v1
  privacy/
    anonimal_client.py
  callbacks.py
  logging.py
  ui/                  # router montado solo si APP_MODE=standalone
tests/
docs/
  ADR-001..005.md
docker-compose.yml
Dockerfile
.env.example
README.md
```

---

## 8. Entorno

```env
APP_MODE=sidekick            # sidekick | standalone
REDIS_URL=redis://fisherboy-redis:6379/0
ANONIMAL_URL=http://anonimal:8xxx
ESCRIBA_URL=http://escriba-api:8000     # requerido en sidekick, opcional en standalone
LLM_API_BASE_URL=            # proveedor por API para la rama de extraccion
LLM_API_KEY=
DATABASE_URL=                # Postgres, desde v3
PRIVACY_MATRIX_PATH=/app/privacy_matrix.yaml
```

---

## 9. docker-compose v1

```yaml
services:
  fisherboy-api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    env_file: .env
    depends_on: [fisherboy-redis]
    networks: [fisherboy, escriba_internal]

  fisherboy-worker:
    build: .
    command: python -m app.worker
    env_file: .env
    depends_on: [fisherboy-redis]
    networks: [fisherboy, escriba_internal]

  fisherboy-redis:
    image: redis:7-alpine
    command: redis-server --save "" --appendonly no
    networks: [fisherboy]

networks:
  fisherboy:
  escriba_internal:
    external: true
```

Postgres entra en v3.

---

## 10. Endpoints v1

```http
POST /api/jobs            # valida schema, rol por modo y callback_url; encola
GET  /api/jobs/{job_id}   # estado y resultado
GET  /healthz
```

Orden de validación en POST: schema, rol por modo, callback_url contra bloques privados. Recién después encola.

---

## 11. Tests v1

Primero los de seguridad y privacidad, después el happy path.

```txt
test_privacy_role_denied_returns_403
test_fail_closed_on_anonimization_failure
test_inbound_ssrf_blocked
test_outbound_callback_ssrf_blocked
test_app_mode_sidekick_no_ui
test_app_mode_standalone_serves_ui
test_happy_path_static_to_markdown_opaco
test_sobre_contract_roundtrip
```

---

## 12. Fase 0, antes de codear v1

1. Inspeccionar el repo de Escriba y de Anonimal con acceso real. Confirmar el contrato actual de Anonimal: hoy expone `POST /anonymize` y `GET /health`, devuelve text, detected_spans, redacted_text, summary, sin pseudonimización reversible y sin auth propia.
2. Diseñar y estabilizar el contrato reversible de Anonimal: pseudonymize, revert, mapping_ref, TTL derivado del ciclo de vida del job, custodia de la clave, control de reversión por rol. No verificar si existe; diseñarlo.
3. Escribir los ADR-001 a 005. El 005, threat model del modo reversible, ya está redactado y se adjunta.
4. Definir la matriz rol por modo.

---

## Empezá por esto

1. Modelo del sobre, sección 3.
2. Esqueleto de Capa 0 con FastAPI, los tres endpoints de la sección 10, y el flag `APP_MODE` montando o no el router de UI.
3. Módulo de seguridad, sección 5, con SSRF de entrada y de callback, y el principio de fallar cerrado.
4. Camino v1 completo: httpx, Trafilatura, Anonimal opaco, webhook.
5. Los tests de la sección 11, los de seguridad primero.

Lo demás entra por fases. No construir browser, discovery ni LLM hasta que v1 atraviese una URL de punta a punta.
