"""Tests de la tool de consulta de tickets de soporte (incident_lookup).

Cobertura del contrato:
- Entrada tipada: `IncidentQuery` valida/rechaza tickets y filtros.
- Salida tipada: `IncidentLookupResult` replica los campos que expone la
  API de incidencias (status, category, origin, branch, created_at...).
- Solo lectura: la tool nunca escribe en el gestor (test explícito).
- Lee del gestor REAL de services/api (misma tabla TinyDB que sirve
  GET /api/incidents) — no de un dataset paralelo.
- Timeout numérico explícito y fallback honesto (nunca inventa estado).
- Ticket inexistente -> fallback honesto.

Corre:  uv run pytest tests/pipelines/test_incident_lookup.py -q
(o con el intérprete que tenga el entorno del monorepo instalado).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.rag_agent.tools.incident_lookup import (
    INCIDENT_TIMEOUT_SECONDS,
    IncidentLookupResult,
    IncidentQuery,
    lookup_incidents,
    reset_reader,
)

# services/api está en sys.path (pytest pythonpath); `database` es el módulo
# del gestor de incidencias (get_incidents_table es la MISMA función que usa
# GET /api/incidents). `models.Incident` es el modelo que usa el POST
# /api/incidents para generar id/created_at/updated_at.
import database  # noqa: E402
from models import Incident  # noqa: E402


@pytest.fixture
def incident_table(tmp_path, monkeypatch):
    """Tabla TinyDB real del gestor de incidencias (en tmp_path).

    Reutiliza database.get_incidents_table() — la MISMA función que usa el
    endpoint GET /api/incidents — con datos insertados directamente en la
    tabla. No es un dataset falso paralelo.
    """
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "operations.json")
    seed = [
        {
            "title": "Paquete perdido en LA Warehouse",
            "description": "El paquete #12345 no aparece en inventario.",
            "category": "lost_parcel",
            "status": "open",
            "origin": "branch",
            "branch": "la_warehouse",
        },
        {
            "title": "Devolución procesada",
            "description": "Cliente reportó devolución hace 10 días.",
            "category": "returns_issue",
            "status": "resolved",
            "origin": "customer",
            "branch": "central",
        },
        {
            "title": "Fallo de sistema WMS",
            "description": "WMS caído durante 2 horas.",
            "category": "system_failure",
            "status": "in_progress",
            "origin": "internal",
            "branch": "zaragoza_warehouse",
        },
        {
            "title": "Reclamación de cliente abierta",
            "description": "Cliente pide seguimiento del paquete #67890.",
            "category": "client_complaint",
            "status": "open",
            "origin": "customer",
            "branch": "central",
        },
    ]
    db, table = database.get_incidents_table()
    try:
        # Replica exacta de create_incident (services/api/routes/incidents.py):
        # el modelo Incident añade id/created_at/updated_at antes de insertar.
        for payload in seed:
            table.insert(Incident(**payload).model_dump(mode="json"))
    finally:
        db.close()
    return database


@pytest.fixture(autouse=True)
def _reset_reader():
    reset_reader()
    yield
    reset_reader()


# ── Contrato de entrada tipado ──────────────────────────────────────────


class TestIncidentQueryContract:
    def test_accepts_uuid_and_slug_ticket_id(self):
        IncidentQuery(ticket_id="4bccd74e-f1ee-4883-8985-cf73ca332b8c")
        IncidentQuery(ticket_id="INC-1234")

    def test_rejects_empty_and_invalid_ticket_id(self):
        with pytest.raises(ValidationError):
            IncidentQuery(ticket_id="")
        with pytest.raises(ValidationError):
            IncidentQuery(ticket_id="  ")
        with pytest.raises(ValidationError):
            IncidentQuery(ticket_id="bad id with spaces")

    def test_filters_are_optional(self):
        q = IncidentQuery(status="open", category="lost_parcel")
        assert q.model_dump()["category"] == "lost_parcel"


# ── Salida tipada: mismos campos que la API de incidencias ──────────────


class TestLookupResultContract:
    def test_result_exposes_incident_api_fields(self):
        incident = {
            "id": "t-1",  # <- la API lo llama `id`; la tool lo mapea a ticket_id
            "title": "Paquete perdido",
            "description": "desc",
            "category": "lost_parcel",
            "status": "open",
            "origin": "branch",
            "branch": "la_warehouse",
            "created_at": "2026-10-26T10:00:00Z",
            "updated_at": "2026-10-26T10:00:00Z",
        }
        result = IncidentLookupResult(ticket_id=incident["id"], **incident, resolved=True)
        # Los mismos campos que models.Incident / IncidentListResponse
        # (la tool expone ticket_id con el valor del id de la API).
        assert result.ticket_id == "t-1"
        assert result.status == "open"
        assert result.category == "lost_parcel"
        assert result.origin == "branch"
        assert result.branch == "la_warehouse"
        assert result.created_at == "2026-10-26T10:00:00Z"


# ── Consulta real contra el gestor (en-proceso, sin HTTP) ───────────────


class TestLookupAgainstRealManager:
    def test_lookup_by_id_success(self, incident_table):
        db, table = incident_table.get_incidents_table()
        try:
            real_id = table.all()[0]["id"]
        finally:
            db.close()
        result = lookup_incidents({"ticket_id": real_id})
        assert result.resolved is True
        assert result.ticket_id == real_id
        assert result.status == "open"
        assert result.category == "lost_parcel"

    def test_lookup_by_unknown_id_is_honest(self, incident_table):
        result = lookup_incidents({"ticket_id": "INC-999999"})
        assert result.resolved is False
        assert "no existe" in (result.error or "")

    def test_lookup_by_filter_single_match(self, incident_table):
        result = lookup_incidents({"status": "resolved"})
        assert result.resolved is True
        assert result.status == "resolved"
        assert result.category == "returns_issue"

    def test_lookup_by_filter_multiple_matches_asks_for_ticket_id(self, incident_table):
        result = lookup_incidents({"status": "open"})
        assert result.resolved is False
        assert "especifica un ticket_id" in (result.error or "")

    def test_lookup_with_no_query_lists_count(self, incident_table):
        result = lookup_incidents({})
        assert result.resolved is True
        assert result.title is None
        assert "4" in (result.description or "")

    def test_invalid_query_is_honest(self, incident_table):
        result = lookup_incidents({"ticket_id": ""})
        assert result.resolved is False
        assert "entrada inválida" in (result.error or "")

    def test_accepts_plain_string_ticket_id(self, incident_table):
        db, table = incident_table.get_incidents_table()
        try:
            real_id = table.all()[0]["id"]
        finally:
            db.close()
        result = lookup_incidents(real_id)
        assert result.resolved is True
        assert result.ticket_id == real_id


# ── Timeout y fallback ──────────────────────────────────────────────────


class TestTimeoutAndFallback:
    def test_explicit_numeric_timeout_is_defined(self):
        assert INCIDENT_TIMEOUT_SECONDS > 0
        assert isinstance(INCIDENT_TIMEOUT_SECONDS, float)

    def test_timeout_zero_raises_as_fallback(self, incident_table):
        result = lookup_incidents({"ticket_id": "x"}, timeout=0)
        assert result.resolved is False
        assert "no pude" in (result.error or "")

    def test_lookup_applies_timeout(self, incident_table, monkeypatch):
        """Timeout vía SIGALRM real: la lectura que duerme dispara el fallback."""
        import signal

        import services.rag_agent.tools.incident_lookup as tool_mod

        DB = incident_table

        def slow_read(self, query):
            signal.pause()  # se bloquea hasta que la alarma lo interrumpe
            return IncidentLookupResult(resolved=True)

        monkeypatch.setattr(tool_mod._QueryReader, "read", slow_read)
        monkeypatch.setattr(tool_mod, "INCIDENT_TIMEOUT_SECONDS", 0.2)
        result = lookup_incidents({"ticket_id": "any"})
        assert result.resolved is False
        assert "no pude" in (result.error or "")

    def test_reader_failure_is_honest(self, incident_table, monkeypatch):
        import services.rag_agent.tools.incident_lookup as tool_mod

        def boom(self, query):
            raise RuntimeError("gestor caído")

        monkeypatch.setattr(tool_mod._QueryReader, "read", boom)
        result = lookup_incidents({"ticket_id": "any"})
        assert result.resolved is False
        assert "fallo" in (result.error or "")


# ── Solo lectura: la tool nunca escribe ──────────────────────────────────


class TestReadOnly:
    def test_lookup_does_not_write_to_the_manager(self, incident_table):
        db, table = incident_table.get_incidents_table()
        try:
            before = len(table.all())
        finally:
            db.close()
        lookup_incidents({"ticket_id": "INC-999999"})
        lookup_incidents({})
        lookup_incidents({"status": "open"})
        db, table = incident_table.get_incidents_table()
        try:
            after = len(table.all())
        finally:
            db.close()
        assert after == before