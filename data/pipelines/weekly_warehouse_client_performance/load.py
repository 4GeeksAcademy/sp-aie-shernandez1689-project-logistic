"""Carga (L) — upsert KPIs semanales en ``reporting.weekly_warehouse_client_performance``.
PIPELINE_DESIGN.md Fase 4 §4.3 (Task 3):
- Recibe la lista de dicts devuelta por ``transform_to_weekly_kpis``.
- **Upsert** vía ``INSERT ... ON CONFLICT (warehouse, client_id, week_start) DO UPDATE``.
- Sin borrado previo. Sin escritura en ``telemetry_events``.
- Cada fila de KPIs es idempotente: si la semana ya existe para ese
  (warehouse, client_id), se actualizan los campos, no se duplica.
Modo local (sin ``DATABASE_URL``): escribe KPIs a
``data/eval/weekly_warehouse_client_performance/kpis.jsonl`` (una línea JSON por
fila de KPI, con el mismo formato que la tabla).
"""
from __future__ import annotations
import json
import logging
from datetime import date as Date
from pathlib import Path
from typing import Any
from prefect import task
from sqlmodel import Session, text
from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db
logger = logging.getLogger(__name__)
# DDL: columna serializada como JSONB → espera string JSON
_KPI_TABLE = "reporting.weekly_warehouse_client_performance"
_UPSERT_SQL = f"""
INSERT INTO {_KPI_TABLE} (warehouse, client_id, week_start,
                          inbound_units_count, outbound_orders_count,
                          stockout_events_count, discrepancy_events_count,
                          discrepancy_rate)
VALUES (:warehouse, :client_id, :week_start,
        :inbound_units_count, :outbound_orders_count,
        :stockout_events_count, :discrepancy_events_count,
        :discrepancy_rate)
ON CONFLICT (warehouse, client_id, week_start) DO UPDATE SET
    inbound_units_count = EXCLUDED.inbound_units_count,
    outbound_orders_count = EXCLUDED.outbound_orders_count,
    stockout_events_count = EXCLUDED.stockout_events_count,
    discrepancy_events_count = EXCLUDED.discrepancy_events_count,
    discrepancy_rate = EXCLUDED.discrepancy_rate;
"""
def _write_local_kpis(rows: list[dict[str, Any]]) -> int:
    """Modo local: escribe KPIs a JSONL en ``data/eval/``. Devuelve filas escritas."""
    from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db
    out_file = pipeline_db.eval_kpis_file()
    serializable_rows = []
    for row in rows:
        sr = dict(row)
        ws = sr.get("week_start")
        if isinstance(ws, Date) and not isinstance(ws, str):
            sr["week_start"] = ws.isoformat()
        serializable_rows.append(sr)
    with out_file.open("w", encoding="utf-8") as f:
        for item in serializable_rows:
            f.write(json.dumps(item, default=str, ensure_ascii=False) + "\n")
    count = len(serializable_rows)
    logger.info("load (local): %d KPI rows written to %s", count, out_file)
    return count
@task(
    name="load_weekly_performance",
    description=(
        "Upsert KPIs en reporting.weekly_warehouse_client_performance "
        "o escritura local JSONL. Idempotente."
    ),
    retries=2,
    retry_delay_seconds=30,
)
def load_weekly_performance(rows: list[dict[str, Any]]) -> int:
    """Persiste KPIs semanales (upsert). Devuelve número de filas procesadas.
    Sin escritura en ``telemetry_events``. Sin borrado previo de datos.
    Idempotente: si la semana ya existe para (warehouse, client_id),
    se actualizan los KPIs.
    """
    if not rows:
        logger.info("load: no rows to upsert — skipping.")
        return 0
    with pipeline_db.session_scope() as session:
        if session is None:
            return _write_local_kpis(rows)
        upserted = 0
        for idx, row in enumerate(rows):
            params = {
                "warehouse": row.get("warehouse"),
                "client_id": row.get("client_id"),
                "week_start": row.get("week_start"),
                "inbound_units_count": int(row.get("inbound_units_count", 0)),
                "outbound_orders_count": int(row.get("outbound_orders_count", 0)),
                "stockout_events_count": int(row.get("stockout_events_count", 0)),
                "discrepancy_events_count": int(row.get("discrepancy_events_count", 0)),
                "discrepancy_rate": float(row.get("discrepancy_rate", 0.0)),
            }
            session.execute(text(_UPSERT_SQL), params)
            upserted += 1
        session.commit()
    logger.info("load: %d KPI rows upserted into %s.", upserted, _KPI_TABLE)
    return upserted
