# Progress

## Estado actual del desarrollo
- Telemetría — Fase 2 (TelemetryService frontend, rama feat-telemetry-event-capture):
  - `uis/talent-pipeline-tracker/types/telemetry.ts`: contrato compartido del envelope (`TelemetryEventEnvelope`) + constante `TELEMETRY_SCHEMA_VERSION = "1.0.0"` (única fuente de la versión de schema).
  - `uis/talent-pipeline-tracker/lib/telemetry.ts`: TelemetryService. Cola local en memoria; batch + debounce (20 eventos o 10 s, lo primero); flush de cierre con `navigator.sendBeacon` en `visibilitychange` hidden y `pagehide`; reintentos con backoff exponencial (hasta 3, base 1 s); auto-envelope: `eventId` (UUID v4), `timestamp` (ISO 8601 UTC), `sessionId` (sessionStorage `trackflow_telemetry_session_id`), `userId` (claim `sub` del JWT `trackflow_access_token`; "anonymous" sin sesión), `schemaVersion` (constante compartida), `requestId` (correlación del lote, `req_...`). API pública única: `track(eventType, properties)`. Lee `NEXT_PUBLIC_TELEMETRY_ENDPOINT` perezosamente; sin endpoint descarta el lote silenciosamente. Exports de test: `flushQueue`, `onVisibilityChange`, `__telemetryInternals`.
  - `uis/talent-pipeline-tracker/lib/telemetry.test.ts`: 10 tests con `node:test` → 10/10 passing (envelope completo, sessionId estable + userId del JWT, anonymous, batch a los 20, debounce a los 10 s con `mock.timers`, sendBeacon en visibilitychange, retry exponencial con `mock.timers`, 4xx sin reintento, sin endpoint descarta, flush explícito).
  - Nota ejecución: `node --test lib/telemetry.test.ts` desde `uis/talent-pipeline-tracker`. Los imports relativos llevan extensión `.ts` (requisito del runner ESM de Node 24); por eso `tsconfig.json` del subproyecto ahora incluye `allowImportingTsExtensions` (válido con `noEmit`).
  - `uis/talent-pipeline-tracker/.env.local.example`: documenta `NEXT_PUBLIC_TELEMETRY_ENDPOINT` (ignorado por .gitignore `.env*`).
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
1. Fase 2 (siguiente en progreso tras service+test): conectar call sites del backoffice a `track()` — los 5 event_types obligatorios del plan de inventario (inbound/outbound/stock/adjustment/transfer creaneds, etc.) pasan por `track()` exclusivamente.
2. Fase 3 telemetría: reemplazar el stub por implementación real — validación completa por event_type según event-schemas.json y persistencia en Supabase, usando TELEMETRY_ENDPOINT y NEXT_PUBLIC_TELEMETRY_ENDPOINT.
3. Definir el idioma base del sitio y confirmar si la version bilingue entra en este hito.
4. Implementar la landing en el orden de secciones requerido por CONTEXT.md.
5. Implementar formulario con todos los campos obligatorios y sus validaciones especificas.
6. Aplicar criterios de calidad transversal: responsive, accesibilidad y SEO on-page.
