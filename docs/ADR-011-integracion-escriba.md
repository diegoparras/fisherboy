# ADR-011 — Integración Fisherboy ↔ Escriba

Estado: aceptado (lado Fisherboy) · propuesto (cambios en Escriba)
Fecha: 2026-06-18

## Contexto

Escriba está en producción y no se toca sin necesidad. Fisherboy debe integrarse como
sidekick siguiendo la regla del pulpo (ADR-001): solo HTTP + datos serializados, sin
importar código. Este ADR fija el contrato y un plan ESCALONADO que deja a Fisherboy
listo sin riesgo para producción, y aísla el único cambio que requiere tocar Escriba.

## Decisión

### Topología

Fisherboy corre con `APP_MODE=sidekick` (sin UI; REST + MCP) detrás de Escriba, en la
red `escriba_internal`. Comparten Anonimal por red interna. Tres conexiones:

1. **Escriba → Fisherboy** — scraping de URLs difíciles. Escriba delega a
   `POST /api/jobs` (o tool MCP `submit_job`) y consulta `GET /api/jobs/{id}`.
2. **Fisherboy → Escriba** — conversión documental. Fisherboy postea los bytes a
   `POST /api/convert` (ya implementado en `extractors/documents.py`).
3. **MCP** — `submit_job` / `get_job` / `revert` disponibles para Escriba, n8n y Claude Code.

### El único bloqueante real: auth de servicio

`POST /api/convert` de Escriba hoy exige cookie de sesión + CSRF (auth de usuario). Para
servicio-a-servicio hace falta que Escriba acepte un **token de servicio por header**
(análogo a Anonimal en ADR-003). Mientras no exista, la dirección Fisherboy→Escriba
queda lista del lado de Fisherboy (manda `X-Service-Token` si `ESCRIBA_TOKEN` está) pero
Escriba la rechaza con 401/403. Es un cambio chico y aislado, a hacer cuando se pueda
redeployar Escriba con calma.

### Plan escalonado

- **Fase A (hecha, sin tocar Escriba)**: Fisherboy sidekick-ready; compose conjunto;
  cliente Escriba→Fisherboy documentado; EscribaClient con token. Riesgo cero para prod.
- **Fase B (cambio quirúrgico en Escriba/Anonimal)**: aceptar `X-Service-Token`.
  Desbloquea Fisherboy→Escriba (documentos) y el segundo consumidor de Anonimal.
- **Fase C (opcional, UI de Escriba)**: toggle "scraping potente" que delega las URLs a
  Fisherboy y muestra el markdown/JSON resultante.

## Cliente listo para pegar en Escriba (Fase C, cuando se decida)

```python
# escriba: app/fisherboy_client.py  — delegar scraping de URLs a Fisherboy
import os, time, httpx

FISHERBOY_URL = os.getenv("FISHERBOY_URL", "http://fisherboy-api:8000")

def scrape_url(url: str, *, rol="dios", privacy_mode="opaco", output_format="markdown",
               tier_hint=None, capture_api=False, timeout_s=120) -> dict:
    """Encola en Fisherboy y espera el resultado. Devuelve el 'sobre'."""
    job = httpx.post(f"{FISHERBOY_URL}/api/jobs", json={
        "url": url, "rol": rol, "privacy_mode": privacy_mode,
        "output_format": output_format, "tier_hint": tier_hint,
        "capture_api": capture_api,
    }, timeout=10).json()
    jid = job["job_id"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        sobre = httpx.get(f"{FISHERBOY_URL}/api/jobs/{jid}", timeout=10).json()
        if sobre["status"] in ("ok", "error"):
            return sobre
        time.sleep(1.2)
    return {"status": "error", "error": "timeout esperando a Fisherboy"}
```

## docker-compose conjunto (ver docker-compose.integration.yml)

Fisherboy en sidekick + su Redis, enchufado a `escriba_internal` (externa, la crea Escriba).
Escriba y Anonimal ya viven ahí. Fisherboy NO expone puerto público en sidekick.

## Consecuencias

Fisherboy queda integrable hoy sin tocar producción. El acoplamiento es solo HTTP/MCP, así
que cada uno deploya y escala por separado. El único cambio en Escriba (token de servicio)
queda aislado y documentado para hacerse cuando haya ventana de redeploy.
