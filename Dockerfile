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

COPY app ./app
COPY privacy_matrix.yaml ./privacy_matrix.yaml

# Hardening: corre como usuario sin privilegios (no root).
RUN useradd -m -u 10001 fisher && chown -R fisher:fisher /app
USER fisher

EXPOSE 8000

# Healthcheck contra el endpoint liviano.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# La API es el comando por defecto; el worker se levanta con `python -m app.worker`
# (ver docker-compose*.yml). En standalone, la API y el worker pueden ser réplicas
# de esta misma imagen.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
