# ADR-001 — Arquitectura y modos

Estado: aceptado
Fecha: 2026-06-17

## Contexto

Fisherboy es el sistema de scraping. Tiene que poder correr como sidekick de Escriba sin interfaz, o standalone con interfaz propia. Escriba está en producción y queda intacto. La infraestructura es distribuida: servidor RISE-M más VPS, con n8n como orquestador. La constelación sigue la regla del pulpo, módulos que se hablan solo por red y datos serializados, sin memoria ni disco compartido.

amada de red que puede tardar o fallar, así que el cliente HTTP hacia Anonimal y Escriba maneja timeout y reintento con límite. El estado que un cálculo necesite junto no se reparte; para Fisherboy esto significa que un job vive en un worker, no se parte entre VPS.
