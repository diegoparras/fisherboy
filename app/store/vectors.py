"""Vector store + embeddings para búsqueda semántica. Ver Capa 7.

Dos partes:
- EmbeddingClient: genera embeddings vía un proveedor OpenAI-compatible (/embeddings).
  Inyectable, así se testea sin red.
- Índice vectorial: PgVectorStore (Postgres + pgvector) para producción, o
  InMemoryVectorIndex (coseno en memoria) como fallback testeable y sin DB.

El pipeline, si los embeddings están habilitados, embebe el contenido del sobre y lo
guarda para recuperación semántica posterior ("traeme lo parecido a X").
"""
from __future__ import annotations

import math

from ..logging import get_logger

log = get_logger("fisherboy.vectors")


class EmbeddingError(Exception):
    pass


class EmbeddingClient:
    """Cliente /embeddings OpenAI-compatible. `embed` recibe textos, devuelve vectores."""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout_s: float = 60.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return bool(self.base_url and self.api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self.available():
            raise EmbeddingError("Embeddings no configurados (LLM_API_BASE_URL/KEY).")
        import httpx

        try:
            resp = httpx.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": texts},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_s,
            )
        except httpx.HTTPError as e:
            raise EmbeddingError(f"No se pudo contactar al proveedor: {type(e).__name__}.") from e
        if not resp.is_success:
            raise EmbeddingError(f"El proveedor de embeddings respondió {resp.status_code}.")
        try:
            data = resp.json()
            return [row["embedding"] for row in data["data"]]
        except (ValueError, KeyError, TypeError) as e:
            raise EmbeddingError("Respuesta inválida del proveedor de embeddings.") from e


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class InMemoryVectorIndex:
    """Índice coseno en memoria. Fallback sin DB y base de los tests."""

    def __init__(self) -> None:
        self._items: list[tuple[str, list[float]]] = []

    def add(self, job_id: str, vector: list[float]) -> None:
        self._items.append((job_id, vector))

    def search(self, query: list[float], k: int = 5) -> list[tuple[str, float]]:
        scored = [(jid, cosine(query, vec)) for jid, vec in self._items]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:k]


class PgVectorStore:
    """Persistencia de embeddings en pgvector. Degrada si no hay DB/extensión."""

    def __init__(self, postgres_store) -> None:
        self.pg = postgres_store

    def available(self) -> bool:
        return self.pg is not None and self.pg.available()

    def save_embedding(self, job_id: str, vector: list[float]) -> bool:
        if not self.available():
            return False
        try:
            with self.pg._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE fisherboy_sobres SET embedding = %s WHERE job_id = %s;",
                    (vector, job_id),
                )
                conn.commit()
            return True
        except Exception as e:  # noqa: BLE001 — sin pgvector/DB: degradar
            log.warning("pgvector: save falló", extra={"error": type(e).__name__})
            return False

    def search(self, vector: list[float], k: int = 5) -> list[tuple[str, float]]:
        if not self.available():
            return []
        try:
            with self.pg._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT job_id, 1 - (embedding <=> %s::vector) AS sim "
                    "FROM fisherboy_sobres WHERE embedding IS NOT NULL "
                    "ORDER BY embedding <=> %s::vector LIMIT %s;",
                    (vector, vector, k),
                )
                return [(row[0], float(row[1])) for row in cur.fetchall()]
        except Exception as e:  # noqa: BLE001
            log.warning("pgvector: search falló", extra={"error": type(e).__name__})
            return []
