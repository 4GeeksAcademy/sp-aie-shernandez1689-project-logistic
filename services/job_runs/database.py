"""Conexión a base de datos para el módulo job_runs.

Reutiliza la misma conexión PostgreSQL que el resto del monorepo.
Sigue el patrón de ``services/api/db_telemetry.py`` y
``services/user-api/app/db.py``.
"""

from __future__ import annotations

import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

# ── Engine compartido (singleton per process) ──────────────────────

_engine = None


def get_engine():
    """Obtiene o crea el engine de base de datos.

    Lee ``DATABASE_URL`` del entorno. Recarga .env si está disponible.
    """
    global _engine
    if _engine is not None:
        return _engine

    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Set the DATABASE_URL environment variable."
        )

    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")

    url = url.set(drivername="postgresql+psycopg")
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})

    _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def get_session() -> Generator[Session, None, None]:
    """Generador de sesiones SQLModel (compatible con FastAPI Depends)."""
    with Session(get_engine()) as session:
        yield session