"""
Router FastAPI para el endpoint de consulta RAG de TrackFlow.

Endpoints
---------
POST /api/rag/query
    {
        "question": "string",
        "k": 5,
        "min_score": 0.45
    }
    -> 200
    {
        "question": "string",
        "answer": "string",
        "sources": [...],
        "has_sufficient_info": bool
    }

GET /api/rag/health -> {"status": "ok", "service": "rag"}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Importamos retrieve() y generate_answer() por separado
from data.pipelines.rag import generate_answer, retrieve

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["rag"])


# ── Schemas ────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    k: int = Field(default=5, ge=1, le=10)
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)


class SourceItem(BaseModel):
    text: str
    source_document: str
    section: str
    score: float
    language: str


class QueryResponse(BaseModel):
    question: str
    answer: str
    sources: list[SourceItem] = []
    has_sufficient_info: bool = True


# ── Endpoint ───────────────────────────────────────────────────────


@router.post("/query", response_model=QueryResponse)
async def query_endpoint(request: QueryRequest) -> Any:
    """Responde una pregunta usando la base de conocimiento de TrackFlow.

    Orquesta retrieve(llamada a Qdrant) → generate_answer(llamada al LLM)
    como pasos separados, permitiendo reutilización individual.
    """
    try:
        # 1. Recuperar contexto desde Qdrant
        context = retrieve(
            query=request.question,
            k=request.k,
            min_score=request.min_score if request.min_score is not None else 0.45,
        )

        # 2. Generar respuesta con el LLM
        answer = generate_answer(
            question=request.question,
            context=context,
        )

        return QueryResponse(
            question=request.question,
            answer=answer,
            sources=[SourceItem(**hit) for hit in context] if context else [],
            has_sufficient_info=len(context) > 0,
        )
    except Exception as exc:
        logger.exception("Error en RAG query: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "rag"}
