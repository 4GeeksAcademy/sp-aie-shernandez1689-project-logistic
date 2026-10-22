-- Migration 003: Create job_runs table
-- DEV-53 — Script nocturno de telemetría
--
-- Tabla de control de ejecuciones para procesos en segundo plano.
-- Cada fila representa un intento de ejecución de un job para una
-- fecha objetivo concreta (target_date).
--
-- Máquina de estados: pending → processing → completed
--                                     ↘ failed
--
-- El estado `processing` actúa como distributed lock (job-level, sin
-- tabla/columna aparte): si existe una fila processing para el job
-- (p. ej. nightly_export), otra instancia aborta silenciosamente.
--
-- La idempotencia se resuelve con la pareja (job_name, target_date):
-- si existe un registro completed para esa pareja exacta, el script no
-- vuelve a ejecutar el trabajo. Solo job_name sin target_date no basta.
--
-- Capa: orquestación nocturna.
-- No confundir con reporting.pipeline_runs (ETL interno, Hito 6).
--
-- Convención de fecha: target_date en UTC. "Ayer" por defecto,
-- sobrescribible con TARGET_DATE=YYYY-MM-DD.

-- 1. Crear la tabla
CREATE TABLE IF NOT EXISTS job_runs (
    id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_name        text        NOT NULL,
    target_date     date        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    started_at      timestamptz NULL,
    finished_at     timestamptz NULL,
    error_message   text        NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- 2. Índice compuesto para idempotencia y lock
--    Permite consultas eficientes del tipo:
--      SELECT * FROM job_runs
--      WHERE job_name = 'nightly_export'
--        AND target_date = '2026-10-20'
--        AND status IN ('processing', 'completed');
--
-- (job_name, target_date) es la clave de idempotencia:
-- si existe un registro completed para esa pareja exacta, el script no
-- vuelve a ejecutar el trabajo. Solo job_name sin target_date no basta.
CREATE INDEX IF NOT EXISTS idx_job_runs_name_date
    ON job_runs (job_name, target_date);

-- Índice adicional para consultas por fecha descendente (más recientes primero)
CREATE INDEX IF NOT EXISTS idx_job_runs_target_date
    ON job_runs (target_date DESC);

-- Índice para filtrar por estado (útil para monitoreo de stuck jobs)
CREATE INDEX IF NOT EXISTS idx_job_runs_status
    ON job_runs (status);

-- 3. Comentarios de documentación inline del esquema
COMMENT ON TABLE job_runs
    IS 'Control de ejecuciones de procesos en segundo plano (DEV-53).
        Máquina de estados: pending → processing → completed | failed.
        processing actúa como distributed lock.';

COMMENT ON COLUMN job_runs.id
    IS 'Identificador único de la ejecución.';

COMMENT ON COLUMN job_runs.job_name
    IS 'Nombre del job (ej. nightly_export, daily_report). Identifica el
        proceso orquestado.';

COMMENT ON COLUMN job_runs.target_date
    IS 'Fecha objetivo del procesamiento en UTC. Clave de idempotencia
        junto con job_name: solo una ejecución por (job, día).';

COMMENT ON COLUMN job_runs.status
    IS 'Estado actual: pending → processing → completed | failed.
        processing es el distributed lock — impide ejecuciones paralelas.';

COMMENT ON COLUMN job_runs.started_at
    IS 'Timestamp UTC de cuando el job pasó a processing (inicio real).';

COMMENT ON COLUMN job_runs.finished_at
    IS 'Timestamp UTC de cuando el job terminó (completed o failed).';

COMMENT ON COLUMN job_runs.error_message
    IS 'Mensaje de excepción si status = failed; NULL si completado
        exitosamente.';

COMMENT ON COLUMN job_runs.created_at
    IS 'Timestamp UTC de creación del registro (cuando se creó en
        pending).';