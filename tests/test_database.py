from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app import db
from app.main import app
from app.models import SKU, StockEntry, StockExit


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


@pytest.fixture
def inventory_engine():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
    try:
        with patch.object(db, "engine", engine):
            with TestClient(app):
                yield engine
    finally:
        engine.dispose()


def test_startup_creates_only_inventory_tables(inventory_engine):
    inspector = inspect(inventory_engine)
    assert set(inspector.get_table_names()) == {"sku", "stockentry", "stockexit"}
    assert "current_stock" not in SKU.__table__.columns
    for model in (StockEntry, StockExit):
        foreign_keys = inspector.get_foreign_keys(model.__tablename__)
        assert len(foreign_keys) == 1
        assert foreign_keys[0]["constrained_columns"] == ["sku_id"]
        assert foreign_keys[0]["referred_table"] == "sku"
        assert foreign_keys[0]["referred_columns"] == ["id"]
        assert not model.__table__.columns.user_uuid.foreign_keys
        assert model.__table__.columns.created_at.type.timezone is True


def test_inventory_models_persist_required_fields(inventory_engine):
    with Session(inventory_engine) as session:
        product = SKU(
            name="Wireless headphones", sku="TEC-EAR-001", client_name="SoundWave",
            category="electronics", warehouse="LA",
        )
        session.add(product)
        session.commit()
        session.refresh(product)
        assert isinstance(product.id, int)
        entry = StockEntry(
            sku_id=product.id, quantity=10, reference="PO-2024-0098",
            warehouse="LA", user_uuid="tinydb-operator",
        )
        dispatch = StockExit(
            sku_id=product.id, quantity=2, exit_type="dispatch",
            tracking_number="1Z999AA10123456784", warehouse="LA",
            user_uuid="tinydb-coordinator",
        )
        loss = StockExit(
            sku_id=product.id, quantity=1, exit_type="loss",
            warehouse="LA", user_uuid="tinydb-coordinator",
        )
        session.add_all([entry, dispatch, loss])
        session.commit()
        for movement in (entry, dispatch, loss):
            session.refresh(movement)
            assert isinstance(movement.id, int)
            assert movement.created_at is not None
            assert movement.sku_id == product.id
        assert entry.reference == "PO-2024-0098"
        assert loss.tracking_number is None


@pytest.mark.parametrize("model", [StockEntry, StockExit])
def test_stock_movements_require_an_existing_sku(inventory_engine, model):
    fields = {"reference": "PO-2024-0098"} if model is StockEntry else {"exit_type": "loss"}
    with Session(inventory_engine) as session:
        session.add(model(sku_id=999, quantity=1, warehouse="ZGZ", user_uuid="tinydb-user", **fields))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "exit_type, tracking_number",
    [("dispatch", None), ("dispatch", " "), ("loss", "TRACK-001"), ("invalid", None)],
)
def test_stock_exit_rejects_invalid_tracking(inventory_engine, exit_type, tracking_number):
    with Session(inventory_engine) as session:
        product = SKU(name="Serum", sku="CSM-001", client_name="GlowLab", category="cosmetics", warehouse="ZGZ")
        session.add(product)
        session.commit()
        session.refresh(product)
        session.add(StockExit(
            sku_id=product.id, quantity=1, exit_type=exit_type,
            tracking_number=tracking_number, warehouse="ZGZ", user_uuid="tinydb-user",
        ))
        with pytest.raises(IntegrityError):
            session.commit()


def test_startup_skips_sql_without_configuration():
    with patch.object(db, "engine", None):
        with patch.object(SQLModel.metadata, "create_all") as create_all:
            with TestClient(app) as client:
                assert client.get("/health").status_code == 200
            create_all.assert_not_called()


def test_startup_propagates_schema_initialization_failure():
    with patch.object(db, "engine", object()):
        with patch.object(SQLModel.metadata, "create_all", side_effect=RuntimeError("schema unavailable")):
            with pytest.raises(RuntimeError, match="schema unavailable"):
                with TestClient(app):
                    pass