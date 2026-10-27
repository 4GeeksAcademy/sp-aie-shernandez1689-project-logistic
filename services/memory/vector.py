"""Almacén vectorial para memoria semántica de agentes.

Usa Qdrant como backend (colección ``trackflow_agent_memory``), con una
dimensión y modelo de embeddings **distintos** a los del RAG documental
(``trackflow_knowledge``). Esto evita contaminación entre memoria episódica
del agente y conocimiento curado de la empresa.

Configuración
-------------
- Colección: ``trackflow_agent_memory``
- Dimensión: 1536 (text-embedding-3-small, reutiliza el cliente Qdrant)
- Distancia: COSINE
- Payload: company, type, source, text, created_at, expires_at
- Estrategia de escritura: append-only (nunca se modifica un punto existente)

El store es de solo-escritura+lectura (el agente no edita memoria previa;
si un hecho queda obsoleto, escribe uno nuevo con ``supersedes`` apuntando
al anterior).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger(__name__)

# ── Configuración de la colección ─────────────────────────────────────

COLLECTION_NAME = "trackflow_agent_memory"
EMBEDDING_DIMENSION = 1536
EMBEDDING_MODEL = "text-embedding-3-small"

# URL base de la API de embeddings 4Geeks (misma que usa el RAG).
EMBEDDINGS_BASE_URL = os.getenv(
    "EMBEDDINGS_BASE_URL",
    "https://api.4geeks.com/v1",
)

# ── Tipos de payload permitidos para memoria ──────────────────────────

# Cada punto lleva un campo ``memory_type`` que clasifica su contenido.
# Solo estos tipos están permitidos.
ALLOWED_MEMORY_TYPES = frozenset({
    "fact",            # Hecho aprobado (business rule, preference)
    "episode",         # Resumen de interacción pasada
    "decision",        # Decisión operativa documentada
    "person_role",     # Mapeo persona → rol
    "user_preference", # Preferencia del usuario
})


class MemoryVectorStore:
    """Almacén vectorial para memoria semántica del agente.

    No confundir con ``trackflow_knowledge`` (RAG documental de solo
    lectura). Esta colección es **exclusiva para memoria del agente**.

    Uso
    ---
        store = MemoryVectorStore()
        await store.store_fact(
            text="Ana es Head of Warehouse Operations",
            memory_type="person_role",
            metadata={"person": "Ana", "role": "Head of Warehouse Operations"},
        )
        results = await store.search("¿quién es Ana?", limit=3)
    """

    def __init__(
        self,
        qdrant_url: str | None = None,
        embedding_url: str | None = None,
    ) -> None:
        self._qdrant_url = qdrant_url or os.getenv(
            "QDRANT_URL", "http://localhost:6333"
        )
        self._embedding_url = embedding_url or EMBEDDINGS_BASE_URL
        self._client = QdrantClient(url=self._qdrant_url)
        self._ensure_collection()

    # ── Gestión de colección ──────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        """Crea la colección si no existe (no la borra si ya existe).

        A diferencia del RAG (que usa clean-and-reload), la memoria del
        agente es **append-only**: nunca se destruye al iniciar.
        """
        collections = self._client.get_collections().collections
        exists = any(c.name == COLLECTION_NAME for c in collections)
        if not exists:
            self._client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=qmodels.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=qmodels.Distance.COSINE,
                ),
            )
            logger.info(
                "Colección %s creada (dimensión=%d, distancia=COSINE)",
                COLLECTION_NAME, EMBEDDING_DIMENSION,
            )
        else:
            logger.debug("Colección %s ya existe", COLLECTION_NAME)

    # ─ Embeddings ──────────────────────────────────────────────────────

    async def _embed(self, text: str) -> list[float]:
        """Vectoriza un texto usando el mismo modelo de embeddings que el RAG."""
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self._embedding_url}/embeddings",
                json={
                    "model": EMBEDDING_MODEL,
                    "input": text,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]

    # ── Escritura ────────────────────────────────────────────────────────

    async def store(
        self,
        text: str,
        memory_type: str,
        metadata: dict[str, Any] | None = None,
        ttl_days: int | None = None,
        supersedes: str | None = None,
    ) -> str:
        """Almacena un hecho en memoria vectorial.

        Args:
            text: Texto descriptivo del hecho. Se vectoriza para búsqueda.
            memory_type: Tipo de memoria (fact, episode, decision, ...).
            metadata: Metadatos adicionales (person, role, source, etc.).
            ttl_days: Días hasta la expiración. None = sin expiración.
            supersedes: UUID del punto que este hecho reemplaza (si aplica).

        Returns:
            UUID del punto creado.

        Raises:
            ValueError: si memory_type no está en ALLOWED_MEMORY_TYPES.
        """
        if memory_type not in ALLOWED_MEMORY_TYPES:
            raise ValueError(
                f"memory_type '{memory_type}' no permitido. "
                f"Permitidos: {sorted(ALLOWED_MEMORY_TYPES)}"
            )

        point_id = str(uuid.uuid4())
        vector = await self._embed(text)

        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "memory_type": memory_type,
            "text": text,
            "company": "trackflow",
            "created_at": now.isoformat(),
        }
        if metadata:
            # Validar que metadata no contenga datos prohibidos
            serialized = json.dumps(metadata, default=str)
            self._validate_payload(serialized)
            payload["metadata"] = metadata
        if ttl_days is not None:
            expires = now.replace(tzinfo=None)  # Qdrant usa datetime sin tz
            from datetime import timedelta
            expires += timedelta(days=ttl_days)
            payload["expires_at"] = expires.isoformat()
        if supersedes:
            payload["supersedes"] = supersedes

        self._client.upsert(
            collection_name=COLLECTION_NAME,
            points=[
                qmodels.PointStruct(
                    id=point_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

        logger.debug(
            "Memoria vectorial escrita: type=%s id=%s", memory_type, point_id,
        )
        return point_id

    def _validate_payload(self, serialized: str) -> None:
        """Valida que el payload no contenga datos prohibidos."""
        forbidden = ["@", "password", "contraseña", "token=", "secret="]
        for pattern in forbidden:
            if pattern in serialized.lower():
                raise ValueError(
                    f"El metadata contiene contenido prohibido ('{pattern}'). "
                    f"No se puede memorizar."
                )

    # ── Lectura ──────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        limit: int = 5,
        memory_type: str | None = None,
        min_score: float = 0.45,
    ) -> list[dict[str, Any]]:
        """Busca hechos en memoria por similitud semántica.

        Args:
            query: Texto de consulta.
            limit: Máximo de resultados.
            memory_type: Filtrar por tipo de memoria (opcional).
            min_score: Umbral mínimo de similitud coseno.

        Returns:
            Lista de dicts con keys: id, memory_type, text, metadata,
            score, created_at, expires_at.
        """
        query_vector = await self._embed(query)

        filter_cond = None
        if memory_type:
            filter_cond = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="memory_type",
                        match=qmodels.MatchValue(value=memory_type),
                    ),
                ]
            )

        hits = self._client.search(
            collection_name=COLLECTION_NAME,
            query_lector=query_vector,
            limit=limit,
            score_threshold=min_score,
            query_filde_r=filter_cond,
        )

        results: list[dict[str, Any]] = []
        for hit in hits:
            result = {
                "id": str(hit.id),
                "memory_type": hit.payload.get("memory_type"),
                "text": hit.payload.get("text"),
                "metadata": hit.payload.get("metadata"),
                "score": hit.score,
                "created_at": hit.payload.get("created_at"),
                "expires_at": hit.payload.get("expires_at"),
                "supersedes": hit.payload.get("supersedes"),
            }
            results.append(result)

        return results

    async def get_by_id(self, point_id: str) -> dict[str, Any] | None:
        """Recupera un punto por su UUID.

        Returns:
            Dict con los datos del punto, o None si no existe.
        """
        points = self._client.retrieve(
            collection_name=COLLECTION_NAME,
            ids=[point_id],
        )
        if not points:
            return None
        point = points[0]
        return {
            "id": str(point.id),
            "memory_type": point.payload.get("memory_type"),
            "text": point.payload.get("text"),
            "metadata": point.payload.get("metadata"),
            "created_at": point.payload.get("created_at"),
            "expires_at": point.payload.get("expires_at"),
            "supersedes": point.payload.get("supersedes"),
        }

    async def delete(self, point_id: str) -> bool:
        """Elimina un punto de memoria."""
        self._client.delete(
            collection_name=COLLECTION_NAME,
            points_selector=qmodels.PointIdsList(
                points=[point_id],
            ),
        )
        logger.debug("Memoria vectorial eliminada: id=%s", point_id)
        return True

    async def close(self) -> None:
        """Cierra el cliente Qdrant."""
        self._client.close()