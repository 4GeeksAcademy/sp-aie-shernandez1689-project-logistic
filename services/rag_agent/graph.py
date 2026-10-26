"""Grafo de agente RAG con LangGraph (TrackFlow).

Estado mínimo del grafo
-----------------------
- question: pregunta del usuario (entrada).
- context:  chunks recuperados por el nodo retrieve (salida -> entrada).
- answer:   respuesta final generada o mensaje honesto de fallback.

No se incluye el historial completo de la conversación: el grafo es
stateless por corrida y cada corrida se inspecciona/retoma vía
checkpointing (thread_id), por lo que el historial no es necesario en
el estado.

Nodos y aristas
---------------
  START -> validate -> (question empty?) -> error -> finalize -> END
                     -> retrieve -> (context empty?) -> no_context -> finalize -> END
                                             -> generate -> finalize -> END

Contrato: retrieve_node llama a retrieve() y generate_node llama a
generate_answer(question, context) con el contexto que retrieve_node
produjo. Nunca se usa query() (evitaría doble recuperación).

El grafo se compila al importar el módulo: un error estructural (nodo
sin conexión, estado mal tipado) falla de forma clara antes de cualquier
ejecución.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph

from data.pipelines.rag import DEFAULT_MIN_SCORE, generate_answer, retrieve
from services.rag_agent import tracing

DEFAULT_K = 5


class AgentState(TypedDict, total=False):
    """Información mínima que un nodo necesita para decidir el siguiente paso."""

    question: str
    context: list[dict[str, Any]]
    answer: str
    error: str


# ── Nodos ───────────────────────────────────────────────────────────────
# Cada nodo registra su salida en el trace (tracing.add_step) para que la
# corrida sea consultable después: qué nodo corrió, en qué orden y qué produjo.


def validate(state: AgentState) -> dict[str, Any]:
    """Recibe la pregunta y decide si hay algo que procesar."""
    question = (state.get("question") or "").strip()
    # Abre el trace de la corrida en el primer paso (thread_id del run).
    tracing.ensure_started(_thread_id(), state.get("question") or "")
    out = {"error": "La pregunta está vacía."} if not question else {"question": question}
    tracing.add_step("validate", out)
    return out


def _thread_id() -> str:
    cfg = get_config()
    return cfg.get("configurable", {}).get("thread_id", "default")


def retrieve_node(state: AgentState) -> dict[str, Any]:
    """Ejecuta retrieve() sobre la base de conocimiento (reutilizado)."""
    context = retrieve(
        query=state["question"],
        k=DEFAULT_K,
        min_score=DEFAULT_MIN_SCORE,
    )
    out: dict[str, Any] = {
        "context": context,
        "n_hits": len(context),
        "sources": [c.get("source_document") for c in context],
    }
    tracing.add_step("retrieve", out)
    return {"context": context}


def generate_node(state: AgentState) -> dict[str, Any]:
    """Genera la respuesta final a partir del contexto ya recuperado."""
    answer = generate_answer(
        question=state["question"],
        context=state["context"],
    )
    tracing.add_step("generate", {"answer": answer})
    return {"answer": answer}


def no_context(state: AgentState) -> dict[str, Any]:
    """Fallback honesto cuando retrieve no devuelve contexto suficiente."""
    out = {
        "answer": (
            "Lo siento, no tengo información sobre eso en mi base de "
            "conocimiento. Consulta con el equipo comercial de TrackFlow "
            "para datos actualizados."
        )
    }
    tracing.add_step("no_context", out)
    return out


def error_node(state: AgentState) -> dict[str, Any]:
    """Terminal para entradas inválidas (pregunta vacía)."""
    out = {"answer": f"Error: {state.get('error', 'entrada inválida')}"}
    tracing.add_step("error", out)
    return out


def finalize(state: AgentState) -> dict[str, Any]:
    """Último paso de toda corrida: cierra y persiste el trace."""
    tracing.end_run(dict(state))
    return {}


# ── Aristas condicionales ───────────────────────────────────────────────


def route_after_validate(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    return "retrieve"


def route_after_retrieve(state: AgentState) -> str:
    if not state.get("context"):
        return "no_context"
    return "generate"


# ── Construcción y compilación (antes de cualquier ejecución) ───────────

_builder = StateGraph(AgentState)
_builder.add_node("validate", validate)
_builder.add_node("retrieve", retrieve_node)
_builder.add_node("generate", generate_node)
_builder.add_node("no_context", no_context)
_builder.add_node("error", error_node)
_builder.add_node("finalize", finalize)

_builder.add_edge(START, "validate")
_builder.add_conditional_edges(
    "validate", route_after_validate, ["retrieve", "error"]
)
_builder.add_conditional_edges(
    "retrieve", route_after_retrieve, ["generate", "no_context"]
)
_builder.add_edge("generate", "finalize")
_builder.add_edge("no_context", "finalize")
_builder.add_edge("error", "finalize")
_builder.add_edge("finalize", END)

# MemorySaver: checkpoint en cada transición de estado relevante.
graph = _builder.compile(checkpointer=MemorySaver())


def run(question: str, *, thread_id: str = "default") -> dict[str, Any]:
    """Ejecuta el grafo una corrida con checkpointing por thread_id.

    La corrida puede inspeccionarse o retomarse con
    graph.get_state(config) / graph.update_state(config, ...).
    """
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke({"question": question}, config)