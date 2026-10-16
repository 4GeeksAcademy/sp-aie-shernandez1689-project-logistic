# Progress

## Estado actual del desarrollo
- Telemetría — Fase 1 (endpoint stub, rama feat-telemetry-event-capture):
  - `services/api/models.py`: nuevos `TelemetryEvent` (envelope estándar del plan §4: eventId, timestamp, sessionId, userId, event_type, schemaVersion, requestId, properties) y `TelemetryBatch` (`{"events": [...]}`, min 1). `extra="forbid"` en ambos para que el contrato sea estricto y se reutilice sin cambios en Fase 3.
  - `services/api/routes/telemetry.py`: `POST /telemetry/events` temporal. Loggea cantidad de eventos y cada `event_type`, responde `200 {"received": N}`. No autenticado (endpoint de ingesta pública). Lee `TELEMETRY_ENDPOINT` desde entorno al arranque (patrón para Fase 3; aún no redirige tráfico).
  - `services/api/main.py`: montado `telemetry_router` en la app FastAPI.
  - `services/api/tests/test_telemetry.py`: 10 tests OK (lote válido, log con caplog, lote vacío/ausente → 422, campos envelope faltantes, schemaVersion/event_type inválidos, extra_forbidden, roundtrip Pydantic, forgery). Suite api completa: 24 passed.
  - `services/api/.env.example`: documenta `JWT_SECRET_KEY` y `TELEMETRY_ENDPOINT`.
  - `services/api/README.md`: sección "Telemetry ingestion (Fase 1 — stub)".
- Proyecto anterior ya completado: services/user-api auth JWT + CRUD users/profiles + 13 criterios de aceptación verificados contra servidores reales (44/44 checks OK). Landing corporativa/backoffice con Lighthouse 96-100 tras refactors, audit before/after en audit/.
- Nota: servicios/api rastrea .pyc en git (mala práctica heredada); los .pyc untracked generados al correr tests se borran para no ensuciar el working tree.

## Proximos pasos previstos
1. Fase 3 telemetría: reemplazar el stub por implementación real — validación completa por event_type según event-schemas.json y persistencia en Supabase, usando TELEMETRY_ENDPOINT (todavía sin uso).
2. Definir el idioma base del sitio y confirmar si la version bilingue entra en este hito.
3. Implementar la landing en el orden de secciones requerido por CONTEXT.md.
4. Implementar formulario con todos los campos obligatorios y sus validaciones especificas.
5. Aplicar criterios de calidad transversal: responsive, accesibilidad y SEO on-page.
