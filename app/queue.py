"""Cola sobre Redis + store del sobre. Ver FISHERBOY-build §1.

v1 usa una cola liviana propia (LPUSH/BRPOP) y guarda cada sobre como JSON con TTL.
Es deliberadamente simple: en v3 Crawlee toma la cola persistente y las sesiones.
El cliente Redis se inyecta, así los tests corren con fakeredis sin un Redis real.
"""
from __future__ import annotations

from .models import Sobre

_QUEUE_KEY = "fisherboy:queue"
_SOBRE_PREFIX = "fisherboy:sobre:"
# TTL del sobre en Redis. Cubre cola + proceso + ventana de consulta del resultado.
_SOBRE_TTL_S = 24 * 60 * 60


class JobQueue:
    def __init__(self, redis_client, *, sobre_ttl_s: int = _SOBRE_TTL_S) -> None:
        self._r = redis_client
        self._ttl = sobre_ttl_s

    def _key(self, job_id: str) -> str:
        return f"{_SOBRE_PREFIX}{job_id}"

    def save(self, sobre: Sobre) -> None:
        self._r.set(self._key(sobre.job_id), sobre.model_dump_json(), ex=self._ttl)

    def get(self, job_id: str) -> Sobre | None:
        raw = self._r.get(self._key(job_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return Sobre.model_validate_json(raw)

    def enqueue(self, sobre: Sobre) -> None:
        """Persiste el sobre y empuja su id a la cola, en ese orden: el worker
        nunca debe sacar un id cuyo sobre todavía no esté guardado."""
        self.save(sobre)
        self._r.lpush(_QUEUE_KEY, sobre.job_id)

    def pop(self, timeout_s: int = 5) -> str | None:
        """Bloquea hasta sacar un job_id o agotar el timeout. None si no hubo.

        Cuando la cola está vacía, la lectura bloqueante (BRPOP) puede cortar el socket
        por timeout (Redis/red que cierra la conexión idle, típico en PaaS). Eso NO es
        un error: se trata como 'cola vacía' y el worker reintenta. Las fallas reales de
        conexión sí se propagan (el worker las loguea y reintenta)."""
        try:
            item = self._r.brpop(_QUEUE_KEY, timeout=timeout_s)
        except Exception as exc:  # noqa: BLE001
            from redis.exceptions import TimeoutError as RedisTimeout
            if isinstance(exc, RedisTimeout):
                return None
            raise
        if item is None:
            return None
        _key, job_id = item
        return job_id.decode("utf-8") if isinstance(job_id, bytes) else job_id

    def depth(self) -> int:
        return int(self._r.llen(_QUEUE_KEY))


def build_redis(redis_url: str):
    import redis  # import perezoso: los tests con fakeredis no necesitan redis-py real

    # socket_keepalive + health_check: evita que la red/PaaS tire la conexión idle y la
    # detecta si murió, en vez de colgar la lectura bloqueante del worker.
    return redis.Redis.from_url(
        redis_url, socket_keepalive=True, health_check_interval=30,
    )


def get_queue(settings) -> JobQueue:
    return JobQueue(build_redis(settings.redis_url))
