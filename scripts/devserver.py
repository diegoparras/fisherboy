# -*- coding: utf-8 -*-
"""Runner de desarrollo TODO-EN-UNO. SOLO para probar local sin infra.

Levanta API + UI + worker en un solo proceso, con la cola sobre fakeredis y la
anonimización por la pasada determinística (regex), sin necesitar Redis ni el modelo
de Anonimal. Sirve para ver Fisherboy corriendo en vivo y mandarle jobs reales.

NO usar en producción: acá la anonimización es solo regex (sin el NER de Anonimal),
y la cola es en memoria. El worker real es `python -m app.worker` con Redis + Anonimal.

Uso:  python scripts/devserver.py [puerto]
"""
import os
import sys
import threading

# La consola de Windows suele ser cp1252 y revienta al imprimir Unicode (→, «»).
# Forzamos UTF-8 en stdout/stderr para que ningún print tumbe el thread del worker.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — entornos sin reconfigure
        pass

os.environ.setdefault("APP_MODE", "standalone")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fakeredis  # noqa: E402
import uvicorn  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.pipeline import build_default_deps, process_job  # noqa: E402
from app.privacy.anonimal_client import build_opaco  # noqa: E402
from app.queue import JobQueue  # noqa: E402

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8078
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

settings = Settings(env={
    "APP_MODE": "standalone",
    "PRIVACY_MATRIX_PATH": os.path.join(ROOT, "privacy_matrix.yaml"),
})

fake = fakeredis.FakeStrictRedis()
queue = JobQueue(fake)

# Deps reales (router con fetch en vivo, crawl, etc.) PERO con anonimización regex-only,
# así no hace falta el servicio Anonimal para la demo.
deps = build_default_deps(settings, redis_client=fake)
deps.anonymize_opaco = lambda text: build_opaco(text, opf_spans=[])

_stop = threading.Event()


def worker_loop():
    print("[worker] arrancado (regex-only, DEV)")
    while not _stop.is_set():
        try:
            job_id = queue.pop(timeout_s=1)
            if not job_id:
                continue
            sobre = queue.get(job_id)
            if sobre is None:
                continue
            sobre = process_job(sobre, deps)
            queue.save(sobre)
            detail = sobre.error or (f"tier {sobre.tier_usado}" if sobre.tier_usado is not None else "")
            print(f"[worker] job {job_id[:8]} -> {sobre.status.value} {detail}".rstrip())
        except Exception as exc:  # noqa: BLE001 — el worker nunca debe morir por un job
            print(f"[worker] error en el loop: {type(exc).__name__}: {exc}")


threading.Thread(target=worker_loop, daemon=True).start()

app = create_app(settings, queue=queue)
print(f"[devserver] http://127.0.0.1:{PORT}  (UI + API, worker en thread, cola fakeredis)")
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
