from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from app import db
from app.main import app
from app.models import SKU, StockEntry, StockExit
from app.routers.inventory import _get_sku
from app.security import create_access_token
from app.schemas import (
    SKUCreateRequest,
    SKUResponse,
    StockEntryCreateRequest,
    StockEntryResponse,
    StockExitCreateRequest,
    StockExitResponse,
)


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


@pytest.mark.parametrize("schema", [
    SKUCreateRequest, SKUResponse, StockEntryCreateRequest,
    StockEntryResponse, StockExitCreateRequest, StockExitResponse,
])
def test_inventory_schemas_are_independent_pydantic_models(schema):
    assert issubclass(schema, BaseModel)
    assert not issubclass(schema, SQLModel)
    assert schema.__module__ == "app.schemas"
    assert set(SQLModel.metadata.tables) == {"sku", "stockentry", "stockexit"}


def test_sku_response_requires_calculated_stock(inventory_engine):
    product = SKU(
        id=1, name="Serum", sku="CSM-001", client_name="GlowLab",
        category="cosmetics", warehouse="ZGZ",
    )
    with pytest.raises(ValidationError):
        SKUResponse.model_validate(product)
    response = SKUResponse.model_validate({**product.model_dump(), "current_stock": 7})
    assert response.model_dump()["current_stock"] == 7
    assert "current_stock" not in SKU.__table__.columns
    with pytest.raises(ValidationError):
        SKUCreateRequest.model_validate(response.model_dump())


@pytest.mark.parametrize("model, schema, fields", [
    (StockEntry, StockEntryResponse, {"reference": "PO-2024-0098"}),
    (StockExit, StockExitResponse, {"exit_type": "dispatch", "tracking_number": "TRACK-001"}),
    (StockExit, StockExitResponse, {"exit_type": "loss"}),
])
def test_movement_schemas_serialize_orm_attributes(model, schema, fields):
    movement = model(id=1, sku_id=1, quantity=2, warehouse="LA", user_uuid="tinydb-user", **fields)
    response = schema.model_validate(movement)
    payload = response.model_dump(mode="json")
    assert payload["sku_id"] == 1
    assert payload["quantity"] == 2
    assert payload["warehouse"] == "LA"
    assert payload["user_uuid"] == "tinydb-user"
    assert isinstance(payload["created_at"], str)


@pytest.mark.parametrize("schema, fields", [
    (StockEntryCreateRequest, {"reference": "PO-2024-0098"}),
    (StockExitCreateRequest, {"exit_type": "loss"}),
])
@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, "2"])
def test_movement_requests_require_positive_integer_quantities(schema, fields, quantity):
    with pytest.raises(ValidationError):
        schema(sku_id=1, quantity=quantity, warehouse="LA", **fields)


@pytest.mark.parametrize("field, value", [
    ("id", 10), ("user_uuid", "forged-user"), ("created_at", "2026-10-14T00:00:00Z"),
])
@pytest.mark.parametrize("schema, fields", [
    (StockEntryCreateRequest, {"reference": "PO-2024-0098"}),
    (StockExitCreateRequest, {"exit_type": "loss"}),
])
def test_movement_requests_reject_server_owned_fields(schema, fields, field, value):
    with pytest.raises(ValidationError):
        schema(sku_id=1, quantity=1, warehouse="LA", **fields, **{field: value})


@pytest.mark.parametrize("exit_type, tracking_number", [
    ("dispatch", None), ("dispatch", " "), ("loss", "TRACK-001"), ("invalid", None),
])
def test_exit_request_validates_tracking_before_persistence(exit_type, tracking_number):
    with pytest.raises(ValidationError):
        StockExitCreateRequest(
            sku_id=1, quantity=1, warehouse="ZGZ",
            exit_type=exit_type, tracking_number=tracking_number,
        )


