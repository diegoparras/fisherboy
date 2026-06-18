# ADR-009 — Observabilidad, parsing auto-reparable, vectores y frontera

Estado: aceptado
Fecha: 2026-06-18

## Contexto

Cierre de las capas que faltaban del documento de construcción: observabilidad
completa (Capa 8), parsing auto-reparable (Capa 4), vector store (Capa 7) y cola de
crawl persistente con sesiones (Capa 1, espíritu Crawlee).

## Decisión

### Observabilidad (Capa 8)

La app ya emite **logs JSON** (una línea por evento, sin PII) y **métricas Prometheus**
en `/metrics` desde el día uno. El backend de visualización se agrega como stack aparte
(`docker-compose.observability.yml`): Prometheus scrapea `/metrics`, Promtail tail-ea
los logs JSON de los contenedores y los manda a Loki, y Grafana los muestra con
datasources y un dashboard ya provisionados (jobs por estado, fetches por tier, CAPTCHAs
por proveedor, logs de error). Es opcional: la app corre igual sin el stack.

### Parsing auto-reparable (Capa 4)

`app/parsing/adaptive.py`. Además del selector, se guarda un fingerprint del elemento
(tag, id, clases, atributos, texto). Si el selector falla en una corrida futura, se
relocaliza el elemento por similitud con el fingerprint y se sugiere un selector nuevo.
Implementado sobre lxml; Scrapling queda como hook. Los fingerprints se persisten por
perfil (dominio). Esto sobrevive cambios de DOM que romperían un scraper rígido.

### Vector store (Capa 7)

`app/store/vectors.py`. `EmbeddingClient` genera embeddings vía un proveedor
OpenAI-compatible (`/embeddings`, inyectable). `InMemoryVectorIndex` hace búsqueda
coseno sin DB (fallback y base de tests); `PgVectorStore` persiste y busca con pgvector
(`<=>`). El pipeline, si `EMBEDDINGS_ENABLED` y hay LLM + Postgres, embebe el contenido
del sobre tras procesarlo. Degradable: un fallo de embeddings nunca tumba el job.

### Frontera de crawl persistente + sesiones (Capa 1)

`app/crawl/frontier.py`. `RedisFrontier` mantiene cola de pendientes + set de visitadas
bajo un `crawl_id`: si el worker muere, otro retoma el crawl donde quedó. `SessionStore`
guarda cookies por dominio para reusar sesión entre crawls. Crawlee-python queda como
hook (runtime propio, pesado); esta frontera propia cubre cola persistente y sesiones con
Redis. La dedup por contenido (SHA-256) la sigue haciendo el crawler.

## Consecuencias

Con esto, todas las capas del documento de construcción tienen implementación real o un
hook claro. Lo pesado (Crawl4AI, browsers, Postgres, Crawlee, Scrapling) es opcional y
perezoso; la imagen base sigue liviana. Lo que queda es profundización, no capas nuevas:
afinar el dashboard, sumar el runtime de Crawlee si hace falta escala, generar embeddings
en batch, y la auth de servicio de Escriba (para cerrar el sub-pipeline documental, hoy
bloqueado por el login por cookie de Escriba — ver ADR-001).
