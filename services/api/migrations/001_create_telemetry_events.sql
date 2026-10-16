-- Migration 001: Create telemetry_events table
-- Fase 1 — Tabla de almacenamiento en Supabase
--
-- Crea la tabla de almacenamiento de eventos de telemetría con:
-- - 8 columnas según el contrato del plan
-- - 3 índices (timestamp, event_type, GIN en tags)
-- - Sin lógica de UPDATE o DELETE (solo INSERT)

-- 1. Crear la tabla
CREATE TABLE IF NOT EXISTS telemetry_events (
    id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp   timestamptz NOT NULL,
    service     text        NOT NULL,
    event_type  text        NOT NULL,
    level       text        NOT NULL DEFAULT 'info',
    value       numeric     NULL,
    message     text        NULL,
    tags        jsonb       NOT NULL DEFAULT '{}'
);

-- 2. Índices analíticos — hacen la tabla consultable a escala
CREATE INDEX IF NOT EXISTS idx_telemetry_events_timestamp
    ON telemetry_events (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_event_type
    ON telemetry_events (event_type);

CREATE INDEX IF NOT EXISTS idx_telemetry_events_tags
    ON telemetry_events USING GIN (tags);

-- 3. Comentarios de columna (documentación inline del esquema)
COMMENT ON TABLE  telemetry_events IS 'Eventos de telemetría inmutables — solo INSERT, nunca UPDATE/DELETE';
COMMENT ON COLUMN telemetry_events.id          IS 'Identificador único del registro (UUID v4, auto-generado)';
COMMENT ON COLUMN telemetry_events.timestamp   IS 'Timestamp del evento en ISO 8601 (UTC)';
COMMENT ON COLUMN telemetry_events.service     IS 'Origen del evento (backoffice, api)';
COMMENT ON COLUMN telemetry_events.event_type  IS 'Tipo de evento en formato entidad_acción (ej. inbound_order_created)';
COMMENT ON COLUMN telemetry_events.level       IS 'Severidad: info, warn, error';
COMMENT ON COLUMN telemetry_events.value       IS 'Valor numérico asociado al evento, si aplica';
COMMENT ON COLUMN telemetry_events.message     IS 'Descripción legible del evento';
COMMENT ON COLUMN telemetry_events.tags        IS 'properties del envelope (solo claves del allowlist)';