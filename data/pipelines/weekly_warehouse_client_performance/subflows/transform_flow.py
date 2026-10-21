"""Subflow de transformación (T) — envuelve ``transform_to_weekly_kpis`` como ``@flow``.

Recibe los eventos crudos como ``TransformInput`` y devuelve los KPIs
agregados como ``TransformOutput``. Reutiliza la tarea Prefect existente
``transform_to_weekly_kpis``, que es una función pura sin efectos secundarios.
"""

from __future__ import annotations

import logging
from typing import Any

from prefect import flow

from data.pipelines.weekly_warehouse_client_performance.subflows.models import (
    TransformInput,
    TransformOutput,
)
from data.pipelines.weekly_warehouse_client_performance.transform import (
    transform_to_weekly_kpis,
)

logger = logging.getLogger(__name__)


@flow(
    name="transform-subflow",
    description=(
        "Subflow de transformación: agrega eventos crudos por "
        "(warehouse, client_id, week_start) a los 5 KPIs semanales. "
        "Función pura (sin IO)."
    ),
    log_prints=True,
)
def transform_subflow(inputs: TransformInput) -> TransformOutput:
    """Ejecuta la transformación de eventos crudos a KPIs semanales.

    Parameters
    ----------
    inputs : TransformInput
        Lista de eventos crudos extraídos por el subflow de extracción.

    Returns
    -------
    TransformOutput
        KPIs agregados con conteo de filas generadas.
    """
    logger.info(
        "Subflow transform: %d raw event(s) to aggregate.",
        len(inputs.raw_events),
    )

    kpi_rows: list[dict[str, Any]] = transform_to_weekly_kpis(inputs.raw_events)

    result = TransformOutput(kpi_rows=kpi_rows, row_count=len(kpi_rows))
    logger.info(
        "Subflow transform: %d KPI row(s) generated from %d event(s).",
        result.row_count,
        len(inputs.raw_events),
    )
    return result