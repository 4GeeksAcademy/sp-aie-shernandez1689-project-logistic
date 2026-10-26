"""Tests de enrutamiento del grafo: tool de tickets vs. RAG.

Ejecutan el grafo real (services.rag_agent.graph) en vivo, mockeando
solo las dependencias externas (retrieve/generate) y leyendo el gestor
de incidencias REAL en una tabla TinyDB temporal (la misma
database.get_incidents_table que sirve GET /api/incidents).

Cubre la arista condicional de la tool:
- Pregunta de ticket    -> intent -> incident_lookup -> finalize (retrieve NO).
- Ticket inexistente    -> incident_lookup con fallback honesto (retrieve NO).
- Filtro con varios hits -> fallback honesto pidiendo ticket_id.
- Pregunta RAG normal   -> intent -> retrieve -> generate (incident_lookup NO).
- Pregunta vacía        -> validate -> error (nada más).

Los traces se escriben en un directorio temporal (no ensucian
data/eval/agent_traces).
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import patch

import pytest

import database
from models import Incident
from services.rag_agent import tracing
from services.rag_agent.graph import graph
from services.rag_agent.tools.incident_lookup import reset_reader

SEED = [
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


@pytest.fixture
def incident_table(tmp_path, monkeypatch):
    """Tabla real del gestor de incidencias aislada en tmp_path."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "operations.json")
    db, table = database.get_incidents_table()
    try:
        for payload in SEED:
            # Replica create_incident: Incident añade id/created_at/updated_at.
            table.insert(Incident(**payload).model_dump(mode="json"))
    finally:
        db.close()
    reset_reader()
    return database


@pytest.fixture
def live_graph(tmp_path, monkeypatch):
    """Grafo compilado con traces a tmp_path y reader recién creado."""
    monkeypatch.setattr(tracing, "TRACES_DIR", tmp_path / "traces")
    reset_reader()
    return graph


def _run_thread(thread_id: str, question: str) -> dict:
    """Invoca el grafo en el thread indicado y devuelve el trace persistido."""
    graph.invoke(
        {"question": question},
        {"configurable": {"thread_id": thread_id}},
    )
    return tracing.load_trace(thread_id)


def _node_order(trace: dict) -> list[str]:
    return [s["node"] for s in trace["steps"]]


class _FakeRAG:
    """Context manager que mockea retrieve/generate sin tocar Qdrant/LLM.

    ``with _FakeRAG(...) as (mock_retrieve, mock_generate):`` restaura los
    mocks al salir.
    """

    def __init__(self, *, context: list | None = None, answer: str = "respuesta RAG"):
        self.context = context or []
        self.answer = answer

    def __enter__(self) -> tuple:
        self._stack = ExitStack()
        self.retrieve = self._stack.enter_context(
            patch("services.rag_agent.graph.retrieve", return_value=self.context)
        )
        self.generate = self._stack.enter_context(
            patch("services.rag_agent.graph.generate_answer", return_value=self.answer)
        )
        return self.retrieve, self.generate

    def __exit__(self, *exc) -> bool:
        self._stack.close()
        return False


def _fake_rag(*, context: list | None = None, answer: str = "respuesta RAG"):
    return _FakeRAG(context=context, answer=answer)


# ── La tool se usa EN LUGAR del RAG ─────────────────────────────────────


def test_ticket_question_uses_incident_lookup_instead_of_rag(live_graph, incident_table):
    """Pregunta de ticket -> incident_lookup; retrieve NUNCA se ejecuta."""
    db, table = incident_table.get_incidents_table()
    try:
        real_id = table.all()[0]["id"]
    finally:
        db.close()

    with _fake_rag() as (mock_retrieve, mock_generate):
        trace = _run_thread(
            "test_ticket_lookup",
            f"¿Cuál es el estado del ticket {real_id}?",
        )

    order = _node_order(trace)
    assert "intent" in order
    assert "incident_lookup" in order
    assert order.index("intent") < order.index("incident_lookup")
    assert "retrieve" not in order, f"retrieve no debe correr: {order}"
    assert "generate" not in order
    mock_retrieve.assert_not_called()
    mock_generate.assert_not_called()

    answer = trace["final_state"]["answer"]
    assert real_id in answer
    assert "open" in answer


