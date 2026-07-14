# Fisherboy — imagen única (API + worker comparten esta imagen).
# Liviana por defecto: tier 0 (httpx) + proxies + conversión Crawl4AI/Trafilatura.
# Los tiers de browser (2/3) se encienden instalando su lib (ver README).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_MODE=standalone

WORKDIR /app

# Dependencias del sistema mínimas para httpx/lxml (Trafilatura usa lxml) + healthcheck.
# ffmpeg: lo usa yt-dlp para muxear video+audio en alta calidad (mp4). Es opcional —
# sin él, la descarga de video cae al mejor progresivo — pero en el server lo queremos.
# unzip: para instalar deno (abajo).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg unzip \
    && rm -rf /var/lib/apt/lists/*

# Runtime de JavaScript (deno) para yt-dlp: YouTube ya NO entrega los formatos completos sin
# un JS runtime (el cliente "android vr" devuelve formatos mochos → "Requested format is not
# available"). yt-dlp autodetecta deno en el PATH. Se instala system-wide (/usr/local/bin).
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && /usr/local/bin/deno --version

COPY requirements.txt .
RUN pip install -r requirements.txt

# yt-dlp: canal NIGHTLY, a propósito. YouTube rompe la extracción cada pocas semanas y la
# release estable de PyPI llega tarde; la nightly trae el arreglo el mismo día. Junto va el
# plugin de PO Tokens (bgutil), que le pide los tokens al sidecar `fisherboy-bgutil` del
# compose (YT_POT_URL). Sin PO Token, YouTube devuelve la lista de formatos vacía y yt-dlp
# corta con "Requested format is not available" — el audio es lo primero que esconde.
#
# Los dos SIN PIN, a propósito: acá "quedarse quieto" es lo que rompe, no lo que estabiliza.
# El precio es que el plugin (que vive acá) y el sidecar (que vive en el compose) se despliegan
# por separado y podrían quedar en majors distintas, que no se entienden. Eso NO se arregla
# fijando versiones a mano —se pudren igual—, sino detectándolo: media.pot_probe() le pregunta
# la versión al sidecar y, si no coinciden, la descarga falla diciendo exactamente eso y que
# hay que redeployar. Ver `no_formats_hint` y /api/download/pot/health.
# Para refrescar estas capas sin invalidar el resto del cache:
#   docker build --build-arg YTDLP_REFRESH=$(date +%s) .
ARG YTDLP_REFRESH=0
RUN pip install -U --pre "yt-dlp[default]" \
    && pip install -U "bgutil-ytdlp-pot-provider" \
    && yt-dlp --version \
    && python -c "from importlib.metadata import version; print('bgutil plugin', version('bgutil-ytdlp-pot-provider'))"

# Navegador (Chromium) para los tiers 2/3 — sitios con JavaScript / anti-bot — y para la
# captura de API/XHR. patchright = Chromium con stealth (tier 2 + captura); playwright =
# Chromium estándar (tier 3). Se instalan en una ruta compartida accesible por el user
# no-root. Esto agranda la imagen (~1 GB) pero hace que el scraping funcione de verdad.
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN patchright install --with-deps chromium \
    && playwright install chromium \
    && chmod -R a+rx /ms-playwright

COPY app ./app
COPY privacy_matrix.yaml ./privacy_matrix.yaml

# Hardening: corre como usuario sin privilegios (no root).
RUN useradd -m -u 10001 fisher && chown -R fisher:fisher /app
USER fisher

EXPOSE 8000

# Sin HEALTHCHECK en la imagen a propósito: la misma imagen corre la API (puerto 8000)
# y el worker (sin puerto). Un healthcheck HTTP marcaría el worker como "unhealthy" para
# siempre. La salud de la API la chequea la plataforma por su dominio/puerto (EasyPanel,
# compose), no la imagen. Si querés un healthcheck a nivel servicio, definilo solo en el
# servicio de API: `curl -fsS http://localhost:8000/healthz`.

# La API es el comando por defecto; el worker se levanta con `python -m app.worker`
# (ver docker-compose*.yml). En standalone, la API y el worker pueden ser réplicas
# de esta misma imagen.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
