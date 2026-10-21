"""Subflow de carga (L) — envuelve ``load_weekly_performance`` como ``@flow``.

Recibe las filas KPI como ``LoadInput`` y persiste los datos mediante
upsert en ``reporting.weekly_warehouse_client_performance`` (o en JSONL local).
Devuelve el número de filas persistidas como ``LoadOutput``.
"""

from __future__ import annotations

import logging

from prefect import flow

from data.pipelines.weekly_warehouse_client_performance.load import (
    load_weekly_performance,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.models import (
    LoadInput,
    LoadOutput,
)

logger = logging.getLogger(__name__)


@flow(
    name="load-subflow",
    description=(
        "Subflow de carga: realiza upsert de los KPIs semanales en "
        "reporting.weekly_warehouse_client_performance (o JSONL local). "
        "Idempotente: si la fila ya existe para (warehouse, client_id, week_start), "
        "se actualizan los KPIs sin duplicar."
    ),
    log_prints=True,
)
def load_subflow(inputs: LoadInput) -> LoadOutput:
    """Ejecuta la persistencia (upsert) de los KPIs semanales.

    Parameters
    ----------
    inputs : LoadInput
        Filas KPI generadas por el subflow de transformación.

    Returns
    -------
    LoadOutput
        Número de filas persistidas correctamente.
    """
    logger.info(
        "Subflow load: %d KPI row(s) to upsert.",
        len(inputs.kpi_rows),
    )

    upserted = load_weekly_performance(inputs.kpi_rows)

    result = LoadOutput(upserted_count=upserted)
    logger.info("Subflow load: %d KPI row(s) upserted.", result.upserted_count)
    return result