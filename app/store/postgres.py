"""Persistencia en Postgres + pgvector. Ver Capa 7 (v3).

Opcional y degradable: sin DATABASE_URL o sin psycopg, `available()` es False y el
guardado es no-op (el sobre vive igual en Redis con TTL). Cuando está, persiste cada
sobre y, si hay embedding, lo guarda en una columna pgvector para búsqueda semántica.

Import perezoso: la imagen base no arrastra el driver. La extensión pgvector se crea
si está disponible; si no, la tabla funciona igual sin la columna de embedding.
"""
from __future__ import annotations

from ..logging import get_logger
from ..models import Sobre

log = get_logger("fisherboy.store")

_DDL = """
CREATE TABLE IF NOT EXISTS fisherboy_sobres (
    job_id        TEXT PRIMARY KEY,
    source_url    TEXT NOT NULL,
    status        TEXT NOT NULL,
    rol           TEXT,
    privacy_mode  TEXT,
    tier_usado    INT,
    content_md    TEXT,
    meta          JSONB,
    created_at    TIMESTAMPTZ DEFAULT now()
);
"""


class PostgresStore:
    def __init__(self, dsn: str, *, vector_dim: int = 1536) -> None:
        self.dsn = dsn or ""
        self.vector_dim = vector_dim
        self._checked = False

    def available(self) -> bool:
        if not self.dsn:
            return False
        import importlib.util

        return importlib.util.find_spec("psycopg") is not None

    def _connect(self):
        import psycopg

        return psycopg.connect(self.dsn)

    def ensure_schema(self) -> bool:
        if not self.available():
            return False
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_DDL)
                try:
                    # pgvector es opcional: si está, sumamos la columna de embedding.
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    cur.execute(
                        f"ALTER TABLE fisherboy_sobres ADD COLUMN IF NOT EXISTS "
                        f"embedding vector({self.vector_dim});"
                    )
                except Exception:  # noqa: BLE001 — sin pgvector seguimos sin embeddings
                    pass
                conn.commit()
            self._checked = True
            return True
        except Exception as e:  # noqa: BLE001 — DB caída: no romper el worker
            log.warning("postgres: no se pudo asegurar el schema", extra={"error": type(e).__name__})
            return False

    def save_sobre(self, sobre: Sobre) -> bool:
        if not self.available():
            return False
        if not self._checked:
            self.ensure_schema()
        import json

        # Nunca persistir los secretos por-job (proxy con credenciales, API key de
        # CAPTCHA, cookies de sesión): el store durable es un sink "hacia afuera".
        # Reusamos public_dump() — misma lista que la API y el webhook. Ver auditoría 2026-06.
        safe_meta = sobre.public_dump(mode="json").get("meta", {})

        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO fisherboy_sobres
                        (job_id, source_url, status, rol, privacy_mode, tier_usado, content_md, meta)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (job_id) DO UPDATE SET
                        status=EXCLUDED.status, content_md=EXCLUDED.content_md,
                        tier_usado=EXCLUDED.tier_usado, meta=EXCLUDED.meta;
                    """,
                    (
                        sobre.job_id, str(sobre.source_url), sobre.status.value,
                        sobre.rol.value, sobre.privacy_mode.value,
                        int(sobre.tier_usado) if sobre.tier_usado is not None else None,
                        sobre.content_md, json.dumps(safe_meta, ensure_ascii=False),
                    ),
                )
                conn.commit()
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("postgres: save falló", extra={"job_id": sobre.job_id, "error": type(e).__name__})
            return False


def build_store(settings) -> PostgresStore | None:
    return PostgresStore(settings.database_url) if settings.database_url else None
