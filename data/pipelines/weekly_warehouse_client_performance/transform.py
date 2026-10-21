"""Transformación (T) — agregar eventos crudos a KPIs semanales.

PIPELINE_DESIGN.md Fase 4 §4.3 (Task 2):

- Recibe la lista de dicts devuelta por ``extract_telemetry_events``.
- Es una **función pura** (sin efectos secundarios, sin IO).
- La receta es Pandas:
  1. ``pd.to_datetime(utc=True)`` sobre ``timestamp``.
  2. Extraer ``warehouse`` y ``client_id`` de ``tags``; descartar filas con
     ``warehouse`` nulo o no permitido (``WAREHOUSES_ALLOWED``) o ``client_id``
     nulo.
  3. Derivar ``week_start`` = lunes de la semana ISO (UTC) desde el timestamp.
  4. ``groupby(["warehouse", "client_id", "week_start"])`` y agregar:
     - ``inbound_order_created`` → ``SUM(quantity)`` como ``inbound_units_count``
     - ``outbound_order_created`` → ``COUNT`` como ``outbound_orders_count``
     - ``stock_threshold_triggered`` → ``COUNT`` como ``stockout_events_count``
     - ``inventory_discrepancy_detected`` → ``COUNT`` como ``discrepancy_events_count``
  5. ``discrepancy_rate = discrepancy_events_count / outbound_orders_count``
     (0 si outbound_orders_count == 0).
- Devuelve lista de dicts JSON-serializables, no DataFrames.

Los nombres de campo coinciden exactamente con la DDL de la tabla destino
(``Brifing-pipeline.md §5``) y con el contrato del endpoint (Briefing §6).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

import pandas as pd
from prefect import task

from data.pipelines.weekly_warehouse_client_performance.config import (
    EVENT_TO_FIELD,
    INBOUND_QUANTITY_TAG,
    WAREHOUSES_ALLOWED,
)

logger = logging.getLogger(__name__)


def _transform_cache_key(
    task_ctx: Any, inputs: dict[str, Any]
) -> str | None:
    """Clave de caché = hash SHA-256 del JSON canónico de los eventos crudos.

    La transformación es una función pura: mismo raw → mismo resultado.
    La clave incluye el nombre de la task para evitar colisiones entre
    distintas tasks que reciban listas similares.

    Vigencia: 24 horas (cache_expiration). Este valor cubre el caso típico
    de una ejecución semanal: si el pipeline falla a mitad de semana y se
    re-ejecuta el mismo día, el resultado transformado se sirve de caché
    sin repetir el cómputo de Pandas.

    Firma requerida por Prefect 3.x: ``(task_run_context, task_parameters)``.
    """
    raw = inputs.get("raw")
    if raw is None:
        return None
    raw_json = json.dumps(raw, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


@task(
    name="transform_to_weekly_kpis",
    description=(
        "Agrega eventos crudos por (warehouse, client_id, week_start) "
        "a los 5 KPIs semanales. Función pura (sin IO)."
    ),
    # cache_key_fn: utiliza el hash del contenido completo de los eventos
    # de entrada. Si el mismo conjunto de eventos se transforma nuevamente
    # dentro de las 24 horas, Prefect devuelve el resultado cacheado sin
    # ejecutar Pandas. Esto ahorra CPU en re-ejecuciones del mismo día
    # (ej. backfill parcial o reintentos del flow padre).
    cache_key_fn=_transform_cache_key,
    cache_expiration=timedelta(hours=24),
)
def transform_to_weekly_kpis(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrega eventos crudos a KPIs semanales por (warehouse, client_id, week_start).

    Parameters
    ----------
    raw : list[dict]
        Lista de eventos con ``timestamp`` (ISO UTC str), ``event_type``,
        ``tags`` (dict con ``warehouse``, ``client_id`` y opcionalmente
        ``quantity`` para inbound).

    Returns
    -------
    list[dict]
        Filas agregadas con los 5 KPIs del Briefing §5.
    """
    if not raw:
        logger.info("transform: empty input — returning empty list.")
        return []

    df = pd.DataFrame(raw)
    if df.empty:
        return []

    # 1. Normalizar timestamp a UTC datetime
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True)

    # 2. Extraer warehouse y client_id de tags
    df["warehouse"] = df["tags"].apply(
        lambda t: t.get("warehouse") if isinstance(t, dict) else None
    )
    df["client_id"] = df["tags"].apply(
        lambda t: t.get("client_id") if isinstance(t, dict) else None
    )
    df["quantity"] = df["tags"].apply(
        lambda t: (
            int(t.get(INBOUND_QUANTITY_TAG, 0))
            if isinstance(t, dict)
            else 0
        )
    )

    # 3. Descartar filas con warehouse/cliente nulo o warehouse no permitido
    before = len(df)
    df = df.dropna(subset=["warehouse", "client_id"])
    df = df[df["warehouse"].isin(WAREHOUSES_ALLOWED)]  # type: ignore[arg-type]
    dropped = before - len(df)
    if dropped:
        logger.info("transform: dropped %d rows (null or non-allowed warehouse/client_id).", dropped)

    if df.empty:
        return []

    # 4. Derivar week_start = lunes de la semana ISO (UTC)
    df["week_start"] = df["ts"].dt.isocalendar().apply(
        lambda r: _iso_monday(r["year"], r["week"]), axis=1
    )
    # Normalizar a date (sin hora)
    df["week_start"] = pd.to_datetime(df["week_start"], utc=True).dt.date

    # 5. Pivotar event_type a columnas de conteo
    df["is_inbound"] = df["event_type"] == "inbound_order_created"
    df["is_outbound"] = df["event_type"] == "outbound_order_created"
    df["is_stockout"] = df["event_type"] == "stock_threshold_triggered"
    df["is_discrepancy"] = df["event_type"] == "inventory_discrepancy_detected"

    # Usar quantity solo para inbound, 0 para otros
    df["inbound_qty"] = df.apply(
        lambda r: r["quantity"] if r["is_inbound"] else 0, axis=1
    )

    grouped = (
        df.groupby(["warehouse", "client_id", "week_start"])
        .agg(
            inbound_units_count=("inbound_qty", "sum"),
            outbound_orders_count=("is_outbound", "sum"),
            stockout_events_count=("is_stockout", "sum"),
            discrepancy_events_count=("is_discrepancy", "sum"),
        )
        .reset_index()
    )

    # 6. Calcular discrepancy_rate con guarda de división por cero
    grouped["discrepancy_rate"] = grouped.apply(
        lambda r: (
            r["discrepancy_events_count"] / r["outbound_orders_count"]
            if r["outbound_orders_count"] > 0
            else 0.0
        ),
        axis=1,
    )

    # Convertir a enteros nativos
    grouped["inbound_units_count"] = grouped["inbound_units_count"].astype(int)
    grouped["outbound_orders_count"] = grouped["outbound_orders_count"].astype(int)
    grouped["stockout_events_count"] = grouped["stockout_events_count"].astype(int)
    grouped["discrepancy_events_count"] = grouped["discrepancy_events_count"].astype(int)
    grouped["discrepancy_rate"] = grouped["discrepancy_rate"].astype(float)

    records = grouped.to_dict(orient="records")

    logger.info(
        "transform: %d raw events → %d KPI rows.",
        len(raw),
        len(records),
    )
    return records


def _iso_monday(year: int, week: int) -> str:
    """Devuelve la fecha ISO (YYYY-MM-DD) del lunes de la semana ISO dada."""
    from datetime import date
    return date.fromisocalendar(int(year), int(week), 1).isoformat()