@pytest.mark.parametrize("field, value", [
    ("name", " "), ("sku", ""), ("client_name", " "),
    ("category", "other"), ("warehouse", "NYC"), ("current_stock", 5),
])
def test_sku_request_validates_context_fields(field, value):
    payload = {
        "name": "Serum", "sku": "CSM-001", "client_name": "GlowLab",
        "category": "cosmetics", "warehouse": "ZGZ",
    }
    with pytest.raises(ValidationError):
        SKUCreateRequest.model_validate({**payload, field: value})


def test_inventory_requests_accept_valid_context_payloads():
    product = SKUCreateRequest(
        name=" Serum ", sku="CSM-001", client_name="GlowLab",
        category="cosmetics", warehouse="ZGZ",
    )
    assert product.name == "Serum"
    entry = StockEntryCreateRequest(sku_id=1, quantity=10, reference="PO-2024-0098", warehouse="ZGZ")
    assert entry.quantity == 10
    dispatch = StockExitCreateRequest(
        sku_id=1, quantity=2, exit_type="dispatch", tracking_number=" TRACK-001 ", warehouse="LA",
    )
    assert dispatch.tracking_number == "TRACK-001"
    loss = StockExitCreateRequest(sku_id=1, quantity=1, exit_type="loss", warehouse="LA")
    assert loss.tracking_number is None


@pytest.mark.parametrize("method, path, payload", [
    ("GET", "/inventory/products", None),
    ("GET", "/inventory/products/1", None),
    ("GET", "/inventory/orders", None),
    ("POST", "/inventory/products", {}),
    ("POST", "/inventory/orders/inbound", {}),
    ("POST", "/inventory/orders/outbound", {}),
])
def test_inventory_requires_authentication(inventory_engine, client, method, path, payload):
    response = client.request(method, path, json=payload)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    with Session(inventory_engine) as session:
        for table in ("sku", "stockentry", "stockexit"):
            assert session.exec(text(f"SELECT COUNT(*) FROM {table}")).scalar_one() == 0


def test_inventory_end_to_end(inventory_engine, client, auth_headers, user):
    product = client.post("/inventory/products", headers=auth_headers, json={
        "name": "Serum", "sku": "CSM-001", "client_name": "GlowLab",
        "category": "cosmetics", "warehouse": "ZGZ",
    })
    assert product.status_code == 201
    sku_id = product.json()["id"]
    assert product.json()["current_stock"] == 0
    for quantity in (10, 5):
        entry = client.post("/inventory/orders/inbound", headers=auth_headers, json={
            "sku_id": sku_id, "quantity": quantity, "reference": "PO-2024-0098",
            "warehouse": "ZGZ",
        })
        assert entry.status_code == 201
        assert entry.json()["user_uuid"] == user["id"]
        assert entry.json()["created_at"]
    dispatch = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": sku_id, "quantity": 4, "exit_type": "dispatch",
        "tracking_number": "TRACK-001", "warehouse": "ZGZ",
    })
    assert dispatch.status_code == 201
    loss = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": sku_id, "quantity": 1, "exit_type": "loss", "warehouse": "ZGZ",
    })
    assert loss.status_code == 201
    assert loss.json()["tracking_number"] is None
    detail = client.get(f"/inventory/products/{sku_id}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["current_stock"] == 10
    products = client.get("/inventory/products", headers=auth_headers)
    assert products.status_code == 200
    assert products.json() == [detail.json()]
    orders = client.get("/inventory/orders", headers=auth_headers)
    assert orders.status_code == 200
    assert len(orders.json()) == 4
    assert [order["order_type"] for order in orders.json()] == ["inbound", "inbound", "outbound", "outbound"]
    for order in orders.json():
        assert order["user_uuid"] == user["id"]
        assert order["product"] == detail.json()


def test_inventory_rejects_insufficient_stock_without_writing(inventory_engine, client, auth_headers):
    with Session(inventory_engine) as session:
        product = SKU(id=1, name="Serum", sku="CSM-001", client_name="GlowLab", category="cosmetics", warehouse="ZGZ")
        session.add(product)
        session.add(StockEntry(sku_id=1, quantity=3, reference="PO-001", warehouse="ZGZ", user_uuid="tinydb-user"))
        session.commit()
    response = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": 1, "quantity": 4, "exit_type": "loss", "warehouse": "ZGZ",
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient stock for SKU 'CSM-001'. Available: 3, requested: 4."
    with Session(inventory_engine) as session:
        assert session.exec(text("SELECT COUNT(*) FROM stockexit")).scalar_one() == 0
    response = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": 1, "quantity": 3, "exit_type": "loss", "warehouse": "ZGZ",
    })
    assert response.status_code == 201
    assert client.get("/inventory/products/1", headers=auth_headers).json()["current_stock"] == 0


