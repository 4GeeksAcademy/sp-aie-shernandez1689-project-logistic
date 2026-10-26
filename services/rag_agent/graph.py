"""Grafo de agente RAG con LangGraph (TrackFlow).

Estado mínimo del grafo
-----------------------
- question: pregunta del usuario (entrada).
- context:  chunks recuperados por el nodo retrieve (salida -> entrada).
- answer:   respuesta final generada o mensaje honesto de fallback.
- incident_query: petición de consulta de ticket (salida de intent).
- inventory_query: petición de consulta de inventario (salida de intent).
- tool_answers / tool_context: resúmenes de las tools para componer la
  respuesta final (acumulados con reductor si corren dos tools).

No se incluye el historial completo de la conversación: el grafo es
stateless por corrida y cada corrida se inspecciona/retoma vía
checkpointing (thread_id), por lo que el historial no es necesario en
el estado.

Nodos y aristas
---------------
  START -> validate -> (pregunta vacía?) -> error -> finalize -> END

  intent  decide de forma AUTOMÁTICA (sin que el usuario indique la fuente)
  si la pregunta necesita el RAG, una tool o ambos:

  - Solo RAG -> retrieve -> (context?) -> generate -> finalize -> END
                                   -> no_context -> finalize
    - Solo tool -> tool(s) -> generate -> finalize -> END
  - RAG + tool -> tool(s) -> retrieve -> generate (compone tool+context)
    -> finalize -> END

tools para comprobar si la knowledge base aporta contexto; el resultado de
El nodo ``intent`` detecta la intención de ticket/incidencia y/o de
stock/inventario de forma independiente: puede decidir 0, 1 o 2 tools, y
cada tool hace UNA sola cosa. Las consultas operativas van solo a las tools;
las preguntas documentales van solo al RAG; una pregunta que pide ambas
fuentes ejecuta las tools y después ``retrieve``. El trace registra las
fuentes realmente ejecutadas y su orden. El usuario nunca indica qué fuente
usar: lo decide el grafo.

Si una tool no puede confirmar el dato (timeout, fallo o inexistencia), el nodo
produce una respuesta honesta sin inventar un estado ni una cifra.

Contrato: retrieve_node llama a retrieve() y generate_node llama a
generate_answer(question, context) con el contexto que retrieve_node
produjo y, si hubo consulta de tool, con un contexto extra que resume el
resultado de la tool. Nunca se usa query() (evitaría doble recuperación).

El grafo se compila al importar el módulo: un error estructural (nodo
sin conexión, estado mal tipado) falla de forma clara antes de cualquier
ejecución.
"""

from __future__ import annotations

import operator
import re
from typing import Annotated, Any, TypedDict

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

# Señales documentales/de negocio. Si una pregunta combina estos términos con
# una consulta operacional, se consultan ambas fuentes; una consulta de estado
# o stock aislada se resuelve solo con su tool.
_RAG_INTENT_RE = re.compile(
    r"pol[ií]tica|aprobaci[oó]n|descuento|devoluci[oó]n|devoluciones|reembolso|"
    r"contrato|tarifa|precio|sla|cobertura|transportista|documentad[oa]s?|"
    r"base de conocimiento|procedimiento|condiciones",
    re.IGNORECASE,
)

# Código SKU del gestor de inventario (p. ej. CLT-SNK-W-42, TEC-EAR-001,...).
_SKU_TOKEN_RE = re.compile(r"\b[A-Z]{2,6}(?:-[A-Z0-9]{1,6}){1,4}\b")

# Token con forma de UUID (para extractar el id de ticket de forma fiable).
_UUID_TOKEN_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _extract_ticket_id(question: str) -> str | None:
    """Extrae el id de ticket de la pregunta de forma robusta.

    Prioridades (deliberadamente conservadoras, sin inventar ids):
    1. El token que sigue inmediatamente a ``ticket``/``incidencia``/
       ``incidente`` (p. ej. "ticket 9e8dec71-...") y que no parezca un SKU.
    2. Un token con forma de UUID (hex con guiones): no confundible con un SKU
       ni con una palabra normal, así "stock del ticket X del producto SKU-Y"
       queda desambiguado.
    3. Fallback: el último token "largo" que no sea una keyword ni un SKU.
    """
    keyword = re.search(
        r"\b(?:ticket|incidencia|incident(?:e|es)?)\s+"
        r"([A-Za-z0-9][A-Za-z0-9._:-]{4,63})\b",
        question,
        re.IGNORECASE,
    )
    if keyword:
        token = keyword.group(1)
        if not _SKU_TOKEN_RE.fullmatch(token):
            return token

    uuid = _UUID_TOKEN_RE.search(question)
    if uuid:
        return uuid.group(0)

    candidate: str | None = None
    for match in re.finditer(r"[A-Za-z0-9][A-Za-z0-9._:-]{4,63}", question):
        token = match.group(0)
        if token.lower() in {"ticket", "incidencia", "incident"}:
            continue
        if _SKU_TOKEN_RE.fullmatch(token):
            continue  # parece un código de producto, no un ticket
        candidate = token
    return candidate


