# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/es/1.0.0/).
Versionado semántico. Cada versión publicada tiene su sección: sin ella, el CI no publica.

## [1.9.1] - 2026-11-20

### Nuevo
- **Cada deploy dice qué versión es y qué cambia**: lo primero que escriben la API y el worker
  en los registros del contenedor (EasyPanel, Resumen) es un cartel con la versión, la fecha,
  qué proceso es (API o worker, y en qué modo) y la lista de cambios.
- La interfaz avisa **Novedades** al entrar después de una actualización, con lo que cambió
  desde la última versión que viste en ese navegador. El historial queda en el menú
  (**Novedades**) y en **Acerca de** ("Qué cambió"). Por API: `GET /api/novedades?desde=X.Y.Z`.
- El CI etiqueta la imagen también con la versión (`ghcr.io/diegoparras/fisherboy:X.Y.Z`),
  crea el Release vX.Y.Z en GitHub con estas notas y las muestra en el resumen de la corrida.
