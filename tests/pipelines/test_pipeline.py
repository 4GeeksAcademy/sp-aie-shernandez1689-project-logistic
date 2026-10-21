"""Tests unitarios del pipeline de desempeño semanal — transformación a KPIs.

Verifica que ``transform_to_weekly_kpis`` (task Prefect) calcula
correctamente los 5 KPIs definidos en Brifing-pipeline.md §2-§5:

  - inbound_units_count      (Volumen de entrada)
  - outbound_orders_count    (Throughput de salida)
  - stockout_events_count    (Frecuencia de quiebre de stock)
  - discrepancy_events_count (Numerador de tasa de discrepancia)
  - discrepancy_rate         (Tasa de discrepancia)

Cada test opera **de forma aislada** con datos en memoria, sin base
de datos ni APIs externas (el requisito explícito de la Fase 2).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ── Añadir raíz del monorepo al sys.path ──────────────────────────────────
# El paquete ``data.pipelines`` vive en <repo>/data/pipelines/.
# Este archivo está en <repo>/tests/pipelines/ → parents[2] = <repo>.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.pipelines.weekly_warehouse_client_performance.transform import (
    transform_to_weekly_kpis,
)


# ===================================================================
# Tests — comportamiento básico
# ===================================================================


class TestTransformBasic:
    """Casos límite y entrada vacía."""

    def test_empty_input(self):
        """Lista vacía → lista vacía."""
        result = transform_to_weekly_kpis([])
        assert result == []

    def test_none_input(self):
        """None no se pasa (raw siempre es lista), pero verificamos que
        el DataFrame vacío se maneje correctamente."""
        result = transform_to_weekly_kpis([])
        assert isinstance(result, list)
        assert len(result) == 0

    def test_empty_dataframe_after_filtering(self, known_scenario):
        """Si tras filtrar solo quedan filas descartadas → lista vacía."""
        # Eventos con warehouse nulo + client_id None — todo debe filtrarse
        events = [
            {
                "timestamp": "2026-10-12T08:00:00+00:00",
                "event_type": "inbound_order_created",
                "tags": {"warehouse": None, "client_id": None, "quantity": 100},
            },
            {
                "timestamp": "2026-10-12T09:00:00+00:00",
                "event_type": "outbound_order_created",
                "tags": {"warehouse": "madrid", "client_id": None},
            },
        ]
        result = transform_to_weekly_kpis(events)
        assert result == []


# ===================================================================
# Tests — verificación de KPIs con valores calculados a mano
# ===================================================================


class TestTransformKPIs:
    """Verifica que los 5 KPIs se calculan correctamente.

    Escenario conocido (``known_scenario``)::

        Eventos para fashion-co / los_angeles / 2026-W42:
          inbound  qty=100   → inbound_units_count = 100
          outbound           → outbound_orders_count = 2
          outbound           → (segundo outbound)
          stockout           → stockout_events_count = 1
          discrepancy        → discrepancy_events_count = 1

        discrepancy_rate = 1 / 2 = 0.5

        Eventos para urban-thread / los_angeles / 2026-W42:
          inbound  qty=50    → inbound_units_count = 50
          (sin outbounds)    → discrepancy_rate = 0.0
    """

    def test_known_kpi_values(self, known_scenario):
        """Verifica los 5 KPIs con el escenario de valores conocidos."""
        result = transform_to_weekly_kpis(known_scenario)

        assert len(result) == 2, "Esperamos 2 filas (fashion-co + urban-thread)"

        # Ordenar para tener resultados deterministas
        result_sorted = sorted(result, key=lambda r: r["client_id"])

        # ── fashion-co ──────────────────────────────────────────────
        row_fc = result_sorted[0]
        assert row_fc["warehouse"] == "los_angeles"
        assert row_fc["client_id"] == "fashion-co"
        assert row_fc["inbound_units_count"] == 100
        assert row_fc["outbound_orders_count"] == 2
        assert row_fc["stockout_events_count"] == 1
        assert row_fc["discrepancy_events_count"] == 1
        assert row_fc["discrepancy_rate"] == 0.5

        # ── urban-thread ────────────────────────────────────────────
        row_ut = result_sorted[1]
        assert row_ut["warehouse"] == "los_angeles"
        assert row_ut["client_id"] == "urban-thread"
        assert row_ut["inbound_units_count"] == 50
        assert row_ut["outbound_orders_count"] == 0
        assert row_ut["stockout_events_count"] == 0
        assert row_ut["discrepancy_events_count"] == 0
        assert row_ut["discrepancy_rate"] == 0.0

    def test_discrepancy_rate_zero_outbounds(self, zero_outbound_scenario):
        """Cuando outbound_orders_count == 0, discrepancy_rate debe ser 0.0
        (no división por cero)."""
        result = transform_to_weekly_kpis(zero_outbound_scenario)

        assert len(result) == 1
        row = result[0]
        assert row["client_id"] == "purestep-footwear"
        assert row["outbound_orders_count"] == 0
        assert row["discrepancy_events_count"] == 1
        assert row["discrepancy_rate"] == 0.0

    def test_discrepancy_rate_no_discrepancies(self, known_scenario):
        """Sin discrepancies → discrepancy_rate = 0.0."""
        # Filtrar solo los eventos que no son discrepancy del scenario conocido
        events = [e for e in known_scenario if e["event_type"] != "inventory_discrepancy_detected"]
        result = transform_to_weekly_kpis(events)

        row_fc = next(r for r in result if r["client_id"] == "fashion-co")
        assert row_fc["discrepancy_events_count"] == 0
        assert row_fc["discrepancy_rate"] == 0.0

    def test_multiple_inbound_quantities_summed(self, event_inbound):
        """Varios eventos inbound del mismo cliente/almacén/semana:
        sus cantidades se suman."""
        events = [
            {**event_inbound, "tags": {**event_inbound["tags"], "quantity": 30}},
            {**event_inbound, "tags": {**event_inbound["tags"], "quantity": 70}},
            {**event_inbound, "tags": {**event_inbound["tags"], "quantity": 100}},
        ]
        result = transform_to_weekly_kpis(events)
        assert len(result) == 1
        assert result[0]["inbound_units_count"] == 200

    def test_multiple_warehouses(self):
        """Eventos de distintos almacenes generan filas separadas."""
        events = [
            {
                "timestamp": "2026-10-12T08:00:00+00:00",
                "event_type": "inbound_order_created",
                "tags": {"warehouse": "los_angeles", "client_id": "fashion-co", "quantity": 100},
            },
            {
                "timestamp": "2026-10-12T08:00:00+00:00",
                "event_type": "inbound_order_created",
                "tags": {"warehouse": "zaragoza", "client_id": "fashion-co", "quantity": 200},
            },
        ]
        result = transform_to_weekly_kpis(events)
        assert len(result) == 2
        warehouses = {r["warehouse"] for r in result}
        assert warehouses == {"los_angeles", "zaragoza"}
        counts = {r["warehouse"]: r["inbound_units_count"] for r in result}
        assert counts["los_angeles"] == 100
        assert counts["zaragoza"] == 200

    def test_week_start_is_monday(self, known_scenario):
        """week_start debe ser el lunes de la semana ISO (2026-10-12)."""
        result = transform_to_weekly_kpis(known_scenario)
        for row in result:
            assert str(row["week_start"]) == "2026-10-12", (
                f"Expected week_start 2026-10-12, got {row['week_start']}"
            )


# ===================================================================
# Tests — filtrado de filas inválidas
# ===================================================================


class TestTransformFiltering:
    """Verifica que la transformación descarta correctamente filas inválidas."""

    def test_null_warehouse_dropped(self, null_warehouse_events):
        """Eventos con warehouse=None son descartados."""
        result = transform_to_weekly_kpis(null_warehouse_events)
        assert result == []

    def test_unknown_warehouse_dropped(self, unknown_warehouse_events):
        """Eventos con warehouse no permitido (e.g. 'madrid') son descartados."""
        result = transform_to_weekly_kpis(unknown_warehouse_events)
        assert result == []

    def test_null_client_id_dropped(self, null_client_id_events):
        """Eventos con client_id=None son descartados."""
        result = transform_to_weekly_kpis(null_client_id_events)
        assert result == []

    def test_malformed_tags_do_not_crash(self, malformed_tags_events):
        """Tags con formato incorrecto (string, None) se manejan sin excepción."""
        # No debe lanzar excepción
        result = transform_to_weekly_kpis(malformed_tags_events)
        # Tags malformados producen warehouse=None, que es descartado
        assert result == []


# ===================================================================
# Tests — tipos de evento y conteos
# ===================================================================


class TestTransformEventTypes:
    """Verifica que cada event_type se cuenta en el KPI correcto."""

    def test_outbound_orders_counted(self, event_outbound):
        """Múltiples outbounds se cuentan correctamente."""
        events = [event_outbound, {**event_outbound, "timestamp": "2026-10-12T11:00:00+00:00"}]
        result = transform_to_weekly_kpis(events)
        assert len(result) == 1
        assert result[0]["outbound_orders_count"] == 2

    def test_stockout_events_counted(self, event_stockout):
        """Múltiples stockouts se cuentan correctamente."""
        events = [event_stockout, {**event_stockout, "timestamp": "2026-10-13T11:00:00+00:00"}]
        result = transform_to_weekly_kpis(events)
        assert len(result) == 1
        assert result[0]["stockout_events_count"] == 2

    def test_discrepancy_events_counted(self, event_discrepancy):
        """Múltiples discrepancias se cuentan correctamente."""
        events = [event_discrepancy, {**event_discrepancy, "timestamp": "2026-10-12T15:00:00+00:00"}]
        result = transform_to_weekly_kpis(events)
        assert len(result) == 1
        assert result[0]["discrepancy_events_count"] == 2

    def test_unrelated_event_types_ignored(self):
        """Event_types que no son los 4 KPIs son ignorados (no cuentan)."""
        events = [
            {
                "timestamp": "2026-10-12T08:00:00+00:00",
                "event_type": "login_failed",
                "tags": {"warehouse": "los_angeles", "client_id": "fashion-co"},
            },
            {
                "timestamp": "2026-10-12T09:00:00+00:00",
                "event_type": "api_response_error_recorded",
                "tags": {"warehouse": "los_angeles", "client_id": "fashion-co"},
            },
        ]
        result = transform_to_weekly_kpis(events)
        # Ambos eventos se descartan porque no son de los 4 KPIs,
        # por lo que is_inbound/is_outbound/is_stockout/is_discrepancy son False
        # → todos los KPIs quedan en 0, pero el groupby sigue generando filas
        # con valores 0 (si hay warehouse/client_id válidos)
        assert len(result) == 1
        row = result[0]
        assert row["inbound_units_count"] == 0
        assert row["outbound_orders_count"] == 0
        assert row["stockout_events_count"] == 0
        assert row["discrepancy_events_count"] == 0
        assert row["discrepancy_rate"] == 0.0


# ===================================================================
# Tests — integridad del output
# ===================================================================


class TestTransformOutputIntegrity:
    """Verifica la estructura y tipos del output."""

    def test_output_keys(self, known_scenario):
        """Cada fila contiene las claves esperadas del Briefing §5."""
        result = transform_to_weekly_kpis(known_scenario)
        expected_keys = {
            "warehouse",
            "client_id",
            "week_start",
            "inbound_units_count",
            "outbound_orders_count",
            "stockout_events_count",
            "discrepancy_events_count",
            "discrepancy_rate",
        }
        for row in result:
            assert set(row.keys()) == expected_keys, f"Missing keys in {row}"

    def test_output_types(self, known_scenario):
        """Tipos de los campos: int para conteos, float para rate."""
        result = transform_to_weekly_kpis(known_scenario)
        for row in result:
            assert isinstance(row["warehouse"], str)
            assert isinstance(row["client_id"], str)
            assert isinstance(row["inbound_units_count"], int)
            assert isinstance(row["outbound_orders_count"], int)
            assert isinstance(row["stockout_events_count"], int)
            assert isinstance(row["discrepancy_events_count"], int)
            assert isinstance(row["discrepancy_rate"], float)

    def test_non_negative_counts(self, known_scenario):
        """Ningún KPI de conteo debe ser negativo."""
        result = transform_to_weekly_kpis(known_scenario)
        for row in result:
            assert row["inbound_units_count"] >= 0
            assert row["outbound_orders_count"] >= 0
            assert row["stockout_events_count"] >= 0
            assert row["discrepancy_events_count"] >= 0
            assert row["discrepancy_rate"] >= 0.0

    def test_discrepancy_rate_range(self, known_scenario, zero_outbound_scenario):
        """discrepancy_rate debe estar en [0.0, 1.0]."""
        result = transform_to_weekly_kpis(known_scenario)
        for row in result:
            assert 0.0 <= row["discrepancy_rate"] <= 1.0

        result2 = transform_to_weekly_kpis(zero_outbound_scenario)
        for row in result2:
            assert 0.0 <= row["discrepancy_rate"] <= 1.0