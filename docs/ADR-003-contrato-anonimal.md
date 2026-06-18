# ADR-003 — Contrato dual de Anonimal y autenticación

Estado: propuesto
Fecha: 2026-06-17

## Contexto

Anonimal hoy hace redacción por spans. Según lo conocido, expone `POST /anonymize` que devuelve text, detected_spans, redacted_text y summary, más `GET /health`, sin pseudonimización reversible y sin autenticación propia, pensado para vivir solo detrás de Escriba. Fisherboy necesita pseudonimización reversible y va a ser un segundo consumidor de Anonimal.

Tarea de Claude Code con acceso al repo: leer el código actual de Anonimal y, si la forma difiere de lo descrito, adaptar manteniendo la semántica de este ADR. Registrar acá el contrato real una vez implementado.

## Decisión

Contrato a implementar en Anonimal.

```
POST /privacy/process
  req:  { text, mode }          # mode: opaco | reversible
  resp opaco:      { processed_text, detected_spans, summary }
  resp reversible: { processed_text, mapping_ref, detected_spans, summary }

POST /privacy/revert
  req:  { content, mapping_ref, rol }
  resp: { content }             # rehidrata; valida rol contra el atado al mapping_ref

GET /health
```

En modo opaco no se guarda mapeo, así que no hay `mapping_ref`. En modo reversible se guarda la tabla de mapeo cifrada local y se devuelve una referencia opaca.

Autenticación de servicio: ahora que hay dos consumidores, Anonimal exige un token de servicio por header, además de quedar restringido por política de red a Escriba y Fisherboy. El token es un secreto compartido por entorno, suficiente para este caso, y la red interna es la segunda barrera.

Tabla de mapeo del modo reversible: cifrada en reposo, con la clave en custodia separada de la tabla. TTL derivado del ciclo de vida del job, contando cola, reintentos y la ventana de revert. Se borra al primer revert exitoso o al expirar el TTL, lo que ocurra antes. La reversión valida el rol del solicitante contra el rol atado al `mapping_ref` y contra la matriz. Cada revert se audita sin loggear el contenido rehidratado.

## Contrato real confirmado (Fase 0, 2026-06-18)

Leído con acceso real (`markitdown-web/anonimal/app.py`). El contrato ACTUAL es:

```
POST /anonymize   req: { text }
  resp: { text, detected_spans, redacted_text, summary }   # to_dict() crudo de OPF
  503 mientras el modelo carga en background; 413 si supera ANONIMAL_MAX_CHARS
GET  /health      resp: { status, model_loaded, device, error }
```

Cada span de `detected_spans` trae `start`, `end`, `text` y `placeholder`
(`<PRIVATE_PERSON>`, `<PRIVATE_EMAIL>`, `<PRIVATE_PHONE>`, `<ACCOUNT_NUMBER>`,
`<PRIVATE_DATE>`, `<PRIVATE_URL>`, `<SECRET>`, `<REDACTED>`). Anonimal "detecta, no
decide": el llamador arma el reemplazo. NO hay auth y NO hay pseudonimización
reversible todavía.

Decisión de implementación para v1: Fisherboy consume `/anonymize` tal cual y arma
el modo OPACO de su lado (`app/privacy/anonimal_client.py`), reemplazando cada
entidad por un marcador tipado estable «TIPO_N» — espejo de la lógica `seudo` de
Escriba pero sin guardar el mapeo. El contrato dual (`/privacy/process`,
`/privacy/revert`) y la auth de servicio de abajo quedan para implementar en
Anonimal en v2, junto con el modo reversible de Fisherboy.

## Consecuencias

Anonimal gana endpoints nuevos y autenticación de servicio, y deja de ser un servicio sin auth en la red. La pseudonimización reversible introduce estado con PII reversible, cuya seguridad y ciclo de vida se gobiernan acá y cuyo modelo de amenaza está en el ADR-005.
