<div align="center">

# 🎣 Fisherboy

**Votre acolyte pour extraire des données du web.**

Pointez‑le vers n'importe quelle page et récupérez du **Markdown propre ou du JSON structuré**
— prêt pour n'importe quel LLM. Fisherboy n'escalade que lorsque le site résiste (statique →
empreinte TLS → navigateur furtif → vrai navigateur), capture le **JSON/XHR caché** que les
single‑page apps consomment déjà, suit la pagination et explore en arbre, et **anonymise les
PII avant de livrer**. Auto‑hébergeable, avec sa propre UI web ou comme service headless REST +
MCP. Membre de la famille [**Escriba**](https://github.com/diegoparras/escriba).

[![License: MIT](https://img.shields.io/badge/License-MIT-1d9e75.svg)](../../LICENSE)
[![Docker image](https://img.shields.io/badge/image-ghcr.io%2Fdiegoparras%2Ffisherboy-2496ED?logo=docker&logoColor=white)](https://github.com/diegoparras/fisherboy/pkgs/container/fisherboy)
![Self-hosted](https://img.shields.io/badge/self--hosted-✓-1d9e75.svg)

[English](../../README.md) · [Español](README.es.md) · **Français** · [Português](README.pt.md) · [Italiano](README.it.md) · [中文](README.zh.md) · [日本語](README.ja.md)

</div>

---

## ✨ Fonctionnalités

- 🎣 **N'importe quelle page → Markdown ou JSON propre** — [Crawl4AI](https://github.com/unclecode/crawl4ai) `fit_markdown` (élague navigation/boilerplate par densité) avec repli sur [Trafilatura](https://github.com/adbar/trafilatura) ; ou extraction structurée vers un JSON Schema via un LLM.
- 🪜 **Fetch par paliers (escalade seulement si bloqué)** — palier 0 `httpx` → palier 1 empreinte TLS (`curl_cffi`) → palier 2 navigateur furtif (Camoufox/Patchright) → palier 3 vrai navigateur (nodriver/Playwright). Une porte détecte blocages/CAPTCHA et monte ; le palier gagnant est mis en cache **par domaine**.
- 🛰️ **Capture de l'API cachée** — au lieu de combattre le HTML rendu, Fisherboy observe le **JSON XHR/fetch** que la page charge déjà et garde cela. Le moyen le plus fiable pour les SPA et les grilles dynamiques.
- 🕷️ **Spider et crawl profond** — suit les liens internes en arbre (avec portée par section), balaie la pagination (postback ASP.NET · « suivant » · `?page=`), et le mode **tarentule** qui capture le contenu + l'API de chaque nœud en un arbre de données.
- 🔌 **Proxies, en simple** — collez un proxy dans **n'importe quel format** (`hôte:port` · `hôte:port:user:pass` · `user:pass@hôte:port` · URL) et Fisherboy le normalise. Un bouton **Tester** route une requête et affiche votre **IP de sortie + pays + latence**, avec un conseil actionnable si ça ne se connecte pas. Pool avec rotation/cooldown, surcharge par job, proxies sauvegardés.
- 🍪 **Cookies de session, sans extension** — collez des cookies (Netscape `cookies.txt` / JSON / `nom=valeur`) ou lisez‑les directement depuis votre navigateur local (Chrome/Firefox/Edge/Brave) pour les pages derrière un login ou une région.
- 🛡️ **Anonymisation des PII avant la livraison** — trois modes de confidentialité bornés par le rôle : **opaque** (`«PERSONNE_1»`), **réversible** (masquer → le LLM raisonne → ré‑hydrater en local) et **direct** (brut, pour données non sensibles). Fail‑closed : si l'anonymisation échoue, rien de brut ne sort. Avec l'Anonimal d'[Escriba](https://github.com/diegoparras/escriba) vous avez le NER complet ; en standalone, un passage regex intégré (email/ID/IP/carte/téléphone).
- ✏️ **Éditeur intégré** — ouvrez le résultat dans une modale à onglets **Markdown · JSON · Tableau** : barre Markdown avec aperçu en direct, éditeur JSON avec validation, et un tableau éditable où **JSON ↔ tableau = changer d'onglet**. Téléchargez `.md` / `.json` / `.csv`.
- 📤 **Tout télécharger** — l'enveloppe entière, juste les données (contenu + enregistrements + arbre + liens), ou un tableau plat d'enregistrements. En un clic, envoyez le résultat à **Escriba** pour poursuivre conversion / anonymisation / export.
- 🔑 **Trois niveaux d'accès** — DIOS / ANGEL / HUMANO, chacun avec son mot de passe et ses limites.
- 🐳 **Image autonome** — API + worker + Redis. Tourne en headless (REST + MCP) derrière Escriba, ou **standalone avec sa propre UI web**.
- 🛡️ **Durci** — fail‑closed par défaut, anti‑SSRF (avec re‑validation à chaque saut de redirection), nettoyage des secrets par job, gating par rôle sur REST **et** MCP, rate‑limiting, conteneur non‑root. Audité.
- 🌐 **REST + MCP** — pilotez‑le depuis `curl`, n8n, Claude Code ou Escriba.

---

## 🚀 Démarrage rapide (Docker)

Le chemin le plus rapide — standalone, avec l'UI web :

```bash
git clone https://github.com/diegoparras/fisherboy.git
cd fisherboy
cp .env.example .env          # définissez SECRET_KEY + GOD/ANGEL/HUMAN_PASSWORD
docker compose -f docker-compose.standalone.yml up -d --build
# → ouvrez http://localhost:8000
```

Pas envie de compiler ? Téléchargez l'image publiée :

```bash
docker pull ghcr.io/diegoparras/fisherboy:latest
```

📖 **Guide de déploiement complet** (Docker Desktop pas à pas, EasyPanel, référence env, production) : [`docs/DEPLOY.md`](../DEPLOY.md).

---

## 🧭 Deux modes

Fisherboy tourne dans l'un de deux modes via `APP_MODE`. **Le cœur est identique** ; le mode
décide seulement si l'UI web est montée et où la conversion de documents est déléguée.

| | `standalone` | `sidekick` |
|---|---|---|
| UI web | ✅ propre | ❌ headless |
| Interface | UI + REST + MCP | REST + MCP |
| Usage | self‑host, personnel | derrière Escriba, réseau interne |

---

## 🔌 API REST

```http
POST /api/jobs            # valide schéma, rôle × mode, callback et proxy (SSRF) ; met en file → 202
GET  /api/jobs/{job_id}   # statut et résultat (l'« enveloppe »)
POST /api/proxy/test      # route une requête via un proxy ; renvoie IP de sortie + pays + latence
POST /api/revert          # ré‑hydrate du contenu pseudonymisé (mode réversible)
GET  /healthz · GET /metrics
```

Champs du job : `url`, `rol`, `privacy_mode` (`opaco`/`reversible`/`directo`), `output_format`
(`markdown`/`llms_txt`/`json`), `tier_hint` (0–3), `crawl_depth`, `max_pages`, `paginate`,
`capture_api`, `tarantula`, `extract_schema`, `proxy`, `cookies`, `callback_url`. Le même
pipeline est exposé comme outils MCP : `python -m app.mcp_server`.

---

## 🔒 Confidentialité et rôles

Le mode est choisi **par job** et **borné par le rôle** (`privacy_matrix.yaml`). Si le rôle
n'autorise pas le mode demandé, la passerelle répond **403** — jamais de rétrogradation
silencieuse.

| Rôle | opaque | réversible | direct |
|------|:------:|:----------:|:------:|
| `humano` | ✅ | — | — |
| `angel`  | ✅ | ✅ | — |
| `dios`   | ✅ | ✅ | ✅ |

Outre le NER (quand Anonimal est présent), un passage regex déterministe s'exécute toujours pour
les PII à haut risque (identifiant national, email, IP, carte avec Luhn, téléphone).

---

## 🛡️ Sécurité

Auditée par une revue adversariale multi‑agents ; problèmes corrigés et verrouillés par les
tests.

- **Fail‑closed par défaut** — sans mot de passe configuré, renvoie 401 ; le mode ouvert de dev est un opt‑in explicite (`FISHERBOY_OPEN_GOD=1`).
- **Anti‑SSRF** — bloque les plages privées/loopback/link‑local/metadata, re‑validées à **chaque saut** de redirection et à chaque requête du navigateur ; le proxy override est validé pareil.
- **Aucune fuite de secrets** — les secrets par job (identifiants proxy, clé CAPTCHA, cookies) sont retirés de l'enveloppe et du webhook.
- **Gating par rôle sur REST et MCP**, rate‑limiting, conteneur non‑root, logs JSON sans PII.

Voir la [checklist de production](../DEPLOY.md#going-to-production) avant de l'exposer.

---

## 🧩 La famille Escriba

Fisherboy est un satellite autonome d'[**Escriba**](https://github.com/diegoparras/escriba), le
hub qui transforme n'importe quel document en Markdown propre et anonyme prêt pour l'IA. Chaque
app s'utilise seule, mais elles partagent un design system et un transfert en un clic **« Envoyer
à Escriba »** — ainsi ce que vous pêchez sur le web file droit vers conversion, anonymisation,
chunking et export.

---

## 📜 Licence

MIT © 2026 Diego Parrás. Les scrapers tiers que Fisherboy peut utiliser portent leurs propres
licences (la plupart permissives : Crawl4AI, Trafilatura — Apache‑2.0 ; curl_cffi, httpx —
MIT/BSD). Certains moteurs optionnels sont en copyleft réseau (AGPL : nodriver, Firecrawl) : pour
un usage personnel non commercial ils n'imposent rien ; les proposer comme service commercial
exige de publier vos modifications.

Auteur : Diego Parrás.
