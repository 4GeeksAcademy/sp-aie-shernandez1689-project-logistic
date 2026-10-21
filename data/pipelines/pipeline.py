#!/usr/bin/env python3
"""Punto de entrada principal del pipeline de desempeno semanal.

Uso:
    python data/pipelines/pipeline.py                          # ultima semana ISO
    python data/pipelines/pipeline.py 2026-07-13               # semana especifica
    python data/pipelines/pipeline.py backfill 2026-07-06 2026-07-20

Importa y ejecuta el flow ``weekly_warehouse_client_performance`` desde el
paquete de pipeline, o el ``backfill_flow`` si se pasa ``backfill`` como primer
argumento.

Mantiene la separacion de capas (PIPELINE_DESIGN.md Fase 5 5.1): este script
es solo un entry-point de linea de comandos; la logica real esta en
``data/pipelines/weekly_warehouse_client_performance/``.

Ejecucion directa como script:
    python data/pipelines/pipeline.py
    python data/pipelines/pipeline.py 2026-07-13
    python data/pipelines/pipeline.py backfill 2026-07-06 2026-07-20

Frecuencia: semanal (lunes a las 06:00 UTC via cron o Prefect scheduler).
Ver ``PIPELINE_DESIGN.md``  "Ciclo de reporting" para detalles.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

# Ajuste de sys.path para permitir ejecucion directa como script:
#   python data/pipelines/pipeline.py
# Sin esto, Python no encuentra el paquete ``data`` porque el directorio del
# script (data/pipelines/) no es la raiz del proyecto.
# Con -m (python -m data.pipelines.pipeline) el interprete resuelve desde
# el directorio de trabajo, asi que el ajuste es inocuo en ese modo.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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