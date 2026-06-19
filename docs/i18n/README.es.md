<div align="center">

# 🎣 Fisherboy

**Tu sidekick para extraer datos de la web.**

Apuntalo a cualquier página y recibí **Markdown limpio o JSON estructurado** — listo para
cualquier LLM. Fisherboy escala solo cuando el sitio se pone difícil (estático → fingerprint
TLS → browser stealth → browser real), captura el **JSON/XHR oculto** que ya consumen las
single‑page apps, sigue el paginado y crawlea en árbol, y **anonimiza la PII antes de
entregar**. Self‑hosteable, con su propia UI web o como servicio headless REST + MCP. Parte
de la familia [**Escriba**](https://github.com/diegoparras/escriba).

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](../../LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)

[English](../../README.md) · **Español** · [Français](README.fr.md) · [Português](README.pt.md) · [Italiano](README.it.md) · [中文](README.zh.md) · [日本語](README.ja.md)

</div>

---

## ✨ Funciones

- 🎣 **Cualquier página → Markdown o JSON limpio** — [Crawl4AI](https://github.com/unclecode/crawl4ai) `fit_markdown` (poda navegación/boilerplate por densidad) con fallback a [Trafilatura](https://github.com/adbar/trafilatura); o extracción estructurada a un JSON Schema vía LLM.
- 🪜 **Fetch escalonado (escala solo si lo bloquean)** — tier 0 `httpx` → tier 1 fingerprint TLS (`curl_cffi`) → tier 2 browser stealth (Camoufox/Patchright) → tier 3 browser real (nodriver/Playwright). Un gate detecta bloqueos/CAPTCHAs y sube; el tier ganador se cachea **por dominio**.
- 🛰️ **Captura del API oculto** — en vez de pelear el HTML renderizado, Fisherboy observa el **JSON XHR/fetch** que la página ya carga y se queda con eso. Lo más confiable para SPAs y grids dinámicos.
- 🕷️ **Araña y crawl profundo** — sigue links internos en árbol (con foco por sección), barre el paginado (postback ASP.NET · "siguiente" · `?page=`), y el modo **tarántula** que captura el contenido + API de cada nodo en un árbol de datos.
- 🔌 **Proxies, fácil** — pegá un proxy en **cualquier formato** (`host:puerto` · `host:puerto:user:clave` · `user:clave@host:puerto` · URL) y Fisherboy lo normaliza. Un botón **Probar** rutea una request y muestra tu **IP de salida + país + latencia**, con una pista accionable si no conecta. Pool con rotación/cooldown, override por job, proxies guardados.
- 🍪 **Cookies de sesión, sin extensión** — pegá cookies (Netscape `cookies.txt` / JSON / `nombre=valor`) o leélas directo de tu navegador local (Chrome/Firefox/Edge/Brave) para páginas tras login o región.
- 🛡️ **Anonimización de PII antes de entregar** — tres modos de privacidad acotados por rol: **opaco** (`«PERSONA_1»`), **reversible** (enmascarar → que el LLM razone → rehidratar local) y **directo** (crudo, para data no sensible). Fail‑closed: si la anonimización falla, nunca sale nada crudo. Con el Anonimal de [Escriba](https://github.com/diegoparras/escriba) tenés NER completo; standalone cae a una pasada regex incorporada (email/ID/IP/tarjeta/teléfono).
- ✏️ **Editor incorporado** — abrí el resultado en un modal con pestañas **Markdown · JSON · Tabla**: toolbar de Markdown con vista previa en vivo, editor JSON con validación, y una tabla editable donde **JSON ↔ tabla es cambiar de pestaña**. Descargá `.md` / `.json` / `.csv`.
- 📤 **Descargá todo** — el sobre entero, solo los datos (contenido + registros + árbol + links), o un array plano de registros. Con un click mandás el resultado a **Escriba** para seguir convirtiendo / anonimizando / exportando.
- 🔑 **Tres niveles de acceso** — DIOS / ANGEL / HUMANO, cada uno con su contraseña y límites (qué tiers, proxies, captura, solver, crawl, tarántula).
- 🐳 **Imagen autocontenida** — API + worker + Redis. Corre headless (REST + MCP) detrás de Escriba, o **standalone con su propia UI web**.
- 🛡️ **Endurecido** — fail‑closed por defecto, anti‑SSRF (con re‑validación de cada salto de redirect), scrub de secretos por job, gating por rol en REST **y** MCP, rate‑limiting, contenedor no‑root. Auditado; ver [`docs/ADR-012`](../ADR-012-auditoria-seguridad.md).
- 🌐 **REST + MCP** — manejalo desde `curl`, n8n, Claude Code o Escriba.

---

## 🚀 Arranque rápido (Docker)

El camino más rápido — standalone, con la UI web:

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # poné SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD
docker compose -f docker-compose.standalone.yml up -d --build
# → abrí http://localhost:8000
```

¿No querés buildear? Bajá la imagen publicada:

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **Guía de deploy completa** (Docker Desktop paso a paso, EasyPanel, referencia de env, producción): [`docs/DEPLOY.md`](../DEPLOY.md).

---

## 🧭 Dos modos

Fisherboy corre en uno de dos modos según `APP_MODE`. **El núcleo es idéntico**; el modo solo
decide si se monta la UI web y a dónde se delega la conversión documental.

| | `standalone` | `sidekick` |
|---|---|---|
| UI web | ✅ propia | ❌ headless |
| Interfaz | UI + REST + MCP | REST + MCP |
| Uso | self‑host, personal | detrás de Escriba, red interna |

---

## 🔌 API REST

```http
POST /api/jobs            # valida schema, rol × modo, callback y proxy (SSRF); encola → 202
GET  /api/jobs/{job_id}   # estado y resultado (el "sobre")
POST /api/proxy/test      # rutea una request por un proxy; devuelve IP de salida + país + latencia
POST /api/revert          # rehidrata contenido pseudonimizado (modo reversible)
POST /api/login           # login por rol (sesión por cookie)
GET  /healthz · GET /metrics
```

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H 'content-type: application/json' \
  -d '{"url":"https://ejemplo.com/nota","rol":"angel","privacy_mode":"opaco"}'
# → { "job_id": "…", "status": "pendiente" }
```

Campos del job: `url`, `rol`, `privacy_mode` (`opaco`/`reversible`/`directo`), `output_format`
(`markdown`/`llms_txt`/`json`), `tier_hint` (0–3), `crawl_depth`, `max_pages`, `paginate`,
`capture_api`, `tarantula`, `extract_schema` (para `json`), `proxy`, `cookies`, `callback_url`.

### MCP

El mismo pipeline se expone como herramientas MCP (`submit_job`, `get_job`, `revert`) para que
n8n, Claude Code o Escriba encolen sin hablar HTTP a mano: `python -m app.mcp_server`.

---

## 🔒 Privacidad y roles

El modo se elige **por job** y queda **acotado por el rol** (`privacy_matrix.yaml`, nunca
hardcodeado). Si el rol no habilita el modo pedido, el gateway responde **403** — nunca baja
de modo en silencio.

| Rol | opaco | reversible | directo |
|------|:------:|:----------:|:------:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

Además del NER (cuando hay Anonimal), siempre corre una pasada regex determinística para PII de
alto riesgo (CUIT/CUIL, email, IP, tarjeta con Luhn, teléfono).

---

## 🛡️ Seguridad

Auditado con una revisión adversarial multi‑agente; hallazgos corregidos y fijados por tests
([`docs/ADR-012`](../ADR-012-auditoria-seguridad.md)).

- **Fail‑closed por defecto** — sin contraseñas configuradas devuelve 401; el modo abierto de dev es opt‑in explícito (`FISHERBOY_OPEN_GOD=1`).
- **Anti‑SSRF** — bloquea rangos privados/loopback/link‑local/metadata, re‑validando **cada salto** de redirect y cada request del browser; el proxy override se valida igual.
- **Sin fuga de secretos** — los secretos por job (credenciales de proxy, key de CAPTCHA, cookies) se limpian del sobre y del webhook.
- **Gating por rol en REST y MCP**, rate‑limiting, contenedor no‑root, logs JSON sin PII.

Ver el [checklist de producción](../DEPLOY.md#going-to-production) antes de exponerlo.

---

## 🧩 La familia Escriba

Fisherboy es un satélite standalone de [**Escriba**](https://github.com/diegoparras/escriba), el
hub que convierte cualquier documento en Markdown limpio y anónimo listo para IA. Cada app se
usa sola, pero comparten un sistema de diseño y un handoff de un click **"Enviar a Escriba"** —
así lo que pescás de la web fluye directo a conversión, anonimización, chunking y export.

---

## 📜 Licencia

MIT © 2026 Diego Parrás. Los scrapers de terceros que Fisherboy puede usar llevan sus propias
licencias (en general permisivas: Crawl4AI, Trafilatura — Apache‑2.0; curl_cffi, httpx —
MIT/BSD). Algunos motores opcionales son copyleft de red (AGPL: nodriver, Firecrawl): para uso
personal no comercial no imponen nada; ofrecerlos como servicio comercial exige liberar tus
cambios.

Autoría: Diego Parrás.
