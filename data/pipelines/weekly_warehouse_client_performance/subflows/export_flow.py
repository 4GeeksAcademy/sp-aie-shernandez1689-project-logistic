"""Subflow opcional de exportación de snapshot — envuelve ``export_eval_snapshot`` como ``@flow``.

Este subflow NO es crítico para el pipeline E → T → L. Su objetivo es generar
un snapshot de los KPIs calculados para trazabilidad y auditoría externa.
Se invoca desde el flow principal con ``return_state=True`` para que un
eventual error en este paso no propague al flujo principal.
"""

from __future__ import annotations

import logging

from prefect import flow

from data.pipelines.weekly_warehouse_client_performance.eval_snapshot import (
    export_eval_snapshot,
)
from data.pipelines.weekly_warehouse_client_performance.subflows.models import (
    ExportInput,
    ExportOutput,
)

logger = logging.getLogger(__name__)


@flow(
    name="export-eval-snapshot-subflow",
    description=(
        "Subflow opcional: exporta snapshot JSON de KPIs semanales "
        "para evaluación/auditoría. Paso NO crítico: un fallo aquí "
        "no interrumpe E → T → L."
    ),
    log_prints=True,
)
def export_eval_snapshot_subflow(inputs: ExportInput) -> ExportOutput:
    """Exporta un snapshot de auditoría de los KPIs calculados.

    Parameters
    ----------
    inputs : ExportInput
        KPIs calculados, ID de corrida y metadata de la semana.

    Returns
    -------
    ExportOutput
        Ruta del archivo generado, número de filas y run_id asociado.
    """
    logger.info(
        "Subflow export: snapshot para run=%s, week=%s.",
        inputs.run_id,
        inputs.iso_week,
    )

    result_dict = export_eval_snapshot(
        rows=inputs.kpi_rows,
        run_id=inputs.run_id,
        iso_week=inputs.iso_week,
        week_start=inputs.week_start,
    )

    result = ExportOutput(
        path=result_dict["path"],
        rows_count=result_dict["rows"],
        run_id=result_dict["run_id"],
    )
    logger.info(
        "Subflow export: snapshot written to %s (%d rows).",
        result.path,
        result.rows_count,
    )
    return result