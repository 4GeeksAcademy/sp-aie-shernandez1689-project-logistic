"""Tests del endpoint GET /telemetry/report (Fase 2).

Verifican que el endpoint:
- Resuelve la ventana por defecto (últimos 7 días UTC) y la pasa al pipeline
- Acepta start_date / end_date explícitos y los normaliza a UTC
- Devuelve la estructura ``{period: {from, to}, metrics: {...}}``
- Cachea el resultado (TTL 60 s): no recalcula en cada request
- Responde 422 si la ventana es inválida
- Responde 503 si no hay DATABASE_URL configurada
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.telemetry as telemetry_routes
from auth import CurrentUser, get_current_user
from cache import telemetry_report_cache
from main import app
from routes.telemetry import _resolve_period


class FakeSession:
    """Context manager que sustituye a sqlmodel.Session sin tocar la DB."""

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


FAKE_METRICS = {
    "events_per_day": [{"date": "2026-10-16", "events": 2}],
    "events_per_type_per_day": [],
    "error_rate_daily": [],
    "error_rate_by_type": [
        {"event_type": "api_response_error_recorded", "total": 4, "errors": 1, "error_rate": 0.25},
    ],
    "api_errors_by_endpoint": [],
    "auth_failure_rate": [{"date": "2026-10-16", "attempts": 1, "failures": 0, "failure_rate": 0.0}],
}


@pytest.fixture
def report_env(monkeypatch):
    """Prepara el entorno para el endpoint de reporte.

    Sustituye la DB real por un objeto truthy, la Session por un fake y el
    pipeline por una función que registra las llamadas. Limpia el cache y la
    dependencia de auth al terminar.
    """
    calls: list[dict] = []
    engine = object()

    monkeypatch.setattr(telemetry_routes, "telemetry_engine", engine)
    monkeypatch.setattr(telemetry_routes, "Session", FakeSession)

    def fake_build(session, start, end):
        calls.append({"start": start, "end": end})
        return FAKE_METRICS

    monkeypatch.setattr(telemetry_routes, "build_telemetry_report", fake_build)

    telemetry_report_cache.invalidate_all()
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="test-admin", email="admin@example.com", role="admin",
    )

    try:
        with TestClient(app) as test_client:
            yield {"client": test_client, "calls": calls, "engine": engine}
    finally:
        app.dependency_overrides.clear()
        telemetry_report_cache.invalidate_all()


# ---------------------------------------------------------------------------
# Unitarios de _resolve_period
# ---------------------------------------------------------------------------


class TestResolvePeriod:
    def test_default_window_is_last_7_days_utc(self):
        """Sin fechas → start = now - 7d, end = now, ambos UTC."""
        now = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
        start, end = _resolve_period(None, None, now=now)
        assert end == now
        assert start == now - timedelta(days=7)
        assert start.tzinfo is not None
        assert end.tzinfo is not None

    def test_default_now_is_rounded_to_minute(self):
        """``now`` real se trunca al minuto para estabilizar la clave de cache."""
        start, end = _resolve_period(None, None)
        assert end.second == 0
        assert end.microsecond == 0
        assert start.second == 0
        assert start.microsecond == 0

    def test_explicit_dates_are_kept_and_converted_to_utc(self):
        """Fechas explícitas se conservan (inicio inclusivo / fin exclusivo)."""
        start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 10, 8, 0, 0, tzinfo=timezone.utc)
        resolved_start, resolved_end = _resolve_period(start, end)
        assert resolved_start == start
        assert resolved_end == end
        assert resolved_start.tzinfo is not None
        assert resolved_end.tzinfo is not None

    def test_invalid_window_raises(self):
        """start >= end → ValueError (el endpoint lo traduce a 422)."""
        start = datetime(2026, 10, 8, tzinfo=timezone.utc)
        end = datetime(2026, 10, 1, tzinfo=timezone.utc)
        with pytest.raises(ValueError):
            _resolve_period(start, end)

    def test_start_not_frozen_when_called_twice(self):
        """Sin now inyectado usa la hora actual truncada al minuto."""
        s1, e1 = _resolve_period(None, None)
        s2, e2 = _resolve_period(None, None)
        assert (e1 - s1) == timedelta(days=7)
        assert (e2 - s2) == timedelta(days=7)


# ---------------------------------------------------------------------------
# Tests del endpoint
# ---------------------------------------------------------------------------


class TestTelemetryReportEndpoint:
    def test_returns_report_structure(self, report_env):
        """Respuesta 200 con {period: {from, to}, metrics: {...}}."""
        client = report_env["client"]
        response = client.get("/telemetry/report")
        assert response.status_code == 200

        body = response.json()
        assert "period" in body
        assert "from" in body["period"]
        assert "to" in body["period"]
        assert "metrics" in body
        assert body["metrics"]["events_per_day"] == FAKE_METRICS["events_per_day"]
        assert body["metrics"]["error_rate_by_type"] == FAKE_METRICS["error_rate_by_type"]
        assert isinstance(body["metrics"]["events_per_day"], list)

    def test_default_window_is_7_days_utc(self, report_env):
        """Sin parámetros → el pipeline recibe una ventana de ~7 días UTC."""
        client = report_env["client"]
        response = client.get("/telemetry/report")
        assert response.status_code == 200

        calls = report_env["calls"]
        assert len(calls) == 1
        start, end = calls[0]["start"], calls[0]["end"]
        assert start.tzinfo is not None
        assert end.tzinfo is not None
        assert timedelta(days=6, hours=23) < (end - start) <= timedelta(days=7)

    def test_explicit_dates_are_passed_to_pipeline(self, report_env):
        """start_date y end_date explícitos llegan al pipeline normalizados UTC."""
        client = report_env["client"]
        start = "2026-10-01T00:00:00Z"
        end = "2026-10-08T00:00:00Z"
        response = client.get(f"/telemetry/report?start_date={start}&end_date={end}")
        assert response.status_code == 200

        calls = report_env["calls"]
        assert len(calls) == 1
        resolved_start, resolved_end = calls[0]["start"], calls[0]["end"]
        assert resolved_start == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert resolved_end == datetime(2026, 10, 8, tzinfo=timezone.utc)
        assert response.json()["period"]["from"].startswith("2026-10-01")
        assert response.json()["period"]["to"].startswith("2026-10-08")

    def test_same_window_is_cached(self, report_env):
        """Misma ventana → el pipeline se ejecuta una sola vez (cache)."""
        client = report_env["client"]
        assert client.get("/telemetry/report").status_code == 200
        assert client.get("/telemetry/report").status_code == 200
        assert len(report_env["calls"]) == 1

    def test_expired_cache_recalculates(self, report_env):
        """Cache expirado → el pipeline se ejecuta de nuevo."""
        client = report_env["client"]
        assert client.get("/telemetry/report").status_code == 200
        assert len(report_env["calls"]) == 1

        # Forzar expiración del almacén interno del cache
        for key, (expires_at, value) in list(telemetry_report_cache._store.items()):
            telemetry_report_cache._store[key] = (time.monotonic() - 1, value)

        assert client.get("/telemetry/report").status_code == 200
        assert len(report_env["calls"]) == 2

    def test_different_window_is_computed_again(self, report_env):
        """Otra ventana → no golpea el cache de la ventana anterior."""
        client = report_env["client"]
        assert client.get("/telemetry/report").status_code == 200
        assert len(report_env["calls"]) == 1

        response = client.get(
            "/telemetry/report?start_date=2026-09-01T00:00:00Z&end_date=2026-09-08T00:00:00Z"
        )
        assert response.status_code == 200
        assert len(report_env["calls"]) == 2

    def test_invalid_window_returns_422(self, report_env):
        """start_date >= end_date → 422."""
        client = report_env["client"]
        response = client.get(
            "/telemetry/report?start_date=2026-10-20T00:00:00Z&end_date=2026-10-01T00:00:00Z"
        )
        assert response.status_code == 422
        assert len(report_env["calls"]) == 0

    def test_missing_database_returns_503(self, monkeypatch):
        """Sin DATABASE_URL configurada → 503."""
        monkeypatch.setattr(telemetry_routes, "telemetry_engine", None)
        monkeypatch.setattr(telemetry_routes, "Session", FakeSession)
        monkeypatch.setattr(
            telemetry_routes,
            "build_telemetry_report",
            lambda session, start, end: FAKE_METRICS,
        )
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        telemetry_report_cache.invalidate_all()
        try:
            with TestClient(app) as client:
                response = client.get("/telemetry/report")
            assert response.status_code == 503
        finally:
            app.dependency_overrides.clear()
            telemetry_report_cache.invalidate_all()

    def test_requires_authentication(self, report_env):
        """Sin token (sin override de auth) → 401."""
        app.dependency_overrides.clear()
        telemetry_report_cache.invalidate_all()
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/telemetry/report")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()
            telemetry_report_cache.invalidate_all()