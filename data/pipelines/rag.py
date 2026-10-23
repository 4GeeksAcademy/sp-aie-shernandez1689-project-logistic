"""
Pipeline de recuperacion (retrieve) y generacion (query) para el RAG
de TrackFlow.

Funciones publicas
------------------
retrieve(question: str, top_k: int = 3, score_threshold: float = 0.45) -> list[dict]
    Recupera los chunks mas relevantes desde Qdrant para una pregunta.

query(question: str, top_k: int = 3) -> dict
    Recupera contexto y genera una respuesta usando el LLM de 4Geeks.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv
from qdrant_client import QdrantClient

# Reutilizamos embed() del modulo de proceso
from data.process.rag import COLLECTION_NAME, embed

load_dotenv(
    os.path.join(os.path.dirname(__file__), "..", "..", "services", "api", ".env")
)

logger = logging.getLogger(__name__)

# ── Configuracion LLM (modelo de generacion SEPARADO del de embeddings) ──
LLM_API_KEY: str | None = os.getenv("LLM_API_KEY")
LLM_MODEL_ID: str = os.getenv("LLM_MODEL_ID", "gpt-4o-mini")
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.4geeks.com/v1")
SIMILARITY_THRESHOLD: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.45"))

QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY: str | None = os.getenv("QDRANT_API_KEY", None)

_qdrant: QdrantClient | None = None


def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant


# ── Retrieve ────────────────────────────────────────────────────────


def retrieve(
    question: str,
    top_k: int = 3,
    score_threshold: float | None = None,
) -> list[dict[str, Any]]:
    """Recupera los chunks mas relevantes desde Qdrant para una pregunta.

    Parameters
    ----------
    question : str
        Pregunta del usuario.
    top_k : int
        Numero maximo de chunks a recuperar.
    score_threshold : float or None
        Umbral minimo de similitud. Por defecto usa SIMILARITY_THRESHOLD.

    Returns
    -------
    Lista de dicts con keys: text, source_document, section, score, language.
    Si ningun chunk supera el umbral, devuelve lista vacia.
    """
    if score_threshold is None:
        score_threshold = SIMILARITY_THRESHOLD

    # 1. Generar embedding de la pregunta
    question_vector = embed(question)

    client = _get_qdrant()
    result = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=question_vector,
        limit=top_k,
        score_threshold=score_threshold,
    )

    hits = []
    for scored_point in result:
        hits.append(
            {
                "text": scored_point.payload.get("text", ""),
                "source_document": scored_point.payload.get("source_document", ""),
                "section": scored_point.payload.get("section", ""),
                "score": scored_point.score,
                "language": scored_point.payload.get("language", ""),
            }
        )

    return hits


# ── Query (recuperacion + generacion) ───────────────────────────────


SYSTEM_PROMPT = """Eres un asistente comercial de TrackFlow, una empresa de logistica 
de e-commerce. Debes responder preguntas de account managers y clientes 
basandote exclusivamente en los documentos de la base de conocimiento.

REGLAS ESTRICTAS:
1. Responde SOLO con la informacion de los fragmentos proporcionados abajo.
2. NUNCA inventes porcentajes, tarifas, plazos o condiciones.
3. Si la pregunta menciona descuentos fuera de los rangos documentados,
   responde que requieren aprobacion de Miguel Torres (Direccion Comercial).
4. Durante picos de demanda declarados (Black Friday, Cyber Monday, rebajas 
   de enero, Prime Day), los SLAs se extienden -- sigue la advertencia del 
   documento de SLA.
5. Las devoluciones internacionales NUNCA son automaticas -- requieren 
   gestion manual.
6. Si no hay informacion suficiente para responder, dilo explicitamente.
7. Cita siempre la fuente (documento y seccion) de donde obtuviste la 
   informacion.
8. Responde en el mismo idioma de la pregunta (espanol o ingles)."""


def query(question: str, top_k: int = 3) -> dict[str, Any]:
    """Recupera contexto y genera una respuesta usando el LLM de 4Geeks.

    Returns
    -------
    dict con claves:
        question: str
        answer: str
        sources: list[dict]  -- chunks utilizados (con score y fuente)
        has_sufficient_info: bool
    """
    # 1. Recuperar contexto
    hits = retrieve(question, top_k=top_k)

    if not hits:
        return {
            "question": question,
            "answer": "Lo siento, no tengo informacion suficiente para "
            "responder a esa pregunta. Consulta con el equipo comercial "
            "de TrackFlow para obtener datos actualizados.",
            "sources": [],
            "has_sufficient_info": False,
        }

    # 2. Armar contexto
    context_blocks = []
    for i, hit in enumerate(hits, 1):
        context_blocks.append(
            f"[Fuente {i}] Documento: {hit['source_document']} | "
            f"Seccion: {hit['section']}\n{hit['text']}"
        )
    context_str = "\n\n".join(context_blocks)

    # 3. Llamar al LLM de generacion
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL_ID,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Contexto:\n{context_str}\n\n"
                f"Pregunta: {question}",
            },
        ],
        "temperature": 0.1,
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            answer = data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.exception("Error al llamar al LLM: %s", exc)
        answer = (
            "Error al generar la respuesta. Por favor, intenta de nuevo "
            "mas tarde."
        )

    return {
        "question": question,
        "answer": answer,
        "sources": hits,
        "has_sufficient_info": True,
    }