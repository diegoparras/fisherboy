# ADR-002 — Modos de privacidad y mapeo a Anonimal

Estado: aceptado
Fecha: 2026-06-17

## Contexto

El usuario elige el modo de privacidad por job, filosofía de Escriba, acotado por su rol. El modo a nivel de job es reversible, opaco o directo. Anonimal por dentro maneja sus propios modos de procesamiento. Sin un mapeo explícito entre los dos vocabularios, aparece drift, y la elección de a qué modo de Anonimal corresponde cada uno define la calidad de extracción del LLM.
