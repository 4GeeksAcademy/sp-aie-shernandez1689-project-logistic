#!/usr/bin/env python3
"""Script nocturno de telemetría — DEV-53.

Exporta los eventos de telemetría del día anterior a CSV (si no existe),
lanza el pipeline de datos como subprocess y deja constancia en
``job_runs`` del resultado.

Uso:
    python scripts/nightly_export.py                     # ayer en UTC
    TARGET_DATE=2026-10-20 python scripts/nightly_export.py   # fecha específica
    python scripts/nightly_export.py --dry-run            # validar sin BD
    TARGET_DATE=2026-10-20 python scripts/nightly_export.py --dry-run  # ídem

La máquina de estados (pending → processing → completed | failed) y el
distributed lock se gestionan a través de la tabla ``job_runs``
(ver ``services/job_runs/``).

El CSV se escribe en ``data/raw/telemetry_YYYY-MM-DD.csv`` y es un snapshot
de backup/auditoría — **no** lo consume el pipeline (lee desde BD).
"""

from __future__ import annotations

import csv
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from sqlmodel import Session, text

# ── Ajuste de sys.path para permitir ejecución directa como script ─────────
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from services.job_runs.database import get_engine
from services.job_runs.state import (
    complete_run,
    create_run,
    fail_run,
    start_processing,
)

# ── Logger ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("nightly_export")

# ── Constantes ─────────────────────────────────────────────────────────────
JOB_NAME = "nightly_export"
RAW_DIR = _REPO_ROOT / "data" / "raw"

# Subprocess del pipeline (Hito 6) — referencia de procesos-2-plano §77.
# Ajustar al entry point real si difiere.
PIPELINE_CMD = [
    sys.executable,
    "-m",
    "data.pipelines.telemetry_kpi_daily.run",
    "--no-prefect",
]

# ── Funciones ───────────────────────────────────────────────────────────────


def resolve_target_date() -> date:
    """Resuelve la fecha objetivo.

    Prioridad:
    1. Variable de entorno ``TARGET_DATE`` (formato YYYY-MM-DD).
    2. Ayer en UTC (``datetime.now(timezone.utc).date() - timedelta(days=1)``).

    Returns:
        date: Fecha objetivo.
    """
    env_date = os.environ.get("TARGET_DATE")
    if env_date:
        # Validar formato
        try:
            dt = datetime.strptime(env_date, "%Y-%m-%d").date()
        except ValueError as exc:
            logger.error("Invalid TARGET_DATE format: %s — expected YYYY-MM-DD", env_date)
            raise SystemExit(1) from exc
        logger.info("Target date from TARGET_DATE env: %s", env_date)
        return dt

    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    logger.info("Target date (yesterday UTC): %s", yesterday.isoformat())
    return yesterday


