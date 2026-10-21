#!/usr/bin/env python3
"""Validación del pipeline de desempeño semanal.

Genera un reporte de validación en ``data/eval/validation_report.md``
que verifica:

1. Ejecución correcta del pipeline (E → T → L).
2. Integridad de los KPIs generados.
3. Idempotencia (re-ejecución produce los mismos resultados).
4. Filtrado correcto de almacenes/eventos.
5. Cálculo correcto de discrepancy_rate.

Uso:
    PYTHONPATH="." .venv/bin/python data/process/validate_pipeline.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Añadir repo root al path (antes de cualquier import del proyecto)
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db
from data.pipelines.weekly_warehouse_client_performance.config import (
    KPI_FIELDS,
    WAREHOUSES_ALLOWED,
)
from data.pipelines.weekly_warehouse_client_performance.pipeline import (
    weekly_flow,
)

OUTPUT = REPO_ROOT / "data" / "eval" / "validation_report.md"
KPI_FILE = pipeline_db.eval_kpis_file()
RUNS_FILE = pipeline_db.eval_runs_file()


def run() -> int:
    sections: list[str] = []
    errors: list[str] = []

    sections.append("# Reporte de Validación — Pipeline de Desempeño Semanal\n")
    sections.append(f"Generado: {datetime.now(timezone.utc).isoformat()}\n")

    # ── 0. Ejecutar el pipeline primero ──────────────────────────────────────
    sections.append("## 0. Ejecución del pipeline\n")
    print("Running pipeline for validation...")
    result = weekly_flow()
    events = result.get("processed_events", 0)
    upserted = result.get("rows_upserted", 0)
    status = result.get("status", "unknown")
    sections.append(f"- Estado: **{status}**")
    sections.append(f"- Eventos procesados: {events}")
    sections.append(f"- Filas upserted: {upserted}")
    sections.append(f"- ISO Week: {result.get('iso_week', '?')}")
    if status != "success":
        errors.append(f"Pipeline execution failed: {result.get('error', 'unknown')}")

    # ── 1. Verificar archivos de salida ──────────────────────────────────────
    sections.append("\n## 1. Archivos de salida\n")

    kpi_exists = KPI_FILE.exists()
    runs_exists = RUNS_FILE.exists()

    sections.append(
        f"- `data/eval/weekly_warehouse_client_performance/weekly_kpis.jsonl`: "
        f"{'✅' if kpi_exists else '❌'} {KPI_FILE.stat().st_size if kpi_exists else 0} bytes"
    )
    sections.append(
        f"- `data/eval/weekly_warehouse_client_performance/pipeline_runs.jsonl`: "
        f"{'✅' if runs_exists else '❌'} {RUNS_FILE.stat().st_size if runs_exists else 0} bytes"
    )

    if not kpi_exists:
        errors.append("No KPI output file found — pipeline may not have produced results.")
        _write_report(sections, errors, OUTPUT)
        return 1

    # ── 2. Cargar y validar KPIs ─────────────────────────────────────────────
    sections.append("\n## 2. KPIs generados\n")

    kpis: list[dict] = []
    with KPI_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                kpis.append(json.loads(line))

    sections.append(f"- Total de filas KPI: **{len(kpis)}**\n")

    if len(kpis) == 0:
        errors.append("No KPI rows generated.")
    else:
        # Verificar campos requeridos
        required_fields = {"warehouse", "client_id", "week_start"} | set(KPI_FIELDS)
        for i, kpi in enumerate(kpis):
            missing = required_fields - kpi.keys()
            if missing:
                errors.append(f"Row {i}: missing fields {missing}")

        # Verificar almacenes permitidos
        for i, kpi in enumerate(kpis):
            if kpi.get("warehouse") not in WAREHOUSES_ALLOWED:
                errors.append(f"Row {i}: invalid warehouse '{kpi.get('warehouse')}'")

        # Verificar tipos
        for i, kpi in enumerate(kpis):
            if not isinstance(kpi.get("inbound_units_count"), int):
                errors.append(f"Row {i}: inbound_units_count is not int")
            if not isinstance(kpi.get("outbound_orders_count"), int):
                errors.append(f"Row {i}: outbound_orders_count is not int")
            if not isinstance(kpi.get("discrepancy_rate"), (int, float)):
                errors.append(f"Row {i}: discrepancy_rate is not numeric")

        # Verificar discrepancy_rate cálculo
        for i, kpi in enumerate(kpis):
            disc = kpi.get("discrepancy_events_count", 0)
            out = kpi.get("outbound_orders_count", 0)
            expected_rate = disc / out if out > 0 else 0.0
            actual_rate = float(kpi.get("discrepancy_rate", -1))
            if abs(expected_rate - actual_rate) > 0.01:
                errors.append(
                    f"Row {i}: discrepancy_rate mismatch: "
                    f"expected {expected_rate:.4f}, got {actual_rate:.4f}"
                )

        # Resumen por almacén
        by_warehouse: dict[str, int] = {}
        for kpi in kpis:
            w = kpi.get("warehouse", "unknown")
            by_warehouse[w] = by_warehouse.get(w, 0) + 1
        sections.append("\n### Filas por almacén\n")
        for w, count in sorted(by_warehouse.items()):
            sections.append(f"- `{w}`: {count} filas")

        # Semanas cubiertas
        weeks = sorted(set(kpi.get("week_start", "") for kpi in kpis))
        sections.append(f"\n- Semanas cubiertas: {', '.join(weeks)}")

        # Mostrar muestra
        sections.append("\n\n### Muestra (primeras 3 filas)\n")
        sections.append("```json")
        sections.append(json.dumps(kpis[:3], indent=2))
        sections.append("```")

        # Resumen de KPIs agregados
        sections.append("\n\n### Resumen de KPIs\n")
        sections.append("| Almacén | Cliente | Inbound | Outbound | Stockout | Disc. | Disc. Rate |")
        sections.append("|---------|---------|---------|----------|----------|-------|-----------|")
        for kpi in kpis[:10]:  # Máximo 10 filas
            sections.append(
                f"| {kpi['warehouse']} | {kpi['client_id']} "
                f"| {kpi['inbound_units_count']} | {kpi['outbound_orders_count']} "
                f"| {kpi['stockout_events_count']} | {kpi['discrepancy_events_count']} "
                f"| {kpi['discrepancy_rate']:.4f} |"
            )

    # ── 3. Validar log de corridas ───────────────────────────────────────────
    sections.append("\n## 3. Log de corridas\n")

    runs: list[dict] = []
    if runs_exists:
        with RUNS_FILE.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    runs.append(json.loads(line))

    sections.append(f"- Total de registros de corrida: **{len(runs)}**\n")

    for run in runs:
        status_r = run.get("status", "unknown")
        rid = run.get("id", "?")
        sections.append(f"- Corrida: `{rid[:8]}...` → **{status_r}**")
        if status_r == "success":
            pe = run.get("processed_events", "?")
            ru = run.get("rows_upserted", "?")
            sections[-1] += f" ({pe} eventos → {ru} filas)"

    # ── 4. Idempotencia (re-ejecutamos y comparamos) ─────────────────────────
    sections.append("\n## 4. Idempotencia\n")

    print("Re-running pipeline for idempotency check...")
    result2 = weekly_flow()
    events2 = result2.get("processed_events", 0)
    upserted2 = result2.get("rows_upserted", 0)
    status2 = result2.get("status", "unknown")

    sections.append(f"- Re-ejecución: {events2} eventos → {upserted2} filas (status: {status2})")
    if upserted2 == len(kpis):
        sections.append("- ✅ **Idempotente**: mismo número de filas en ambas ejecuciones")
    else:
        sections.append(
            f"- ⚠️ Diferencia: primera ejecución {len(kpis)} filas, "
            f"segunda {upserted2} filas"
        )

    # ── Cierre ─────────────────────────────────────────────────────────────────
    sections.append("\n---\n")
    if errors:
        sections.append(f"## ❌ Errores ({len(errors)})\n")
        for err in errors:
            sections.append(f"- {err}")
        status_emoji = "❌"
    else:
        sections.append("## ✅ Validación exitosa\n")
        sections.append("Todas las verificaciones pasaron correctamente.")
        status_emoji = "✅"

    sections.append(f"\n*Fin del reporte — {status_emoji}*")

    _write_report(sections, errors, OUTPUT)
    return 0 if not errors else 1


def _write_report(sections: list[str], errors: list[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(sections), encoding="utf-8")
    if errors:
        print(f"Validation report: {path} (ERRORS: {len(errors)})")
    else:
        print(f"\nValidation report: {path} (ALL CHECKS PASSED)")


if __name__ == "__main__":
    sys.exit(run())
