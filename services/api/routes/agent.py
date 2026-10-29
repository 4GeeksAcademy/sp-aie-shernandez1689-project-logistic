"""Endpoint del agente RAG (LangGraph).

POST /agent/query  — invoca el grafo compilado y devuelve su estado final.
POST /agent/sessions — abre un flujo ``chat_session`` (flow_id = thread_id).
WS   /agent/ws     — el mismo grafo como chat en tiempo real de
                     ``first_line_cx``: tokens en streaming, interrupción,
                     reconexión con rehidratación y control de operador
                     pause/resume/cancel (ver ``agent_chat.py``).
GET  /agent/health — health check.

El endpoint NO contiene lógica de negocio: solo arma el input, llama a
``graph.invoke`` y formatea la salida. El enrutamiento, la recuperación
(Qdrant) y la generación (LLM) viven en services/rag_agent/graph.py y en
data/pipelines/rag.py.

Errores: cualquier fallo dentro del grafo se traduce a un 500 con un
mensaje claro y se registra en logs — nunca un stack trace crudo.

Convive con el endpoint RAG clásico (POST /api/rag/query); la respuesta
sigue anclada al mismo knowledge base (retrieve/generate_answer).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, Field

from agent_chat import (
    CLOSE_BAD_REQUEST,
    CLOSE_FORBIDDEN,
    CLOSE_NOT_FOUND,
    CLOSE_SESSION_CLOSED,
    CLOSE_UNAUTHORIZED,
    CONTROL_ROLES,
    FLOW_TYPE,
    chat_service,
)
from auth import CurrentUser, authenticate_token, get_current_user
from services.rag_agent.graph import graph
from services.rag_agent import guardrail_observability as obs
from services.rag_agent.tracing import TRACES_DIR, load_trace

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentQueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)


class AgentQueryResponse(BaseModel):
    question: str
    answer: str
    nodes_executed: list[str]
    thread_id: str


@router.post("/query", response_model=AgentQueryResponse)
def agent_query(request: AgentQueryRequest) -> Any:
    """Ejecuta el grafo del agente para una pregunta."""
    thread_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    try:
        state = graph.invoke({"question": request.question}, config)
    except Exception as exc:  # fallo en cualquier nodo del grafo
        logger.exception("Fallo del grafo en thread_id=%s: %s", thread_id, exc)
        raise HTTPException(
            status_code=500,
            detail="No se pudo procesar la pregunta con el agente. "
            "Inténtalo de nuevo más tarde.",
        ) from exc

    answer = state.get("answer") or ""
    if state.get("error"):
        raise HTTPException(status_code=400, detail=state["error"])

    # Nodos ejecutados según el trace persistido (mismo thread_id).
    try:
        nodes = [s["node"] for s in load_trace(thread_id)["steps"]]
    except FileNotFoundError:  # trazabilidad no debe tumbar la respuesta
        nodes = []

    return AgentQueryResponse(
        question=request.question,
        answer=answer,
        nodes_executed=nodes,
        thread_id=thread_id,
    )


class ChatSessionCreated(BaseModel):
    agent_id: str
    flow_id: str
    flow_type: str
    created_at: str


@router.post("/sessions", response_model=ChatSessionCreated, status_code=201)
def create_chat_session(user: CurrentUser = Depends(get_current_user)) -> Any:
    """Abre un flujo ``chat_session`` de ``first_line_cx`` para ``WS /agent/ws``."""
    session = chat_service.create_session(user.id)
    return ChatSessionCreated(**session.ids(flow_type=FLOW_TYPE, created_at=session.created_at))


@router.websocket("/ws")
async def agent_chat_ws(
    websocket: WebSocket, token: str | None = None, flow_id: str | None = None
) -> None:
    """Chat en streaming con ``first_line_cx`` ligado a una sesión existente.

    ``?token=`` (JWT de user-api; el navegador no puede mandar cabeceras en
    el handshake) y ``?flow_id=`` (sesión de ``POST /agent/sessions`` = thread
    de LangGraph) son obligatorios. Si algo falla, el socket se cierra con un
    código 44xx antes de enviar ningún evento de chat; si no, el primer
    evento es ``session_restored`` (ver ``agent_chat.py``).
    """
    await websocket.accept()
    try:
        user = authenticate_token((token or "").strip())
    except HTTPException:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return
    if not flow_id:
        await websocket.close(code=CLOSE_BAD_REQUEST)
        return
    session = chat_service.get(flow_id)
    if session is None:
        await websocket.close(code=CLOSE_NOT_FOUND)
        return
    # Sesión ajena: solo un rol de control, como observador (supervisor).
    observer = session.owner_id != user.id
    if observer and user.role not in CONTROL_ROLES:
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    if session.closed:
        await websocket.close(code=CLOSE_SESSION_CLOSED)
        return
    await chat_service.serve(websocket, session, user, graph, observer)


@router.get("/health")
async def agent_health() -> dict[str, str]:
    return {"status": "ok", "service": "agent", "traces_dir": str(TRACES_DIR)}


@router.get("/guardrails/summary")
async def guardrails_summary() -> dict[str, Any]:
    """Resumen simple de cuántas veces se activó cada guardarraíl.

    Reconstruye el resumen a partir del log persistido en
    ``AGENT_GUARDRAIL_EVENTS_FILE`` (data/audit/guardrail_events.jsonl),
    de modo que refleja la sesión de pruebas completa (no solo la memoria
    del proceso actual). Devuelve total y desglose por estado y por tipo
    de fallo (structural | content | security).
    """
    try:
        return obs.summary_from_file()
    except OSError as exc:
        logger.warning("No se pudo leer el log de guardarraíles: %s", exc)
        return obs.build_summary({}, {})