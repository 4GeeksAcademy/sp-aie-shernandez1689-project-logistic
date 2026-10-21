"""Paquete del pipeline de desempeño semanal por almacén y cliente.

Contiene las tres etapas del flujo (Extracción → Transformación → Carga),
el log de corridas, las consultas de lectura para la capa HTTP y el trigger
de ejecución. Ver PIPELINE_DESIGN.md Fase 5 §5.1 para el árbol reconciliado.
"""

from data.pipelines.weekly_warehouse_client_performance.config import (
    EVENT_TYPES_KPI,
    EVENT_TO_FIELD,
    PIPELINE_NAME,
    REPORTING_TABLE,
    RUNS_TABLE,
    WEEKLY_TABLE,
    WAREHOUSES_ALLOWED,
)
from data.pipelines.weekly_warehouse_client_performance.extract import (
    extract_telemetry_events,
)
from data.pipelines.weekly_warehouse_client_performance.transform import (
    transform_to_weekly_kpis,
)
from data.pipelines.weekly_warehouse_client_performance.load import (
    load_weekly_performance,
)
from data.pipelines.weekly_warehouse_client_performance.runs_log import (
    log_run_finish,
    log_run_start,
)
from data.pipelines.weekly_warehouse_client_performance.queries import (
    get_latest_pipeline_run,
    get_weekly_performance,
    latest_computed_week,
)
from data.pipelines.weekly_warehouse_client_performance.trigger import (
    trigger_weekly_run,
)

__all__ = [
    "EVENT_TYPES_KPI",
    "EVENT_TO_FIELD",
    "PIPELINE_NAME",
    "REPORTING_TABLE",
    "RUNS_TABLE",
    "WAREHOUSES_ALLOWED",
    "WEEKLY_TABLE",
    "extract_telemetry_events",
    "get_latest_pipeline_run",
    "get_weekly_performance",
    "latest_computed_week",
    "load_weekly_performance",
    "log_run_finish",
    "log_run_start",
    "transform_to_weekly_kpis",
    "trigger_weekly_run",
]