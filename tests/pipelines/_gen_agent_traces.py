"""Genera los traces-fixture que consumen los evals del agente RAG.

Cada escenario ejecuta el grafo real (services.rag_agent.graph) con las
dependencias externas mockeadas (Qdrant y LLM), igual que tests/pipelines/
test_rag.py. El resultado es un trace JSON persistido en
data/eval/agent_traces/<thread_id>.json con el mismo schema que produciría
una corrida con servicios vivos.

Escenarios:
  1. eval_policy_discount — pregunta de política conocida; el chunk viene
     del company-knowledge-base real (trackflow-storage-pricing.es.md).
  2. eval_empty_question  — pregunta vacía -> rama error, sin retrieve.
  3. eval_no_context      — retrieve sin hits -> no_context, sin generate.
  4. eval_ticket_lookup   — pregunta de ticket -> tool incident_lookup
     (lee el gestor de incidencias real) EN LUGAR del RAG.

Ejecución: uv run python tests/pipelines/_gen_agent_traces.py
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from services.rag_agent.graph import graph
from services.rag_agent import tracing

REPO_ROOT = Path(__file__).resolve().parents[2]
KB_DOC = REPO_ROOT / "docs" / "company-knowledge-base" / "trackflow-storage-pricing.es.md"

# Id de un incidente real del gestor (seed de services/api/data/tinydb.json):
# es un lost_parcel abierto con origen branch (sucursal la_warehouse).
EVAL_TICKET_ID = "4bccd74e-f1ee-4883-8985-cf73ca332b8c"


def _policy_chunk() -> dict:
    """Fragmento real del company-knowledge-base (política de descuentos)."""
    text = KB_DOC.read_text(encoding="utf-8")
    start = text.index("Cualquier descuento fuera de los rangos anteriores")
    snippet = text[start : start + 200].split("\n\n")[0]
    return {
        "text": snippet,
        "source_document": "trackflow-storage-pricing",
        "section": "Descuentos por volumen",
        "score": 0.87,
        "language": "es",
    }


def _run(question: str, thread_id: str, *, hits: list | None, llm_answer: str | None) -> dict:
    """Ejecuta el grafo con mocks y devuelve su trace persistido."""
    client = MagicMock()
    client.search.return_value = [
        MagicMock(payload={k: v for k, v in h.items() if k != "score"}, score=h["score"])
        for h in (hits or [])
    ]

    llm_resp = MagicMock()
    llm_resp.raise_for_status.return_value = None
    llm_resp.json.return_value = {"choices": [{"message": {"content": llm_answer}}]}
    llm_client = MagicMock()
    llm_client.__enter__.return_value = llm_client
    llm_client.post.return_value = llm_resp

    with (
        patch("data.pipelines.rag.embed", return_value=[0.1] * 8),
        patch("data.pipelines.rag._get_qdrant", return_value=client),
        patch("data.pipelines.rag.httpx.Client", return_value=llm_client),
    ):
        graph.invoke({"question": question}, {"configurable": {"thread_id": thread_id}})

    return tracing.load_trace(thread_id)


def main() -> None:
    # 1. Pregunta anclada al KB: retrieve trajo el chunk real y el LLM
    #    respondió citando la política (Miguel Torres).
    t1 = _run(
        "¿Qué aprobación requiere un descuento fuera de los rangos documentados?",
        "eval_policy_discount",
        hits=[_policy_chunk()],
        llm_answer=(
            "Cualquier descuento fuera de los rangos documentados requiere "
            "aprobación de Miguel Torres (Dirección Comercial). "
            "[Fuente: trackflow-storage-pricing / Descuentos por volumen]"
        ),
    )
    assert t1["final_state"].get("answer"), "trace 1 sin respuesta"

    # 2. Pregunta vacía: nunca llega a retrieve.
    t2 = _run("", "eval_empty_question", hits=[], llm_answer=None)
    assert "Error" in t2["final_state"]["answer"]

    # 3. Sin contexto sobre el umbral: honestidad, sin generate.
    t3 = _run(
        "¿Cuánto cuesta el frío interplanetario?",
        "eval_no_context",
        hits=[],
        llm_answer=None,
    )
    assert "no tengo información" in t3["final_state"]["answer"].lower()

    # 4. Consulta de ticket: la tool incident_lookup lee el gestor real
    #    en-proceso. retrieve/generate están mockeados; si el grafo llamara
    #    al RAG aquí, fallaría al no haber context — por eso scoped como es.
    t4 = _run(
        f"¿cuál es el estado del ticket {EVAL_TICKET_ID}?",
        "eval_ticket_lookup",
        hits=[],  # el RAG no debe tocarse: la tool va en su lugar
        llm_answer=None,
    )
    assert "4bccd74e" in t4["final_state"]["answer"]
    assert t4["final_state"]["incident_result"]["resolved"] is True

    for tid in (
        "eval_policy_discount",
        "eval_empty_question",
        "eval_no_context",
        "eval_ticket_lookup",
    ):
        path = tracing.TRACES_DIR / f"{tid}.json"
        steps = [s["node"] for s in json.loads(path.read_text())["steps"]]
        print(f"{tid}: {' -> '.join(steps)}  [{path}]")


if __name__ == "__main__":
    main()