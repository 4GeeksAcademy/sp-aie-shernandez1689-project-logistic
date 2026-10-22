"""Tests del scheduler nocturno — verificación de fecha objetivo y lógica.

Todas las pruebas usan lógica pura (sin conexión a BD ni pipeline).
El scheduler se testea en tres capas:

1. ``resolve_target_week`` — lógica de resolución de fechas (probado aquí).
2. ``run_scheduler`` con mock de pipeline — lógica de guardianes y ciclo de vida.
3. Integración end-to-end (saltado aquí, requiere PostgreSQL real).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import ANY, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.job_runner.scheduler import resolve_target_week


# =========================================================================
# Tests: resolve_target_week
# =========================================================================


class TestResolveTargetWeek:
    """Verifica la resolución de la semana objetivo."""

    def test_none_returns_last_complete_week(self):
        """Sin TARGET_DATE, devuelve el lunes de la última semana ISO completa."""
        d = resolve_target_week(None)
        assert d.isoweekday() == 1, "Debe ser lunes"
        # Debe ser una fecha pasada (semana completa anterior)
        today = datetime.now(timezone.utc).date()
        assert d < today

    def test_iso_date_returns_monday(self):
        """YYYY-MM-DD → lunes de esa semana ISO."""
        d = resolve_target_week("2026-07-13")
        assert d.isoformat() == "2026-07-13"
        assert d.isoweekday() == 1  # Monday

    def test_iso_date_midweek_returns_monday(self):
        """Miércoles → lunes de esa misma semana ISO."""
        d = resolve_target_week("2026-07-15")
        assert d.isoweekday() == 1
        assert d.isoformat() == "2026-07-13"

    def test_iso_date_sunday_returns_monday(self):
        """Domingo → lunes de esa misma semana ISO."""
        d = resolve_target_week("2026-07-19")
        assert d.isoweekday() == 1
        assert d.isoformat() == "2026-07-13"

    def test_iso_week_format(self):
        """YYYY-WNN → lunes de esa semana."""
        d = resolve_target_week("2026-W29")
        assert d.isoformat() == "2026-07-13"
        assert d.isoweekday() == 1

    def test_iso_week_format_single_digit(self):
        """YYYY-WN (single digit week) → lunes de esa semana."""
        d = resolve_target_week("2026-W1")
        assert d.isoformat() == "2025-12-29"
        assert d.isoweekday() == 1

    def test_invalid_format_raises(self):
        """Formato inválido → ValueError."""
        with pytest.raises(ValueError, match="TARGET_DATE"):
            resolve_target_week("not-a-date")

    def test_invalid_date_raises(self):
        """Fecha inválida → ValueError."""
        with pytest.raises(ValueError):
            resolve_target_week("2026-13-01")

    def test_cross_year_week(self):
        """Semana ISO que cruza de año (ej. W1 de 2027 comienza en 2026)."""
        d = resolve_target_week("2027-W1")
        assert d.isoformat() == "2027-01-04"  # 2027-01-04 es lunes de W1
        assert d.isoweekday() == 1


# =========================================================================
# Tests: run_scheduler (con mocks)
# =========================================================================


class FakeSession:
    """Sesión ficticia que registra operaciones para verificación."""

    def __init__(self):
        self.operations: list[str] = []
        self.committed = False

    def close(self):
        self.operations.append("close")

    def __enter__(self):
        return self

    def __exit__(self, *args: Any) -> None:
        self.operations.append("__exit__")


class TestRunSchedulerGuardianes:
    """Verifica que el scheduler aborta cuando debe."""

    def test_aborta_si_hay_processing_lock(self):
        """Si hay processing lock, el scheduler aborta con sys.exit(1)."""
        with (
            patch(
                "services.job_runner.scheduler.init_db",
                return_value=None,
            ),
            patch(
                "services.job_runner.scheduler.get_engine",
            ),
            patch(
                "services.job_runner.scheduler.has_processing_lock",
                return_value=True,
            ),
            patch(
                "services.job_runner.scheduler.Session",
                return_value=FakeSession(),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from services.job_runner.scheduler import run_scheduler

            run_scheduler(target_date_str="2026-07-13")

        assert exc_info.value.code == 1

    def test_skips_si_fecha_ya_completada(self):
        """Si la fecha ya está completada, el scheduler retorna sin error."""
        with (
            patch(
                "services.job_runner.scheduler.init_db",
                return_value=None,
            ),
            patch(
                "services.job_runner.scheduler.get_engine",
            ),
            patch(
                "services.job_runner.scheduler.has_processing_lock",
                return_value=False,
            ),
            patch(
                "services.job_runner.scheduler.has_completed_for_date",
                return_value=True,
            ),
            patch(
                "services.job_runner.scheduler.Session",
                return_value=FakeSession(),
            ),
            patch(
                "services.job_runner.scheduler.logger",
            ) as mock_logger,
        ):
            from services.job_runner.scheduler import run_scheduler

            # No debería lanzar excepción
            run_scheduler(target_date_str="2026-07-13")

            mock_logger.info.assert_any_call(
                "SCHEDULER SKIP | pipeline=%s date=%s status=SUCCESS — already processed",
                "weekly_warehouse_client_performance",
                "2026-07-13",
            )

    def test_ejecuta_pipeline_normal(self):
        """Flujo normal: crea JobRun, ejecuta pipeline, todo OK."""
        fake_session = FakeSession()
        fake_run = type("Run", (), {"id": "fake-run-id"})()

        with (
            patch(
                "services.job_runner.scheduler.init_db",
                return_value=None,
            ),
            patch(
                "services.job_runner.scheduler.get_engine",
            ),
            patch(
                "services.job_runner.scheduler.has_processing_lock",
                return_value=False,
            ),
            patch(
                "services.job_runner.scheduler.has_completed_for_date",
                return_value=False,
            ),
            patch(
                "services.job_runner.scheduler.create_job_run",
                return_value=fake_run,
            ) as mock_create,
            patch(
                "services.job_runner.scheduler.run_job",
                return_value=None,
            ) as mock_run_job,
            patch(
                "services.job_runner.scheduler.Session",
                return_value=fake_session,
            ),
        ):
            from services.job_runner.scheduler import run_scheduler

            run_scheduler(target_date_str="2026-07-13")

        # Verificar que se llamó create_job_run con los parámetros correctos
        mock_create.assert_called_once_with(
            fake_session,
            pipeline="weekly_warehouse_client_performance",
            date_from="2026-07-13",
            date_to=None,
        )

        # Verificar que se llamó run_job con trigger_weekly_run
        mock_run_job.assert_called_once()
        args, kwargs = mock_run_job.call_args
        # run_id es el 2º argumento posicional (session, run_id, fn, ...)
        assert args[1] == "fake-run-id", f"Expected run_id 'fake-run-id', got {args[1]}"
        assert kwargs.get("week_start") == date(2026, 7, 13)

    def test_excepcion_en_scheduler_loguea_y_sale(self):
        """Si ocurre una excepción inesperada en run_scheduler, se propaga."""
        with (
            patch(
                "services.job_runner.scheduler.init_db",
                side_effect=RuntimeError("BD caída"),
            ),
            pytest.raises(RuntimeError, match="BD caída"),
        ):
            from services.job_runner.scheduler import run_scheduler

            run_scheduler(target_date_str="2026-07-13")


class TestMainEntryPoint:
    """Verifica que main() lee TARGET_DATE del entorno."""

    def test_main_con_target_date(self):
        """main() debe leer TARGET_DATE del entorno."""
        with (
            patch(
                "services.job_runner.scheduler.run_scheduler",
            ) as mock_run,
            patch.dict(
                "os.environ",
                {"TARGET_DATE": "2026-W30"},
                clear=True,
            ),
        ):
            from services.job_runner.scheduler import main

            main()

            mock_run.assert_called_once_with(target_date_str="2026-W30")

    def test_main_sin_target_date(self):
        """main() sin TARGET_DATE debe pasar None."""
        with (
            patch(
                "services.job_runner.scheduler.run_scheduler",
            ) as mock_run,
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
        ):
            from services.job_runner.scheduler import main

            main()

            mock_run.assert_called_once_with(target_date_str=None)

    def test_main_exception_causes_sys_exit_2(self):
        """Si run_scheduler lanza excepción, main() hace sys.exit(2)."""
        with (
            patch(
                "services.job_runner.scheduler.run_scheduler",
                side_effect=RuntimeError("Algo falló"),
            ),
            patch.dict(
                "os.environ",
                {},
                clear=True,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            from services.job_runner.scheduler import main

            main()

        assert exc_info.value.code == 2