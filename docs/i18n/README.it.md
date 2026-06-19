<div align="center">

# 🎣 Fisherboy

**Il tuo alleato per estrarre dati dal web.**

Puntalo a qualsiasi pagina e ricevi **Markdown pulito o JSON strutturato** — pronto per
qualsiasi LLM. Fisherboy scala solo quando il sito reagisce (statico → fingerprint TLS →
browser stealth → browser reale), cattura il **JSON/XHR nascosto** che le single‑page app già
consumano, segue la paginazione ed esegue il crawl ad albero, e **anonimizza la PII prima di
consegnare**. Auto‑ospitabile, con la propria UI web o come servizio headless REST + MCP. Parte
della famiglia [**Escriba**](https://github.com/diegoparras/escriba).

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](../../LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)

[English](../../README.md) · [Español](README.es.md) · [Français](README.fr.md) · [Português](README.pt.md) · **Italiano** · [中文](README.zh.md) · [日本語](README.ja.md)

</div>

---

## ✨ Funzionalità

- 🎣 **Qualsiasi pagina → Markdown o JSON pulito** — [Crawl4AI](https://github.com/unclecode/crawl4ai) `fit_markdown` (rimuove navigazione/boilerplate per densità) con fallback a [Trafilatura](https://github.com/adbar/trafilatura); oppure estrazione strutturata verso un JSON Schema tramite LLM.
- 🪜 **Fetch a livelli (scala solo se bloccato)** — tier 0 `httpx` → tier 1 fingerprint TLS (`curl_cffi`) → tier 2 browser stealth (Camoufox/Patchright) → tier 3 browser reale (nodriver/Playwright). Un gate rileva blocchi/CAPTCHA e sale; il tier vincente viene messo in cache **per dominio**.
- 🛰️ **Cattura dell'API nascosta** — invece di combattere l'HTML renderizzato, Fisherboy osserva il **JSON XHR/fetch** che la pagina già carica e tiene quello. Il modo più affidabile per le SPA e le griglie dinamiche.
- 🕷️ **Spider e crawl profondo** — segue i link interni ad albero (con ambito per sezione), scorre la paginazione (postback ASP.NET · "successivo" · `?page=`), e la modalità **tarantola** che cattura contenuto + API di ogni nodo in un albero di dati.
- 🔌 **Proxy, facile** — incolla un proxy in **qualsiasi formato** (`host:porta` · `host:porta:utente:password` · `utente:password@host:porta` · URL) e Fisherboy lo normalizza. Un pulsante **Prova** instrada una richiesta e mostra il tuo **IP di uscita + paese + latenza**, con un suggerimento utile se non si connette. Pool con rotazione/cooldown, override per job, proxy salvati.
- 🍪 **Cookie di sessione, senza estensione** — incolla i cookie (Netscape `cookies.txt` / JSON / `nome=valore`) o leggili direttamente dal tuo browser locale (Chrome/Firefox/Edge/Brave) per pagine dietro login o regione.
- 🛡️ **Anonimizzazione della PII prima della consegna** — tre modalità di privacy limitate dal ruolo: **opaco** (`«PERSONA_1»`), **reversibile** (mascherare → l'LLM ragiona → re‑idratare in locale) e **diretto** (grezzo, per dati non sensibili). Fail‑closed: se l'anonimizzazione fallisce, nulla di grezzo esce. Con l'Anonimal di [Escriba](https://github.com/diegoparras/escriba) hai il NER completo; standalone usa un passaggio regex integrato (email/ID/IP/carta/telefono).
- ✏️ **Editor integrato** — apri il risultato in un modale con schede **Markdown · JSON · Tabella**: barra Markdown con anteprima dal vivo, editor JSON con validazione, e una tabella modificabile dove **JSON ↔ tabella è solo cambiare scheda**. Scarica `.md` / `.json` / `.csv`.
- 📤 **Scarica tutto** — l'intera busta, solo i dati (contenuto + record + albero + link), o un array piatto di record. Con un clic invii il risultato a **Escriba** per continuare conversione / anonimizzazione / export.
- 🔑 **Tre livelli di accesso** — DIOS / ANGEL / HUMANO, ciascuno con la propria password e limiti.
- 🐳 **Immagine autosufficiente** — API + worker + Redis. Gira headless (REST + MCP) dietro Escriba, o **standalone con la propria UI web**.
- 🛡️ **Irrobustito** — fail‑closed per default, anti‑SSRF (con ri‑validazione a ogni hop di redirect), scrub dei segreti per job, gating per ruolo su REST **e** MCP, rate‑limiting, container non‑root. Auditato; vedi [`docs/ADR-012`](../ADR-012-auditoria-seguridad.md).
- 🌐 **REST + MCP** — guidalo da `curl`, n8n, Claude Code o Escriba.

---

## 🚀 Avvio rapido (Docker)

La via più veloce — standalone, con la UI web:

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # imposta SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD
docker compose -f docker-compose.standalone.yml up -d --build
# → apri http://localhost:8000
```

Non vuoi compilare? Scarica l'immagine pubblicata:

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **Guida al deploy completa** (Docker Desktop passo passo, EasyPanel, riferimento env, produzione): [`docs/DEPLOY.md`](../DEPLOY.md).

---

## 🧭 Due modalità

Fisherboy gira in una di due modalità via `APP_MODE`. **Il core è identico**; la modalità decide
solo se montare la UI web e dove delegare la conversione dei documenti.

| | `standalone` | `sidekick` |
|---|---|---|
| UI web | ✅ propria | ❌ headless |
| Interfaccia | UI + REST + MCP | REST + MCP |
| Uso | self‑host, personale | dietro Escriba, rete interna |

---

## 🔌 API REST

```http
POST /api/jobs            # valida schema, ruolo × modalità, callback e proxy (SSRF); accoda → 202
GET  /api/jobs/{job_id}   # stato e risultato (la "busta")
POST /api/proxy/test      # instrada una richiesta tramite proxy; restituisce IP di uscita + paese + latenza
POST /api/revert          # re‑idrata contenuto pseudonimizzato (modalità reversibile)
GET  /healthz · GET /metrics
```

Campi del job: `url`, `rol`, `privacy_mode` (`opaco`/`reversible`/`directo`), `output_format`
(`markdown`/`llms_txt`/`json`), `tier_hint` (0–3), `crawl_depth`, `max_pages`, `paginate`,
`capture_api`, `tarantula`, `extract_schema`, `proxy`, `cookies`, `callback_url`. La stessa
pipeline è esposta come strumenti MCP: `python -m app.mcp_server`.

---

## 🔒 Privacy e ruoli

La modalità si sceglie **per job** ed è **limitata dal ruolo** (`privacy_matrix.yaml`). Se il
ruolo non abilita la modalità richiesta, il gateway risponde **403** — non declassa mai in
silenzio.

| Ruolo | opaco | reversibile | diretto |
|------|:------:|:----------:|:------:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

Oltre al NER (quando c'è Anonimal), gira sempre un passaggio regex deterministico per PII ad alto
rischio (ID nazionale, email, IP, carta con Luhn, telefono).

---

## 🛡️ Sicurezza

Auditata con una revisione adversarial multi‑agente; problemi corretti e fissati dai test
([`docs/ADR-012`](../ADR-012-auditoria-seguridad.md)).

- **Fail‑closed per default** — senza password configurate risponde 401; la modalità aperta di dev è un opt‑in esplicito (`FISHERBOY_OPEN_GOD=1`).
- **Anti‑SSRF** — blocca intervalli privati/loopback/link‑local/metadata, ri‑validando **ogni hop** di redirect e ogni richiesta del browser; anche il proxy override è validato.
- **Nessuna fuga di segreti** — i segreti per job (credenziali proxy, chiave CAPTCHA, cookie) vengono rimossi dalla busta e dal webhook.
- **Gating per ruolo su REST e MCP**, rate‑limiting, container non‑root, log JSON senza PII.

Vedi la [checklist di produzione](../DEPLOY.md#going-to-production) prima di esporlo.

---

## 🧩 La famiglia Escriba

Fisherboy è un satellite standalone di [**Escriba**](https://github.com/diegoparras/escriba),
l'hub che trasforma qualsiasi documento in Markdown pulito e anonimo pronto per l'IA. Ogni app si
usa da sola, ma condividono un design system e un handoff con un clic **"Invia a Escriba"** —
così ciò che peschi dal web va dritto a conversione, anonimizzazione, chunking ed export.

---

## 📜 Licenza

MIT © 2026 Diego Parrás. Gli scraper di terze parti che Fisherboy può usare hanno le proprie
licenze (per lo più permissive: Crawl4AI, Trafilatura — Apache‑2.0; curl_cffi, httpx — MIT/BSD).
Alcuni motori opzionali sono copyleft di rete (AGPL: nodriver, Firecrawl): per uso personale non
commerciale non impongono nulla; offrirli come servizio commerciale richiede di rilasciare le
proprie modifiche.

Autore: Diego Parrás.
