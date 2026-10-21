#!/usr/bin/env python3
"""Punto de entrada principal del pipeline de desempeño semanal.

Uso:
    .venv/bin/python -m data.pipelines.pipeline              # última semana ISO
    .venv/bin/python -m data.pipelines.pipeline 2026-07-13   # semana específica
    .venv/bin/python -m data.pipelines.pipeline backfill 2026-07-06 2026-07-20

Importa y ejecuta el flow ``weekly_warehouse_client_performance`` desde el
paquete de pipeline, o el ``backfill_flow`` si se pasa ``backfill`` como primer
argumento.

Mantiene la separación de capas (PIPELINE_DESIGN.md Fase 5 §5.1): este script
es solo un entry-point de línea de comandos; la lógica real está en
``data/pipelines/weekly_warehouse_client_performance/``.
"""

from __future__ import annotations

import sys
from datetime import date

from data.pipelines.weekly_warehouse_client_performance import db as pipeline_db
from data.pipelines.weekly_warehouse_client_performance.pipeline import (
    backfill_flow,
    weekly_flow,
)


def main() -> None:
    pipeline_db.ensure_local_dirs()

    args = sys.argv[1:]

    if not args:
        # Última semana ISO completa
        result = weekly_flow()
        print(result)
        return

    if args[0] == "backfill":
        first_week = date.fromisoformat(args[1]) if len(args) > 1 else None
        last_week = date.fromisoformat(args[2]) if len(args) > 2 else first_week
        if first_week is None:
            print("Usage: python -m data.pipelines.pipeline backfill <first_week> [<last_week>]")
            sys.exit(1)
        results = backfill_flow(first_week, last_week)
        for r in results:
            print(r)
        return

    # Semana específica
    week_start = date.fromisoformat(args[0])
    result = weekly_flow(week_start=week_start)
    print(result)


if __name__ == "__main__":
    main()