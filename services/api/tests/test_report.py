"""Tests del endpoint GET /telemetry/report (Fase 2, ahora asíncrono).

Verifican que el endpoint:
- Resuelve la ventana de fechas y la pasa a send_task de Celery
- Devuelve 202 Accepted con ``{"task_id": "..."}`` inmediatamente
- Responde 422 si la ventana es inválida
- Responde 503 si no hay DATABASE_URL configurada
- Requiere autenticación
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import routes.telemetry as telemetry_routes
from auth import CurrentUser, get_current_user
from main import app
from routes.telemetry import _resolve_period


@pytest.fixture
def report_env(monkeypatch):
    """Prepara el entorno para el endpoint asíncrono de reporte.

    Sustituye la DB real por un objeto truthy y celery_app.send_task por
    un mock que registra las llamadas y devuelve un AsyncResult simulado.
    Limpia la dependencia de auth al terminar.
    """
    calls: list[dict] = []
    engine = object()

    monkeypatch.setattr(telemetry_routes, "telemetry_engine", engine)

    def fake_send_task(name, args):
        calls.append({"name": name, "args": args})
        mock_result = MagicMock()
        mock_result.id = "fake-task-uuid-1234"
        return mock_result

    monkeypatch.setattr(telemetry_routes.celery_app, "send_task", fake_send_task)

    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="test-admin", email="admin@example.com", role="admin",
    )

    try:
        with TestClient(app) as test_client:
            yield {"client": test_client, "calls": calls, "engine": engine}
    finally:
        app.dependency_overrides.clear()


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
# Tests del endpoint asíncrono
# ---------------------------------------------------------------------------


class TestTelemetryReportEndpoint:
    def test_returns_202_with_task_id(self, report_env):
        """Respuesta 202 con {"task_id": "..."}."""
        client = report_env["client"]
        response = client.get("/telemetry/report")
        assert response.status_code == 202

        body = response.json()
        assert "task_id" in body
        assert body["task_id"] == "fake-task-uuid-1234"

    def test_enqueues_celery_task_with_default_window(self, report_env):
        """Sin parámetros → send_task recibe la tarea con ventana ~7 días."""
        client = report_env["client"]
        response = client.get("/telemetry/report")
        assert response.status_code == 202

        calls = report_env["calls"]
        assert len(calls) == 1
        assert calls[0]["name"] == "generate_telemetry_report"
        # args son strings ISO, verificar que la ventana es ~7 días
        start = datetime.fromisoformat(calls[0]["args"][0])
        end = datetime.fromisoformat(calls[0]["args"][1])
        assert start.tzinfo is not None
        assert end.tzinfo is not None
        assert timedelta(days=6, hours=23) < (end - start) <= timedelta(days=7)

    def test_explicit_dates_are_passed_to_celery(self, report_env):
        """start_date y end_date explícitos llegan a send_task normalizados UTC."""
        client = report_env["client"]
        start = "2026-10-01T00:00:00Z"
        end = "2026-10-08T00:00:00Z"
        response = client.get(f"/telemetry/report?start_date={start}&end_date={end}")
        assert response.status_code == 202

        calls = report_env["calls"]
        assert len(calls) == 1
        resolved_start = datetime.fromisoformat(calls[0]["args"][0])
        resolved_end = datetime.fromisoformat(calls[0]["args"][1])
        assert resolved_start == datetime(2026, 10, 1, tzinfo=timezone.utc)
        assert resolved_end == datetime(2026, 10, 8, tzinfo=timezone.utc)

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
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        try:
            with TestClient(app) as client:
                response = client.get("/telemetry/report")
            assert response.status_code == 503
        finally:
            app.dependency_overrides.clear()

    def test_requires_authentication(self, report_env):
        """Sin token (sin override de auth) → 401."""
        app.dependency_overrides.clear()
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/telemetry/report")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests del endpoint GET /tasks/{task_id}
# ---------------------------------------------------------------------------


class TestTaskStatusEndpoint:
    def test_returns_pending_for_unknown_task(self, monkeypatch):
        """Tarea que no existe en Redis → status pending."""
        mock_async_result = MagicMock()
        mock_async_result.state = "PENDING"
        mock_async_result.result = None

        def fake_async_result(task_id, app):
            return mock_async_result

        monkeypatch.setattr("routes.tasks.AsyncResult", fake_async_result)

        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        try:
            with TestClient(app) as client:
                response = client.get("/tasks/some-unknown-uuid")
            assert response.status_code == 200
            body = response.json()
            assert body["task_id"] == "some-unknown-uuid"
            assert body["status"] == "pending"
            assert "result" not in body
        finally:
            app.dependency_overrides.clear()

    def test_returns_success_with_result(self, monkeypatch):
        """Tarea completada con éxito → status success + result."""
        mock_async_result = MagicMock()
        mock_async_result.state = "SUCCESS"
        mock_async_result.result = {"period": {"from": "...", "to": "..."}, "metrics": {}}

        def fake_async_result(task_id, app):
            return mock_async_result

        monkeypatch.setattr("routes.tasks.AsyncResult", fake_async_result)

        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        try:
            with TestClient(app) as client:
                response = client.get("/tasks/some-uuid")
            assert response.status_code == 200
            body = response.json()
            assert body["task_id"] == "some-uuid"
            assert body["status"] == "success"
            assert "period" in body["result"]
            assert "metrics" in body["result"]
        finally:
            app.dependency_overrides.clear()

    def test_returns_failure_with_error(self, monkeypatch):
        """Tarea que falló → status failure + error message."""
        mock_async_result = MagicMock()
        mock_async_result.state = "FAILURE"
        mock_async_result.result = RuntimeError("DB connection lost")

        def fake_async_result(task_id, app):
            return mock_async_result

        monkeypatch.setattr("routes.tasks.AsyncResult", fake_async_result)

        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        try:
            with TestClient(app) as client:
                response = client.get("/tasks/some-uuid")
            assert response.status_code == 200
            body = response.json()
            assert body["task_id"] == "some-uuid"
            assert body["status"] == "failure"
            assert "error" in body["result"]
        finally:
            app.dependency_overrides.clear()

    def test_returns_started_for_running_task(self, monkeypatch):
        """Tarea en ejecución → status started."""
        mock_async_result = MagicMock()
        mock_async_result.state = "STARTED"
        mock_async_result.result = None

        def fake_async_result(task_id, app):
            return mock_async_result

        monkeypatch.setattr("routes.tasks.AsyncResult", fake_async_result)

        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        try:
            with TestClient(app) as client:
                response = client.get("/tasks/some-uuid")
            assert response.status_code == 200
            body = response.json()
            assert body["task_id"] == "some-uuid"
            assert body["status"] == "started"
            assert "result" not in body
        finally:
            app.dependency_overrides.clear()

    def test_requires_authentication(self, monkeypatch):
        """Sin token → 401."""
        app.dependency_overrides.clear()
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/tasks/some-uuid")
            assert response.status_code in (401, 403)
        finally:
            app.dependency_overrides.clear()