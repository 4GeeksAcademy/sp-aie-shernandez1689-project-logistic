"""Configuración del pipeline de desempeño semanal.

Centraliza los nombres de dominio (los 4 KPI event types, la tabla destino,
el esquema de reporting) para que las etapas E/T/L y la capa HTTP usen un
único vocabulario — el del Brifing-pipeline.md §2-§5 y la validación de
vocabulario de PIPELINE_DESIGN.md §5.5.
"""

from __future__ import annotations

# ── Pipeline ────────────────────────────────────────────────────────────────

PIPELINE_NAME = "weekly_warehouse_client_performance"

# ── Origen (telemetry_events, solo lectura) ─────────────────────────────────

# Los 4 KPIs del Brifing-pipeline.md §3 — métricas obligatorias del plan de
# telemetría. Solo estos event_type entran al extract (defensa de alcance).
EVENT_TYPES_KPI: tuple[str, ...] = (
    "inbound_order_created",      # Volumen de entrada
    "outbound_order_created",     # Throughput de salida + denominador de tasa
    "stock_threshold_triggered",  # Frecuencia de quiebre de stock
    "inventory_discrepancy_detected",  # Numerador de tasa de discrepancia
)

# Mapa evento → campo calculado (contrato inalterable, PIPELINE_DESIGN §5.5).
EVENT_TO_FIELD: dict[str, str] = {
    "inbound_order_created": "inbound_units_count",
    "outbound_order_created": "outbound_orders_count",
    "stock_threshold_triggered": "stockout_events_count",
    "inventory_discrepancy_detected": "discrepancy_events_count",
}

# Dimensiones permitidas de almacén (PIPELINE_DESIGN §5.5). Valores fuera de
# este conjunto se descartan en transform (defensa).
WAREHOUSES_ALLOWED: frozenset[str] = frozenset({"los_angeles", "zaragoza"})

# Clave del tag JSONB donde vive la cantidad para el volumen de entrada.
INBOUND_QUANTITY_TAG = "quantity"

# ── Destino (reporting.*) ───────────────────────────────────────────────────

REPORTING_SCHEMA = "reporting"
WEEKLY_TABLE = f"{REPORTING_SCHEMA}.weekly_warehouse_client_performance"
RUNS_TABLE = f"{REPORTING_SCHEMA}.pipeline_runs"
REPORTING_TABLE = WEEKLY_TABLE  # alias para la capa HTTP

# ── Campos calculados (orden y nombres exactos del Briefing §5) ─────────────

KPI_FIELDS: tuple[str, ...] = (
    "inbound_units_count",
    "outbound_orders_count",
    "stockout_events_count",
    "discrepancy_events_count",
    "discrepancy_rate",
)

# ── Log de corridas ─────────────────────────────────────────────────────────

RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"

# ── Rutas de datos locales (modo sin-DB para desarrollo/validación) ─────────

# data/raw/  → eventos sintéticos de entrada (cuando no hay DATABASE_URL).
# data/eval/ → salidas de validación del pipeline (validación sin DB).
DEFAULT_RAW_INPUT = "data/raw/weekly_warehouse_client_performance/events.jsonl"