class AgentState(TypedDict, total=False):
    """Información mínima que un nodo necesita para decidir el siguiente paso."""

    question: str
    context: list[dict[str, Any]]
    answer: str
    error: str
    # Consultas decididas por intent (independientes: puede haber 0, 1 o 2).
    incident_query: dict[str, Any] | None
    inventory_query: dict[str, Any] | None
    use_rag: bool
    # Resultados de las tools (solo lectura), guardados JSON-serializables.
    incident_result: dict[str, Any] | None
    inventory_result: dict[str, Any] | None
    # Resúmenes de las tools para componer la respuesta final con el KB.
    # Reductores de lista: si corren dos tools, ambas aportan al acumulado.
    tool_answers: Annotated[list[str], operator.add]
    tool_context: Annotated[list[dict[str, Any]], operator.add]


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
    """Genera la respuesta final componiendo las fuentes disponibles.

    Decide de forma automática qué hay que componer según lo que corrió:
    - Tool(s) sin contexto KB   -> usa el resumen de la tool (sin LLM).
    - KB (con o sin tool)       -> LLC compone tool(s) + contexto KB en una
      única respuesta ("RAG y/o tool" según lo que haya aportado).
    """
    tool_context = state.get("tool_context") or []
    kb_context = state.get("context") or []
    tool_answers = state.get("tool_answers") or []

    if tool_answers and not kb_context:
        # Tool-only: no hay KB para anclar; la respuesta ES el resumen honesto
        # de la tool (no se inventa contexto).
        answer = "\n".join(tool_answers)
    else:
        # RAG (y tool si la hubo): el LLM compone una única respuesta.
        answer = generate_answer(question=state["question"], context=tool_context + kb_context)

    tracing.add_step("generate", {"answer": answer})
    return {"answer": answer}


def intent_node(state: AgentState) -> dict[str, Any]:
    """Clasifica la intención y decide qué fuentes hacen falta: RAG, tool o ambos.

    Alimenta el estado con ``incident_query`` y/o ``inventory_query`` de forma
    INDEPENDIENTE (cada tool hace una sola cosa; pueden darse las dos a la vez
    si la pregunta menciona un ticket y además stock). Deja las consultas en
    ``None`` cuando no hay intención de tool, y el grafo sigue con retrieve
    (knowledge base). Es deliberadamente conservador.

    La decisión de correr tool(s) y/o RAG es automática a partir de la
    pregunta: el usuario nunca indica qué fuente usar.
    """
    question = (state.get("question") or "").strip()
    incident_query: dict[str, Any] | None = None
    inventory_query: dict[str, Any] | None = None

    if _INCIDENT_INTENT_RE.search(question):
        # El id de ticket se extrae de forma robusta; se ignora cualquier token
        # con forma de SKU para no confundir "stock del ticket X y SKU Y".
        incident_query = (
            {"ticket_id": _extract_ticket_id(question)}
            if _extract_ticket_id(question)
            else {"status": "open"}
        )

    if _INVENTORY_INTENT_RE.search(question):
        sku_match = _SKU_TOKEN_RE.search(question)
        if sku_match:
            inventory_query = {"sku": sku_match.group(0)}
        else:
            # Sin SKU concreto: el agente lista/consulta stock por filtro.
            inventory_query = {"sku_id": None}

    has_tool_intent = incident_query is not None or inventory_query is not None
    # Tool-only para hechos operacionales concretos; RAG para preguntas
    # documentales, y ambos si la pregunta mezcla las dos intenciones.
    use_rag = not has_tool_intent or bool(_RAG_INTENT_RE.search(question))
    out = {
        "incident_query": incident_query,
        "inventory_query": inventory_query,
        "use_rag": use_rag,
    }
    tracing.add_step("intent", out)
    return out