def test_missing_ticket_is_honest_without_rag(live_graph, incident_table):
    """Ticket inexistente -> fallback honesto; retrieve NUNCA se ejecuta."""
    with _fake_rag() as (mock_retrieve, mock_generate):
        trace = _run_thread("test_ticket_missing", "¿Estado del ticket INC-999999?")

    order = _node_order(trace)
    assert "incident_lookup" in order
    assert "retrieve" not in order
    mock_retrieve.assert_not_called()
    answer = trace["final_state"]["answer"]
    assert "No pude confirmar el estado de ese ticket ahora mismo" in answer
    assert "no existe" in answer


def test_filter_multiple_matches_is_honest(live_graph, incident_table):
    """Filtro con varios hits -> la tool pide ticket_id (sin inventar)."""
    with _fake_rag() as (mock_retrieve, _):
        trace = _run_thread("test_ticket_filter_multi", "ver ticket")

    order = _node_order(trace)
    assert "incident_lookup" in order
    assert "retrieve" not in order
    mock_retrieve.assert_not_called()
    answer = trace["final_state"]["answer"]
    assert "No pude confirmar el estado de ese ticket ahora mismo" in answer
    assert "especifica un ticket_id" in answer


# ── El RAG se usa cuando NO hay intención de ticket ─────────────────────


def test_rag_question_goes_through_retrieve_not_incident_lookup(live_graph):
    """Pregunta normal -> intent -> retrieve -> generate (sin incident_lookup)."""
    context = [{"text": "Snippet de política.", "source_document": "kb", "score": 0.9}]
    with _fake_rag(context=context, answer="Los descuentos los aprueba Miguel Torres.") as (
        mock_retrieve,
        mock_generate,
    ):
        trace = _run_thread(
            "test_rag_question",
            "¿Quién aprueba los descuentos fuera de rango?",
        )

    order = _node_order(trace)
    assert "intent" in order
    assert "incident_lookup" not in order, f"no debe usar la tool: {order}"
    assert "retrieve" in order and "generate" in order
    assert order.index("retrieve") < order.index("generate")
    mock_retrieve.assert_called_once()
    assert "Miguel Torres" in trace["final_state"]["answer"]


# ── Entrada vacía ───────────────────────────────────────────────────────


def test_empty_question_goes_to_error(live_graph):
    """Pregunta vacía -> error; ni retrieve ni incident_lookup corren.

    Nota: finalize cierra el trace (end_run) y por eso no se registra como
    paso; es comportamiento preexistente del módulo tracing.
    """
    with _fake_rag() as (mock_retrieve, _):
        trace = _run_thread("test_empty_question", "")

    order = _node_order(trace)
    assert order == ["validate", "error"], order
    mock_retrieve.assert_not_called()
    assert "Error" in trace["final_state"]["answer"]


# ── El trace sigue siendo consultable con la tool ───────────────────────


def test_trace_schema_holds_for_incident_run(live_graph, incident_table):
    """El trace de una corrida de tickets conserva el schema v1."""
    db, table = incident_table.get_incidents_table()
    try:
        real_id = table.all()[0]["id"]
    finally:
        db.close()

    with _fake_rag():
        trace = _run_thread("test_ticket_schema", f"¿estado del ticket {real_id}?")

    assert trace["schema_version"] == 1
    assert trace["thread_id"] == "test_ticket_schema"
    seqs = [s["seq"] for s in trace["steps"]]
    assert seqs == list(range(1, len(seqs) + 1)), seqs
    incident_result = trace["final_state"].get("incident_result")
    assert incident_result is not None
    assert incident_result["ticket_id"] == real_id
    assert incident_result["resolved"] is True