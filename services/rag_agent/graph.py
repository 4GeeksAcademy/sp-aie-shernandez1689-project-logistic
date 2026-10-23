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
  START -> validate -> (question empty?) -> error -> END
                     -> retrieve -> (context empty?) -> no_context -> END
                                             -> generate -> END

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
from langgraph.graph import END, START, StateGraph

from data.pipelines.rag import DEFAULT_MIN_SCORE, generate_answer, retrieve

DEFAULT_K = 5


class AgentState(TypedDict, total=False):
    """Información mínima que un nodo necesita para decidir el siguiente paso."""

    question: str
    context: list[dict[str, Any]]
    answer: str
    error: str


# ── Nodos ───────────────────────────────────────────────────────────────


def validate(state: AgentState) -> dict[str, Any]:
    """Recibe la pregunta y decide si hay algo que procesar."""
    question = (state.get("question") or "").strip()
    if not question:
        return {"error": "La pregunta está vacía."}
    return {"question": question}


def retrieve_node(state: AgentState) -> dict[str, Any]:
    """Ejecuta retrieve() sobre la base de conocimiento (reutilizado)."""
    context = retrieve(
        query=state["question"],
        k=DEFAULT_K,
        min_score=DEFAULT_MIN_SCORE,
    )
    return {"context": context}


def generate_node(state: AgentState) -> dict[str, Any]:
    """Genera la respuesta final a partir del contexto ya recuperado."""
    answer = generate_answer(
        question=state["question"],
        context=state["context"],
    )
    return {"answer": answer}


def no_context(state: AgentState) -> dict[str, Any]:
    """Fallback honesto cuando retrieve no devuelve contexto suficiente."""
    return {
        "answer": (
            "Lo siento, no tengo información sobre eso en mi base de "
            "conocimiento. Consulta con el equipo comercial de TrackFlow "
            "para datos actualizados."
        )
    }


def error_node(state: AgentState) -> dict[str, Any]:
    """Terminal para entradas inválidas (pregunta vacía)."""
    return {"answer": f"Error: {state.get('error', 'entrada inválida')}"}


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

_builder.add_edge(START, "validate")
_builder.add_conditional_edges(
    "validate", route_after_validate, ["retrieve", "error"]
)
_builder.add_conditional_edges(
    "retrieve", route_after_retrieve, ["generate", "no_context"]
)
_builder.add_edge("generate", END)
_builder.add_edge("no_context", END)
_builder.add_edge("error", END)

# MemorySaver: checkpoint en cada transición de estado relevante.
graph = _builder.compile(checkpointer=MemorySaver())


def run(question: str, *, thread_id: str = "default") -> dict[str, Any]:
    """Ejecuta el grafo una corrida con checkpointing por thread_id.

    La corrida puede inspeccionarse o retomarse con
    graph.get_state(config) / graph.update_state(config, ...).
    """
    config = {"configurable": {"thread_id": thread_id}}
    return graph.invoke({"question": question}, config)