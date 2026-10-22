"""Subflow opcional de backup CSV — exporta eventos crudos como CSV.

Este subflow NO es crítico para el pipeline E → T → L. Su objetivo es
generar un backup CSV de los eventos extraídos desde ``telemetry_events``
en ``data/raw/`` para auditoría/trazabilidad externa (solo backup — el
pipeline lee desde BD).

Se invoca desde el flow principal con ``return_state=True`` para que un
eventual error en este paso no propague al flujo principal.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path

from prefect import flow

from data.pipelines.weekly_warehouse_client_performance.subflows.models import (
    CsvBackupInput,
    CsvBackupOutput,
)

logger = logging.getLogger(__name__)

# ── Constante: directorio de backup CSV ─────────────────────────────────────

RAW_CSV_DIR = (
    Path(__file__).resolve().parents[4]  # raíz del monorepo
    / "data"
    / "raw"
    / "weekly_warehouse_client_performance"
)


@flow(
    name="csv-backup-subflow",
    description=(
        "Subflow opcional: exporta eventos crudos como CSV en data/raw/ "
        "con nombre semanal (weekly_events_YYYY-WNN.csv). "
        "Paso NO crítico: un fallo aquí no interrumpe E → T → L."
    ),
    log_prints=True,
)
def csv_backup_subflow(inputs: CsvBackupInput) -> CsvBackupOutput:
    """Exporta los eventos crudos como CSV a ``data/raw/``.

    Parameters
    ----------
    inputs : CsvBackupInput
        Eventos crudos, etiqueta ISO de la semana y fecha de inicio.

    Returns
    -------
    CsvBackupOutput
        Ruta del archivo CSV generado, número de filas y etiqueta ISO.

    Raises
    ------
    OSError
        Si no se puede escribir en el directorio ``data/raw/``.
    """
    iso_week = inputs.iso_week
    week_start = inputs.week_start
    events = inputs.raw_events

    # Nombre del archivo: weekly_events_YYYY-WNN.csv
    csv_filename = f"weekly_events_{iso_week}.csv"
    csv_path = RAW_CSV_DIR / csv_filename

    # Asegurar que el directorio existe
    RAW_CSV_DIR.mkdir(parents=True, exist_ok=True)

    # Escribir CSV
    fieldnames = ["timestamp", "event_type", "tags"]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            # tags es un dict → serializar como string JSON para CSV
            row = {
                "timestamp": event.get("timestamp", ""),
                "event_type": event.get("event_type", ""),
                "tags": str(event.get("tags", {})),
            }
            writer.writerow(row)

    row_count = len(events)

    logger.info(
        "CSV backup exported: %d events → %s",
        row_count,
        csv_path,
    )

    return CsvBackupOutput(
        path=str(csv_path),
        rows_count=row_count,
        iso_week=iso_week,
    )