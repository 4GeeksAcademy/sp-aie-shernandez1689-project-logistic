/**
 * Telemetría TrackFlow — contrato compartido del envelope.
 *
 * Este tipo y la constante de schemaVersion se reutilizan por todos los
 * componentes que emiten eventos (`lib/telemetry.ts` + call sites de
 * `track()`). La versión del schema NUNCA debe definirse por duplicado:
 * si el properties de un `event_type` evoluciona, se cambia aquí y en
 * el backend (`docs/telemetry/event-schemas.json`).
 */

/**
 * Versión semver del schema de `properties` que emite el frontend hoy.
 * Se envía en cada evento; el backend la usa para validar y migrar.
 */
export const TELEMETRY_SCHEMA_VERSION = "1.0.0";

/**
 * Envelope estándar de un evento de telemetría
 * (docs/telemetry/telemetry-plan.md §4).
 *
 * Los campos de correlación (eventId, timestamp, sessionId, userId,
 * schemaVersion, requestId) los completa automáticamente el servicio en
 * `lib/telemetry.ts` — los componentes que llaman a `track()` nunca los
 * pasan manualmente.
 */
export interface TelemetryEventEnvelope {
  /** UUID v4, generado en el emisor (momento de captura). */
  eventId: string;
  /** ISO 8601 en UTC (reloj del emisor). */
  timestamp: string;
  /** ID de sesión del frontend, persistido en Session Storage. */
  sessionId: string;
  /** ID del usuario autenticado; "anonymous" si no hay sesión. */
  userId: string;
  /** Nombre del evento: dominio_entidad_accion en pasado. */
  event_type: string;
  /** Semver x.y.z del schema de `properties`. */
  schemaVersion: string;
  /** Correlation ID del lote/request HTTP de envío. */
  requestId: string;
  /** Payload específico del evento (allowlist por event_type). */
  properties: Record<string, unknown>;
}