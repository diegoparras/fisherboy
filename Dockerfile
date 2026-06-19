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
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

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
