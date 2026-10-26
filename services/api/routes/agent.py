"""Endpoint del agente RAG (LangGraph).

POST /agent/query  — invoca el grafo compilado y devuelve su estado final.
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

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.rag_agent.graph import graph
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


@router.get("/health")
async def agent_health() -> dict[str, str]:
    return {"status": "ok", "service": "agent", "traces_dir": str(TRACES_DIR)}