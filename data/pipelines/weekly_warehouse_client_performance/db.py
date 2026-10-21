"""Acceso a datos del pipeline de desempeño semanal.

Resuelve el motor de SQLAlchemy hacia Supabase/PostgreSQL desde la variable
de entorno ``DATABASE_URL`` (mismo patrón que ``services/api/db_telemetry.py``:
``postgresql+psycopg`` con ``sslmode=require`` por defecto).

Fallback local (modo desarrollo/validación sin DB):
Cuando ``DATABASE_URL`` no está configurada, el pipeline opera en **modo
local**:
- la extracción lee eventos sintéticos desde ``data/raw/`` (JSON Lines),
- la carga escribe el consolidado y el log de corridas en ``data/eval/``.

Este fallback permite ejecutar y validar el pipeline de punta a punta sin
necesitar credenciales, pero **no** sustituye al contrato de producción: si
``DATABASE_URL`` existe, la extracción y la carga usan exclusivamente SQL
contra ``telemetry_events`` (solo lectura) y ``reporting.*``.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from sqlalchemy.engine import Engine, create_engine, make_url


# Mismo comportamiento que services/api/db_telemetry.py: cargar .env desde la
# raíz del monorepo si existe (para desarrollo) y leer DATABASE_URL.
REPO_ROOT = Path(__file__).resolve().parents[3]

for _env_candidate in (REPO_ROOT / ".env", REPO_ROOT / "services" / "api" / ".env"):
    if _env_candidate.exists():
        load_dotenv(_env_candidate)

# Datos locales (modo sin-DB)
RAW_EVENTS_PATH = REPO_ROOT / "data" / "raw" / "weekly_warehouse_client_performance"
EVAL_PATH = REPO_ROOT / "data" / "eval" / "weekly_warehouse_client_performance"
RUNS_EVAL_PATH = REPO_ROOT / "data" / "eval" / "weekly_warehouse_client_performance" / "pipeline_runs.jsonl"


def _create_postgres_engine(database_url: str) -> Engine:
    """Crea un engine PostgreSQL a partir de una URL de conexión."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
    url = url.set(drivername="postgresql+psycopg")
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine | None:
    """Devuelve el engine a Supabase si ``DATABASE_URL`` está configurada.

    Retorna ``None`` en modo local (sin base de datos).
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None
    return _create_postgres_engine(database_url)


def is_db_available() -> bool:
    """True si el pipeline puede operar en modo base de datos."""
    return bool(os.getenv("DATABASE_URL"))


@contextmanager
def session_scope() -> Iterator[Any]:
    """Context manager de sesión: ``with session_scope() as session: ...``.

    En modo DB abre una sesión SQLAlchemy real; en modo local produce una
    sesión nula (``None``) para que las funciones de E/T/L puedan degradar
    a la lectura/escritura de archivos locales.
    """
    engine = get_engine()
    if engine is None:
        yield None
        return
    from sqlmodel import Session

    with Session(engine) as session:
        yield session


# ── Modo local: rutas de archivos ───────────────────────────────────────────


def ensure_local_dirs() -> None:
    """Crea los directorios locales de entrada/salida si no existen."""
    RAW_EVENTS_PATH.mkdir(parents=True, exist_ok=True)
    EVAL_PATH.mkdir(parents=True, exist_ok=True)


def raw_events_file() -> Path:
    ensure_local_dirs()
    return RAW_EVENTS_PATH / "events.jsonl"


def eval_kpis_file() -> Path:
    ensure_local_dirs()
    return EVAL_PATH / "weekly_kpis.jsonl"


def eval_runs_file() -> Path:
    ensure_local_dirs()
    return RUNS_EVAL_PATH

def eval_kpis_dir() -> Path:
    """Directorio para KPIs de evaluación (modo local)."""
    ensure_local_dirs()
    return EVAL_PATH
