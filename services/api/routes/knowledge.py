"""
Router FastAPI para el endpoint público mínimo de consulta Knowledge.

Endpoints
---------
POST /knowledge/query
    {
        "question": "string"
    }
    -> 200
    {
        "answer": "string"
    }

GET /knowledge/health -> {"status": "ok", "service": "knowledge"}

NOTA: Este endpoint NO devuelve chunks, puntuaciones ni datos crudos de Qdrant.
      Solo expone la respuesta generada por el modelo (contrato Fase 3).
      Los detalles de recuperación se registran en servidor para depuración.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Única importación del pipeline — sin lógica duplicada.
from data.pipelines.rag import query as rag_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


# ── Schemas ────────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        min_length=1,
        max_length=1000,
    )


class QueryResponse(BaseModel):
    answer: str


# ── Endpoint ───────────────────────────────────────────────────────────


@router.post("/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest) -> QueryResponse:
    """Responde una pregunta usando la base de conocimiento de TrackFlow.

    El endpoint delega completamente en query() del pipeline RAG:
    retrieve() → generate_answer() — todo en data/pipelines/rag.py.

    No se exponen chunks, scores ni metadatos de Qdrant al cliente.
    Cualquier detalle de depuración se registra exclusivamente en servidor.
    """
    start = time.perf_counter()
    logger.info("Knowledge query recibida: %.80s", request.question)

    try:
        answer = rag_query(question=request.question)
    except Exception as exc:
        logger.exception("Error en knowledge query: %s", exc)
        raise HTTPException(status_code=500, detail="Error interno del servidor.")

    elapsed = time.perf_counter() - start
    logger.info(
        "Knowledge query completada en %.2fs — respuesta %d caracteres",
        elapsed,
        len(answer),
    )

    return QueryResponse(answer=answer)


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "knowledge"}