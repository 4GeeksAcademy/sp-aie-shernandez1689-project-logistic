"""Tests del endpoint stub de telemetría (Fase 1).

Verifican el contrato del envelope y el comportamiento del endpoint
temporal POST /telemetry/events sin tocar datos de producción.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import models
from main import app
from models import TelemetryBatch, TelemetryEvent


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def build_event(overrides: dict | None = None) -> dict:
    event = {
        "eventId": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "timestamp": "2026-10-16T14:30:00.000Z",
        "sessionId": "sess_abc123",
        "userId": "usr_456",
        "event_type": "inbound_order_created",
        "schemaVersion": "1.0.0",
        "requestId": "req_789",
        "properties": {
            "warehouse": "los_angeles",
            "client_id": "client_001",
            "product_id": "SKU-001",
            "product_category": "electronics",
            "quantity": 500,
            "reference": "PO-2026-001",
        },
    }
    if overrides:
        event.update(overrides)
    return event


def test_telemetry_endpoint_accepts_batch_and_returns_received(client):
    payload = {"events": [build_event(), build_event({"event_type": "login_succeeded"})]}
    response = client.post("/telemetry/events", json=payload)
    assert response.status_code == 200
    assert response.json() == {"received": 2}


def test_telemetry_endpoint_logs_event_types_and_count(client, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        payload = {"events": [build_event(), build_event({"event_type": "login_succeeded"})]}
        response = client.post("/telemetry/events", json=payload)
        assert response.status_code == 200

    log_entries = [record.message for record in caplog.records]
    assert any(
        "Telemetry batch received: 2 event(s)" in message
        and "inbound_order_created" in message
        and "login_succeeded" in message
        for message in log_entries
    )


def test_telemetry_batch_empty_events_rejected(client):
    response = client.post("/telemetry/events", json={"events": []})
    assert response.status_code == 422


def test_telemetry_batch_missing_events_rejected(client):
    response = client.post("/telemetry/events", json={})
    assert response.status_code == 422


def test_telemetry_event_missing_envelope_field_rejected(client):
    event = build_event()
    del event["eventId"]
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 422


def test_telemetry_event_invalid_schema_version_rejected(client):
    event = build_event({"schemaVersion": "1.0"})
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 422


def test_telemetry_event_invalid_event_type_rejected(client):
    event = build_event({"event_type": "CreatedOrder"})
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 422


def test_telemetry_event_extra_field_rejected(client):
    event = build_event({"unexpected": "value"})
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 422


def test_telemetry_model_roundtrip():
    event = TelemetryEvent(**build_event())
    payload = TelemetryBatch(events=[event])
    assert payload.events[0].eventId == event.eventId
    assert payload.events[0].properties["warehouse"] == "los_angeles"


def test_telemetry_model_rejects_forged_fields():
    with pytest.raises(ValidationError):
        TelemetryEvent(**build_event({"server_field": "forged"}))