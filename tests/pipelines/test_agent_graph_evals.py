"""Evals del agente RAG (LangGraph) — corren contra el trace persistido.

No ejecutan el grafo en vivo: leen los traces JSON de
data/eval/agent_traces/ (generados con

    uv run python tests/pipelines/_gen_agent_traces.py

con Qdrant/LLM mockeados, igual que test_rag.py).

Evals:
- Orden del trace: intent -> retrieve corre ANTES que generate.
- Pregunta vacía: rama error, retrieve nunca corre.
- Sin contexto: no_context (honestidad), generate nunca corre.
- Ticket: la tool incident_lookup se usa EN LUGAR del RAG (retrieve
  nunca corre) y lee el gestor real de incidencias.
- Inventario: la tool inventory_lookup se usa EN LUGAR del RAG (retrieve
  nunca corre) y devuelve el stock real del gestor.
- Anclaje: la respuesta sigue anclada en el company-knowledge-base
  (política de descuentos -> Miguel Torres, fuente trackflow-storage-pricing).

Comando único:
    uv run pytest tests/pipelines/test_agent_graph_evals.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.rag_agent.tracing import TRACES_DIR

KB_DIR = Path(__file__).resolve().parents[2] / "docs" / "company-knowledge-base"
KIND_TOPIC = "trackflow-storage-pricing.es.md"
EVAL_TICKET_ID = "4bccd74e-f1ee-4883-8985-cf73ca332b8c"


def _load(thread_id: str) -> dict:
    path = TRACES_DIR / f"{thread_id}.json"
    if not path.exists():
        pytest.fail(
            f"Trace no encontrado: {path}. Regenéralo con "
            "`uv run python tests/pipelines/_gen_agent_traces.py`"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _node_order(trace: dict) -> list[str]:
    return [s["node"] for s in trace["steps"]]


# ── Orden del trace ─────────────────────────────────────────────────────


def test_retrieve_runs_before_generate():
    """Eval 1: en la corrida con contexto, retrieve debe ejecutarse antes
    que generate (flujo explícito y trazable, no query() monolítico)."""
    trace = _load("eval_policy_discount")
    order = _node_order(trace)
    assert "retrieve" in order and "generate" in order
    assert order.index("retrieve") < order.index("generate"), (
        f"retrieve debe ir antes de generate, got: {order}"
    )
    # intent clasifica la pregunta antes de decidir herramienta/RAG.
    assert order.index("intent") < order.index("retrieve"), order
    # validate abre siempre la corrida
    assert order[0] == "validate"


# ── Condiciones de enrutamiento ─────────────────────────────────────────


def test_empty_question_routes_to_error_without_retrieve():
    """Eval 2: pregunta vacía -> nodo error, retrieve NUNCA se ejecuta."""
    trace = _load("eval_empty_question")
    order = _node_order(trace)
    assert "error" in order
    assert "retrieve" not in order, f"retrieve no debe correr: {order}"
    assert "Error" in trace["final_state"]["answer"]


def test_empty_context_routes_to_no_context_not_generate():
    """Eval 3: retrieve sin hits -> no_context (respuesta honesta),
    generate NUNCA se ejecuta sobre contexto vacío."""
    trace = _load("eval_no_context")
    order = _node_order(trace)
    assert "retrieve" in order
    assert "no_context" in order
    assert "generate" not in order, f"generate no debe correr: {order}"
    answer = trace["final_state"]["answer"].lower()
    assert "no tengo información" in answer, answer


# ── Tool de tickets: se usa EN LUGAR del RAG ─────────────────────────────


def test_ticket_question_uses_incident_lookup_instead_of_rag():
    """Eval 4: consulta de ticket -> intent -> incident_lookup (tool real),
    retrieve NUNCA se ejecuta (la tool es alternativa al RAG). La respuesta
    confirma el estado real del gestor y el resultado queda en el estado."""
    trace = _load("eval_ticket_lookup")
    order = _node_order(trace)
    assert "intent" in order
    assert "incident_lookup" in order
    assert order.index("intent") < order.index("incident_lookup"), order
    assert "retrieve" not in order, f"retrieve no debe correr: {order}"
    assert "generate" not in order

    answer = trace["final_state"]["answer"]
    assert EVAL_TICKET_ID in answer
    assert "open" in answer  # estado real del gestor (seed)
    incident_result = trace["final_state"].get("incident_result")
    assert incident_result is not None
    assert incident_result["resolved"] is True
    assert incident_result["ticket_id"] == EVAL_TICKET_ID


# ── Tool de inventario: se usa EN LUGAR del RAG ─────────────────────────


def test_inventory_question_uses_inventory_lookup_instead_of_rag():
    """Eval 5: consulta de stock -> intent -> inventory_lookup (tool real),
    retrieve NUNCA se ejecuta (la tool es alternativa al RAG). La respuesta
    confirma el stock real del gestor y el resultado queda en el estado."""
    trace = _load("eval_inventory_lookup")
    order = _node_order(trace)
    assert "intent" in order
    assert "inventory_lookup" in order
    assert order.index("intent") < order.index("inventory_lookup"), order
    assert "retrieve" not in order, f"retrieve no debe correr: {order}"
    assert "generate" not in order

    answer = trace["final_state"]["answer"]
    assert "CLT-SNK-W-42" in answer
    assert "40" in answer  # current_stock real del gestor (seed)
    inventory_result = trace["final_state"].get("inventory_result")
    assert inventory_result is not None
    assert inventory_result["resolved"] is True
    assert inventory_result["product"]["sku"] == "CLT-SNK-W-42"
    assert inventory_result["product"]["current_stock"] == 40


# ── Anclaje en la base de conocimiento (criterio de aceptación) ─────────


def test_response_anchored_to_knowledge_base():
    """Eval 4: la respuesta a la pregunta de política sigue anclada en el
    company-knowledge-base: cita la entidad Miguel Torres y la fuente
    trackflow-storage-pricing, y el contexto venía de ese documento."""
    trace = _load("eval_policy_discount")
    answer = trace["final_state"]["answer"]
    assert "Miguel Torres" in answer, f"respuesta sin ancla del BRIEFING: {answer}"

    # el chunk recuperado proviene del documento real del KB
    retrieve_step = next(
        s for s in trace["steps"] if s["node"] == "retrieve"
    )
    sources = retrieve_step["output"]["sources"]
    assert "trackflow-storage-pricing" in sources, sources
    # el chunk es contenido real del archivo (no inventado)
    kb_text = (KB_DIR / KIND_TOPIC).read_text(encoding="utf-8")
    ctx_text = retrieve_step["output"]["context"][0]["text"]
    assert ctx_text.strip() in kb_text, "el chunk no pertenece al KB real"


# ── Schema del trace ────────────────────────────────────────────────────


def test_trace_schema_is_consultable():
    """Eval 6: todo trace tiene schema, pasos ordenados y estado final —
    consultable después de la corrida, no solo impreso en consola."""
    for tid in (
        "eval_policy_discount",
        "eval_empty_question",
        "eval_no_context",
        "eval_ticket_lookup",
        "eval_inventory_lookup",
    ):
        trace = _load(tid)
        assert trace["schema_version"] == 1
        assert trace["thread_id"] == tid
        assert trace["question"] is not None
        assert trace["completed_at"], "corrida sin cerrar"
        seqs = [s["seq"] for s in trace["steps"]]
        assert seqs == list(range(1, len(seqs) + 1)), seqs
        assert "answer" in trace["final_state"]