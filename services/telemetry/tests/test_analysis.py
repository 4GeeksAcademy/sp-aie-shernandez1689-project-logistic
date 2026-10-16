"""Tests del pipeline de análisis de telemetría (Fase 1).

Ejercita cada función de métrica del módulo ``analysis`` con datos
sintéticos inyectados a través de un mock de SQLAlchemy ``Session``,
verificando que la transformación en Pandas produce el resultado
esperado sin necesidad de conexión a Supabase.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import create_autospec

import pandas as pd
import pytest

# Añadir la raíz del monorepo al sys.path para poder importar el paquete
# services.telemetry. Este archivo vive en
# <repo>/services/telemetry/tests/ → parents[3] = <repo>
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.telemetry.analysis import (
    _extract_tags,
    _rows_to_dataframe,
    _to_records,
    api_errors_by_endpoint,
    auth_failure_rate_daily,
    error_rate_by_type,
    error_rate_daily,
    events_per_day,
    events_per_type_per_day,
)
from sqlalchemy.engine import Result
from sqlmodel import Session


# ---------------------------------------------------------------------------
# Fixture de sesión mockeada
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session():
    """Crea un Session mockeado.

    Cada test ajusta ``mock_session.execute.return_value.all.return_value``
    a una lista de dicts (el equivalente a las filas SQL), que es el formato
    que ``analysis.py`` convierte con ``pd.DataFrame(...)``.
    """
    return create_autospec(Session, instance=True)


# ---------------------------------------------------------------------------
# Tests unitarios de los helpers internos
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_rows_to_dataframe_empty(self):
        """Lista vacía → DataFrame con timestamp dtype UTC."""
        df = _rows_to_dataframe([])
        assert df.empty
        assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])

    def test_rows_to_dataframe_with_timestamps(self):
        """Strings ISO se convierten a datetime UTC."""
        rows = [{"timestamp": "2026-10-16T14:30:00.000Z"}]
        df = _rows_to_dataframe(rows)
        assert not df.empty
        assert df["timestamp"].iloc[0].tz is not None
        assert str(df["timestamp"].iloc[0].tz) == "UTC"

    def test_extract_tags_missing_column(self):
        """Sin columna ``tags`` → serie de Nones."""
        df = pd.DataFrame({"a": [1, 2]})
        result = _extract_tags(df, "endpoint")
        assert len(result) == 2
        assert result.isna().all()

    def test_extract_tags_with_data(self):
        """Extracción correcta de una clave del dict tags."""
        df = pd.DataFrame({"tags": [{"endpoint": "/api/incidents"}, {"endpoint": None}]})
        result = _extract_tags(df, "endpoint")
        assert result.iloc[0] == "/api/incidents"
        assert pd.isna(result.iloc[1])

    def test_to_records_structure(self):
        """reset_index + to_dict produce lista de dicts."""
        df = pd.DataFrame({"date": ["2026-10-16"], "events": [5]}).set_index("date")
        records = _to_records(df)
        assert isinstance(records, list)
        assert len(records) == 1
        assert "date" in records[0]
        assert "events" in records[0]


# ---------------------------------------------------------------------------
# Tests de métrica 1: events_per_day
# ---------------------------------------------------------------------------


class TestEventsPerDay:
    def test_returns_records(self, mock_session):
        """Datos sintéticos → lista de dicts con date y events."""
        rows = [
            {"timestamp": "2026-10-16T10:00:00Z"},
            {"timestamp": "2026-10-16T11:00:00Z"},
            {"timestamp": "2026-10-17T10:00:00Z"},
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = events_per_day(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["date"] == "2026-10-16"
        assert result[0]["events"] == 2
        assert result[1]["events"] == 1

    def test_empty_result(self, mock_session):
        """Sin datos → lista vacía."""
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = []

        result = events_per_day(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests de métrica 2: events_per_type_per_day
# ---------------------------------------------------------------------------


class TestEventsPerTypePerDay:
    def test_returns_records(self, mock_session):
        """Agrupación correcta por date + event_type."""
        rows = [
            {"timestamp": "2026-10-16T10:00:00Z", "event_type": "inbound_order_created"},
            {"timestamp": "2026-10-16T11:00:00Z", "event_type": "login_succeeded"},
            {"timestamp": "2026-10-16T12:00:00Z", "event_type": "inbound_order_created"},
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = events_per_type_per_day(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert len(result) == 2
        # inbound: 2 eventos, login_succeeded: 1
        inbound = [r for r in result if r["event_type"] == "inbound_order_created"]
        login = [r for r in result if r["event_type"] == "login_succeeded"]
        assert inbound[0]["events"] == 2
        assert login[0]["events"] == 1

    def test_empty_result(self, mock_session):
        """Sin datos → lista vacía."""
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = []

        result = events_per_type_per_day(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests de métrica 3: error_rate_daily
# ---------------------------------------------------------------------------


class TestErrorRateDaily:
    def test_mixed_levels(self, mock_session):
        """Eventos error/warn y info → tasa correcta."""
        rows = [
            {"timestamp": "2026-10-16T10:00:00Z", "event_type": "api_response_error_recorded", "level": "error"},
            {"timestamp": "2026-10-16T11:00:00Z", "event_type": "api_response_error_recorded", "level": "warn"},
            {"timestamp": "2026-10-16T12:00:00Z", "event_type": "api_response_error_recorded", "level": "error"},
            {"timestamp": "2026-10-16T13:00:00Z", "event_type": "api_response_error_recorded", "level": "info"},
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = error_rate_daily(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 17, tzinfo=timezone.utc),
        )
        assert len(result) == 1
        # 3 error/warn de 4 totales → 0.75
        assert result[0]["error_rate"] == 0.75
        assert result[0]["total"] == 4
        assert result[0]["errors"] == 3

    def test_empty_result(self, mock_session):
        """Sin datos → lista vacía."""
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = []

        result = error_rate_daily(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests de métrica 4: error_rate_by_type
# ---------------------------------------------------------------------------


class TestErrorRateByType:
    def test_rates_by_type(self, mock_session):
        """Múltiples tipos → tasa agrupada."""
        rows = [
            {"event_type": "api_response_error_recorded", "level": "error"},
            {"event_type": "api_response_error_recorded", "level": "warn"},
            {"event_type": "api_response_error_recorded", "level": "info"},
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = error_rate_by_type(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 17, tzinfo=timezone.utc),
        )
        assert len(result) == 1
        rec = result[0]
        assert rec["event_type"] == "api_response_error_recorded"
        assert rec["error_rate"] == pytest.approx(0.6667, rel=1e-3)

    def test_empty_result(self, mock_session):
        """Sin datos → lista vacía."""
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = []

        result = error_rate_by_type(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests de métrica 5: api_errors_by_endpoint
# ---------------------------------------------------------------------------


class TestApiErrorsByEndpoint:
    def test_groups_by_endpoint(self, mock_session):
        """Extrae endpoint de tags y agrupa."""
        rows = [
            {
                "timestamp": "2026-10-16T10:00:00Z",
                "level": "error",
                "tags": {"endpoint": "/api/inventory", "status_code": 500},
            },
            {
                "timestamp": "2026-10-16T11:00:00Z",
                "level": "warn",
                "tags": {"endpoint": "/api/inventory", "status_code": 404},
            },
            {
                "timestamp": "2026-10-16T12:00:00Z",
                "level": "error",
                "tags": {"endpoint": "/api/suppliers", "status_code": 500},
            },
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = api_errors_by_endpoint(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 17, tzinfo=timezone.utc),
        )
        assert len(result) == 2
        inv = [r for r in result if r["endpoint"] == "/api/inventory"][0]
        sup = [r for r in result if r["endpoint"] == "/api/suppliers"][0]
        assert inv["errors"] == 2
        assert inv["server_errors"] == 1
        assert sup["errors"] == 1
        assert sup["server_errors"] == 1

    def test_no_tags_endpoint(self, mock_session):
        """Filas sin endpoint en tags → se descartan."""
        rows = [
            {
                "timestamp": "2026-10-16T10:00:00Z",
                "level": "error",
                "tags": {"status_code": 500},
            },
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = api_errors_by_endpoint(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 17, tzinfo=timezone.utc),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests de métrica 6: auth_failure_rate_daily
# ---------------------------------------------------------------------------


class TestAuthFailureRateDaily:
    def test_failure_rate(self, mock_session):
        """Cálculo de tasa diaria de fallos."""
        rows = [
            {"timestamp": "2026-10-16T10:00:00Z", "event_type": "login_succeeded"},
            {"timestamp": "2026-10-16T11:00:00Z", "event_type": "login_succeeded"},
            {"timestamp": "2026-10-16T12:00:00Z", "event_type": "login_failed"},
            {"timestamp": "2026-10-17T10:00:00Z", "event_type": "login_failed"},
        ]
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = rows

        result = auth_failure_rate_daily(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert len(result) == 2
        day1 = [r for r in result if r["date"] == "2026-10-16"][0]
        day2 = [r for r in result if r["date"] == "2026-10-17"][0]
        assert day1["attempts"] == 3
        assert day1["failures"] == 1
        assert day1["failure_rate"] == pytest.approx(0.3333, rel=1e-3)
        assert day2["attempts"] == 1
        assert day2["failures"] == 1
        assert day2["failure_rate"] == 1.0

    def test_no_auth_events(self, mock_session):
        """Sin eventos de auth → lista vacía."""
        mock_session.execute.return_value = create_autospec(Result, instance=True)
        mock_session.execute.return_value.all.return_value = []

        result = auth_failure_rate_daily(
            mock_session,
            datetime(2026, 10, 16, tzinfo=timezone.utc),
            datetime(2026, 10, 18, tzinfo=timezone.utc),
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests de integración de helpers internos
# ---------------------------------------------------------------------------


class TestRowsToDataframe:
    def test_timestamp_conversion(self):
        """pd.to_datetime con utc=True produce tz-aware en UTC."""
        rows = [{"timestamp": "2026-10-16T10:00:00Z"}]
        df = _rows_to_dataframe(rows)
        assert df["timestamp"].iloc[0].tzname() == "UTC"

    def test_no_timestamp_column(self):
        """Filas sin columna timestamp no se rompen y conservan sus datos."""
        rows = [{"foo": "bar"}]
        df = _rows_to_dataframe(rows)
        assert "foo" in df.columns
        assert "timestamp" not in df.columns
        assert df["foo"].iloc[0] == "bar"


if __name__ == "__main__":
    pytest.main(["-v", __file__])