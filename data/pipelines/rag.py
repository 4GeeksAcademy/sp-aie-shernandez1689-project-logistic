"""
Pipeline de recuperación (retrieve) y generación (generate_answer) para el RAG
de TrackFlow.

Funciones públicas
------------------
retrieve(query: str, *, k: int = 5, min_score: float) -> list[dict]
    Recupera los chunks más relevantes desde Qdrant para una consulta.

generate_answer(question: str, context: list[dict]) -> str
    Arma el prompt con el contexto y llama al LLM de generación.

query(question: str, *, k: int = 5, min_score: float | None = None) -> str
    Orquesta retrieve() + generate_answer() — devuelve solo la respuesta texto.
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
DEFAULT_MIN_SCORE: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.45"))

QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY: str | None = os.getenv("QDRANT_API_KEY", None)

_qdrant: QdrantClient | None = None


def _get_qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _qdrant


# ── System prompt (voz y audiencia desde company-BRIEFING) ──────────────

SYSTEM_PROMPT = """Eres un asistente comercial de TrackFlow, una empresa de logística para e-commerce.
Debes responder preguntas de account managers y clientes como lo haría un vendedor de TrackFlow 
en una llamada: con datos exactos, sin prometer condiciones que no existen en los acuerdos estándar.

Tu respuesta debe basarse EXCLUSIVAMENTE en los fragmentos de la base de conocimiento 
proporcionados abajo.

REGLAS ESTRICTAS:
1. Responde SOLO con la información de los fragmentos proporcionados.
2. NUNCA inventes porcentajes, tarifas, plazos o condiciones.
3. Si la pregunta menciona descuentos fuera de los rangos documentados,
   responde que requieren aprobación de Miguel Torres (Dirección Comercial).
4. Durante picos de demanda declarados (Black Friday, Cyber Monday, rebajas
   de enero, Prime Day), los SLAs se extienden — sigue la advertencia del
   documento de SLA.
5. Las devoluciones internacionales NUNCA son automáticas — requieren
   gestión manual.
6. Si no hay información suficiente para responder, dilo explícitamente.
7. Cita siempre la fuente (documento y sección) de donde obtuviste la
   información.
8. Responde en el mismo idioma de la pregunta (español o inglés).
9. No prometas un SLA de entrega durante fechas de alta demanda declaradas.
10. Ningún descuento de almacenamiento puede ofrecerse sin mencionar que
    requiere aprobación de Miguel Torres."""


# ── Retrieve ───────────────────────────────────────────────────────────


def retrieve(
    query: str,
    *,
    k: int = 5,
    min_score: float,
) -> list[dict[str, Any]]:
    """Recupera los chunks más relevantes desde Qdrant para una consulta.

    Parameters
    ----------
    query : str
        Consulta del usuario.
    k : int
        Número máximo de vecinos a recuperar. Por defecto 5.
    min_score : float
        Umbral mínimo de similitud (obligatorio). Los resultados por
        debajo de este score son descartados.

    Returns
    -------
    list[dict]
        Lista de diccionarios con claves: text, source_document, section,
        score, language. Si ningún resultado supera el umbral, devuelve
        lista vacía.
    """
    # 1. Generar embedding de la consulta
    query_vector = embed(query)

    client = _get_qdrant()
    result = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        limit=k,
        score_threshold=min_score,
    )

    hits: list[dict[str, Any]] = []
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


# ── Generate answer (prompt + LLM) ─────────────────────────────────────


def generate_answer(question: str, context: list[dict[str, Any]]) -> str:
    """Arma el prompt con el contexto recuperado y llama al LLM de generación.

    Parameters
    ----------
    question : str
        Pregunta original del usuario.
    context : list[dict]
        Lista de fragmentos recuperados por retrieve(). Cada dict debe
        contener al menos 'text', 'source_document', 'section'.

    Returns
    -------
    str
        Respuesta generada por el LLM.
    """
    # Sin contexto → respuesta honesta
    if not context:
        return (
            "Lo siento, no tengo información suficiente en mi base de conocimiento "
            "para responder a esa pregunta. Por favor, consulta con el equipo "
            "comercial de TrackFlow para obtener datos actualizados."
        )

    # 1. Armar contexto formateado
    context_blocks: list[str] = []
    for i, hit in enumerate(context, 1):
        source_doc = hit.get("source_document", "desconocido")
        section = hit.get("section", "General")
        text = hit.get("text", "")
        context_blocks.append(
            f"[Fuente {i}] Documento: {source_doc} | "
            f"Sección: {section}\n{text}"
        )
    context_str = "\n\n".join(context_blocks)

    # 2. Llamar al LLM de generacion
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
            answer: str = data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.exception("Error al llamar al LLM: %s", exc)
        answer = (
            "Error al generar la respuesta. Por favor, intenta de nuevo "
            "más tarde."
        )

    return answer


# ── Query (orquestador: retrieve + generate_answer) ────────────────────


def query(
    question: str,
    *,
    k: int = 5,
    min_score: float | None = None,
) -> str:
    """Orquesta retrieve() + generate_answer() y devuelve la respuesta final.

    Es la única función que deben llamar consumidores externos simples.

    Parameters
    ----------
    question : str
        Pregunta del usuario.
    k : int
        Número máximo de chunks a recuperar. Por defecto 5.
    min_score : float or None
        Umbral mínimo de similitud. Si es None, usa DEFAULT_MIN_SCORE (0.45).

    Returns
    -------
    str
        Respuesta generada por el LLM basada en el contexto recuperado.
    """
    if min_score is None:
        min_score = DEFAULT_MIN_SCORE

    # 1. Recuperar contexto
    context = retrieve(query=question, k=k, min_score=min_score)

    # 2. Generar respuesta
    answer = generate_answer(question=question, context=context)

    return answer
