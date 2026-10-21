"""Flows de Prefect para el pipeline de desempeño semanal.

PIPELINE_DESIGN.md Fase 4 §4.2 (flow principal) y §4.5 (backfill):

Flow principal ``weekly_warehouse_client_performance``:
1. Resuelve la ventana temporal (por defecto: última semana ISO completa).
2. log_run_start → extract → transform → load → log_run_finish.
3. Paso opcional: export_eval_snapshot con return_state=True
   (un fallo aquí NO interrumpe E → T → L).

Flow de backfill ``backfill_weekly_warehouse_client_performance``:
- Itera sobre un rango de semanas ISO llamando al flow principal por semana.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from prefect import flow

from data.pipelines.weekly_warehouse_client_performance.config import (
    PIPELINE_NAME,
)
from data.pipelines.weekly_warehouse_client_performance.eval_snapshot import (
    export_eval_snapshot,
)
from data.pipelines.weekly_warehouse_client_performance.extract import (
    extract_telemetry_events,
)
from data.pipelines.weekly_warehouse_client_performance.load import (
    load_weekly_performance,
)
from data.pipelines.weekly_warehouse_client_performance.runs_log import (
    log_run_finish,
    log_run_start,
)
from data.pipelines.weekly_warehouse_client_performance.transform import (
    transform_to_weekly_kpis,
)

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _iso_monday_from_date(d: date) -> date:
    """Devuelve el lunes de la semana ISO que contiene *d*."""
    iso_year, iso_week, _ = d.isocalendar()
    return date.fromisocalendar(iso_year, iso_week, 1)


def resolve_window(week_start: date | None = None) -> tuple[datetime, datetime, str]:
    """Calcula la ventana ``[start, end)`` para la extracción.

    Parameters
    ----------
    week_start : date | None
        Lunes de la semana ISO a procesar. Si es ``None``, se usa la
        **última semana ISO completa** (la semana anterior a la actual).

    Returns
    -------
    tuple[datetime, datetime, str]
        ``(start, end, iso_week_label)`` con ``start`` y ``end`` en UTC.
        ``iso_week_label`` es ``"2026-W28"`` para humanización.
    """
    now_utc = datetime.now(timezone.utc)

    if week_start is None:
        today = now_utc.date()
        this_monday = _iso_monday_from_date(today)
        if today == this_monday:
            last_monday = this_monday - timedelta(days=7)
        else:
            last_monday = this_monday - timedelta(days=7)
        week_start = last_monday

    wk = _iso_monday_from_date(week_start)
    start = datetime(wk.year, wk.month, wk.day, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    iso_label = f"{wk.isocalendar()[0]}-W{wk.isocalendar()[1]:02d}"
    return start, end, iso_label


# ── Flow principal ──────────────────────────────────────────────────────────


@flow(
    name=PIPELINE_NAME,
    log_prints=True,
    retries=1,
    retry_delay_seconds=60,
)
def weekly_flow(week_start: date | None = None) -> dict[str, Any]:
    """Ejecuta el pipeline semanal: E → T → L + snapshot opcional.

    Parameters
    ----------
    week_start : date | None
        Lunes de la semana ISO a procesar. Por defecto: la última
        semana ISO completa.

    Returns
    -------
    dict
        Resumen de la ejecución con ``run_id``, ``iso_week``,
        ``processed_events``, ``rows_upserted``, ``status``,
        y ``snapshot_status``.
    """
    # 1. Resolver ventana
    start, end, iso_week = resolve_window(week_start)
    logger.info("Processing week %s [%s, %s).", iso_week, start, end)

    # 2. Registrar inicio
    run_id = log_run_start(
        pipeline=PIPELINE_NAME,
        start_window=start,
        end_window=end,
    )

    try:
        # 3. E → T → L  (etapas obligatorias con @task)
        raw: list[dict] = extract_telemetry_events(start, end)
        rows: list[dict] = transform_to_weekly_kpis(raw)
        upserted: int = load_weekly_performance(rows)

        # 4. Paso opcional: snapshot de evaluación con return_state=True
        #    Un fallo aquí captura el estado "Failed" en lugar de propagar excepción.
        snapshot_state = export_eval_snapshot(
            rows=rows,
            run_id=run_id,
            iso_week=iso_week,
            week_start=str(start.date()),
            return_state=True,
        )
        snapshot_status = "completed" if snapshot_state.is_completed() else str(snapshot_state.type)

        # 5. Cierre exitoso
        log_run_finish(
            run_id,
            success=True,
            processed_events=len(raw),
            rows_upserted=upserted,
        )

        logger.info(
            "Weekly flow %s completed: %d events → %d KPI rows (upserted=%d). "
            "Snapshot: %s.",
            iso_week,
            len(raw),
            len(rows),
            upserted,
            snapshot_status,
        )

        return {
            "run_id": run_id,
            "iso_week": iso_week,
            "week_start": str(start.date()),
            "processed_events": len(raw),
            "rows_upserted": upserted,
            "status": "success",
            "snapshot_status": snapshot_status,
        }

    except Exception as exc:
        logger.exception("Weekly flow %s failed: %s", iso_week, exc)
        log_run_finish(
            run_id,
            success=False,
            processed_events=0,
            rows_upserted=0,
            error_message=str(exc),
        )
        return {
            "run_id": run_id,
            "iso_week": iso_week,
            "week_start": str(start.date()),
            "processed_events": 0,
            "rows_upserted": 0,
            "status": "failed",
            "error": str(exc),
        }


# ── Backfill ─────────────────────────────────────────────────────────────────


@flow(
    name="backfill_weekly_warehouse_client_performance",
    log_prints=True,
)
def backfill_flow(first_week: date, last_week: date) -> list[dict[str, Any]]:
    """Recalcula semanas históricas ejecutando el flow principal por semana.

    Parameters
    ----------
    first_week : date
        Lunes ISO de la primera semana a recalcular.
    last_week : date
        Lunes ISO de la última semana a recalcular (inclusive).

    Returns
    -------
    list[dict]
        Lista de resúmenes por semana (misma estructura que ``weekly_flow``).
    """
    results: list[dict[str, Any]] = []

    current = _iso_monday_from_date(first_week)
    last = _iso_monday_from_date(last_week)

    if last < current:
        logger.warning("backfill: last_week (%s) < first_week (%s) — empty.", last, current)
        return results

    while current <= last:
        logger.info("backfill: processing week starting %s.", current)
        result = weekly_flow(week_start=current)
        results.append(result)
        current += timedelta(days=7)

    logger.info("backfill: completed %d week(s).", len(results))
    return results
