"""Worker: saca jobs de la cola, corre el pipeline, guarda el sobre y hace callback.

`python -m app.worker`. Loop simple y robusto: un job que explota nunca tumba el
worker (process_job no lanza). Escala horizontal corriendo más réplicas del worker.
"""
from __future__ import annotations

import signal

from .callbacks import post_callback
from .config import get_settings
from .logging import get_logger, setup_logging
from .models import JobStatus, Sobre
from .pipeline import PipelineDeps, build_default_deps, process_job
from .queue import JobQueue, get_queue

log = get_logger("fisherboy.worker")

_running = True


def _stop(*_args) -> None:
    global _running
    _running = False
    log.info("worker recibió señal de apagado")


def handle_one(queue: JobQueue, deps: PipelineDeps, settings, job_id: str) -> Sobre | None:
    sobre = queue.get(job_id)
    if sobre is None:
        log.warning("job_id sin sobre en el store", extra={"job_id": job_id})
        return None

    sobre = process_job(sobre, deps)
    queue.save(sobre)

    # El callback_url viaja en meta (el Sobre del contrato no lo incluye).
    callback_url = sobre.meta.get("callback_url")
    if callback_url:
        post_callback(
            sobre,
            callback_url,
            timeout_s=settings.callback_timeout_s,
            allowlist=settings.callback_allowlist,
            allow_private=settings.allow_private_targets,
        )
    return sobre


def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    queue = get_queue(settings)
    deps = build_default_deps(settings)
    log.info("worker arrancado", extra={"app_mode": settings.app_mode.value})

    while _running:
        try:
            job_id = queue.pop(timeout_s=5)
        except Exception:  # noqa: BLE001 — Redis caído: reintentar, no morir
            log.exception("error al sacar de la cola")
            continue
        if job_id is None:
            continue
        try:
            handle_one(queue, deps, settings, job_id)
        except Exception:  # noqa: BLE001 — red de seguridad final
            log.exception("error manejando job", extra={"job_id": job_id})

    log.info("worker detenido")


if __name__ == "__main__":
    run()