def csv_path(target_date: date) -> Path:
    """Devuelve la ruta del archivo CSV para una fecha."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return RAW_DIR / f"telemetry_{target_date.isoformat()}.csv"


def csv_exists(target_date: date) -> bool:
    """Comprueba si el CSV ya fue exportado para la fecha dada."""
    return csv_path(target_date).exists()


def export_telemetry_to_csv(session: Session, target_date: date) -> int:
    """Exporta los eventos de telemetría de ``target_date`` a CSV.

    La consulta SQL selecciona todas las columnas de ``telemetry_events``
    para el rango [target_date 00:00, target_date+1 00:00) en UTC.

    El CSV se escribe en ``data/raw/telemetry_{target_date}.csv`` con
    cabecera. Es un snapshot de backup/auditoría.

    Args:
        session: Sesión SQLModel activa.
        target_date: Fecha objetivo.

    Returns:
        int: Número de filas exportadas.

    Raises:
        RuntimeError: Si el CSV ya existe (se debe comprobar antes).
    """
    path = csv_path(target_date)
    if path.exists():
        raise RuntimeError(f"CSV already exists: {path}")

    start_ts = f"{target_date.isoformat()}T00:00:00Z"
    end_ts = f"{target_date.isoformat()}T23:59:59.999999Z"

    # Consulta SQL — todas las columnas de telemetry_events para el día
    stmt = text("""
        SELECT
            id,
            timestamp,
            service,
            event_type,
            level,
            value,
            message,
            tags
        FROM telemetry_events
        WHERE timestamp >= :start_ts
          AND timestamp <  :end_ts
        ORDER BY timestamp ASC
    """)

    rows = session.execute(stmt, {"start_ts": start_ts, "end_ts": end_ts}).all()

    if not rows:
        logger.info("No telemetry events found for %s — writing empty CSV.", target_date)

    # Escribir CSV con cabecera
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "timestamp", "service", "event_type", "level", "value", "message", "tags"])
        for row in rows:
            writer.writerow([
                str(row.id) if row.id else "",
                row.timestamp.isoformat() if row.timestamp else "",
                row.service or "",
                row.event_type or "",
                row.level or "",
                str(row.value) if row.value is not None else "",
                row.message or "",
                str(row.tags) if row.tags else "{}",
            ])

    logger.info("Exported %d event(s) to %s", len(rows), path)
    return len(rows)


def run_pipeline() -> None:
    """Lanza el pipeline de datos como subprocess.

    El pipeline lee desde la base de datos (no desde el CSV).
    """
    logger.info("Launching pipeline: %s", " ".join(PIPELINE_CMD))
    result = subprocess.run(
        PIPELINE_CMD,
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,  # 10 min max
    )

    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr)"
        stdout = result.stdout.strip() or "(no stdout)"
        logger.error(
            "Pipeline failed (exit code %d):\n  stdout: %s\n  stderr: %s",
            result.returncode,
            stdout[:500],
            stderr[:500],
        )
        raise RuntimeError(
            f"Pipeline exited with code {result.returncode}: {stderr[:500]}"
        )

    logger.info("Pipeline completed successfully (exit code 0).")
    if result.stdout.strip():
        logger.info("Pipeline output:\n%s", result.stdout.strip())


# ── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point del script nocturno."""
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logger.info("🧪 DRY RUN — no database required, only validating logic.")

    target_date = resolve_target_date()
    csv_path(target_date)  # crea directorio data/raw/ si no existe

    # ── Validaciones sin BD ──────────────────────────────────
    csv_file = csv_path(target_date)
    if csv_file.exists():
        logger.info("[dry] CSV already exists: %s — export would be skipped", csv_file)
    else:
        logger.info("[dry] CSV would be created: %s", csv_file)

    logger.info("[dry] Pipeline command: %s", " ".join(PIPELINE_CMD))
    logger.info("[dry] Job name: %s | target_date: %s", JOB_NAME, target_date)
    logger.info("[dry] Expected flow: pending → processing → CSV export → pipeline → completed")

    if dry_run:
        logger.info("✅ Dry run completed. No database connection attempted.")
        return

    # ── Conexión a BD ────────────────────────────────────────
    logger.info("Connecting to database...")
    engine = get_engine()
    with Session(engine) as session:
        # ── Paso 1: Crear registro en pending ─────────────────────
        run = create_run(session, JOB_NAME, target_date)
        logger.info("Job run created: id=%s status=%s", run.id, run.status)

        # ── Paso 2: Adquirir lock ─────────────────────────────────
        if not start_processing(session, run.id):
            logger.info(
                "Lock not acquired for %s target_date=%s — "
                "another instance is already processing. Aborting silently.",
                JOB_NAME,
                target_date,
            )
            sys.exit(0)

        # ── Paso 3: Ejecutar el trabajo ───────────────────────────
        try:
            # 3a. Exportar CSV (solo si no existe)
            path = csv_path(target_date)
            if path.exists():
                logger.info("CSV already exists: %s — skipping export.", path)
            else:
                export_telemetry_to_csv(session, target_date)

            # 3b. Lanzar pipeline como subprocess
            run_pipeline()

            # ── Paso 4: Marcar como completado ────────────────────
            complete_run(session, run.id)
            logger.info("Nightly export completed successfully for %s.", target_date)

        except Exception as exc:
            logger.exception("Nightly export failed for %s.", target_date)
            fail_run(session, run.id, str(exc))
            sys.exit(1)


if __name__ == "__main__":
    main()