"""
Router FastAPI para el endpoint de consulta RAG de TrackFlow.

Endpoints
---------
POST /api/rag/query
    {
        "question": "string",
        "top_k": 3
    }
    -> 200
    {
        "question": "string",
        "answer": "string",
        "sources": [...],
        "has_sufficient_info": bool
    }
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# Importamos el pipeline de RAG
from data.pipelines.rag import query as rag_query

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/rag", tags=["rag"])


# ── Schemas ────────────────────────────────────────────────────────


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    top_k: int = Field(default=3, ge=1, le=10)


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
    """Responde una pregunta usando la base de conocimiento de TrackFlow."""
    try:
        result = rag_query(question=request.question, top_k=request.top_k)
        return QueryResponse(**result)
    except Exception as exc:
        logger.exception("Error en RAG query: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "rag"}