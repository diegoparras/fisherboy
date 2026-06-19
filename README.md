<div align="center">

# 🎣 Fisherboy

**Your sidekick for extracting data from the web.**

Point it at any page and get back **clean Markdown or structured JSON** — ready for any
LLM. Fisherboy escalates only when a site fights back (static → TLS fingerprint → stealth
browser → real browser), captures the **hidden JSON/XHR** that single‑page apps already
consume, follows pagination and crawls in a tree, and **anonymizes PII before it leaves**.
Self‑hostable, with its own web UI or as a headless REST + MCP service. Part of the
[**Escriba**](https://github.com/diegoparras/escriba) family.

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)
![Tests](https://img.shields.io/badge/tests-151%20passing-30d158.svg)
![Hardened](https://img.shields.io/badge/security-audited-0f8f6a.svg)

**English** · [Español](docs/i18n/README.es.md) · [Français](docs/i18n/README.fr.md) · [Português](docs/i18n/README.pt.md) · [Italiano](docs/i18n/README.it.md) · [中文](docs/i18n/README.zh.md) · [日本語](docs/i18n/README.ja.md)

</div>

---

## ✨ Features

- 🎣 **Any page → clean Markdown or JSON** — [Crawl4AI](https://github.com/unclecode/crawl4ai) `fit_markdown` (prunes nav/boilerplate by density) with a [Trafilatura](https://github.com/adbar/trafilatura) fallback; or structured extraction to a JSON Schema via an LLM.
- 🪜 **Tiered fetch (escalates only when blocked)** — tier 0 `httpx` → tier 1 TLS fingerprint (`curl_cffi`) → tier 2 stealth browser (Camoufox/Patchright) → tier 3 real browser (nodriver/Playwright). A gate detects blocks/CAPTCHAs and steps up; the winning tier is cached **per domain**.
- 🛰️ **Hidden API capture** — instead of fighting the rendered HTML, Fisherboy watches the **XHR/fetch JSON** the page already loads and keeps that. The most reliable way to scrape SPAs and dynamic grids.
- 🕷️ **Spider & deep crawl** — follow internal links into a tree (with section scoping), sweep pagination (ASP.NET postback · "next" · `?page=`), and the **tarantula** mode that captures each node's content + API into a data tree.
- 🔌 **Proxies, made easy** — paste a proxy in **any format** (`host:port` · `host:port:user:pass` · `user:pass@host:port` · URL) and Fisherboy normalizes it. A **Test** button routes a request through it and shows your **exit IP + country + latency**, with an actionable hint if it can't connect. Pool with rotation/cooldown, per‑job override, save your proxies.
- 🍪 **Session cookies, no extension** — paste cookies (Netscape `cookies.txt` / JSON / `name=value`) or read them straight from your local browser (Chrome/Firefox/Edge/Brave) for pages behind a login or a region.
- 🛡️ **PII anonymization before delivery** — three privacy modes bounded by role: **opaque** (`«PERSON_1»`), **reversible** (mask → let the LLM reason → re‑hydrate locally) and **direct** (raw, for non‑sensitive data). Fail‑closed: if anonymization fails, nothing raw ever leaves. With [Escriba](https://github.com/diegoparras/escriba)'s Anonimal you get full NER; standalone falls back to a built‑in regex pass (email/ID/IP/card/phone).
- ✏️ **Built‑in editor** — open the result in a modal editor with tabs **Markdown · JSON · Table**: a Markdown toolbar with live preview, a validating JSON editor, and an editable table where **JSON ↔ table is just switching tabs**. Download `.md` / `.json` / `.csv`.
- 📤 **Download everything** — the whole envelope, just the data (content + records + tree + links), or a flat records array. One click sends the result to **Escriba** for further conversion / anonymization / export.
- 🔑 **Three access levels** — DIOS / ANGEL / HUMANO, each with its own password and limits (which tiers, proxies, capture, solver, crawl, tarantula).
- 🐳 **Self‑contained image** — API + worker + Redis. Runs headless (REST + MCP) behind Escriba, or **standalone with its own web UI**.
- 🛡️ **Hardened** — fail‑closed by default, anti‑SSRF (incl. per‑hop redirect re‑validation), per‑job secret scrubbing, role gating on REST **and** MCP, rate‑limiting, non‑root container. Audited.
- 🌐 **REST + MCP** — drive it from `curl`, n8n, Claude Code or Escriba.

---

## 🚀 Quick start (Docker)

The fastest path — standalone, with the web UI:

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # set SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD
docker compose -f docker-compose.standalone.yml up -d --build
# → open http://localhost:8000
```

Don't want to build? Pull the published image instead:

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **Full deployment guide** (Docker Desktop step‑by‑step, EasyPanel, env reference, going to production): [`docs/DEPLOY.md`](docs/DEPLOY.md).

---

## 🧭 Two modes

Fisherboy runs in one of two modes, chosen by `APP_MODE`. **The core is identical**; the
mode only decides whether the web UI is mounted and where document conversion is delegated.

| | `standalone` | `sidekick` |
|---|---|---|
| Web UI | ✅ its own | ❌ headless |
| Interface | UI + REST + MCP | REST + MCP |
| Use | self‑host, personal | behind Escriba, internal network |

---

## 🔌 REST API

```http
POST /api/jobs            # validates schema, role × privacy mode, callback & proxy (SSRF); enqueues → 202
GET  /api/jobs/{job_id}   # status and result (the "envelope")
POST /api/proxy/test      # routes a request through a proxy; returns exit IP + country + latency
POST /api/revert          # re‑hydrates pseudonymized content (reversible mode)
POST /api/login           # role login (cookie session)
GET  /healthz · GET /metrics
```

```bash
curl -X POST http://localhost:8000/api/jobs \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/article","rol":"angel","privacy_mode":"opaco"}'
# → { "job_id": "…", "status": "pendiente" }

curl http://localhost:8000/api/jobs/<job_id>
# → the envelope with anonymized content_md once status == "ok"
```

Job fields: `url`, `rol`, `privacy_mode` (`opaco`/`reversible`/`directo`), `output_format`
(`markdown`/`llms_txt`/`json`), `tier_hint` (0–3), `crawl_depth`, `max_pages`, `paginate`,
`capture_api`, `tarantula`, `extract_schema` (for `json`), `proxy`, `cookies`, `callback_url`.

### MCP

The same pipeline is exposed as MCP tools (`submit_job`, `get_job`, `revert`) so n8n,
Claude Code or Escriba can enqueue without hand‑writing HTTP:

```bash
python -m app.mcp_server      # requires fastmcp
```

---

## 🔒 Privacy & roles

The privacy mode is chosen **per job** and **bounded by role** (`privacy_matrix.yaml`, never
hardcoded). If the role doesn't allow the requested mode, the gateway returns **403** — it
never silently downgrades.

| Role | opaque | reversible | direct |
|------|:------:|:----------:|:------:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

- **opaque** — each entity becomes a stable typed marker (`«PERSON_1»`, `«ID_2»`). The LLM reasons over markers without seeing PII; the original is not recoverable.
- **reversible** — same, but an encrypted token→value map is kept so you can re‑hydrate later (`POST /api/revert`, single‑use, role‑bound).
- **direct** — raw, only for non‑sensitive data.

A deterministic regex pass always runs for high‑risk PII (national ID, email, IP, Luhn‑valid
card, phone), on top of the NER model when Anonimal is configured.

---

## 🛡️ Security

Audited with an adversarial multi‑agent review; findings fixed and locked by tests.

- **Fail‑closed by default** — with no passwords configured the service returns 401; the open
  dev mode is an explicit opt‑in (`FISHERBOY_OPEN_GOD=1`).
- **Anti‑SSRF** — DNS resolved and private / loopback / link‑local / cloud‑metadata ranges
  blocked, re‑validated on **every redirect hop** (GET and POST) and on every browser request;
  the proxy override is validated against the same denylist.
- **No secret leakage** — per‑job secrets (proxy creds, CAPTCHA key, cookies) are scrubbed from
  the envelope returned by the API and from the webhook payload.
- **Role gating on REST and MCP** — capabilities (tier, proxy, capture, solver, crawl,
  tarantula) gated per role; tarantula and browser‑cookie reading are vetoed in sidekick mode.
- **Rate‑limiting**, hard page caps, non‑root container, structured JSON logs without PII.

See the [production checklist](docs/DEPLOY.md#going-to-production) before exposing it.

---

## 🌍 Internationalization

This README is available in: **English** (here) · [Español](docs/i18n/README.es.md) ·
[Français](docs/i18n/README.fr.md) · [Português](docs/i18n/README.pt.md) ·
[Italiano](docs/i18n/README.it.md) · [中文](docs/i18n/README.zh.md) ·
[日本語](docs/i18n/README.ja.md).

---

## 🧩 The Escriba family

Fisherboy is a standalone satellite of [**Escriba**](https://github.com/diegoparras/escriba),
the hub that turns any document into clean, anonymized Markdown ready for AI. Each app stands
on its own, yet they share a design system and a one‑click **"Send to Escriba"** handoff — so
what you fish out of the web flows straight into conversion, anonymization, chunking and export.

- **[Escriba](https://github.com/diegoparras/escriba)** — the hub: documents → Markdown + PII anonymization + LLM prep.
- **Fisherboy** — the web: scraping & data extraction (this repo).

---

## 🏗️ Architecture

Eight layers — REST/MCP surface · Redis queue + workers · discovery · tiered fetch with
proxies & anti‑CAPTCHA · self‑healing parsing · conversion & LLM extraction · Anonimal
privacy · output (Markdown/JSON/llms.txt/vector store/Postgres/webhook) · observability
(Prometheus/Loki/Grafana). Design notes live in [`docs/FISHERBOY-build.md`](docs/FISHERBOY-build.md).

```bash
pip install curl_cffi      # tier 1 (TLS fingerprint)
pip install camoufox       # tier 2 (stealth)    — or patchright
pip install nodriver       # tier 3 (real browser) — or playwright
```

High tiers are lazy‑imported: the base image stays light and each tier turns on by installing
its lib. The router detects what's available and builds the chain itself.

---

## 📜 License

MIT © 2026 Diego Parrás. The third‑party scrapers Fisherboy can use carry their own licenses
(mostly permissive: Crawl4AI, Trafilatura — Apache‑2.0; curl_cffi, httpx — MIT/BSD). Some
optional engines are network‑copyleft (AGPL: nodriver, Firecrawl): for personal, non‑commercial
use they impose nothing; offering them as a commercial service requires releasing your changes.

Authored by Diego Parrás.
