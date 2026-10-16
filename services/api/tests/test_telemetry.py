"""Tests del endpoint de telemetría (Fase 2 — validación parcial).

Verifican que el endpoint real ``POST /telemetry/events``:
- Acepta lotes de eventos y devuelve ``{received, stored, rejected}``
- Rechaza individualmente eventos inválidos sin cancelar el lote
- Rechaza lotes vacíos o sin campo ``events`` con 422
- Mantiene compatibilidad con ``TelemetryEvent`` sin modificaciones
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


def test_telemetry_endpoint_accepts_batch_and_returns_stats(client):
    """Lote de eventos válidos → 200 con received=stored, rejected=0."""
    payload = {"events": [build_event(), build_event({"event_type": "login_succeeded"})]}
    response = client.post("/telemetry/events", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body == {"received": 2, "stored": 2, "rejected": 0}


def test_telemetry_endpoint_logs_event_types_and_count(client, caplog):
    import logging

    with caplog.at_level(logging.INFO):
        payload = {"events": [build_event(), build_event({"event_type": "login_succeeded"})]}
        response = client.post("/telemetry/events", json=payload)
        assert response.status_code == 200

    log_entries = [record.message for record in caplog.records]
    # El log debe incluir received, stored, rejected y los event_types
    assert any(
        "Telemetry batch received: 2 event(s)" in message
        and "stored: 2" in message
        and "rejected: 0" in message
        and "inbound_order_created" in message
        and "login_succeeded" in message
        for message in log_entries
    )


def test_telemetry_batch_empty_events_rejected(client):
    """Lista vacía → 422 (min_length=1)."""
    response = client.post("/telemetry/events", json={"events": []})
    assert response.status_code == 422


def test_telemetry_batch_missing_events_rejected(client):
    """Falta el campo events → 422 (required)."""
    response = client.post("/telemetry/events", json={})
    assert response.status_code == 422


def test_telemetry_batch_extra_envelope_field_rejected(client):
    """Campo extra en el envelope → 422 (extra='forbid')."""
    response = client.post(
        "/telemetry/events",
        json={"events": [build_event()], "unexpected": "value"},
    )
    assert response.status_code == 422


def test_telemetry_event_missing_envelope_field_individually_rejected(client):
    """Evento sin eventId → se rechaza individualmente (200, rejected=1)."""
    event = build_event()
    del event["eventId"]
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["stored"] == 0
    assert body["rejected"] == 1


def test_telemetry_event_invalid_schema_version_individually_rejected(client):
    """Evento con schemaVersion inválido → rechazo individual, no 422 global."""
    event = build_event({"schemaVersion": "1.0"})
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["stored"] == 0
    assert body["rejected"] == 1


def test_telemetry_event_invalid_event_type_individually_rejected(client):
    """Evento con event_type inválido → rechazo individual."""
    event = build_event({"event_type": "CreatedOrder"})
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["stored"] == 0
    assert body["rejected"] == 1


def test_telemetry_event_extra_field_individually_rejected(client):
    """Evento con campo extra → rechazo individual (extra='forbid')."""
    event = build_event({"unexpected": "value"})
    response = client.post("/telemetry/events", json={"events": [event]})
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 1
    assert body["stored"] == 0
    assert body["rejected"] == 1


def test_telemetry_mixed_batch_partial_rejection(client):
    """Lote mixto: eventos válidos se almacenan, inválidos se rechazan."""
    payload = {
        "events": [
            build_event(),                                                  # válido
            build_event({"event_type": "login_succeeded"}),                  # válido
            build_event({"schemaVersion": "not-semver"}),                   # inválido
            build_event({"event_type": "page_navigated", "properties": {    # válido
                "page": "/dashboard",
                "section": "inventory",
                "warehouse": "los_angeles",
                "previous_page": "/login",
            }}),
            build_event({"event_type": "CreatedOrder"}),                    # inválido (PascalCase)
        ]
    }
    response = client.post("/telemetry/events", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["received"] == 5
    assert body["rejected"] == 2  # invalid schemaVersion + invalid event_type
    assert body["stored"] == 3    # 5 - 2


def test_telemetry_model_roundtrip():
    """TelemetryEvent + TelemetryBatch siguen funcionando sin cambios."""
    event = TelemetryEvent(**build_event())
    payload = TelemetryBatch(events=[event])
    assert payload.events[0].eventId == event.eventId
    assert payload.events[0].properties["warehouse"] == "los_angeles"


def test_telemetry_model_rejects_forged_fields():
    """TelemetryEvent sigue rechazando campos extra (sin cambios)."""
    with pytest.raises(ValidationError):
        TelemetryEvent(**build_event({"server_field": "forged"}))