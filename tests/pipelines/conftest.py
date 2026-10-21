"""Fixtures compartidos para los tests del pipeline de desempeño semanal.

Proporciona datos de prueba sintéticos que imitan la estructura de
``telemetry_events`` (según Brifing-pipeline.md §3) para ejercitar
la función ``transform_to_weekly_kpis`` de forma aislada, sin base
de datos ni APIs externas.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


# ── Timestamp de referencia (lunes de la semana ISO) ──────────────────────
# 2026-10-12 es un lunes (semana ISO 2026-W42).
MONDAY_ISO = "2026-10-12"


def _ts(day_offset: int = 0, hour: int = 12, minute: int = 0) -> str:
    """Genera un timestamp ISO UTC dentro de la semana de referencia.

    Parameters
    ----------
    day_offset : int
        Días desde el lunes (0 = lunes, 1 = martes, ...).
    hour : int
        Hora del día (UTC).
    minute : int
        Minuto.

    Returns
    -------
    str
        Timestamp ISO 8601 con offset "+00:00".
    """
    base = datetime(2026, 10, 12, tzinfo=timezone.utc)
    dt = base.replace(hour=hour, minute=minute) + timedelta(days=day_offset)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# Fixtures: eventos individuales (para construir escenarios)
# ---------------------------------------------------------------------------


@pytest.fixture
def event_inbound() -> dict[str, Any]:
    """Evento de entrada de inventario."""
    return {
        "timestamp": _ts(0, 8, 0),
        "event_type": "inbound_order_created",
        "tags": {
            "warehouse": "los_angeles",
            "client_id": "fashion-co",
            "quantity": 100,
        },
    }


@pytest.fixture
def event_outbound() -> dict[str, Any]:
    """Evento de salida de pedido."""
    return {
        "timestamp": _ts(0, 10, 0),
        "event_type": "outbound_order_created",
        "tags": {
            "warehouse": "los_angeles",
            "client_id": "fashion-co",
        },
    }


@pytest.fixture
def event_stockout() -> dict[str, Any]:
    """Evento de quiebre de stock."""
    return {
        "timestamp": _ts(1, 9, 0),
        "event_type": "stock_threshold_triggered",
        "tags": {
            "warehouse": "zaragoza",
            "client_id": "soundwave-electronics",
        },
    }


@pytest.fixture
def event_discrepancy() -> dict[str, Any]:
    """Evento de discrepancia de inventario."""
    return {
        "timestamp": _ts(0, 14, 0),
        "event_type": "inventory_discrepancy_detected",
        "tags": {
            "warehouse": "los_angeles",
            "client_id": "fashion-co",
        },
    }


# ---------------------------------------------------------------------------
# Fixture: escenario completo para un cálculo manual de KPI
# ---------------------------------------------------------------------------

# Escenario: misma semana, mismo almacén (los_angeles), mismo cliente (fashion-co)
#   1 inbound  con quantity=100  → inbound_units_count = 100
#   2 outbound                    → outbound_orders_count = 2
#   1 stockout                    → stockout_events_count = 1
#   1 discrepancy                 → discrepancy_events_count = 1
#   discrepancy_rate = 1/2 = 0.5
#
# Además un inbound de otro cliente (urban-thread) con quantity=50
#   → debe generar una segunda fila separada


@pytest.fixture
def known_scenario() -> list[dict[str, Any]]:
    """Conjunto de eventos con valores conocidos para verificar KPIs a mano.

    Returns
    -------
    list[dict]
        Eventos de la semana 2026-W42.
    """
    return [
        {
            "timestamp": _ts(0, 8, 0),
            "event_type": "inbound_order_created",
            "tags": {"warehouse": "los_angeles", "client_id": "fashion-co", "quantity": 100},
        },
        {
            "timestamp": _ts(0, 10, 0),
            "event_type": "outbound_order_created",
            "tags": {"warehouse": "los_angeles", "client_id": "fashion-co"},
        },
        {
            "timestamp": _ts(0, 11, 0),
            "event_type": "outbound_order_created",
            "tags": {"warehouse": "los_angeles", "client_id": "fashion-co"},
        },
        {
            "timestamp": _ts(0, 12, 0),
            "event_type": "stock_threshold_triggered",
            "tags": {"warehouse": "los_angeles", "client_id": "fashion-co"},
        },
        {
            "timestamp": _ts(0, 14, 0),
            "event_type": "inventory_discrepancy_detected",
            "tags": {"warehouse": "los_angeles", "client_id": "fashion-co"},
        },
        # Segundo cliente (debe generar fila separada)
        {
            "timestamp": _ts(0, 9, 0),
            "event_type": "inbound_order_created",
            "tags": {"warehouse": "los_angeles", "client_id": "urban-thread", "quantity": 50},
        },
    ]


# ---------------------------------------------------------------------------
# Fixture: escenario de discrepancy_rate con cero outbounds
# ---------------------------------------------------------------------------


@pytest.fixture
def zero_outbound_scenario() -> list[dict[str, Any]]:
    """Eventos con 0 outbound_orders → discrepancy_rate debe ser 0.0."""
    return [
        {
            "timestamp": _ts(0, 8, 0),
            "event_type": "inventory_discrepancy_detected",
            "tags": {"warehouse": "zaragoza", "client_id": "purestep-footwear"},
        },
        {
            "timestamp": _ts(0, 9, 0),
            "event_type": "stock_threshold_triggered",
            "tags": {"warehouse": "zaragoza", "client_id": "purestep-footwear"},
        },
    ]


# ---------------------------------------------------------------------------
# Fixtures: escenarios de input inválido/malformado
# ---------------------------------------------------------------------------


@pytest.fixture
def null_warehouse_events() -> list[dict[str, Any]]:
    """Eventos con warehouse nulo — deben ser descartados."""
    return [
        {
            "timestamp": _ts(0, 8, 0),
            "event_type": "inbound_order_created",
            "tags": {"warehouse": None, "client_id": "fashion-co", "quantity": 50},
        },
        {
            "timestamp": _ts(0, 10, 0),
            "event_type": "outbound_order_created",
            "tags": {"warehouse": None, "client_id": "fashion-co"},
        },
    ]


@pytest.fixture
def unknown_warehouse_events() -> list[dict[str, Any]]:
    """Eventos con warehouse no permitido — deben ser descartados."""
    return [
        {
            "timestamp": _ts(0, 8, 0),
            "event_type": "inbound_order_created",
            "tags": {"warehouse": "madrid", "client_id": "fashion-co", "quantity": 30},
        },
    ]


@pytest.fixture
def null_client_id_events() -> list[dict[str, Any]]:
    """Eventos con client_id nulo — deben ser descartados."""
    return [
        {
            "timestamp": _ts(0, 8, 0),
            "event_type": "inbound_order_created",
            "tags": {"warehouse": "los_angeles", "client_id": None, "quantity": 100},
        },
    ]


@pytest.fixture
def malformed_tags_events() -> list[dict[str, Any]]:
    """Eventos con tags malformados (string en vez de dict) — no deben romper."""
    return [
        {
            "timestamp": _ts(0, 8, 0),
            "event_type": "inbound_order_created",
            "tags": "this is not a dict",
        },
        {
            "timestamp": _ts(0, 10, 0),
            "event_type": "outbound_order_created",
            "tags": None,
        },
    ]


@pytest.fixture
def missing_timestamp_event() -> list[dict[str, Any]]:
    """Evento sin timestamp."""
    return [
        {
            "timestamp": None,
            "event_type": "inbound_order_created",
            "tags": {"warehouse": "los_angeles", "client_id": "fashion-co", "quantity": 100},
        },
    ]