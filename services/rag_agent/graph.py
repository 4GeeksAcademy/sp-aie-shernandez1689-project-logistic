"""Grafo de agente RAG con LangGraph (TrackFlow).

Estado del grafo
----------------
- question: pregunta del usuario (entrada).
- context:  chunks recuperados por el nodo retrieve (salida -> entrada).
- answer:   respuesta final generada o mensaje honesto de fallback.
- incident_query: petición de consulta de ticket (salida de intent).
- inventory_query: petición de consulta de inventario (salida de intent).
- tool_answers / tool_context: resúmenes de las tools (acumulados con reductor).
- memory_context: contexto recuperado de la memoria persistente (preferencias,
  hechos aprobados, episodios anteriores). NO es el historial de la
  conversación — el grafo sigue siendo stateless por corrida.
- _memory_proposal: propuesta de memoria generada por ``memorize_node``
  al final de la corrida. Contiene tipo (preference|fact|none), payload
  con la información a persistir, y se incrusta como pregunta en la
  respuesta al usuario. ``remember_node`` de la SIGUIENTE corrida la
  procesa si el usuario confirma o rechaza.

ACTUALIZACIÓN — Memoria persistente con flujo de propuesta (2026-10-27)
-----------------------------------------------------------------------
Se implementa un diseño de **memoria por propuesta explícita**:

1. ``remember``: Al inicio de la corrida, carga memoria existente Y
   procesa propuestas pendientes del checkpoint anterior usando un
   clasificador de intención estructurado (accept|reject|edit|topic_change).
   Si el usuario además hace una nueva pregunta tras resolver la propuesta,
   la conversación continúa con normalidad (no salta a finalize).

2. ``memorize``: Después de ``generate`` (antes de ``finalize``),
   auto-evalúa la interacción. Si ya hay una propuesta pendiente sin
   resolver, NO genera una segunda. **Nunca escribe directamente a memoria.**

3. **Auditoría**: Cada propuesta, aceptación, rechazo, edición o descarte
   se registra en ``data/audit/memory_proposals.jsonl`` con marca de tiempo,
   mensaje origen y resultado.

4. **Cambio de tema = descarte**: Si el usuario cambia de tema sin
   responder clara y directamente a la propuesta, se descarta automáticamente.
   Nunca se asume aprobación por silencio o ambigüedad.

Nodos y aristas (con memoria por propuesta)
---------------------------------------------
  START -> validate -> (error?) -> remember -> (answer? -> finalize)
                                            -> (else -> intent -> ...)
  ... -> generate -> memorize -> finalize -> END

  El resto del flujo (intent → tools → retrieve → generate) permanece
  igual. Los detalles de las aristas están documentados más abajo.

ACTUALIZACIÓN — Migración a MCP (2026-10-27)
---------------------------------------------
Los nodos ``incident_lookup`` e ``inventory_lookup`` ya no importan las
tools directas en-proceso. En su lugar se conectan al TrackFlow MCP
Server (``mcps/trackflow-server``) via ``langchain-mcp-adapters``.

Las tools directas antiguas están marcadas como ``DEPRECATED`` y no deben
usarse. Ver ``services/rag_agent/tools/DEPRECATED.md``.
"""

from __future__ import annotations

import operator
import re
from typing import Annotated, Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph

from data.pipelines.guardrails import (
    GuardrailState,
    classify_bounds,
    isolate_context_chunks,
    redirect_response,
    sanitize_external_content,
    validate_output,
)
from data.pipelines.rag import (
    DEFAULT_MIN_SCORE,
    generate_answer,
    generate_casual_answer,
    retrieve,
)
from services.rag_agent import tracing
from services.rag_agent import mcp_client
from services.rag_agent.memory_nodes import (
    consolidate_node,
    memorize_node,
    remember_node,
)

DEFAULT_K = 5

