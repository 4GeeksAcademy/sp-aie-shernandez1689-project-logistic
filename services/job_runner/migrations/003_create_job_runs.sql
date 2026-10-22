-- Migration 003: Create job_runs table
-- Control de estado para ejecuciones de pipelines
--
-- Crea la tabla de registro de ejecuciones con:
-- - 11 columnas según el contrato del job_runner
-- - 2 índices compuestos (pipeline+status, pipeline+date_from)
-- - Sin lógica de UPDATE o DELETE (solo INSERT y UPDATE de estado)

-- 1. Crear la tabla
CREATE TABLE IF NOT EXISTS job_runs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline        text        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending',
    started_at      timestamptz NULL,
    finished_at     timestamptz NULL,
    date_from       text        NULL,
    date_to         text        NULL,
    processed_events integer    NOT NULL DEFAULT 0,
    rows_upserted   integer    NOT NULL DEFAULT 0,
    error_message   text        NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- 2. Índices analíticos
CREATE INDEX IF NOT EXISTS idx_job_runs_pipeline_status
    ON job_runs (pipeline, status);

CREATE INDEX IF NOT EXISTS idx_job_runs_pipeline_date
    ON job_runs (pipeline, date_from);

-- 3. Comentarios de columna (documentación inline del esquema)
COMMENT ON TABLE  job_runs IS 'Registro de ejecuciones de pipelines — control de estado y concurrencia';
COMMENT ON COLUMN job_runs.id               IS 'Identificador único del registro (UUID v4, auto-generado)';
COMMENT ON COLUMN job_runs.pipeline         IS 'Nombre del pipeline (ej. weekly_warehouse_client_performance)';
COMMENT ON COLUMN job_runs.status           IS 'Estado: pending, processing, success, failed';
COMMENT ON COLUMN job_runs.started_at       IS 'Timestamp de inicio de la ejecución (UTC)';
COMMENT ON COLUMN job_runs.finished_at      IS 'Timestamp de finalización (UTC) — solo success o failed';
COMMENT ON COLUMN job_runs.date_from        IS 'ISO date del inicio de la ventana procesada (inclusivo)';
COMMENT ON COLUMN job_runs.date_to          IS 'ISO date del fin de la ventana procesada (exclusivo)';
COMMENT ON COLUMN job_runs.processed_events AS 'Número de eventos procesados durante la ejecución';
COMMENT ON COLUMN job_runs.rows_upserted    AS 'Número de filas insertadas o actualizadas';
COMMENT ON COLUMN job_runs.error_message    AS 'Mensaje de error detallado — solo si status = failed';
COMMENT ON COLUMN job_runs.created_at       AS 'Timestamp de creación del registro (UTC)';