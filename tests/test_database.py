from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app import db


def test_postgres_engine_uses_psycopg_and_ssl():
    engine = db._create_postgres_engine("postgresql://localhost/inventory")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.query["sslmode"] == "require"
        assert engine.pool._pre_ping is True
        assert engine.url.password is None
    finally:
        engine.dispose()


def test_postgres_engine_preserves_explicit_ssl_mode():
    engine = db._create_postgres_engine(
        "postgresql://localhost/inventory?sslmode=verify-full"
    )
    try:
        assert engine.url.query["sslmode"] == "verify-full"
    finally:
        engine.dispose()


def test_postgres_engine_rejects_other_backends():
    with pytest.raises(ValueError, match="must be a PostgreSQL"):
        db._create_postgres_engine("sqlite://")


def test_get_db_requires_configuration():
    with patch.object(db, "engine", None):
        with pytest.raises(RuntimeError, match="DATABASE_URL"):
            next(db.get_db())


def test_dependency_creates_and_closes_a_session_per_request():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    sessions = []
    application = FastAPI()

    @application.get("/probe")
    def probe(session: db.DBSession):
        sessions.append(session)
        return {"value": session.exec(text("SELECT 1")).scalar_one()}

    try:
        with patch.object(db, "engine", engine):
            with patch.object(Session, "close", autospec=True) as close:
                with TestClient(application) as client:
                    assert client.get("/probe").json() == {"value": 1}
                    assert client.get("/probe").json() == {"value": 1}
                assert len(sessions) == 2
                assert sessions[0] is not sessions[1]
                assert [call.args[0] for call in close.call_args_list] == sessions
    finally:
        for session in sessions:
            session.close()
        engine.dispose()


def test_get_db_rolls_back_and_closes_on_exception():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE probe (value INTEGER)"))
        with patch.object(db, "engine", engine):
            dependency = db.get_db()
            session = next(dependency)
            session.exec(text("INSERT INTO probe VALUES (1)"))
            with pytest.raises(ValueError, match="request failed"):
                dependency.throw(ValueError("request failed"))
            assert not session.in_transaction()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM probe")).scalar_one() == 0
    finally:
        engine.dispose()