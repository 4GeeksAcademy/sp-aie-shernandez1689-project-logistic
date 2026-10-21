"""Snapshot de evaluación opcional — exporta KPIs como reporte JSON de auditoría.

Este módulo NO es crítico para el pipeline E → T → L. Su objetivo es generar
un snapshot de los KPIs calculados para trazabilidad y auditoría externa,
que puede fallar sin interrumpir la carga principal.

Se invoca desde el flow con ``return_state=True`` para que un eventual error
en este paso no propague al flujo principal.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date, datetime, timezone
from typing import Any

from prefect import task

logger = logging.getLogger(__name__)


@task(
    name="export_eval_snapshot",
    description=(
        "Exporta snapshot JSON de KPIs semanales para evaluación/auditoría. "
        "Paso NO crítico: un fallo aquí no interrumpe E → T → L."
    ),
)
def export_eval_snapshot(
    rows: list[dict[str, Any]],
    run_id: str,
    iso_week: str,
    week_start: str | None = None,
) -> dict[str, Any]:
    """Escribe un reporte de auditoría JSON en ``data/eval/…/snapshots/``.

    Parameters
    ----------
    rows : list[dict]
        KPIs calculados por transform.
    run_id : str
        ID del run de pipeline (para trazabilidad).
    iso_week : str
        Etiqueta ISO de la semana (ej. "2026-W28").
    week_start : str | None
        Fecha ISO del lunes de la semana (opcional).

    Returns
    -------
    dict
        Resumen del snapshot: ruta del archivo, número de filas, run_id.
    """
    from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db

    out_dir = pipeline_db.eval_kpis_dir() / "snapshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"snapshot_{iso_week}_{run_id[:8]}.json"

    snapshot: dict[str, Any] = {
        "meta": {
            "run_id": run_id,
            "iso_week": iso_week,
            "week_start": week_start,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "pipeline": "weekly_warehouse_client_performance",
        },
        "kpis": [],
    }

    for row in rows:
        entry = dict(row)
        ws = entry.get("week_start")
        if isinstance(ws, Date):
            entry["week_start"] = ws.isoformat()
        snapshot["kpis"].append(entry)

    with out_file.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, default=str, ensure_ascii=False, indent=2)

    logger.info(
        "eval snapshot exported: %d KPI rows → %s",
        len(rows),
        out_file,
    )

    return {
        "path": str(out_file),
        "rows": len(rows),
        "run_id": run_id,
    }