# Respuesta de respaldo (defense-in-depth) cuando generate_node se alcanza con
# un estado de guardarraíl bloqueado (jailbreak / off_domain / personal_task).
GUARDRAIL_DOMAIN_REDIRECT = (
    "Solo puedo ayudarte con asuntos de TrackFlow — SLAs, devoluciones, "
    "transportistas, tarifas, tickets e inventario. "
    "¿En qué puedo ayudarte con tus operaciones hoy?"
)

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
    """Información mínima que un nodo necesita para decidir el siguiente paso.

    Nodos de memoria:
    - memory_context: contexto recuperado de los stores KV/vectorial
      (preferencias, hechos aprobados, episodios anteriores). Lo produce
      el nodo ``remember`` y lo consume ``generate``.
    - _memory_loaded: flag interno (remember ejecutado).
    - _memorized: metadatos de lo guardado por ``memorize``.
    - _memory_proposal: propuesta de memoria generada por ``memorize``.
      Solo puede haber una pendiente a la vez.
    - _memory_intent_classification: etiqueta estructurada del clasificador
      de intención (accept|reject|edit|topic_change|ambiguous) producida
      por ``remember``.
    """

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
    # Memoria persistente (nodos remember / memorize).
    memory_context: dict[str, Any]
    _memory_loaded: bool
    _memorized: dict[str, Any]
    # Propuesta de memoria generada por generate_node para que el usuario
    # confirme en la siguiente interacción. Contiene:
    #   - "text": pregunta en lenguaje natural para el usuario
    #   - "type": tipo de memoria propuesta (preference | fact | none)
    #   - "payload": dict con la información a persisitir si se acepta
    _memory_proposal: dict[str, Any]    # Clasificación de intención estructurada del mensaje del usuario
    # frente a la propuesta pendiente (producida por remember_node).
    # Valores: "accept" | "reject" | "edit" | "topic_change" | "ambiguous"
    _memory_intent_classification: str | None
    # Consolidación de memoria.
    consolidation_pending: bool
    _consolidation_report: dict[str, Any]
    # Guardarraíl: resultado de classify_bounds sobre la pregunta.
    # Valores: "jailbreak" | "off_domain" | "domain" | "small_talk" |
    #          "personal_task" | "casual"
    guardrail_state: str

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
    - KB (con o sin tool)       -> LLM compone tool(s) + contexto KB en una
      única respuesta ("RAG y/o tool" según lo que haya aportado).

    Si el nodo ``remember`` cargó ``memory_context`` (preferencias, hechos
    aprobados, episodios anteriores), se inyecta en la respuesta como
    contexto adicional para personalización.
    """
    tool_context = state.get("tool_context") or []
    kb_context = state.get("context") or []
    tool_answers = state.get("tool_answers") or []
    memory_ctx = state.get("memory_context") or {}

    # Defense-in-depth: si por cualquier camino futuro el flujo llega aquí con
    # un estado de guardarraíl bloqueante, NUNCA se abre el LLM: se devuelve
    # la redirección (o la respuesta ya fijada por el nodo guardrail).
    gr_state = state.get("guardrail_state")
    if gr_state in (
        GuardrailState.JAILBREAK.value,
        GuardrailState.OFF_DOMAIN.value,
        GuardrailState.PERSONAL_TASK.value,
    ):
        answer = state.get("answer") or GUARDRAIL_DOMAIN_REDIRECT
        tracing.add_step("generate", {"answer": answer})
        return {"answer": answer}

    # Inyectar memoria como prefacio contextual si existe.
    memory_lines = []

    # Mensaje de resultado de propuesta de memoria (aceptada/rechazada/editada)
    proposal_result = memory_ctx.get("_proposal_result")
    if proposal_result:
        memory_lines.append(proposal_result)

    if memory_ctx.get("prefs"):
        prefs = memory_ctx["prefs"]
        if isinstance(prefs, dict):
            pref_strs = [f"  - {k}: {v}" for k, v in prefs.items()]
            memory_lines.append("Preferencias del usuario:\n" + "\n".join(pref_strs))
    if memory_ctx.get("episodes"):
        episodes = memory_ctx["episodes"]
        if isinstance(episodes, list) and episodes:
            last = episodes[-1]
            if isinstance(last, dict) and last.get("summary"):
                memory_lines.append(
                    f"Resumen del episodio anterior: {last['summary']}"
                )
    if memory_ctx.get("semantic_hits"):
        hits = memory_ctx["semantic_hits"]
        if isinstance(hits, list) and hits:
            memory_lines.append(
                "Hechos recordados relevantes:\n"
                + "\n".join(f"  - {h}" for h in hits[:3])
            )

    if tool_answers and not kb_context:
        # Tool-only: no hay KB para anclar; la respuesta ES el resumen honesto
        # de la tool (no se inventa contexto). El texto de la tool se aísla
        # como dato no confiable (anti-inyección) por si un resultado externo
        # incrustara instrucciones.
        safe_tool_answers = [
            sanitize_external_content(a) for a in (tool_answers or [])
        ]
        if memory_lines:
            answer = "\n".join(memory_lines + [""] + safe_tool_answers)
        else:
            answer = "\n".join(safe_tool_answers)
    else:
        # RAG (y tool si la hubo): el LLM compone una única respuesta.
        # ANTI-INYECCIÓN: el contenido externo (fragmentos RAG + resúmenes
        # de tools + memoria) se aísla como DATO NO CONFIABLE antes de
        # llegar al LLM. Cualquier instrucción incrustada en un documento
        # recuperado se neutraliza; el modelo lo trata como contenido sobre
        # el que responder, nunca como órdenes que obedecer.
        combined_context = isolate_context_chunks(tool_context + kb_context)
        if memory_lines:
            combined_context = (
                [{"text": "\n".join(memory_lines), "source_document": "agent_memory", "section": "memoria"}]
                + combined_context
            )
        answer = generate_answer(
            question=state["question"],
            context=combined_context,
        )

    # Validación de salida (guardarraíl de contenido):
    #  - Si la pregunta es casual (permitida), se fuerza un cierre con
    #    redirección al contexto de la empresa.
    #  - Se valida que la respuesta no filtre instrucciones internas, datos
    #    sensibles del BRIEFING ni formato malformado; si no es válida, se
    #    sustituye por una respuesta segura.
    if gr_state == GuardrailState.CASUAL.value:
        answer = (answer or "").strip()
        if not answer:
            answer = GUARDRAIL_DOMAIN_REDIRECT
        else:
            answer = answer.rstrip() + (
                "\n\nY hablando de logística: solo puedo ayudarte con asuntos "
                "de TrackFlow — SLAs, devoluciones, transportistas, tarifas, "
                "tickets e inventario. ¿En qué puedo ayudarte hoy?"
            )
    else:
        valid, _reason = validate_output(answer)
        if not valid:
            answer = GUARDRAIL_DOMAIN_REDIRECT

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
    """Ejecuta la tool de consulta de tickets via MCP (solo lectura).

    Se conecta al TrackFlow MCP Server (``mcps/trackflow-server``) via
    ``langchain-mcp-adapters`` para obtener la información de tickets.
    Sustituye a la antigua tool directa ``services/rag_agent/tools/incident_lookup.py``
    (DEPRECATED — ver ``services/rag_agent/tools/DEPRECATED.md``).

    Si el resultado no está resuelto (timeout, fallo o ticket inexistente),
    produce una respuesta honesta; nunca inventa un estado.

    El resultado se guarda como dict JSON-serializable (tracing y
    checkpointer persisten solo JSON) y se añade un resumen en
    ``tool_context`` para que generate pueda componer una respuesta
    con KB si procede.

    Contrato de salida: idéntico al anterior (campos ``resolved``,
    ``ticket_id``, ``status``, ``origin``, ``branch``, ``error``, etc.)
    para que el resto del grafo no requiera cambios.
    """
    query = state.get("incident_query") or {}
    try:
        result = mcp_client.lookup_incidents_mcp(query)
    except Exception as exc:
        result = {"resolved": False, "error": str(exc)}

    incident_result = result
    # Resumen honesto (sin inventar estado) que generate usará para componer.
    if result.get("resolved") and result.get("ticket_id"):
        answer = (
            f"El ticket {result['ticket_id']} está "
            f"{result.get('status', 'desconocido')} "
            f"(origen: {result.get('origin', 'desconocido')}, "
            f"sucursal: {result.get('branch', 'desconocida')})."
        )
    elif result.get("resolved"):
        answer = result.get("description") or "Consulta de incidencias completada."
    else:
        answer = (
            "No pude confirmar el estado de ese ticket ahora mismo. "
            + (result.get("error") or "Inténtalo de nuevo más tarde.")
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
    """Ejecuta la tool de consulta de inventario via MCP (solo lectura).

    Se conecta al TrackFlow MCP Server (``mcps/trackflow-server``) via
    ``langchain-mcp-adapters`` para obtener la información de stock.
    Sustituye a la antigua tool directa
    ``services/rag_agent/tools/inventory_lookup.py``
    (DEPRECATED — ver ``services/rag_agent/tools/DEPRECATED.md``).

    Si el resultado no está resuelto (timeout, fallo o producto inexistente),
    produce una respuesta honesta; nunca inventa una cifra de stock.

    El resultado se guarda como dict JSON-serializable (tracing y
    checkpointer persisten solo JSON) y se añade un resumen en
    ``tool_context`` para que generate pueda componer una respuesta
    con KB si procede.

    Contrato de salida: idéntico al anterior (campos ``resolved``,
    ``product``, ``error``, etc.) para que el resto del grafo no
    requiera cambios.
    """
    query = state.get("inventory_query") or {}
    try:
        result = mcp_client.lookup_inventory_mcp(query)
    except Exception as exc:
        result = {"resolved": False, "error": str(exc)}

    inventory_result = result
    # Resumen honesto (sin inventar stock) que generate usará para componer.
    if result.get("resolved") and result.get("product"):
        product = result["product"]
        answer = (
            f"El producto {product['sku']} ({product['name']}) tiene "
            f"{product['current_stock']} unidades en stock "
            f"(almacén {product['warehouse']}, categoría {product['category']})."
        )
    elif result.get("resolved"):
        answer = result.get("description") or "Consulta de inventario completada."
    else:
        answer = (
            "No pude consultar el stock de ese producto ahora mismo. "
            + (result.get("error") or "Inténtalo de nuevo más tarde.")
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


# ── Nodo guardarraíl ────────────────────────────────────────────────────
#
# Primera línea de defensa DETERMINISTA (sin LLM): clasifica la pregunta con
# ``classify_bounds`` ANTES de abrir el LLM, retrieve o tools. Según el estado:
#
#   - jailbreak     -> rechazo firme + redirección (el LLM NUNCA se abre).
#   - personal_task -> rechazo: solicitud de uso personal ajena al dominio de
#                      TrackFlow + redirección al propósito del agente.
#   - off_domain    -> fuera del dominio comercial -> redirección (sin LLM).
#   - small_talk    -> saludo cortés -> respuesta breve + redirección suave.
#   - casual        -> pregunta general permitida -> se responde con LLM y
#                      la respuesta cierra redirigiendo al contexto empresa.
#   - domain        -> dentro del dominio -> flujo normal (LLM + tools).


def guardrail_node(state: AgentState) -> dict[str, Any]:
    """Clasifica la pregunta y, si es bloqueante, produce la respuesta final.

    Reglas de prioridad:
    - El jailbreak SIEMPRE es bloqueante (aunque haya propuesta de memoria
      pendiente): nunca se trata como confirmación.
    - personal_task y off_domain bloquean la corrida (sin LLM ni tools).
    - small_talk se responde breve y se redirige suavemente (sin LLM).
    - casual se marca como permisible (el LLM responderá y un post-proceso
      añadirá la redirección al cierre).
    - domain continúa con el flujo normal.
    """
    question = state.get("question") or ""
    gr_state = classify_bounds(question)

    out: dict[str, Any] = {"guardrail_state": gr_state.value}

    # Respuestas con redirección (cortan la corrida, salvo la propuesta de
    # memoria que se procesa primero en el estado "recuerda").
    if gr_state is GuardrailState.JAILBREAK:
        out["answer"] = (
            "No puedo ignorar mis instrucciones ni cambiar mi rol: soy el "
            "asistente de TrackFlow. " + redirect_response()
        )
        tracing.add_step("guardrail", {"state": gr_state.value, "answer": out["answer"]})
        return out

    if gr_state is GuardrailState.PERSONAL_TASK:
        out["answer"] = (
            "No puedo ayudarte con esa tarea: es un uso personal ajeno a mi "
            "propósito. " + redirect_response()
        )
        tracing.add_step("guardrail", {"state": gr_state.value, "answer": out["answer"]})
        return out

    if gr_state is GuardrailState.OFF_DOMAIN:
        out["answer"] = redirect_response()
        tracing.add_step("guardrail", {"state": gr_state.value, "answer": out["answer"]})
        return out

    if gr_state is GuardrailState.SMALL_TALK:
        out["answer"] = "¡Hola!" if "hola" in question.lower() else redirect_response()
        # Si hay propuesta de memoria pendiente, la respuesta final la genera
        # remember_node al procesar la propuesta (no esta redirección suave).
        tracing.add_step("guardrail", {"state": gr_state.value, "answer": out["answer"]})
        return out

    if gr_state is GuardrailState.CASUAL:
        # Pregunta general permitida: NUNCA bloquea (el LLM responde). La
        # redirección al cierre la añade generate_node (validate_output).
        out["answer"] = ""  # limpiar cualquier respuesta obsoleta del estado.
        tracing.add_step("guardrail", {"state": gr_state.value, "answer": "casual_allowed"})
        return out

    # DOMAIN (y cualquier otro estado no bloqueante): limpiar respuestas
    # obsoletas del checkpoint y continuar el flujo normal.
    out["answer"] = ""
    tracing.add_step("guardrail", {"state": gr_state.value, "answer": "continue"})
    return out


def route_after_guardrail(state: AgentState) -> str:
    """Tras el guardarraíl decide: continuar (remember) o finalizar.

    - jailbreak / personal_task / off_domain: respuesta ya generada -> finalize
      (pero si hay propuesta de memoria pendiente y es small_talk/off_domain,
      primero se procesa en remember; el jailbreak y personal_task siempre
      rechazan y cortan).
    - small_talk con propuesta pendiente -> remember (procesa la propuesta).
    - small_talk sin propuesta -> finalize.
    - casual -> casual (nodo dedicado: responde con LLM + redirección al
      cierre; sin retrieve/tools porque la KB no contiene esos datos).
    - domain -> remember (flujo normal).
    """
    gr_state = state.get("guardrail_state")

    if gr_state == GuardrailState.JAILBREAK.value:
        return "finalize"
    if gr_state in (GuardrailState.PERSONAL_TASK.value, GuardrailState.OFF_DOMAIN.value):
        # Si hay propuesta de memoria pendiente, se procesa primero (remember)
        # y la nueva respuesta bloqueada será la de la propuesta resuelta.
        pending = state.get("_memory_proposal") or {}
        proposal_type = pending.get("type")
        if proposal_type not in (None, "none", ""):
            return "remember"
        return "finalize"
    if gr_state == GuardrailState.SMALL_TALK.value:
        pending = state.get("_memory_proposal") or {}
        proposal_type = pending.get("type")
        if proposal_type not in (None, "none", ""):
            return "remember"
        return "finalize"
    if gr_state == GuardrailState.CASUAL.value:
        return "casual"
    # domain y resto -> flujo normal.
    return "remember"


def casual_node(state: AgentState) -> dict[str, Any]:
    """Responde una pregunta general/casual PERMITIDA y cierra redirigiendo.

    Solo se alcanza cuando ``guardrail_node`` clasificó la pregunta como
    ``GuardrailState.CASUAL`` (hora, fecha, conversión simple). La base de
    conocimiento de TrackFlow NO contiene estos datos, por lo que se consulta
    el LLM directamente (``generate_casual_answer``) sin retrieve() ni tools.

    La respuesta es la del LLM + una frase final que redirige al contexto de
    la empresa (requisito de la tarea: "permite preguntas casuales pero
    cierra la respuesta reconduciendo hacia el contexto de la empresa").
    """
    question = state.get("question") or ""
    answer = generate_casual_answer(question)
    answer = (answer or "").strip()
    if not answer:
        answer = GUARDRAIL_DOMAIN_REDIRECT
    else:
        # Asegurar el cierre con redirección al dominio (si el LLM no lo hizo).
        if "TrackFlow" not in answer:
            answer = answer.rstrip() + (
                "\n\nY hablando de logística: solo puedo ayudarte con asuntos "
                "de TrackFlow — SLAs, devoluciones, transportistas, tarifas, "
                "tickets e inventario. ¿En qué puedo ayudarte hoy?"
            )
    out = {"answer": answer}
    tracing.add_step("casual", out)
    return out


def route_after_validate(state: AgentState) -> str:
    if state.get("error"):
        return "error"
    return "guardrail"


def route_after_remember(state: AgentState) -> str:
    """Tras cargar memoria, decide el siguiente paso.

    Si ``remember_node`` ya generó una respuesta y la clasificación de
    intención indica que fue una confirmación pura (sin nueva pregunta),
    salta directamente a ``finalize``.

    Si el usuario combinó la respuesta a la propuesta con una nueva
    pregunta (ej. "sí, y dime el stock del SKU X"), la propuesta se
    resuelve pero la conversación continúa: routea a ``intent`` para
    procesar la nueva pregunta con el mismo mensaje.

    Si no había propuesta pendiente, sigue a ``intent`` como siempre.
    """
    from services.rag_agent.memory_nodes import _is_pure_confirmation

    has_answer = bool(state.get("answer"))
    intent_class = state.get("_memory_intent_classification")

    if intent_class in ("accept", "reject"):
        if has_answer:
            # Si el mensaje era solo confirmación (sin nueva pregunta), finaliza
            question = (state.get("question") or "").strip()
            if _is_pure_confirmation(question):
                return "finalize"
            # Mensaje combinado: confirmación + nueva pregunta → sigue a intent
            return "intent"

    if intent_class in ("edit", "topic_change", "ambiguous"):
        # Propuesta descartada, el usuario preguntó algo → continúa
        return "intent"

    # Sin propuesta pendiente: comportamiento normal
    if has_answer:
        return "finalize"
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


def route_after_generate(state: AgentState) -> str:
    """Tras generar la respuesta, se memoriza la interacción."""
    return "memorize"


def route_after_no_context(state: AgentState) -> str:
    """Tras no_context también se memoriza (por si hay preferencias)."""
    return "memorize"


def route_after_memorize(state: AgentState) -> str:
    """Tras memorize, va a consolidate si hay solicitud pendiente, o finaliza."""
    if state.get("consolidation_pending"):
        return "consolidate"
    return "finalize"


def route_after_consolidate(state: AgentState) -> str:
    """Tras consolidación, siempre finaliza."""
    return "finalize"


# ── Construcción y compilación (antes de cualquier ejecución) ───────────

_builder = StateGraph(AgentState)
_builder.add_node("validate", validate)
_builder.add_node("guardrail", guardrail_node)
_builder.add_node("casual", casual_node)
_builder.add_node("remember", remember_node)
_builder.add_node("intent", intent_node)
_builder.add_node("incident_lookup", incident_lookup_node)
_builder.add_node("inventory_lookup", inventory_lookup_node)
_builder.add_node("retrieve", retrieve_node)
_builder.add_node("generate", generate_node)
_builder.add_node("no_context", no_context)
_builder.add_node("memorize", memorize_node)
_builder.add_node("consolidate", consolidate_node)
_builder.add_node("error", error_node)
_builder.add_node("finalize", finalize)

_builder.add_edge(START, "validate")
_builder.add_conditional_edges(
    "validate", route_after_validate, ["error", "guardrail"]
)
_builder.add_conditional_edges(
    "guardrail", route_after_guardrail, ["casual", "remember", "finalize"]
)
_builder.add_edge("casual", "finalize")
_builder.add_conditional_edges(
    "remember", route_after_remember, ["intent", "finalize"]
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
_builder.add_conditional_edges(
    "generate", route_after_generate, ["memorize"]
)
_builder.add_conditional_edges(
    "no_context", route_after_no_context, ["memorize"]
)
_builder.add_conditional_edges(
    "memorize", route_after_memorize, ["consolidate", "finalize"]
)
_builder.add_conditional_edges(
    "consolidate", route_after_consolidate, ["finalize"]
)
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