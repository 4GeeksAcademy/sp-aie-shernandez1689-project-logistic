import sys
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import database
from auth import CurrentUser, get_current_user
from main import app
from models import IncidentSummaryResponse


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "operations.json")
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id="test-admin", email="admin@example.com", role="admin",
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def incident_payload():
    return {
        "title": "Lost parcel", "description": "Investigate delivery",
        "category": "lost_parcel", "status": "open", "origin": "customer",
        "branch": "central",
    }


@pytest.fixture
def supplier_payload():
    return {
        "name": "Carrier", "country": "USA", "categories": ["carrier_last_mile"],
        "rate_per_shipment": 2.5, "currency": "USD", "status": "active",
        "contact_email": "carrier@example.com", "notes": "Internal contact notes",
    }


def test_supplier_list_is_minimal_and_delete_is_empty(client, supplier_payload):
    response = client.post("/suppliers", json=supplier_payload)
    assert response.status_code == 201
    supplier_id = response.json()["id"]
    database_handle, table = database.get_suppliers_table()
    try:
        table.update({"hashed_password": "private", "internal_token": "secret"}, doc_ids=[supplier_id])
    finally:
        database_handle.close()
    response = client.get("/suppliers")
    assert response.status_code == 200
    assert set(response.json()[0]) == {
        "id", "name", "country", "categories", "rate_per_shipment", "currency", "status",
    }
    response = client.get(f"/suppliers/{supplier_id}")
    assert response.status_code == 200
    assert response.json()["contact_email"] == supplier_payload["contact_email"]
    assert "hashed_password" not in response.json()
    assert "internal_token" not in response.json()
    assert client.patch(f"/suppliers/{supplier_id}/rate", json={"rate_per_shipment": 3}).status_code == 200
    assert client.patch(f"/suppliers/{supplier_id}/status", json={"status": "suspended"}).status_code == 200
    response = client.delete(f"/suppliers/{supplier_id}")
    assert response.status_code == 204
    assert response.content == b""


@pytest.mark.parametrize("field", ["id", "updated_at", "hashed_password"])
def test_supplier_create_rejects_server_owned_fields(client, supplier_payload, field):
    response = client.post("/suppliers", json={**supplier_payload, field: "forged"})
    assert response.status_code == 422
    assert client.get("/suppliers").json() == []


@pytest.mark.parametrize("suffix, payload", [
    ("rate", {"rate_per_shipment": 3, "name": "forged"}),
    ("status", {"status": "suspended", "contact_email": "forged@example.com"}),
])
def test_supplier_patch_rejects_unwritable_fields(client, supplier_payload, suffix, payload):
    supplier_id = client.post("/suppliers", json=supplier_payload).json()["id"]
    assert client.patch(f"/suppliers/{supplier_id}/{suffix}", json=payload).status_code == 422


def test_incident_list_summary_and_status_contracts(client, incident_payload):
    response = client.post("/api/incidents", json=incident_payload)
    assert response.status_code == 201
    incident_id = response.json()["id"]
    assert set(response.json()) == set(incident_payload) | {"id", "created_at", "updated_at"}
    database_handle, table = database.get_incidents_table()
    try:
        table.update({"hashed_password": "private", "internal_token": "secret"})
    finally:
        database_handle.close()
    response = client.get("/api/incidents")
    assert response.status_code == 200
    assert set(response.json()[0]) == set(incident_payload) | {"id", "created_at"}
    response = client.get("/api/incidents/summary")
    assert response.status_code == 200
    assert set(response.json()) == {"total", "status", "category", "origin", "branch"}
    assert response.json()["total"] == 1
    assert response.json()["status"] == {"open": 1, "in_progress": 0, "resolved": 0, "discarded": 0}
    response = client.get(f"/api/incidents/{incident_id}")
    assert response.status_code == 200
    assert "hashed_password" not in response.json()
    assert "internal_token" not in response.json()
    assert client.patch(f"/api/incidents/{incident_id}/status", json={"status": "in_progress"}).status_code == 200
    assert client.get("/api/incidents?status=open").json() == []
    assert client.get("/api/incidents/summary").json()["status"]["in_progress"] == 1


@pytest.mark.parametrize("field", ["id", "created_at", "updated_at", "hashed_password"])
def test_incident_create_rejects_server_owned_fields(client, incident_payload, field):
    assert client.post("/api/incidents", json={**incident_payload, field: "forged"}).status_code == 422
    assert client.get("/api/incidents").json() == []


def test_incident_status_rejects_other_fields(client, incident_payload):
    incident_id = client.post("/api/incidents", json=incident_payload).json()["id"]
    assert client.patch(f"/api/incidents/{incident_id}/status", json={
        "status": "in_progress", "title": "forged",
    }).status_code == 422


def test_summary_rejects_negative_counts(client):
    summary = client.get("/api/incidents/summary").json()
    assert summary["total"] == 0
    summary["status"]["open"] = -1
    with pytest.raises(ValidationError):
        IncidentSummaryResponse.model_validate(summary)


def test_operations_openapi_uses_typed_inputs_and_outputs(client):
    schema = client.get("/openapi.json").json()
    assert schema["paths"]["/api/incidents"]["post"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/IncidentCreate",
    }
    assert schema["paths"]["/api/incidents/summary"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/IncidentSummaryResponse",
    }
    for route in app.routes:
        if isinstance(route, APIRoute):
            if route.status_code == 204:
                assert route.response_model is None
            else:
                assert route.response_model is not None