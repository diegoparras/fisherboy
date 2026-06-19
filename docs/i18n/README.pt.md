<div align="center">

# 🎣 Fisherboy

**Seu parceiro para extrair dados da web.**

Aponte para qualquer página e receba **Markdown limpo ou JSON estruturado** — pronto para
qualquer LLM. O Fisherboy escala só quando o site reage (estático → fingerprint TLS → browser
stealth → browser real), captura o **JSON/XHR oculto** que as single‑page apps já consomem,
segue a paginação e rastreia em árvore, e **anonimiza a PII antes de entregar**. Auto‑hospedável,
com sua própria UI web ou como serviço headless REST + MCP. Parte da família
[**Escriba**](https://github.com/diegoparras/escriba).

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](../../LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)

[English](../../README.md) · [Español](README.es.md) · [Français](README.fr.md) · **Português** · [Italiano](README.it.md) · [中文](README.zh.md) · [日本語](README.ja.md)

</div>

---

## ✨ Recursos

- 🎣 **Qualquer página → Markdown ou JSON limpo** — [Crawl4AI](https://github.com/unclecode/crawl4ai) `fit_markdown` (poda navegação/boilerplate por densidade) com fallback para [Trafilatura](https://github.com/adbar/trafilatura); ou extração estruturada para um JSON Schema via LLM.
- 🪜 **Fetch escalonado (escala só quando bloqueado)** — tier 0 `httpx` → tier 1 fingerprint TLS (`curl_cffi`) → tier 2 browser stealth (Camoufox/Patchright) → tier 3 browser real (nodriver/Playwright). Um gate detecta bloqueios/CAPTCHAs e sobe; o tier vencedor é cacheado **por domínio**.
- 🛰️ **Captura da API oculta** — em vez de brigar com o HTML renderizado, o Fisherboy observa o **JSON XHR/fetch** que a página já carrega e fica com isso. O jeito mais confiável para SPAs e grids dinâmicos.
- 🕷️ **Spider e crawl profundo** — segue links internos em árvore (com escopo por seção), varre a paginação (postback ASP.NET · "próximo" · `?page=`), e o modo **tarântula** que captura o conteúdo + API de cada nó numa árvore de dados.
- 🔌 **Proxies, fácil** — cole um proxy em **qualquer formato** (`host:porta` · `host:porta:user:senha` · `user:senha@host:porta` · URL) e o Fisherboy normaliza. Um botão **Testar** roteia uma requisição e mostra seu **IP de saída + país + latência**, com uma dica acionável se não conectar. Pool com rotação/cooldown, override por job, proxies salvos.
- 🍪 **Cookies de sessão, sem extensão** — cole cookies (Netscape `cookies.txt` / JSON / `nome=valor`) ou leia direto do seu navegador local (Chrome/Firefox/Edge/Brave) para páginas atrás de login ou região.
- 🛡️ **Anonimização de PII antes de entregar** — três modos de privacidade limitados por papel: **opaco** (`«PESSOA_1»`), **reversível** (mascarar → o LLM raciocina → re‑hidratar localmente) e **direto** (cru, para dados não sensíveis). Fail‑closed: se a anonimização falhar, nada cru sai. Com o Anonimal do [Escriba](https://github.com/diegoparras/escriba) você tem NER completo; standalone usa uma passagem regex embutida (email/ID/IP/cartão/telefone).
- ✏️ **Editor embutido** — abra o resultado num modal com abas **Markdown · JSON · Tabela**: barra de Markdown com pré‑visualização ao vivo, editor JSON com validação, e uma tabela editável onde **JSON ↔ tabela é só trocar de aba**. Baixe `.md` / `.json` / `.csv`.
- 📤 **Baixe tudo** — o envelope inteiro, só os dados (conteúdo + registros + árvore + links), ou um array plano de registros. Com um clique você envia o resultado ao **Escriba** para continuar convertendo / anonimizando / exportando.
- 🔑 **Três níveis de acesso** — DIOS / ANGEL / HUMANO, cada um com senha e limites próprios.
- 🐳 **Imagem autocontida** — API + worker + Redis. Roda headless (REST + MCP) atrás do Escriba, ou **standalone com sua própria UI web**.
- 🛡️ **Endurecido** — fail‑closed por padrão, anti‑SSRF (com re‑validação a cada salto de redirect), scrub de segredos por job, gating por papel no REST **e** MCP, rate‑limiting, contêiner não‑root. Auditado; veja [`docs/ADR-012`](../ADR-012-auditoria-seguridad.md).
- 🌐 **REST + MCP** — controle por `curl`, n8n, Claude Code ou Escriba.

---

## 🚀 Início rápido (Docker)

O caminho mais rápido — standalone, com a UI web:

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # defina SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD
docker compose -f docker-compose.standalone.yml up -d --build
# → abra http://localhost:8000
```

Não quer compilar? Baixe a imagem publicada:

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **Guia de deploy completo** (Docker Desktop passo a passo, EasyPanel, referência de env, produção): [`docs/DEPLOY.md`](../DEPLOY.md).

---

## 🧭 Dois modos

O Fisherboy roda em um de dois modos via `APP_MODE`. **O núcleo é idêntico**; o modo só decide
se a UI web é montada e para onde a conversão de documentos é delegada.

| | `standalone` | `sidekick` |
|---|---|---|
| UI web | ✅ própria | ❌ headless |
| Interface | UI + REST + MCP | REST + MCP |
| Uso | self‑host, pessoal | atrás do Escriba, rede interna |

---

## 🔌 API REST

```http
POST /api/jobs            # valida schema, papel × modo, callback e proxy (SSRF); enfileira → 202
GET  /api/jobs/{job_id}   # status e resultado (o "envelope")
POST /api/proxy/test      # roteia uma requisição por um proxy; devolve IP de saída + país + latência
POST /api/revert          # re‑hidrata conteúdo pseudonimizado (modo reversível)
GET  /healthz · GET /metrics
```

Campos do job: `url`, `rol`, `privacy_mode` (`opaco`/`reversible`/`directo`), `output_format`
(`markdown`/`llms_txt`/`json`), `tier_hint` (0–3), `crawl_depth`, `max_pages`, `paginate`,
`capture_api`, `tarantula`, `extract_schema`, `proxy`, `cookies`, `callback_url`. O mesmo
pipeline é exposto como ferramentas MCP: `python -m app.mcp_server`.

---

## 🔒 Privacidade e papéis

O modo é escolhido **por job** e **limitado pelo papel** (`privacy_matrix.yaml`). Se o papel não
permite o modo pedido, o gateway responde **403** — nunca rebaixa em silêncio.

| Papel | opaco | reversível | direto |
|------|:------:|:----------:|:------:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

Além do NER (quando há Anonimal), sempre roda uma passagem regex determinística para PII de alto
risco (ID nacional, email, IP, cartão com Luhn, telefone).

---

## 🛡️ Segurança

Auditado com revisão adversarial multi‑agente; achados corrigidos e fixados por testes
([`docs/ADR-012`](../ADR-012-auditoria-seguridad.md)).

- **Fail‑closed por padrão** — sem senhas configuradas devolve 401; o modo aberto de dev é opt‑in explícito (`FISHERBOY_OPEN_GOD=1`).
- **Anti‑SSRF** — bloqueia faixas privadas/loopback/link‑local/metadata, re‑validando **cada salto** de redirect e cada requisição do browser; o proxy override é validado igual.
- **Sem vazamento de segredos** — segredos por job (credenciais de proxy, chave de CAPTCHA, cookies) são removidos do envelope e do webhook.
- **Gating por papel no REST e MCP**, rate‑limiting, contêiner não‑root, logs JSON sem PII.

Veja o [checklist de produção](../DEPLOY.md#going-to-production) antes de expor.

---

## 🧩 A família Escriba

O Fisherboy é um satélite standalone do [**Escriba**](https://github.com/diegoparras/escriba), o
hub que transforma qualquer documento em Markdown limpo e anônimo pronto para IA. Cada app é
usado sozinho, mas compartilham um sistema de design e um handoff de um clique **"Enviar ao
Escriba"** — assim o que você pesca da web flui direto para conversão, anonimização, chunking e
exportação.

---

## 📜 Licença

MIT © 2026 Diego Parrás. Os scrapers de terceiros que o Fisherboy pode usar têm suas próprias
licenças (em geral permissivas: Crawl4AI, Trafilatura — Apache‑2.0; curl_cffi, httpx — MIT/BSD).
Alguns motores opcionais são copyleft de rede (AGPL: nodriver, Firecrawl): para uso pessoal não
comercial não impõem nada; oferecê‑los como serviço comercial exige liberar suas alterações.

Autoria: Diego Parrás.