def incident_lookup_node(state: AgentState) -> dict[str, Any]:
    """Ejecuta la tool de consulta de tickets (solo lectura).

    Lee del gestor de incidencias real via lookup_incidents(). Si el
    resultado no está resuelto (timeout, fallo o ticket inexistente),
    produce una respuesta honesta; nunca inventa un estado.

    La tool devuelve un IncidentLookupResult (Pydantic); aquí se guarda
    en el estado como dict JSON-serializable (tracing y checkpointer
    persisten solo JSON) y se añade un resumen en ``tool_context`` para
    que generate pueda componer una respuesta con KB si procede.
    """
    query = state.get("incident_query") or {}
    try:
        result = lookup_incidents(query)
    except Exception as exc:  # la tool ya convierte fallos, por si acaso
        result = IncidentLookupResult(resolved=False, error=str(exc))

    incident_result = result.model_dump(mode="json")
    # Resumen honesto (sin inventar estado) que generate usará para componer.
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

    out = {
        "incident_result": incident_result,
        "tool_answers": [answer],
        "tool_context": [
            {
                "text": answer,
                "source_document": "incident_lookup",
                "section": "consulta de ticket",
            }
        ],
    }
    tracing.add_step("incident_lookup", out)
    return out


def inventory_lookup_node(state: AgentState) -> dict[str, Any]:
    """Ejecuta la tool de consulta de inventario (solo lectura).

    Lee del gestor de inventario real via lookup_inventory(). Si el
    resultado no está resuelto (timeout, fallo o producto inexistente),
    produce una respuesta honesta; nunca inventa una cifra de stock.

    La tool devuelve un InventoryLookupResult (Pydantic); aquí se guarda
    en el estado como dict JSON-serializable (tracing y checkpointer
    persisten solo JSON) y se añade un resumen en ``tool_context`` para
    que generate pueda componer una respuesta con KB si procede.
    """
    query = state.get("inventory_query") or {}
    try:
        result = lookup_inventory(query)
    except Exception as exc:  # la tool ya convierte fallos, por si acaso
        result = InventoryLookupResult(resolved=False, error=str(exc))

    inventory_result = result.model_dump(mode="json")
    # Resumen honesto (sin inventar stock) que generate usará para componer.
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

    out = {
        "inventory_result": inventory_result,
        "tool_answers": [answer],
        "tool_context": [
            {
                "text": answer,
                "source_document": "inventory_lookup",
                "section": "consulta de stock",
            }
        ],
    }
    tracing.add_step("inventory_lookup", out)
    return out


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
    """Enruta a la/s tool/s y/o al RAG de forma automática.

    Cada tool detectada corre (incident_lookup -> inventory_lookup, una
    sola cosa cada una). Hechos operacionales aislados se resuelven solo con
    tool; preguntas documentales con RAG; preguntas mixtas consultan ambas.
    No hay ningún flag que el usuario deba indicar.
    """
    if state.get("incident_query") is not None:
        return "incident_lookup"
    if state.get("inventory_query") is not None:
        return "inventory_lookup"
    return "retrieve"


def route_after_incident_lookup(state: AgentState) -> str:
    """Tras el ticket, consulta inventario o sigue la decisión de fuentes."""
    if state.get("inventory_query") is not None:
        return "inventory_lookup"
    return "retrieve" if state.get("use_rag") else "generate"


def route_after_inventory_lookup(state: AgentState) -> str:
    """Tras las tools, consulta KB solo si la pregunta también lo requiere."""
    return "retrieve" if state.get("use_rag") else "generate"


def route_after_retrieve(state: AgentState) -> str:
    """Tras recuperar el KB: si hay contexto o respuesta de tool, se compone
    la respuesta (generate); si no hay nada, fallback honesto (no_context)."""
    if state.get("context") or state.get("tool_answers"):
        return "generate"
    return "no_context"


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
    "incident_lookup",
    route_after_incident_lookup,
    ["inventory_lookup", "retrieve", "generate"],
)
_builder.add_conditional_edges("inventory_lookup", route_after_inventory_lookup, ["retrieve", "generate"])
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