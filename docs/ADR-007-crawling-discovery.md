# ADR-007 — Crawling multipágina, discovery y persistencia

Estado: aceptado
Fecha: 2026-06-18

## Contexto

Un job podía traer una sola URL. La Capa 1-2 del build doc pide crawling multipágina,
discovery (sitemap/RSS/Katana), dedup, robots.txt y persistencia (Capa 7). Este ADR
fija cómo se construye sin reescribir el pipeline ni el router.

## Decisión

### Crawler BFS sobre el router

`app/crawl/crawler.py` recorre en anchura desde la URL semilla hasta `crawl_depth`
saltos o `max_pages` páginas (lo que llegue primero). Cada página se trae con el
**TierRouter** (hereda proxies, escalado y anti-captcha). Dedup doble: por URL
normalizada (sin fragmento) y por **SHA-256 del contenido** (misma página servida en
dos URLs no se procesa dos veces). Una página que falla no corta el crawl.

El pipeline arma el markdown de cada página, lo concatena en un bundle con encabezado
por URL, y lo pasa por la rama de privacidad como un solo documento.

### Discovery

`app/crawl/discovery.py`: extracción de links del HTML (filtra fragmentos, mailto/js,
y opcionalmente externos), parseo de `sitemap.xml`/`sitemapindex` y de feeds RSS/Atom,
todo con stdlib sobre texto ya traído. **Katana** queda como hook (`katana_available()`
detecta el binario en PATH) para mapeo profundo de endpoints; sin él, links + sitemap +
RSS cubren el grueso.

### robots.txt

`app/crawl/robots.py`: `RobotsChecker` cachea el parser por origen. El texto del
robots.txt se inyecta vía callable (el crawler lo trae con el router), así el módulo no
toca la red y se testea con texto fijo. Default permisivo si no hay robots accesible; el
límite duro lo ponen `crawl_depth`/`max_pages`. Se respeta salvo `RESPECT_ROBOTS=0`.

### Persistencia (Capa 7)

`app/store/postgres.py`: opcional y degradable. Sin `DATABASE_URL` o sin `psycopg`,
`available()` es False y el guardado es no-op (el sobre vive en Redis con TTL). Cuando
está, persiste cada sobre y, si pgvector está disponible, agrega una columna de
embedding para búsqueda semántica (la generación del embedding queda para sumar junto
con el vector store). Una DB caída nunca tumba el worker: se loguea y se sigue.

## Consecuencias

El crawl reusa toda la maquinaria de fetch sin duplicarla. El dedup por contenido evita
reprocesar páginas espejo. La persistencia es aditiva: quien no la configura corre igual
con Redis. Crawlee (cola persistente, sesiones) y el vector store completo entran en v3
sobre estas costuras.
