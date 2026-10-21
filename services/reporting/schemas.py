"""Modelos pydantic de request/response para los endpoints de reporting.

PIPELINE_DESIGN.md Fase 5 §5.2 — contratos exactos del Briefing §6:
- ``GET /reporting/weekly-warehouse-client-performance``
- ``GET /reporting/pipeline-runs/latest``
- ``POST /reporting/pipeline-runs``
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class WeeklyEntry(BaseModel):
    """Una fila del reporte semanal por almacén y cliente."""

    warehouse: str = Field(..., description="Almacén (los_angeles | zaragoza)")
    client_id: str = Field(..., description="Identificador del cliente")
    inbound_units_count: int = Field(..., ge=0)
    outbound_orders_count: int = Field(..., ge=0)
    stockout_events_count: int = Field(..., ge=0)
    discrepancy_events_count: int = Field(..., ge=0)
    discrepancy_rate: float = Field(..., ge=0.0)


class WeeklyPerformanceResponse(BaseModel):
    """Respuesta de GET /reporting/weekly-warehouse-client-performance."""

    week_start: str = Field(..., description="ISO date del lunes de la semana")
    entries: list[WeeklyEntry]


class PipelineRunResponse(BaseModel):
    """Respuesta de GET /reporting/pipeline-runs/latest."""

    id: str
    pipeline: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    processed_events: int = 0
    rows_upserted: int = 0
    error_message: str | None = None


class TriggerRunRequest(BaseModel):
    """Cuerpo opcional de POST /reporting/pipeline-runs."""

    week_start: str | None = Field(
        None,
        description="ISO date del lunes de la semana a procesar (opcional: por defecto última semana completa)",
    )


class TriggerRunResponse(BaseModel):
    """Respuesta de POST /reporting/pipeline-runs."""

    run_id: str
    status: str = "running"