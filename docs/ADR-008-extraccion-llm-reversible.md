# ADR-008 — Extracción estructurada por LLM y modo reversible end-to-end

Estado: aceptado
Fecha: 2026-06-18

## Contexto

La Capa 5 pide extracción estructurada por LLM con validación, y el ADR-002 define el
modo reversible. Faltaba la rama LLM del pipeline y la implementación real del
reversible (el contrato `/privacy/process` de Anonimal del ADR-003 todavía no existe).

## Decisión

### Rama de extracción por LLM

`output_format=json` + `extract_schema` dispara la extracción. `app/extractors/
llm_extract.py` arma un prompt que pide SOLO JSON que cumpla el schema, llama a un
proveedor OpenAI-compatible (`/chat/completions`, `response_format=json_object`),
parsea tolerando ```json fences```, y valida contra el JSON Schema con `jsonschema` si
está. El cliente LLM se inyecta (`complete(system, user) -> str`), así se testea sin
red. Sin `LLM_API_BASE_URL`/`KEY`, la rama da un error claro (no rompe el resto).

### Dónde manda el privacy_mode

El modo SOLO importa en esta rama, que sale a un proveedor externo (ADR-002):

- **directo** → el markdown crudo va al LLM. Solo para data no sensible.
- **opaco** → se pseudonimiza antes de mandar; la salida queda pseudonimizada.
- **reversible** → se pseudonimiza, el LLM extrae sobre los marcadores, y la salida se
  **re-hidrata local** con la tabla de mapeo. El LLM nunca ve PII real; el solicitante
  recibe los valores reales. Un `reversible` pedido sin cripto disponible cae a opaco,
  nunca a crudo (fail-safe).

La rama LOCAL (markdown/llms.txt) es ajena a esto: siempre pasa por Anonimal opaco.

### Modo reversible: implementación del lado de Fisherboy

`app/privacy/reversible.py`. Mientras Anonimal no exponga `/privacy/process`, Fisherboy
cumple la semántica del ADR-003 de su lado:

- La tabla token→original se **cifra en reposo con Fernet** (`REVERSIBLE_KEY`; sin ella,
  clave por proceso con aviso) y se guarda bajo un `mapping_ref` opaco, atado al ROL que
  lo creó, con TTL (`REVERSIBLE_TTL_S`), en Redis (compartido) o memoria.
- `revert` valida el rol del solicitante contra la matriz (debe habilitar reversible) Y
  contra el rol dueño del `mapping_ref`, rehidrata, y **borra la tabla (un solo uso)**.
  Se audita sin loggear contenido. Expuesto por `POST /api/revert` y la tool MCP `revert`.

### Amenaza primaria (ADR-005 T1)

La garantía está acotada por la **recall de detección**, no por el cifrado: un span no
detectado viaja al LLM sin enmascarar. Por eso la pasada determinística de
`detectors.py` (CUIT/CUIL/email/IP/tarjeta/teléfono) corre además del modelo, y el sesgo
es conservador. La capacidad de revertir agrega estado con PII reversible, gobernado por
el cifrado, el TTL, el atado a rol y el borrado de un solo uso.

## Consecuencias

La rama LLM cierra el camino markdown↔JSON estructurado, con la privacidad concentrada
en un solo lugar y testeable con un LLM falso. El reversible funciona end-to-end hoy sin
esperar a Anonimal; cuando Anonimal sume `/privacy/process`, el cliente se reapunta sin
tocar el pipeline. La garantía queda explícita y acotada, no presentada como absoluta.
