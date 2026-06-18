# ADR-005 — Threat model de la pseudonimización reversible

Estado: propuesto
Fecha: 2026-06-17

## Contexto

El modo reversible permite que la extracción por LLM corra sobre un proveedor externo sin exponer PII real. La discusión previa asumió que el control central era el cifrado de la tabla de mapeo. Ese supuesto está mal ordenado. El control que de verdad determina qué PII sale de la infraestructura es la recall de detección de spans del modelo de Anonimal. La tabla solo cubre las entidades que el detector encontró. Una entidad que el detector se pierde nunca entra al mapeo, viaja al LLM en texto plano, y ningún cifrado la protege.

## Activos

1. PII en el material scrapeado.
2. La tabla de mapeo, que vincula marcador con entidad real.
3. La clave que cifra la tabla.
4. La capacidad de reversión.

## Amenazas, por severidad

### T1 — Falla de recall de detección (primaria)
El detector no encuentra un span de PII y la entidad viaja al LLM externo sin enmascarar. Es la vía de fuga dominante y la única que no mitiga ninguna medida sobre la tabla. Justificación de severidad: impacto alto, PII real a un tercero; explotabilidad pasiva, basta un falso negativo del modelo sin necesidad de atacante; alcance amplio, cualquier entidad de una clase mal cubierta; sin factor mitigante una vez enviado el dato. Severidad alta.

### T2 — Compromiso de la tabla de mapeo
Si la tabla y su clave se filtran juntas, todos los marcadores revierten a PII real. Requiere vulnerar infraestructura interna. Severidad alta pero secundaria respecto de T1. Factor mitigante: cifrado y custodia separada de la clave.

### T3 — Bypass de autorización de reversión
Un rol que no debería revertir obtiene el `mapping_ref` y rehidrata. Severidad media a alta según la PII del mapeo.

### T4 — Persistencia más allá de la necesidad
La tabla sobrevive al job y acumula PII reversible sin uso, ampliando la ventana de T2 y T3.

### T5 — Exposición de Anonimal
Al sumar Fisherboy como segundo consumidor, cualquier componente de la red interna alcanza a Anonimal. Severidad media, dependiente de la segmentación y la auth del ADR-003.

### T6 — Retención en el proveedor externo
El proveedor retiene el texto pseudonimizado. Si la recall fue imperfecta, la PII filtrada queda retenida fuera de control, sin posibilidad de borrado.

## Decisión

Sobre T1, el eje:

1. La recall se trata como el SLA de privacidad medible del modo reversible, con un piso por clase de entidad, a obtener midiendo el modelo.
2. Sesgo conservador. Ante baja confianza, enmascarar. Los falsos positivos degradan extracción pero no filtran; los falsos negativos filtran.
3. Allowlist por clase de entidad. Solo las clases que el modelo detecta de forma confiable son elegibles para reversible. Las de recall pobre fuerzan opaco o rechazo.
4. Pasada determinística previa al envío para PII de alto riesgo atrapable por regla: CUIT, CUIL, email, IP, tarjeta, teléfono. Corre además del modelo, no en su lugar.
5. Fallar cerrado. Si la cobertura o la confianza caen bajo umbral, el job no va al LLM externo en reversible. Baja a procesamiento local o da error.
6. Auditoría de recall en el tiempo con muestreo, y alerta si deriva bajo el piso.

Sobre T2, T3, T4: tabla cifrada en reposo, clave en custodia separada, TTL derivado del ciclo de vida del job, `mapping_ref` opaco atado a job y rol, borrado al primer revert exitoso o al expirar el TTL. La reversión valida rol y se audita sin loggear el contenido.

Sobre T5: Anonimal suma auth de servicio y política de red estricta, según ADR-003.

## Consecuencias

La garantía del modo reversible queda explícita y acotada, medible por recall, en lugar de presentarse como absoluta. La calidad de extracción puede bajar por el sobre-enmascarado conservador, costo aceptado a cambio de no filtrar. Hay contenido que reversible va a rechazar por no poder garantizar cobertura, y ese rechazo es correcto.

## Preguntas abiertas

1. Piso de recall por clase de entidad, a fijar midiendo el modelo.
2. Si reversible se bloquea por completo cuando hay PII determinística de alto riesgo que no se puede garantizar enmascarada.
3. Ubicación de la custodia de la clave.
4. Umbral de cobertura bajo el cual el job falla cerrado.
