"""Grafo de agente RAG con LangGraph (TrackFlow).

Estado mínimo del grafo
-----------------------
- question: pregunta del usuario (entrada).
- context:  chunks recuperados por el nodo retrieve (salida -> entrada).
- answer:   respuesta final generada o mensaje honesto de fallback.
- incident_query: petición de consulta de ticket (salida de intent).
- inventory_query: petición de consulta de inventario (salida de intent).

No se incluye el historial completo de la conversación: el grafo es
stateless por corrida y cada corrida se inspecciona/retoma vía
checkpointing (thread_id), por lo que el historial no es necesario en
el estado.

Nodos y aristas
---------------
  START -> validate -> (question empty?) -> error -> finalize -> END
                     -> intent  -> (consulta de ticket?) -> incident_lookup -> finalize -> END
                                 -> (consulta de inventario?) -> inventory_lookup -> finalize -> END
                                 -> retrieve -> (context empty?) -> no_context -> finalize -> END
                                                       -> generate -> finalize -> END

El nodo ``intent`` (arista condicional post-validate) decide si la
pregunta es una consulta de tickets de soporte o de inventario: si sí,
el agente usa la tool correspondiente (``incident_lookup`` /
``inventory_lookup``, lectura en-proceso del gestor real) EN LUGAR DE
(no además de) el RAG — ni las incidencias ni el stock viven en el
company-knowledge-base. Si la tool no puede confirmar el dato (timeout,
fallo o inexistencia), el nodo produce una respuesta honesta sin
inventar un estado ni una cifra.

Contrato: retrieve_node llama a retrieve() y generate_node llama a
generate_answer(question, context) con el contexto que retrieve_node
produjo. Nunca se usa query() (evitaría doble recuperación).

El grafo se compila al importar el módulo: un error estructural (nodo
sin conexión, estado mal tipado) falla de forma clara antes de cualquier
ejecución.
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph

from data.pipelines.rag import DEFAULT_MIN_SCORE, generate_answer, retrieve
from services.rag_agent import tracing
from services.rag_agent.tools.incident_lookup import (
    IncidentLookupResult,
    IncidentQuery,
    lookup_incidents,
)
from services.rag_agent.tools.inventory_lookup import (
    InventoryLookupResult,
    InventoryQuery,
    lookup_inventory,
)

DEFAULT_K = 5

# Detección heurística de intención de consulta de ticket de soporte.
_INCIDENT_INTENT_RE = re.compile(
    r"ticket|incidencia|incident|estado de .*ticket|status de .*ticket",
    re.IGNORECASE,
)

# Detección heurística de intención de consulta de inventario (stock).
# Solo stock/inventario/sku: "producto" es demasiado genérico y robaría
# preguntas RAG de la knowledge base (precios/almacenamiento).
_INVENTORY_INTENT_RE = re.compile(
    r"inventario|inventory|stock|sku",
    re.IGNORECASE,
)

# Código SKU del gestor de inventario (p. ej. CLT-SNK-W-42, TEC-EAR-001,...).
_SKU_TOKEN_RE = re.compile(r"\b[A-Z]{2,6}(?:-[A-Z0-9]{1,6}){1,4}\b")


class AgentState(TypedDict, total=False):
    """Información mínima que un nodo necesita para decidir el siguiente paso."""

    question: str
    context: list[dict[str, Any]]
    answer: str
    error: str
    incident_query: dict[str, Any] | None
    incident_result: dict[str, Any] | None
    inventory_query: dict[str, Any] | None
    inventory_result: dict[str, Any] | None


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


def intent_node(state: AgentState) -> dict[str, Any]:
    """Clasifica la intención: ticket, inventario o pregunta RAG.

    Alimenta el estado con ``incident_query`` o ``inventory_query`` solo
    cuando la pregunta menciona un ticket/incidencia o stock/inventario;
    de lo contrario deja ``None`` y el grafo sigue con retrieve (knowledge
    base). Es deliberadamente conservador.

    Precedencia: si la pregunta es de ticket, NO se consulta inventario
    (una sola tool por corrida); la arista condicional decide a partir de
    qué campo quedó poblado.
    """
    question = (state.get("question") or "").strip()
    incident_query: dict[str, Any] | None = None
    inventory_query: dict[str, Any] | None = None

    if _INCIDENT_INTENT_RE.search(question):
        candidate: str | None = None
        for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9._:-]{4,63}", question):
            token = match.group(0)
            if token.lower() in {"ticket", "incidencia", "incident"}:
                continue
            candidate = token
        incident_query = {"ticket_id": candidate} if candidate else {"status": "open"}

    elif _INVENTORY_INTENT_RE.search(question):
        sku_match = _SKU_TOKEN_RE.search(question)
        if sku_match:
            inventory_query = {"sku": sku_match.group(0)}
        else:
            # Sin SKU concreto: el agente lista/consulta stock por filtro.
            inventory_query = {"sku_id": None}

    out = {"incident_query": incident_query, "inventory_query": inventory_query}
    tracing.add_step("intent", out)
    return out


def incident_lookup_node(state: AgentState) -> dict[str, Any]:
    """Ejecuta la tool de consulta de tickets (solo lectura).

    Lee del gestor de incidencias real via lookup_incidents(). Si el
    resultado no está resuelto (timeout, fallo o ticket inexistente),
    produce una respuesta honesta; nunca inventa un estado.

    La tool devuelve un IncidentLookupResult (Pydantic); aquí se guarda
    en el estado como dict JSON-serializable (tracing y checkpointer
    persisten solo JSON).
    """
    query = state.get("incident_query") or {}
    try:
        result = lookup_incidents(query)
    except Exception as exc:  # la tool ya convierte fallos, por si acaso
        result = IncidentLookupResult(resolved=False, error=str(exc))

    if result.resolved and result.ticket_id:
        answer = (
            f"El ticket {result.ticket_id} está "
            f"{result.status} (origen: {result.origin}, "
            f"sucursal: {result.branch})."
        )
    elif result.resolved:
        answer = result.description or "Consulta de incidencias completada."
    else:
        answer = (
            "No pude confirmar el estado de ese ticket ahora mismo. "
            + (result.error or "Inténtalo de nuevo más tarde.")
        )

    incident_result = result.model_dump(mode="json")
    out = {
        "answer": answer,
        "incident_result": incident_result,
    }
    tracing.add_step("incident_lookup", out)
    return {"answer": answer, "incident_result": incident_result}


def inventory_lookup_node(state: AgentState) -> dict[str, Any]:
    """Ejecuta la tool de consulta de inventario (solo lectura).

    Lee del gestor de inventario real via lookup_inventory(). Si el
    resultado no está resuelto (timeout, fallo o producto inexistente),
    produce una respuesta honesta; nunca inventa una cifra de stock.

    La tool devuelve un InventoryLookupResult (Pydantic); aquí se guarda
    en el estado como dict JSON-serializable (tracing y checkpointer
    persisten solo JSON).
    """
    query = state.get("inventory_query") or {}
    try:
        result = lookup_inventory(query)
    except Exception as exc:  # la tool ya convierte fallos, por si acaso
        result = InventoryLookupResult(resolved=False, error=str(exc))

    if result.resolved and result.product:
        product = result.product
        answer = (
            f"El producto {product['sku']} ({product['name']}) tiene "
            f"{product['current_stock']} unidades en stock "
            f"(almacén {product['warehouse']}, categoría {product['category']})."
        )
    elif result.resolved:
        answer = result.description or "Consulta de inventario completada."
    else:
        answer = (
            "No pude consultar el stock de ese producto ahora mismo. "
            + (result.error or "Inténtalo de nuevo más tarde.")
        )

    inventory_result = result.model_dump(mode="json")
    out = {
        "answer": answer,
        "inventory_result": inventory_result,
    }
    tracing.add_step("inventory_lookup", out)
    return {"answer": answer, "inventory_result": inventory_result}


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
    return "intent"


def route_after_intent(state: AgentState) -> str:
    """Decide entre las tools (tickets/inventario) y el RAG.

    Arista condicional de las tools: solo se usa la consulta de tickets
    (incident_lookup) cuando la pregunta pide explicitamente un
    ticket/incidencia, o la de inventario (inventory_lookup) cuando pide
    stock/sku; en cualquier otro caso el agente sigue con retrieve() sobre
    el knowledge base. Las tools y el RAG son alternativas (no se ejecutan
    ambos). Precedencia: incidente > inventario.
    """
    if state.get("incident_query") is not None:
        return "incident_lookup"
    if state.get("inventory_query") is not None:
        return "inventory_lookup"
    return "retrieve"


def route_after_retrieve(state: AgentState) -> str:
    if not state.get("context"):
        return "no_context"
    return "generate"


def route_after_incident_lookup(state: AgentState) -> str:
    return "finalize"


def route_after_inventory_lookup(state: AgentState) -> str:
    return "finalize"


# ── Construcción y compilación (antes de cualquier ejecución) ───────────

_builder = StateGraph(AgentState)
_builder.add_node("validate", validate)
_builder.add_node("intent", intent_node)
_builder.add_node("incident_lookup", incident_lookup_node)
_builder.add_node("inventory_lookup", inventory_lookup_node)
_builder.add_node("retrieve", retrieve_node)
_builder.add_node("generate", generate_node)
_builder.add_node("no_context", no_context)
_builder.add_node("error", error_node)
_builder.add_node("finalize", finalize)

_builder.add_edge(START, "validate")
_builder.add_conditional_edges(
    "validate", route_after_validate, ["error", "intent"]
)
_builder.add_conditional_edges(
    "intent", route_after_intent, ["incident_lookup", "inventory_lookup", "retrieve"]
)
_builder.add_conditional_edges(
    "retrieve", route_after_retrieve, ["generate", "no_context"]
)
_builder.add_edge("incident_lookup", "finalize")
_builder.add_edge("inventory_lookup", "finalize")
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