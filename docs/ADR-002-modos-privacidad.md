# ADR-002 — Modos de privacidad y mapeo a Anonimal

Estado: aceptado
Fecha: 2026-06-17

## Contexto

El usuario elige el modo de privacidad por job, filosofía de Escriba, acotado por su rol. El modo a nivel de job es reversible, opaco o directo. Anonimal por dentro maneja sus propios modos de procesamiento. Sin un mapeo explícito entre los dos vocabularios, aparece drift, y la elección de a qué modo de Anonimal corresponde cada uno define la calidad de extracción del LLM.

## Decisión

PrivacyMode a nivel de job: `reversible`, `opaco`, `directo`.

Mapeo a Anonimal:

- `reversible` → pseudonimización con tabla de mapeo. Cada entidad detectada se reemplaza por un marcador tipado y estable dentro del documento, por ejemplo «PERSONA_1», «CUIT_2». El mapeo entre marcador y valor real se guarda cifrado local y se referencia con un `mapping_ref` opaco. Permite rehidratar después.
- `opaco` → mismo reemplazo por marcador tipado y estable dentro del documento, sin guardar el mapeo. El LLM ve «CUIT_1» de forma consistente y puede razonar de manera relacional, pero el valor real no se puede recuperar. Sirve cuando la salida no necesita el dato real, como clasificar movimientos o extraer montos y fechas.
- `directo` → sin Anonimal. El contenido va crudo al LLM.

La diferencia entre opaco y reversible es solo si se guarda el mapeo. Los dos preservan el tipo de entidad y la consistencia dentro del documento, para no degradar la extracción de más. Queda descartada la redacción total que borra el tipo de entidad, porque le saca al LLM la señal que necesita.

El modo solo aplica a la rama de extracción por LLM, que sale a un proveedor externo. La rama de conversión local pasa siempre por Anonimal en modo opaco antes de cualquier salida.

## Revisión 2026-06-18 — el privacy_mode manda en las dos ramas

Decisión del dueño del proyecto que SUPERSEDE el párrafo anterior: anonimizar es
**opt-in**, no obligatorio en la rama local. El `privacy_mode` ahora gobierna también
la salida de markdown/llms.txt:

- `directo`            → contenido **crudo**, NO pasa por Anonimal, en cualquier formato.
  Es la elección explícita para data no sensible; la responsabilidad es de quien lo pide.
- `opaco` / `reversible` → enmascarado opaco con marcadores estables, fail-closed.

Motivo: con la regla vieja, `directo` no cambiaba nada en la salida markdown (siempre
anonimizaba), lo que volvía el modo inútil y confuso. La garantía fail-closed (ADR-004)
sigue intacta para opaco/reversible: solo aplica cuando se PIDE anonimización.

La matriz rol por modo vive en `privacy_matrix.yaml`, no hardcodeada. El `default_mode`
sigue siendo `opaco` (privacidad por defecto); para salida cruda se pide `directo`
explícito, y el rol debe habilitarlo (hoy solo `dios`).

Tarea de Claude Code con acceso al repo: leer los nombres de modo reales que expone Anonimal hoy y reconciliarlos con esta semántica. Si Anonimal nombra a estos modos distinto, se respeta la semántica de este ADR y se ajustan los nombres en el cliente.

## Consecuencias

Opaco que preserva tipo conserva la señal para el LLM y a la vez no permite recuperar PII, que es el punto medio correcto. El mapeo explícito entre los dos vocabularios cierra el drift. La calidad de extracción del modo opaco queda atada a que el marcador preserve tipo, así que el cliente de Anonimal valida que la respuesta venga tipada.
