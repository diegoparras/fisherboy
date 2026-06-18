# ADR-004 — Modelo de seguridad

Estado: aceptado
Fecha: 2026-06-17

## Contexto

Scraping significa hacer fetch a URLs arbitrarias, lo que abre SSRF de entrada. El `callback_url` lo provee el usuario y el worker le hace POST, lo que abre SSRF de salida. Una falla de anonimización no puede terminar en contenido crudo entregado. Estas defensas se construyen en v1, no se difieren.

## Decisión

1. Fallar cerrado. Si el job pidió anonimización y Anonimal falla o da timeout, el job queda en estado error y nunca se devuelve contenido crudo.

2. SSRF de entrada. Antes de hacer fetch se resuelve el DNS y se bloquean IP privada, loopback, link-local y direcciones de metadata de cloud, incluida 169.254.169.254. Para evitar DNS rebinding, se conecta a la IP ya resuelta y validada, o se re-valida en cada redirect. Límite de bytes, timeout y máximo de redirects.

3. SSRF de salida. El `callback_url` se valida contra los mismos bloques que el fetch de entrada. En producción se restringe a una allowlist de destinos.

4. Rol por modo. Si el rol no habilita el modo de privacidad pedido, se responde 403 con mensaje claro y no se encola. No hay downgrade silencioso de modo. El rechazo se audita.

5. Secretos por entorno, nunca en logs. Los logs JSON no incluyen contenido sensible ni PII. El token de servicio de Anonimal y la clave del LLM viven en variables de entorno.

## Consecuencias

Algunas URLs internas legítimas se van a bloquear, que es el comportamiento correcto por defecto y se abre por excepción explícita si hace falta. La allowlist de callback agrega fricción operativa a cambio de cerrar la exfiltración por callback. El fallar cerrado prioriza no filtrar por encima de completar el job.
