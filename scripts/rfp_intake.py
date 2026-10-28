#!/usr/bin/env python3
"""CLI de intake de RFPs (Hito 9, Parte 1).

Ejecuta el mismo pipeline que expone ``POST /rfp/upload`` sobre uno o
varios PDFs de RFP y muestra el estado del ticket de cada uno
(rechazado/descartado o analizado con sus metadatos y departamentos).
Es una herramienta de operaciones/validación: no sustituye a la API.

Uso:
    python scripts/rfp_intake.py rfp-requests/trackflow/trackflow-request-1.pdf
    python scripts/rfp_intake.py rfp-requests/trackflow/            # dir
    python scripts/rfp_intake.py --no-persist rfp-requests/trackflow/trackflow-request-1.pdf

Opciones:
    -p/--persist     Persistir el ticket (por defecto True).
    --no-persist     No persistir (solo validación, no escribe JSONL/BD).
    --json           Salida en JSON (una entrada por archivo).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.pipelines.rfp_intake.config import raw_rfp_dir  # noqa: E402
from data.pipelines.rfp_intake.pipeline import process_rfp_pdf  # noqa: E402


def _collect_inputs(paths: list[str]) -> list[Path]:
    """Expande directorios a PDFs y valida que existan los archivos."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.glob("*.pdf")))
            continue
        if p.is_file():
            if p.suffix.lower() != ".pdf":
                print(f"[warn] ignorando no-PDF: {p}", file=sys.stderr)
                continue
            files.append(p)
            continue
        print(f"[error] no existe: {p}", file=sys.stderr)
    return files


def _render_human(r: dict, pdf: Path) -> None:
    """Imprime el resultado de un ticket en formato legible."""
    ticket = r.get("ticket_id", "?")
    status = r.get("status", "?")
    print(f"\n== {pdf.name} → ticket {ticket} [{status}]")
    if status == "descartado":
        print(f"   motivo: {r.get('reason')}")
        return
    c = r.get("classification", {})
    m = r.get("metadata", {})
    print(f"   tipo: {c.get('doc_type')} | cliente: {c.get('client_name')} | "
          f"país: {c.get('client_country')} | moneda: {r.get('currency')}")
    print(f"   volumen mensual: {m.get('monthly_volume')} | presupuesto: "
          f"{m.get('budget_range')} | deadline: {m.get('deadline')}")
    depts = [d.get("department_id") for d in r.get("departments", [])]
    print(f"   servicios: {', '.join(c.get('services_requested') or []) or '—'}")
    print(f"   departamentos activos: {', '.join(depts) or '—'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="PDF o directorio con PDFs de RFP")
    parser.add_argument("--persist", action="store_true", default=True,
                        help="Persistir el ticket (por defecto)")
    parser.add_argument("--no-persist", dest="persist", action="store_false",
                        help="Solo validar sin persis-tir (no escribe JSONL/BD)")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args(argv)

    files = _collect_inputs(args.paths)
    if not files:
        print("[error] ningún PDF encontrado para procesar.", file=sys.stderr)
        return 2

    # El raw_dir lo resuelve config.raw_rfp_dir() (data/raw/rfp/).
    raw_dir = raw_rfp_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    failed = 0
    for pdf in files:
        try:
            r = process_rfp_pdf(pdf, raw_dir=raw_dir, persist=args.persist)
        except Exception as exc:  # noqa: BLE001 - CLI debe reportar y seguir
            print(f"[error] fallo procesando {pdf.name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if args.json:
            results.append({
                "source": str(pdf),
                "ticket_id": r.get("ticket_id"),
                "status": r.get("status"),
                "reason": r.get("reason"),
                "classification": r.get("classification"),
                "metadata": r.get("metadata"),
                "currency": r.get("currency"),
                "departments": r.get("departments"),
            })
        else:
            _render_human(r, pdf)

    if args.json:
        json.dump(results, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")

    if failed:
        print(f"[resumen] {len(files)} archivos, {failed} fallos.", file=sys.stderr)
        return 1
    print(f"[resumen] {len(files)} archivos procesados sin errores.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())