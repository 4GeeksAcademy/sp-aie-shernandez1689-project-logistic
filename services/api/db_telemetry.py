"""Database engine and session for telemetry persistence.

Creates a SQLAlchemy/SQLModel engine from DATABASE_URL (PostgreSQL via
Supabase) and exposes a get_telemetry_db dependency for FastAPI routes.
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
from sqlmodel import Session, create_engine


API_DIR = Path(__file__).resolve().parent
load_dotenv(API_DIR / ".env")

logger = logging.getLogger(__name__)


def _create_postgres_engine(database_url: str):
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
    url = url.set(drivername="postgresql+psycopg")
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True)


database_url = os.getenv("DATABASE_URL")
engine = _create_postgres_engine(database_url) if database_url else None


def get_telemetry_db() -> Generator[Session, None, None]:
    if engine is None:
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Set a valid Supabase PostgreSQL connection string in the .env file."
        )
    with Session(engine) as session:
        yield session


TelemetryDBSession = Annotated[Session, Depends(get_telemetry_db)]