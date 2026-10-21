-- Migration 002: Create reporting tables
-- Fase 2 §2.5 — Tablas de destino del pipeline de desempeño semanal
--
-- Crea el esquema `reporting` (dedicado, separado de `telemetry_events`)
-- con dos tablas:
--
--   1. reporting.weekly_warehouse_client_performance
--      El entregable materializado del pipeline: un consolidado semanal
--      por (warehouse, client_id, week_start) con los 4 KPIs de negocio
--      del Brifing-pipeline.md §2.
--
--   2. reporting.pipeline_runs
--      Log auditable de cada corrida del pipeline (Fase 3 §3.2): estado,
--      ventana de tiempo, contadores y error. Es la fuente que consume
--      GET /reporting/pipeline-runs/latest.
--
-- Nunca se escribe en `telemetry_events` (fuente inmutable, solo lectura).

-- 0. Esquema dedicado
CREATE SCHEMA IF NOT EXISTS reporting;

-- 1. Tabla del entregable — grano semanal almacén × cliente
CREATE TABLE IF NOT EXISTS reporting.weekly_warehouse_client_performance (
    id uuid primary key default gen_random_uuid(),
    warehouse text not null,
    client_id text not null,
    week_start date not null,
    inbound_units_count integer not null default 0,
    outbound_orders_count integer not null default 0,
    stockout_events_count integer not null default 0,
    discrepancy_events_count integer not null default 0,
    discrepancy_rate numeric not null default 0,
    computed_at timestamptz not null default now(),
    unique (warehouse, client_id, week_start)
);

-- Índice de apoyo para la consulta por semana (endpoint 1 del Briefing §6)
CREATE INDEX IF NOT EXISTS idx_wwcp_week_start
    ON reporting.weekly_warehouse_client_performance (week_start DESC);

-- Comentarios de columna (documentación inline del esquema)
COMMENT ON TABLE  reporting.weekly_warehouse_client_performance
    IS 'Consolidado semanal por almacén y cliente — KPIs de desempeño de negocio (Pipeline Fase 2 §2.5).';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.warehouse
    IS 'Almacén (los_angeles | zaragoza) — dimensión del grano.';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.client_id
    IS 'Identificador B2B del cliente propietario del SKU — dimensión del grano.';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.week_start
    IS 'Lunes de la semana ISO (UTC) — dimensión temporal del grano.';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.inbound_units_count
    IS 'KPI Volumen de entrada: SUM(tags.quantity) de inbound_order_created.';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.outbound_orders_count
    IS 'KPI Throughput de salida: COUNT(outbound_order_created).';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.stockout_events_count
    IS 'KPI Frecuencia de quiebre de stock: COUNT(stock_threshold_triggered).';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.discrepancy_events_count
    IS 'Conteo de apoyo: COUNT(inventory_discrepancy_detected) — numerador de la tasa.';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.discrepancy_rate
    IS 'KPI Tasa de discrepancia: discrepancia / outbound (0 si no hubo pedidos).';
COMMENT ON COLUMN reporting.weekly_warehouse_client_performance.computed_at
    IS 'Timestamp de la última corrida que materializó esta fila (auditoría).';

-- 2. Log de corridas del pipeline — fuente auditable de estado
CREATE TABLE IF NOT EXISTS reporting.pipeline_runs (
    id uuid primary key default gen_random_uuid(),
    pipeline text not null default 'weekly_warehouse_client_performance',
    status text not null default 'running',   -- running | success | failed
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    processed_events integer not null default 0,
    rows_upserted integer not null default 0,
    error_message text
);

-- Índice de apoyo para la consulta de la última corrida (endpoint 3)
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at
    ON reporting.pipeline_runs (started_at DESC);

-- Comentarios de columna (documentación inline del esquema)
COMMENT ON TABLE  reporting.pipeline_runs
    IS 'Log de corridas del pipeline de desempeño semanal (Fase 3 §3.2).';
COMMENT ON COLUMN reporting.pipeline_runs.pipeline
    IS 'Nombre del pipeline (hoy constante: weekly_warehouse_client_performance).';
COMMENT ON COLUMN reporting.pipeline_runs.status
    IS 'Estado de la corrida: running | success | failed.';
COMMENT ON COLUMN reporting.pipeline_runs.processed_events
    IS 'Registros de origen leídos en la extracción (eventos telemetry_events).';
COMMENT ON COLUMN reporting.pipeline_runs.rows_upserted
    IS 'Filas destino escritas/actualizadas en reporting.weekly_warehouse_client_performance.';
COMMENT ON COLUMN reporting.pipeline_runs.error_message
    IS 'Detalle del error cuando status = failed; NULL si la corrida fue exitosa.';