# ADR-001 — Arquitectura y modos

Estado: aceptado
Fecha: 2026-06-17

## Contexto

Fisherboy es el sistema de scraping. Tiene que poder correr como sidekick de Escriba sin interfaz, o standalone con interfaz propia. Escriba está en producción y queda intacto. La infraestructura es distribuida: servidor RISE-M más VPS, con n8n como orquestador. La constelación sigue la regla del pulpo, módulos que se hablan solo por red y datos serializados, sin memoria ni disco compartido.

## Decisión

1. Repo propio, independiente de Escriba. No se importan módulos de Escriba. La comunicación entre ambos es solo HTTP y datos serializados.
2. `APP_MODE` define el modo en tiempo de arranque.
   - `sidekick`: no sirve interfaz. Se lo llama por REST y MCP desde n8n, Claude Code o Escriba. Delega la conversión documental a la API de Escriba. Vive detrás de la red interna.
   - `standalone`: monta interfaz web propia y gateway propio. Corre conversión propia, o sigue llamando a Escriba si `ESCRIBA_URL` está seteada.
3. El núcleo es idéntico en los dos modos. El modo solo decide si se monta el router de UI y a quién se delega la conversión y la anonimización.
4. Construcción por fases v1 a v4. Cada fase termina en algo que funciona de punta a punta.
5. Escriba no se toca como crawler. Tarea de Claude Code con acceso al repo: confirmar que Escriba expone el endpoint de conversión documental que Fisherboy va a consumir en modo sidekick, y dejar registrado su contrato exacto en este ADR al implementarlo.

## Contrato real confirmado (Fase 0, 2026-06-18)

Leído con acceso real al repo de Escriba (`markitdown-web`). El endpoint de conversión
documental que Fisherboy consume en modo sidekick es:

```
POST /api/convert        (multipart/form-data)
  campos: file (UploadFile) | url (str)  — uno de los dos, obligatorio
          llm_api_key, llm_model, llm_base_url, llm_provider, lang, ocr,
          advanced, pages, yt_cookies, anonymize, anon_strict, anon_rules,
          anon_detectors  — todos opcionales
  auth: cookie de sesión + CSRF; el rol acota capacidades (OCR, tamaño, rate)
  resp: JSON con el markdown convertido y metadata
```

Escriba queda intacto como crawler: Fisherboy no importa nada de su código, solo le
hace POST a este endpoint para el sub-pipeline documental (PDFs y docs linkeados).

## Consecuencias

Independencia de despliegue y escala por módulo. A cambio, cada juntura entre servicios es una llamada de red que puede tardar o fallar, así que el cliente HTTP hacia Anonimal y Escriba maneja timeout y reintento con límite. El estado que un cálculo necesite junto no se reparte; para Fisherboy esto significa que un job vive en un worker, no se parte entre VPS.