def test_inventory_stock_is_filtered_by_warehouse(inventory_engine, client, auth_headers):
    with Session(inventory_engine) as session:
        session.add_all([
            SKU(id=1, name="Serum", sku="CSM-LA", client_name="GlowLab", category="cosmetics", warehouse="LA"),
            SKU(id=2, name="Serum", sku="CSM-ZGZ", client_name="GlowLab", category="cosmetics", warehouse="ZGZ"),
        ])
        session.commit()
        session.add_all([
            StockEntry(sku_id=1, quantity=20, reference="PO-LA", warehouse="LA", user_uuid="tinydb-user"),
            StockEntry(sku_id=1, quantity=99, reference="PO-OTHER", warehouse="ZGZ", user_uuid="tinydb-user"),
            StockEntry(sku_id=2, quantity=15, reference="PO-ZGZ", warehouse="ZGZ", user_uuid="tinydb-user"),
            StockExit(sku_id=1, quantity=3, exit_type="loss", warehouse="LA", user_uuid="tinydb-user"),
            StockExit(sku_id=1, quantity=10, exit_type="loss", warehouse="ZGZ", user_uuid="tinydb-user"),
        ])
        session.commit()
    response = client.get("/inventory/products", headers=auth_headers)
    assert response.status_code == 200
    assert [product["current_stock"] for product in response.json()] == [17, 15]
    response = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": 1, "quantity": 18, "exit_type": "loss", "warehouse": "LA",
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient stock for SKU 'CSM-LA'. Available: 17, requested: 18."
    with Session(inventory_engine) as session:
        assert session.exec(text("SELECT COUNT(*) FROM stockexit")).scalar_one() == 2


@pytest.mark.parametrize("path, fields", [
    ("inbound", {"reference": "PO-001"}), ("outbound", {"exit_type": "loss"}),
])
def test_inventory_rejects_missing_sku_and_wrong_warehouse(inventory_engine, client, auth_headers, path, fields):
    payload = {"sku_id": 1, "quantity": 1, "warehouse": "ZGZ", **fields}
    response = client.post(f"/inventory/orders/{path}", headers=auth_headers, json=payload)
    assert response.status_code == 404
    with Session(inventory_engine) as session:
        session.add(SKU(id=1, name="Serum", sku="CSM-001", client_name="GlowLab", category="cosmetics", warehouse="LA"))
        session.commit()
    response = client.post(f"/inventory/orders/{path}", headers=auth_headers, json=payload)
    assert response.status_code == 400
    assert client.get("/inventory/orders", headers=auth_headers).json() == []


def test_inventory_missing_product_returns_404(inventory_engine, client, auth_headers):
    assert client.get("/inventory/products/999", headers=auth_headers).status_code == 404
    assert client.get("/inventory/products", headers=auth_headers).json() == []
    assert client.get("/inventory/orders", headers=auth_headers).json() == []


@pytest.mark.parametrize("fields", [
    {"exit_type": "dispatch"},
    {"exit_type": "loss", "tracking_number": "TRACK-001"},
    {"exit_type": "loss", "quantity": 0},
    {"exit_type": "loss", "user_uuid": "forged-user"},
])
def test_inventory_validates_outbound_requests(inventory_engine, client, auth_headers, fields):
    response = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": 1, "quantity": 1, "warehouse": "LA", **fields,
    })
    assert response.status_code == 422
    assert client.get("/inventory/orders", headers=auth_headers).json() == []


