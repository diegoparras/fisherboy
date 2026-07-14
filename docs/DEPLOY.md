# Deploying Fisherboy

Three ways to run it, from easiest to most controlled:

1. [Docker Desktop](#1-docker-desktop-windows--mac) — local, with the web UI.
2. [EasyPanel](#2-easypanel) — one‑click‑ish on your own server.
3. [Plain Docker / Compose](#3-plain-docker--compose) — any host.

Then: [environment reference](#environment-reference) and the
[production checklist](#going-to-production).

---

## 1. Docker Desktop (Windows / Mac)

**Step by step, from zero:**

1. **Install Docker Desktop** — <https://www.docker.com/products/docker-desktop/>. Open it and
   wait until it says **"Engine running"** (bottom‑left whale is green).
2. **Get the code:**
   ```bash
   git clone https://github.com/diegoparras/fisherboy.git
   cd fisherboy
   ```
3. **Create your `.env`:**
   ```bash
   cp .env.example .env
   ```
   Open `.env` and set at least:
   ```ini
   APP_MODE=standalone
   SECRET_KEY=<a long random string>
   GOD_PASSWORD=<your admin password>
   ANGEL_PASSWORD=<optional>
   HUMAN_PASSWORD=<optional>
   COOKIE_SECURE=0          # 0 because Docker Desktop serves http://localhost (no HTTPS)
   ```
   > Generate a key quickly: `python -c "import secrets; print(secrets.token_hex(32))"`.
4. **Start it:**
   ```bash
   docker compose -f docker-compose.standalone.yml up -d --build
   ```
   First build takes a few minutes (it installs the converters). Later starts are instant.
5. **Open** <http://localhost:8000>. You'll get the login screen — enter your `GOD_PASSWORD`.
6. **Logs / stop:**
   ```bash
   docker compose -f docker-compose.standalone.yml logs -f       # follow logs
   docker compose -f docker-compose.standalone.yml down          # stop
   ```

**Just want to try it, no login?** For a throwaway local run you can open it without
passwords by setting `FISHERBOY_OPEN_GOD=1` in `.env` (enters as `dios` automatically). Never
do this on anything reachable by others.

> **Docker Desktop won't start on Windows?** A known issue corrupts its AF_UNIX sockets under
> `AppData`. Quitting Docker Desktop, deleting the stale socket files and restarting usually
> fixes it.

---

## 2. EasyPanel

EasyPanel can either **build from the repo** or **pull the prebuilt image**. The prebuilt
image is simplest.

### Option A — pull the image (recommended)

1. In EasyPanel, **Create → App → Docker Image**.
2. Image: `ghcr.io/diegoparras/fisherboy:latest`
   *(published automatically by GitHub Actions; for a private repo, add a GHCR registry
   credential in EasyPanel first).*
3. **Command** (this service = the API): `uvicorn app.main:app --host 0.0.0.0 --port 8000`
4. **Port:** container `8000` → expose / map to your domain. EasyPanel terminates HTTPS for you.
5. **Environment** (see the [reference](#environment-reference)):
   ```ini
   APP_MODE=standalone
   SECRET_KEY=<random>
   GOD_PASSWORD=<...>
   REDIS_URL=redis://fisherboy-redis:6379/0
   COOKIE_SECURE=1
   ```
6. Add a **Redis** service in the same project (EasyPanel has a Redis template) and point
   `REDIS_URL` at it.
7. Add a **second service from the same image** for the worker, with command
   `python -m app.worker` and the **same env**.
8. Add the **PO Token provider** — see below. Without it, YouTube downloads fail.

### YouTube downloads: the PO Token provider (bgutil)

YouTube (SABR) hands out **no downloadable formats** unless the request carries a
proof‑of‑origin token — the thing a real browser produces by running BotGuard. Without it
yt‑dlp dies with `Requested format is not available`, and audio (mp3) is the first thing to
go. `docker-compose*.yml` ships this as the `fisherboy-bgutil` sidecar; **on EasyPanel you
have to add it as its own service**, because EasyPanel doesn't read the compose file.

1. **Create → App → Docker Image** in the *same project*.
2. Image: `brainicism/bgutil-ytdlp-pot-provider:1.3.1`
3. **No domain, no exposed port.** Only the API and the worker talk to it, over the project's
   internal network. It's stateless — no volume.
4. On the **API and the worker**, add the env var pointing at it:
   ```ini
   YT_POT_URL=http://<project>_<service>:4416
   ```
   EasyPanel's internal hostname is `<projectName>_<serviceName>` — e.g. a service named
   `bgutil` in project `fisherboy` is `http://fisherboy_bgutil:4416`. Check the exact name in
   the service's own page.

Two things worth knowing:

- **It does not replace cookies.** The PO token proves "a real client asked"; it says nothing
  about *who*. If YouTube answers `Sign in to confirm you're not a bot`, that's your
  datacenter IP being burned — the fix there is session cookies (`YT_COOKIES`, or the
  Advanced panel in the UI) plus a residential `YT_PROXY`. You can hit both walls at once.
- **Keep the image fresh.** YouTube breaks extraction every few weeks. The image carries
  yt‑dlp from the *nightly* channel and GitHub Actions rebuilds it weekly, so in EasyPanel
  turn on **auto‑deploy** (or hit Deploy) to pull the new `:latest` — otherwise you stay on
  the yt‑dlp you first pulled and downloads start failing again on their own.

### Option B — build from source

1. **Create → App → GitHub repo**, point it at `diegoparras/fisherboy`.
2. Build type: **Dockerfile** (the repo has one). Same env / port / Redis / worker as above,
   **plus the bgutil service** described above.

> EasyPanel sits behind HTTPS, so keep `COOKIE_SECURE=1`. Don't set `FISHERBOY_OPEN_GOD`.

---

## 3. Plain Docker / Compose

**Standalone (UI + worker + Redis), self‑contained:**

```bash
cp .env.example .env      # set SECRET_KEY + passwords
docker compose -f docker-compose.standalone.yml up -d --build
```

**Sidekick (behind Escriba, headless)** — joins the external `escriba_internal` network to
reach Anonimal/Escriba:

```bash
docker network create escriba_internal   # if it doesn't exist
docker compose up -d --build              # uses docker-compose.yml
```

**Single image, by hand:**

```bash
docker build -t fisherboy .
docker run -d --name fisherboy-redis redis:7-alpine
docker run -d --name fisherboy-api  --env-file .env \
  -e REDIS_URL=redis://fisherboy-redis:6379/0 -p 8000:8000 \
  --link fisherboy-redis fisherboy
docker run -d --name fisherboy-worker --env-file .env \
  -e REDIS_URL=redis://fisherboy-redis:6379/0 \
  --link fisherboy-redis fisherboy python -m app.worker
```

Optional add‑ons: `docker-compose.observability.yml` (Prometheus + Loki + Grafana) and the
`persist` profile in `docker-compose.yml` (Postgres + pgvector).

---

## Environment reference

| Variable | Default | What it does |
|---|---|---|
| `APP_MODE` | `sidekick` | `standalone` (mounts the web UI) or `sidekick` (headless). |
| `SECRET_KEY` | — | **Required with auth.** Signs the session cookie; use the same value on every replica. |
| `GOD_PASSWORD` / `ANGEL_PASSWORD` / `HUMAN_PASSWORD` | — | Role passwords. Set at least one to enable login. |
| `FISHERBOY_OPEN_GOD` | off | Dev only: open access as `dios` with no login. **Never in production.** |
| `COOKIE_SECURE` | `1` | Send the session cookie only over HTTPS. Set `0` for local http. |
| `REDIS_URL` | `redis://fisherboy-redis:6379/0` | Queue + envelope store. |
| `ANONIMAL_URL` | — | Anonimal (inside Escriba) for full NER. **Empty → built‑in regex anonymizer.** |
| `MAX_JOBS_PER_MIN` | `60` | Rate‑limit of job submissions per IP. |
| `CRAWL_MAX_PAGES` | `100` | Hard cap of pages per job. |
| `MAX_FETCH_TIER` | `3` | Escalation ceiling (0 static · 1 TLS · 2 stealth · 3 browser). |
| `PROXIES` | — | Comma/line‑separated proxy pool. |
| `YT_POT_URL` | `http://fisherboy-bgutil:4416` | PO Token provider (bgutil). **YouTube hands out no formats without it** — mp3 is the first to go. On EasyPanel point it at your own service name. Empty = off. |
| `YT_COOKIES` / `YT_PROXY` | — | Session cookies file + proxy for yt‑dlp. The answer to `Sign in to confirm you're not a bot` (burned datacenter IP) — a different wall than the PO token. |
| `LLM_API_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | — | For `output_format=json` (LLM extraction). |
| `ALLOW_PRIVATE_TARGETS` | `0` | Dev/test only — **disables SSRF protection**. Never in prod. |

Full list with comments: [`.env.example`](../.env.example).

---

## Going to production

Before exposing Fisherboy to anyone but you:

- [ ] Set `SECRET_KEY` (same on all replicas) and `GOD/ANGEL/HUMAN_PASSWORD`.
- [ ] **Do not** set `FISHERBOY_OPEN_GOD` or `HUMAN_OPEN`.
- [ ] `COOKIE_SECURE=1` behind HTTPS (EasyPanel/your reverse proxy terminates TLS).
- [ ] `ALLOW_PRIVATE_TARGETS=0`.
- [ ] An **egress firewall** blocking internal ranges (RFC1918 + `169.254.0.0/16`) — the only
      real mitigation for DNS‑rebinding on the browser tiers.
- [ ] In **sidekick**, don't publish the port (internal network only). In **standalone**, sit
      behind a reverse proxy.
- [ ] Tune `MAX_JOBS_PER_MIN` / `CRAWL_MAX_PAGES` to your use.
- [ ] Keep secrets in a secret manager, not in a committed `.env`.
