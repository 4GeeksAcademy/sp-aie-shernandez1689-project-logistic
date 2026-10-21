"""Extracción (E) — leer `telemetry_events` en modo SOLO LECTURA.

PIPELINE_DESIGN.md Fase 4 §4.3 (Task 1):

- SQL filtra por ``event_type IN (4 KPIs)`` y ventana de ``timestamp``
  ``[start, end)`` en UTC (inicio inclusivo / fin exclusivo).
- Devuelve lista de dicts ``{"timestamp", "event_type", "tags"}``, no Row.
- Es la **única** lectura de ``telemetry_events`` de todo el sistema de
  reporting; ningún endpoint de ``services/reporting/`` consulta esta tabla.

Modo local (sin ``DATABASE_URL``): lee eventos sintéticos desde
``data/raw/weekly_warehouse_client_performance/events.jsonl`` (líneas JSON con
``timestamp``, ``event_type`` y ``tags``), filtrando la misma ventana y los
mismos event types. Esto permite validar el pipeline sin infraestructura.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prefect import task
from sqlmodel import Session, text

from data.pipelines.weekly_warehouse_client_performance.config import (
    EVENT_TYPES_KPI,
)
from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db

logger = logging.getLogger(__name__)


def _read_local_events(
    start: datetime,
    end: datetime,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Modo local: carga eventos JSONL respetando ventana y event types.

    Ignora filas malformadas con warning (misma filosofía "rechaza sin
    abortar" de la ingesta de telemetría).
    """
    source = path or pipeline_db.raw_events_file()
    if not source.exists():
        logger.warning("No raw events file at %s — empty extract.", source)
        return []

    events: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed JSONL line %d in %s", line_number, source)
                continue

            event_type = record.get("event_type")
            if event_type not in EVENT_TYPES_KPI:
                continue

            ts_raw = record.get("timestamp")
            if not ts_raw:
                continue
            try:
                ts = _coerce_datetime(ts_raw)
            except (TypeError, ValueError) as exc:
                logger.warning("Skipping invalid timestamp %r (%s)", ts_raw, exc)
                continue

            if not (start <= ts < end):
                continue

            events.append(
                {
                    "timestamp": ts.isoformat(),
                    "event_type": event_type,
                    "tags": _coerce_tags(record.get("tags")),
                }
            )
    return events


def _coerce_datetime(value: Any) -> datetime:
    """Normaliza un timestamp (str ISO | datetime) a datetime UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _coerce_tags(tags: Any) -> dict[str, Any]:
    return tags if isinstance(tags, dict) else {}


@task(
    name="extract_telemetry_events",
    description=(
        "Lee telemetry_events SOLO LECTURA: event_type IN (4 KPIs) y ventana "
        "[start, end) UTC. Devuelve list[dict]."
    ),
)
def extract_telemetry_events(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Extrae los eventos de los 4 KPIs en la ventana ``[start, end)`` UTC.

    Lee ``telemetry_events`` en **solo lectura** (SELECT). Nunca escribe.
    Devuelve dicts JSON-serializables con ``timestamp`` (ISO UTC),
    ``event_type`` y ``tags`` (dict de propiedades del envelope).
    """
    if end <= start:
        logger.warning("Invalid window: end (%s) <= start (%s) — empty extract.", end, start)
        return []

    with pipeline_db.session_scope() as session:
        if session is None:
            return _read_local_events(start, end)

        stmt = text(
            """
            SELECT timestamp, event_type, tags
            FROM telemetry_events
            WHERE timestamp >= :start_ts AND timestamp < :end_ts
              AND event_type IN :event_types
            """
        )
        rows = session.execute(
            stmt,
            {
                "start_ts": start,
                "end_ts": end,
                "event_types": list(EVENT_TYPES_KPI),
            },
        ).all()

    extracted: list[dict[str, Any]] = []
    for row in rows:
        extracted.append(
            {
                "timestamp": _coerce_datetime(row.timestamp).isoformat(),
                "event_type": row.event_type,
                "tags": _coerce_tags(row.tags),
            }
        )
    logger.info("extract: %d event(s) in window [%s, %s)", len(extracted), start, end)
    return extracted