def test_inventory_lock_query_uses_postgres_for_update(inventory_engine):
    with Session(inventory_engine) as session:
        session.add(SKU(id=1, name="Serum", sku="CSM-001", client_name="GlowLab", category="cosmetics", warehouse="LA"))
        session.commit()
        with patch.object(session, "exec", wraps=session.exec) as execute:
            assert _get_sku(session, 1, lock=True).id == 1
        statement = execute.call_args.args[0]
        assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))


def test_inventory_stock_cannot_be_set_at_creation(inventory_engine, client, auth_headers):
    payload = {
        "name": "Serum", "sku": "CSM-001", "client_name": "GlowLab",
        "category": "cosmetics", "warehouse": "ZGZ",
    }
    response = client.post("/inventory/products", headers=auth_headers, json={
        **payload, "current_stock": 100,
    })
    assert response.status_code == 422
    assert client.get("/inventory/products", headers=auth_headers).json() == []
    product = client.post("/inventory/products", headers=auth_headers, json=payload)
    assert product.status_code == 201
    assert product.json()["current_stock"] == 0
    assert client.get("/inventory/orders", headers=auth_headers).json() == []
    sku_id = product.json()["id"]
    response = client.post("/inventory/orders/outbound", headers=auth_headers, json={
        "sku_id": sku_id, "quantity": 1, "exit_type": "loss", "warehouse": "ZGZ",
    })
    assert response.status_code == 400
    assert response.json()["detail"] == "Insufficient stock for SKU 'CSM-001'. Available: 0, requested: 1."
    with Session(inventory_engine) as session:
        assert "current_stock" not in {column["name"] for column in inspect(inventory_engine).get_columns("sku")}
        assert session.exec(text("SELECT COUNT(*) FROM stockentry")).scalar_one() == 0
        assert session.exec(text("SELECT COUNT(*) FROM stockexit")).scalar_one() == 0


def test_inventory_persists_each_authenticated_users_uuid(
    inventory_engine, client, auth_headers, user, make_user,
):
    product = client.post("/inventory/products", headers=auth_headers, json={
        "name": "Serum", "sku": "CSM-001", "client_name": "GlowLab",
        "category": "cosmetics", "warehouse": "ZGZ",
    })
    assert product.status_code == 201
    sku_id = product.json()["id"]
    inbound = client.post("/inventory/orders/inbound", headers=auth_headers, json={
        "sku_id": sku_id, "quantity": 5, "reference": "PO-001", "warehouse": "ZGZ",
    })
    assert inbound.status_code == 201
    operator = make_user(email="operator@example.com")
    token = create_access_token(operator["id"], operator["email"], operator["role"])
    operator_headers = {"Authorization": f"Bearer {token}"}
    outbound = client.post("/inventory/orders/outbound", headers=operator_headers, json={
        "sku_id": sku_id, "quantity": 2, "exit_type": "loss", "warehouse": "ZGZ",
    })
    assert outbound.status_code == 201
    with Session(inventory_engine) as session:
        assert session.get(StockEntry, inbound.json()["id"]).user_uuid == user["id"]
        assert session.get(StockExit, outbound.json()["id"]).user_uuid == operator["id"]
    assert client.get(f"/inventory/products/{sku_id}", headers=operator_headers).json()["current_stock"] == 3


@pytest.mark.parametrize("path, fields", [
    ("inbound", {"reference": "PO-001"}), ("outbound", {"exit_type": "loss"}),
])
def test_inventory_rejects_forged_user_uuid(inventory_engine, client, auth_headers, path, fields):
    response = client.post(f"/inventory/orders/{path}", headers=auth_headers, json={
        "sku_id": 1, "quantity": 1, "warehouse": "LA",
        "user_uuid": "forged-user", **fields,
    })
    assert response.status_code == 422
    assert client.get("/inventory/orders", headers=auth_headers).json() == []