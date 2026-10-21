"""Consultas de solo lectura para la capa HTTP.

PIPELINE_DESIGN.md Fase 5 §5.2:

- ``latest_computed_week()`` → ``date | None``
- ``get_weekly_performance(week_start)`` → ``list[dict]``
- ``get_latest_pipeline_run()`` → ``dict | None``

Son las únicas funciones que ``services/reporting/router.py`` importa
desde ``data/pipelines/`` para los endpoints **GET** (solo lectura).

En modo local (sin DATABASE_URL) leen desde los archivos ``data/eval/``.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, text

from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db
from data.pipelines.weekly_warehouse_client_performance.config import (
    KPI_FIELDS,
    RUNS_TABLE,
    WEEKLY_TABLE,
)

logger = logging.getLogger(__name__)


def latest_computed_week() -> date | None:
    """Devuelve la semana (lunes) más reciente con datos consolidados.

    Returns
    -------
    date | None
        ``week_start`` máximo, o ``None`` si no hay datos.
    """
    with pipeline_db.session_scope() as session:
        if session is None:
            return _latest_local_week()

        stmt = text(
            f"SELECT MAX(week_start) FROM {WEEKLY_TABLE}"
        )
        result = session.execute(stmt).scalar()
        return result


def _latest_local_week() -> date | None:
    """Modo local: lee el week_start más reciente desde JSONL."""
    kpis_file = pipeline_db.eval_kpis_file()
    if not kpis_file.exists():
        return None

    latest: date | None = None
    with kpis_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ws = record.get("week_start")
            if ws:
                try:
                    ws_date = date.fromisoformat(ws) if isinstance(ws, str) else ws
                except (TypeError, ValueError):
                    continue
                if latest is None or ws_date > latest:
                    latest = ws_date
    return latest


def get_weekly_performance(week_start: date) -> list[dict[str, Any]]:
    """Devuelve todas las filas KPI de una semana.

    Parameters
    ----------
    week_start : date
        Lunes de la semana ISO a consultar.

    Returns
    -------
    list[dict]
        Lista de entradas con warehouse, client_id y los 5 KPIs.
        Ordenadas por warehouse, luego client_id.
    """
    with pipeline_db.session_scope() as session:
        if session is None:
            return _get_local_weekly_performance(week_start)

        columns = ", ".join(
            ["warehouse", "client_id", "week_start"] + list(KPI_FIELDS)
        )
        stmt = text(
            f"""
            SELECT {columns}
            FROM {WEEKLY_TABLE}
            WHERE week_start = :week_start
            ORDER BY warehouse, client_id
            """
        )
        rows = session.execute(stmt, {"week_start": week_start}).all()

    return [
        {
            "warehouse": row.warehouse,
            "client_id": row.client_id,
            "week_start": str(row.week_start),
            "inbound_units_count": row.inbound_units_count,
            "outbound_orders_count": row.outbound_orders_count,
            "stockout_events_count": row.stockout_events_count,
            "discrepancy_events_count": row.discrepancy_events_count,
            "discrepancy_rate": float(row.discrepancy_rate),
        }
        for row in rows
    ]


def _get_local_weekly_performance(week_start: date) -> list[dict[str, Any]]:
    """Modo local: filtra las filas KPI desde JSONL por week_start."""
    kpis_file = pipeline_db.eval_kpis_file()
    if not kpis_file.exists():
        return []

    entries: list[dict[str, Any]] = []
    ws_str = week_start.isoformat()

    with kpis_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(record.get("week_start", "")) == ws_str:
                entries.append(record)

    return sorted(entries, key=lambda r: (r.get("warehouse", ""), r.get("client_id", "")))


def get_latest_pipeline_run() -> dict[str, Any] | None:
    """Devuelve la última corrida registrada.

    Returns
    -------
    dict | None
        Metadata de la última corrida (id, status, started_at, finished_at,
        processed_events, rows_upserted, error_message),
        o ``None`` si no hay corridas registradas.
    """
    with pipeline_db.session_scope() as session:
        if session is None:
            return _get_latest_local_run()

        stmt = text(
            f"""
            SELECT id, pipeline, status, started_at, finished_at,
                   processed_events, rows_upserted, error_message
            FROM {RUNS_TABLE}
            ORDER BY started_at DESC
            LIMIT 1
            """
        )
        row = session.execute(stmt).one_or_none()

    if row is None:
        return None

    return {
        "id": str(row.id),
        "pipeline": row.pipeline,
        "status": row.status,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "processed_events": row.processed_events,
        "rows_upserted": row.rows_upserted,
        "error_message": row.error_message,
    }


def _get_latest_local_run() -> dict[str, Any] | None:
    """Modo local: lee la última corrida desde JSONL.

    Cada corrida genera dos líneas (start + finish). Esta función
    las fusiona por run_id y devuelve la corrida más reciente.
    """
    runs_file = pipeline_db.eval_runs_file()
    if not runs_file.exists():
        return None

    # Leer todas las líneas y agrupar por id
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    with runs_file.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            rid = record.get("id")
            if not rid:
                continue
            if rid not in merged:
                merged[rid] = {}
                order.append(rid)
            merged[rid].update(record)

    if not merged:
        return None

    # Última corrida (la de orden más tardío)
    latest_id = order[-1]
    return merged.get(latest_id)