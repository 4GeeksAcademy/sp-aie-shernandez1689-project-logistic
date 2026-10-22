"""Validación de job_runner contra PostgreSQL real (temporal).

Cubre: happy path, cancelación por lock, omisión por duplicado,
carrera atómica, y la garantía de que ninguna ejecución fallida deja
un registro en 'processing'.
"""
from __future__ import annotations

import os
import sys
from datetime import date

os.environ["DATABASE_URL"] = (
    "postgresql://testuser:testpass@localhost:55432/testdb?sslmode=disable"
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from sqlmodel import Session, select

from services.job_runner import (
    create_job_run,
    get_job_run,
    has_completed_for_date,
    has_processing_lock,
    run_job,
    update_job_run_status,
)
from services.job_runs.database import get_engine
from services.job_runs.models import JobRun, JobRunStatus

PASS = 0
FAIL = 0


def check(label: str, cond: bool):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✔ {label}")
    else:
        FAIL += 1
        print(f"  ✘ {label}")


def rows(session) -> list[JobRun]:
    return list(session.exec(select(JobRun).order_by(JobRun.created_at)))


def main():
    engine = get_engine()
    # Limpiar tabla para validación reproducible.
    with Session(engine) as s:
        for r in s.exec(select(JobRun)):
            s.delete(r)
        s.commit()

    JOB = "validate_job"
    D1 = date(2026, 10, 20)
    D2 = date(2026, 10, 21)

    # ── 1. Happy path: work OK → executed + completed ─────────────
    print("\n1) run_job happy path")
    with Session(engine) as s:
        executed = {"ran": False}

        def ok_work(sess):
            executed["ran"] = True

        out = run_job(s, JOB, D1, ok_work)
        check("outcome == executed", out.outcome == "executed")
        check("outcome.run_id not None", out.run_id is not None)
        check("work ran", executed["ran"] is True)
        check("has_completed_for_date", has_completed_for_date(s, JOB, D1))
        check("not has_processing_lock", has_processing_lock(s, JOB) is False)
        run = get_job_run(s, out.run_id)
        check("status == completed", run["status"] == JobRunStatus.COMPLETED.value)
        check("no error_message", run["error_message"] is None)
        check("finished_at set", run["finished_at"] is not None)
        run_id_happy = out.run_id

    # ── 2. Duplicado: completed para (JOB, D1) → skipped_duplicate ─
    print("\n2) run_job duplicate skip")
    with Session(engine) as s:
        executed = {"ran": False}

        def dup_work(sess):
            executed["ran"] = True

        out = run_job(s, JOB, D1, dup_work)
        check("outcome == skipped_duplicate", out.outcome == "skipped_duplicate")
        check("work NOT ran", executed["ran"] is False)
        check("run_id == previous", out.run_id == run_id_happy)
        # No debe haber filas adicionales para (JOB, D1).
        same = [r for r in rows(s) if r.job_name == JOB and r.target_date == D1]
        check("only 1 row for (JOB, D1)", len(same) == 1)

    # ── 3. Lock: processing existente → canceled_locked ───────────
    print("\n3) run_job canceled (lock held)")
    with Session(engine) as s:
        # Simular otra instancia en processing para el MISMO job, otra fecha.
        r = create_job_run(s, JOB, D2)
        update_job_run_status(s, r.id, JobRunStatus.PROCESSING.value)
        check("has_processing_lock (job-level)", has_processing_lock(s, JOB) is True)

        executed = {"ran": False}

        def lock_work(sess):
            executed["ran"] = True

        out = run_job(s, JOB, D2, lock_work)
        check("outcome == canceled_locked", out.outcome == "canceled_locked")
        check("work NOT ran", executed["ran"] is False)

    # ── 4. Falla: work lanza → failed + no processing ─────────────
    print("\n4) run_job failure guarantee (NO processing left)")
    JOB2 = "validate_job_fail"
    with Session(engine) as s:

        def bad_work(sess):
            raise RuntimeError("boom simulado")

        try:
            run_job(s, JOB2, D1, bad_work)
            check("exception re-raised", False)
        except RuntimeError as exc:
            check("exception re-raised", "boom simulado" in str(exc))

        matching = [r for r in rows(s) if r.job_name == JOB2]
        check("1 row for failed job", len(matching) == 1)
        failed = matching[0]
        check("status == failed", failed.status == JobRunStatus.FAILED.value)
        check("error_message saved", failed.error_message is not None and "boom simulado" in failed.error_message)
        check("finished_at set", failed.finished_at is not None)
        # Garantía central: ninguna fila del job en processing.
        proc = [r for r in rows(s) if r.job_name == JOB2 and r.status == JobRunStatus.PROCESSING.value]
        check("NO processing rows for failed job", len(proc) == 0)

    # ── 5. Reintento tras fallo con fecha distinta es posible ─────
    print("\n5) retry after failure (different date)")
    with Session(engine) as s:

        def ok2(sess):
            pass

        out = run_job(s, JOB2, D2, ok2)
        check("outcome == executed", out.outcome == "executed")
        check("has_completed_for_date D2", has_completed_for_date(s, JOB2, D2))
        check("failed D1 still present (not completed)", has_completed_for_date(s, JOB2, D1) is False)

    # ── 6. create/update/query API básica ──────────────────────────
    print("\n6) CRUD básico job_runner")
    with Session(engine) as s:
        r = create_job_run(s, "crud_job", D1)
        check("created status == pending", r.status == JobRunStatus.PENDING.value)
        get_back = get_job_run(s, r.id)
        check("get_job_run round-trip", get_back is not None and get_back["id"] == r.id)
        ok = update_job_run_status(s, r.id, JobRunStatus.FAILED.value, error_message="err")
        check("update to failed ok", ok is True)
        get_back2 = get_job_run(s, r.id)
        check("error_message persisted", get_back2["error_message"] == "err")

    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()