#!/usr/bin/env python3
"""Genera datos sintéticos de eventos para validar el pipeline en modo local.

Crea eventos de los 4 KPIs para una semana ISO específica, con mezcla de
almacenes (los_angeles, zaragoza), clientes variados, y valores de quantity
realistas para inbound.

Uso:
    .venv/bin/python data/process/seed_events.py
    .venv/bin/python data/process/seed_events.py 2026-07-13  # semana específica

Los eventos se escriben en ``data/raw/weekly_warehouse_client_performance/events.jsonl``
(sobrescribe cualquier data anterior).
"""

from __future__ import annotations

import json
import random
import sys
from datetime import date, datetime, timedelta, timezone

# Semilla reproducible
RANDOM_SEED = 42
random.seed(RANDOM_SEED)

# Almacenes y clientes
WAREHOUSES = ["los_angeles", "zaragoza"]
CLIENTS = [
    "fashion-co",
    "soundwave-electronics",
    "glowlab-cosmetics",
    "urban-thread",
    "purestep-footwear",
]

# Probabilidades relativas de cada event_type
EVENT_DISTRIBUTION = {
    "inbound_order_created": 30,
    "outbound_order_created": 50,
    "stock_threshold_triggered": 5,
    "inventory_discrepancy_detected": 15,
}


def _iso_monday(d: date) -> date:
    """Devuelve el lunes de la semana ISO que contiene d."""
    iso_year, iso_week, _ = d.isocalendar()
    return date.fromisocalendar(iso_year, iso_week, 1)


def generate_events(
    week_start: date,
    events_per_day: int = 40,
    output_path: str | None = None,
) -> list[dict]:
    """Genera eventos sintéticos para una semana ISO completa.

    Parameters
    ----------
    week_start : date
        Lunes de la semana ISO.
    events_per_day : int
        Número aproximado de eventos por día.
    output_path : str | None
        Ruta del archivo JSONL de salida. Si es None, se usa la ruta por defecto.

    Returns
    -------
    list[dict]
        Lista de eventos generados.
    """
    events: list[dict] = []

    for day_offset in range(7):  # lunes a domingo
        day_date = week_start + timedelta(days=day_offset)
        n_events = int(events_per_day * random.uniform(0.8, 1.2))

        for _ in range(n_events):
            # Elegir event_type según distribución
            event_type = random.choices(
                list(EVENT_DISTRIBUTION.keys()),
                weights=list(EVENT_DISTRIBUTION.values()),
                k=1,
            )[0]

            # Timestamp aleatorio dentro del día (hora UTC)
            hour = random.randint(6, 22)
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            ts = datetime(
                day_date.year, day_date.month, day_date.day,
                hour, minute, second,
                tzinfo=timezone.utc,
            )

            warehouse = random.choice(WAREHOUSES)
            client_id = random.choice(CLIENTS)

            tags: dict = {
                "warehouse": warehouse,
                "client_id": client_id,
            }

            # Añadir quantity para inbound
            if event_type == "inbound_order_created":
                tags["quantity"] = random.randint(10, 500)
                tags["product_category"] = random.choice(
                    ["fashion", "electronics", "cosmetics"]
                )
                tags["product_id"] = random.choice([
                    "CLT-SNK-W-42", "TEC-EAR-001", "CSM-SRM-030",
                    "CLT-CHN-N-32", "TEC-CHG-065",
                ])

            elif event_type == "outbound_order_created":
                tags["exit_type"] = random.choice(["dispatch", "loss"])
                tags["product_id"] = random.choice([
                    "CLT-SNK-W-42", "TEC-EAR-001", "CSM-SRM-030",
                ])

            elif event_type == "stock_threshold_triggered":
                tags["product_id"] = random.choice([
                    "CLT-SNK-W-42", "TEC-EAR-001", "CSM-SRM-030",
                ])
                tags["threshold_level"] = random.randint(0, 10)

            elif event_type == "inventory_discrepancy_detected":
                tags["product_id"] = random.choice([
                    "CLT-SNK-W-42", "TEC-EAR-001", "CSM-SRM-030",
                ])
                tags["expected_qty"] = random.randint(50, 200)
                tags["actual_qty"] = random.randint(30, 180)

            events.append({
                "timestamp": ts.isoformat(),
                "event_type": event_type,
                "tags": tags,
            })

    # Escribir JSONL
    from pathlib import Path
    if output_path:
        out = Path(output_path)
    else:
        out = Path("data/raw/weekly_warehouse_client_performance/events.jsonl")

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, default=str) + "\n")

    print(f"Generated {len(events)} events → {out}")
    return events


if __name__ == "__main__":
    # Semana por defecto: la última semana ISO completa
    today = date.today()
    this_monday = _iso_monday(today)
    if today == this_monday:
        last_monday = this_monday - timedelta(days=7)
    else:
        last_monday = this_monday - timedelta(days=7)

    if len(sys.argv) > 1:
        target_week = date.fromisoformat(sys.argv[1])
    else:
        target_week = last_monday

    generate_events(target_week)