"""Métricas Prometheus. Ver Capa 8.

Singleton con degradación: si prometheus_client no está instalado, todo es no-op y el
sistema corre igual. Cuando está, expone contadores/histograma y `/metrics` los sirve.
Nunca métricas con PII: solo cardinalidad acotada (status, tier, vendor de captcha).
"""
from __future__ import annotations

import importlib.util

_HAS_PROM = importlib.util.find_spec("prometheus_client") is not None


class _NoopMetric:
    def labels(self, *a, **k):
        return self

    def inc(self, *a, **k):
        pass

    def observe(self, *a, **k):
        pass


class Metrics:
    def __init__(self) -> None:
        if _HAS_PROM:
            from prometheus_client import Counter, Histogram

            self.jobs = Counter("fisherboy_jobs_total", "Jobs por estado final", ["status"])
            self.fetch_tier = Counter("fisherboy_fetch_tier_total", "Fetches por tier ganador", ["tier"])
            self.captcha = Counter("fisherboy_captcha_total", "CAPTCHAs detectados", ["vendor"])
            self.job_seconds = Histogram("fisherboy_job_seconds", "Duración del job en segundos")
        else:
            self.jobs = self.fetch_tier = self.captcha = self.job_seconds = _NoopMetric()

    @property
    def enabled(self) -> bool:
        return _HAS_PROM

    def inc_job(self, status: str) -> None:
        self.jobs.labels(status=status).inc()

    def inc_tier(self, tier: int | None) -> None:
        self.fetch_tier.labels(tier=str(tier if tier is not None else "none")).inc()

    def inc_captcha(self, vendor: str) -> None:
        self.captcha.labels(vendor=vendor or "unknown").inc()

    def render(self) -> tuple[bytes, str]:
        """Devuelve (cuerpo, content_type) para el endpoint /metrics."""
        if not _HAS_PROM:
            return b"# prometheus_client no instalado\n", "text/plain"
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return generate_latest(), CONTENT_TYPE_LATEST


_metrics: Metrics | None = None


def get_metrics() -> Metrics:
    global _metrics
    if _metrics is None:
        _metrics = Metrics()
    return _metrics
