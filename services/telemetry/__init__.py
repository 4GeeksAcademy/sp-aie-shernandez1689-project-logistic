"""Pipeline de análisis operacional de telemetría para TrackFlow.

Este paquete implementa la Fase 1 del proyecto de reporte técnico
(tele-report.md): funciones de métrica independientes que cargan eventos
desde Supabase, los transforman con Pandas y devuelven resultados
JSON-serializables para el endpoint ``GET /telemetry/report``.
"""

from .analysis import (
    api_errors_by_endpoint,
    auth_failure_rate_daily,
    build_telemetry_report,
    error_rate_by_type,
    error_rate_daily,
    events_per_day,
    events_per_type_per_day,
)

__all__ = [
    "api_errors_by_endpoint",
    "auth_failure_rate_daily",
    "build_telemetry_report",
    "error_rate_by_type",
    "error_rate_daily",
    "events_per_day",
    "events_per_type_per_day",
]