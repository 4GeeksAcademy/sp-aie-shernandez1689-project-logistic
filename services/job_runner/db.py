"""Database engine and session factory for job_runner.

Sigue el mismo patrón que ``services/api/db_telemetry.py``: crea un engine
SQLAlchemy/SQLModel a partir de ``DATABASE_URL`` (PostgreSQL vía Supabase)
y expone la dependencia ``JobRunnerDBSession`` y la función ``get_db()``.

Uso:

    from services.job_runner.db import get_db, init_db

    # Al arrancar la aplicación:
    init_db()

    # En cada request/worker:
    with get_db() as session:
        run = create_job_run(session, pipeline="mi_pipeline")
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

# Cargar .env desde services/api/ (compartido con el resto de servicios)
API_DIR = Path(__file__).resolve().parents[1] / "api"
load_dotenv(API_DIR / ".env")

logger = logging.getLogger(__name__)

_engine = None


def _create_postgres_engine(database_url: str):
    """Crea un engine PostgreSQL con SSL requerido por defecto.

    Idéntico al de ``db_telemetry.py`` para mantener consistencia.
    """
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
    url = url.set(drivername="postgresql+psycopg")
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True)


def get_engine():
    """Devuelve el engine global, creándolo si es necesario."""
    global _engine
    if _engine is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL is not configured. "
                "Set a valid Supabase PostgreSQL connection string in the .env file."
            )
        _engine = _create_postgres_engine(database_url)
    return _engine


def init_db():
    """Crea la tabla ``job_runs`` si no existe (migración automática).

    Llámalo una vez al arrancar la aplicación (lifespan de FastAPI,
    worker de Prefect, script independiente).
    """
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    logger.info("job_runs table initialized (if not exists)")


def get_db() -> Generator[Session, None, None]:
    """Generador de sesiones SQLModel con rollback automático en fallo.

    Cada llamada produce una sesión independiente; al salir del contexto
    se cierra (y se hace rollback si la transacción sigue activa).
    """
    engine = get_engine()
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise


JobRunnerDBSession = Annotated[Session, Depends(get_db)]