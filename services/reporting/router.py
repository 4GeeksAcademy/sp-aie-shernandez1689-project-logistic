"""Router de reporting — 3 endpoints del pipeline de desempeño semanal.

PIPELINE_DESIGN.md Fase 5 §5.2:

1. ``GET /reporting/weekly-warehouse-client-performance`` — KPIs semanales.
2. ``GET /reporting/pipeline-runs/latest`` — estado de la última corrida.
3. ``POST /reporting/pipeline-runs`` — disparo manual (autenticado con token).

Regla estricta (Fase 5 §5.1): este router **importa únicamente** funciones de
``data/pipelines/weekly_warehouse_client_performance/``. No contiene SQL de
agregación ni lógica de transformación.
"""

from __future__ import annotations

import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

# ── Acceso al paquete data.pipelines (mismo patrón que routes/telemetry.py) ──
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.pipelines.weekly_warehouse_client_performance import queries
from data.pipelines.weekly_warehouse_client_performance import trigger
from services.reporting.deps import get_current_user, verify_reporting_run_token
from services.reporting.schemas import (
    PipelineRunResponse,
    TriggerRunRequest,
    TriggerRunResponse,
    WeeklyEntry,
    WeeklyPerformanceResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reporting", tags=["Reporting"])


@router.get(
    "/weekly-warehouse-client-performance",
    response_model=WeeklyPerformanceResponse,
)
async def get_weekly_performance(
    week_start: str | None = Query(
        None,
        description="ISO date del lunes de la semana (opcional: por defecto la más reciente)",
    ),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Devuelve los KPIs semanales por almacén y cliente.

    Si no se especifica ``week_start``, se devuelve la semana más reciente
    con datos consolidados.

    Contrato exacto del Briefing §6 (Endpoint 1).
    """
    ws: date | None = None
    if week_start is not None:
        try:
            ws = date.fromisoformat(week_start)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid week_start format (expected ISO date): {exc}",
            )

    if ws is None:
        latest = queries.latest_computed_week()
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No weekly performance data available yet. Run the pipeline first.",
            )
        ws = latest

    entries_raw = queries.get_weekly_performance(ws)

    # Cachear respuesta 60s (mismo patrón que telemetry/report)
    from services.api.cache import telemetry_report_cache

    cache_key = f"reporting:kpis:{ws.isoformat()}"
    hit, cached = telemetry_report_cache.get(cache_key)
    if hit:
        return cached

    entries = [
        WeeklyEntry(
            warehouse=e["warehouse"],
            client_id=e["client_id"],
            inbound_units_count=e["inbound_units_count"],
            outbound_orders_count=e["outbound_orders_count"],
            stockout_events_count=e["stockout_events_count"],
            discrepancy_events_count=e["discrepancy_events_count"],
            discrepancy_rate=float(e["discrepancy_rate"]),
        )
        for e in entries_raw
    ]

    response = {
        "week_start": ws.isoformat(),
        "entries": [e.model_dump() for e in entries],
    }

    telemetry_report_cache.set(cache_key, response)
    return response


@router.get(
    "/pipeline-runs/latest",
    response_model=PipelineRunResponse,
)
async def get_latest_pipeline_run(
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Devuelve la metadata de la última corrida del pipeline.

    Contrato del Briefing §6 (Endpoint 3).
    """
    run = queries.get_latest_pipeline_run()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pipeline runs found. Run the pipeline first.",
        )

    return PipelineRunResponse(
        id=run["id"],
        pipeline=run["pipeline"],
        status=run["status"],
        started_at=run.get("started_at"),
        finished_at=run.get("finished_at"),
        processed_events=run.get("processed_events", 0),
        rows_upserted=run.get("rows_upserted", 0),
        error_message=run.get("error_message"),
    ).model_dump()


@router.post(
    "/pipeline-runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TriggerRunResponse,
)
async def trigger_pipeline_run(
    body: TriggerRunRequest | None = None,
    token: None = Depends(verify_reporting_run_token),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Dispara una corrida manual del pipeline de desempeño semanal.

    Opcionalmente acepta ``week_start`` en el body (ISO date del lunes de la
    semana a procesar). Si no se especifica, se procesa la última semana ISO
    completa.

    Autenticación requerida: JWT Bearer + opcionalmente ``REPORTING_RUN_TOKEN``.

    Contrato del Briefing §6 (Endpoint 2).
    """
    ws: date | None = None
    if body and body.week_start:
        try:
            ws = date.fromisoformat(body.week_start)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid week_start format (expected ISO date): {exc}",
            )

    run_id = trigger.trigger_weekly_run(week_start=ws)

    return TriggerRunResponse(
        run_id=run_id,
        status="running",
    ).model_dump()