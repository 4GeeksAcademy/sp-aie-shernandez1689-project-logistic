"""Servicio unificado de memoria para agentes TrackFlow.

Interfaz explícita de lectura/escritura. El agente nunca acumula estado
inyectándolo al system prompt; debe llamar a estos métodos para leer y
escribir memoria.

Flujo recomendado en el grafo del agente
-----------------------------------------
1. Al inicio de la corrida, el nodo ``remember`` consulta memoria relevante
   (KV + vectorial) y la inyecta en el estado como contexto adicional.
2. Tras la generación de la respuesta, un nodo ``memorize`` evalúa si hay
   hechos nuevos que merezcan ser memorizados (preferencias del usuario,
   decisiones aprobadas, resúmenes de sesión).
3. El agente **nunca** memoriza automáticamente sin pasar por la validación
   del servicio.
"""

from __future__ import annotations

import logging
from typing import Any

from .kv import MemoryKVStore
from .vector import MemoryVectorStore

logger = logging.getLogger(__name__)


class MemoryService:
    """Servicio unificado de memoria (KV + Vectorial).

    Proporciona una interfaz única para que el agente lea y escriba
    memoria, ocultando los detalles de cada backend.

    Uso típico
    ----------
        memory = MemoryService()
        await memory.remember("person:role:andres")
        await memory.store_fact("Andrés es CTO de TrackFlow", "person_role")
    """

    def __init__(
        self,
        kv_store: MemoryKVStore | None = None,
        vector_store: MemoryVectorStore | None = None,
    ) -> None:
        self._kv = kv_store or MemoryKVStore()
        self._vec = vector_store or MemoryVectorStore()

    # ── Lectura explícita desde el agente ────────────────────────────

    async def remember(self, key: str) -> Any | None:
        """Lee un hecho concreto de la memoria KV.

        El agente usa este método para recordar un hecho específico
        (ej. el rol de una persona, una preferencia guardada).
        """
        return await self._kv.read(key)

    async def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Busca en memoria vectorial por similitud semántica.

        El agente usa este método cuando no sabe exactamente qué buscar
        y necesita encontrar hechos relacionados semánticamente.
        """
        return await self._vec.search(query, limit=limit)

    async def get_user_prefs(self, user_id: str) -> dict[str, Any]:
        """Recupera todas las preferencias conocidas de un usuario."""
        return await self._kv.read_user_prefs(user_id)

    async def get_episode(self, session_id: str) -> dict[str, Any] | None:
        """Recupera el resumen de una sesión anterior."""
        return await self._kv.read_episode_summary(session_id)

    async def get_fact(self, fact_key: str) -> Any | None:
        """Lee un hecho aprobado de la memoria KV."""
        return await self._kv.read(fact_key)

    # ── Escritura explícita desde el agente ───────────────────────────

    async def store_fact(
        self,
        key: str,
        value: Any,
        ttl_days: int | None = None,
        vectorize: bool = True,
    ) -> None:
        """Memoriza un hecho en la KV store y opcionalmente en vectorial.

        Args:
            key: Clave del hecho (ej. ``person:role:andres``).
            value: Valor del hecho (string, dict, etc.).
            ttl_days: Días hasta expiración (None = permanente).
            vectorize: Si True, también indexa en Qdrant para búsqueda semántica.

        Raises:
            ValueError: si la clave o el valor violan las reglas de negocio.
        """
        await self._kv.write(key, value)
        logger.debug("Hecho memorizado en KV: %s", key)

        if vectorize:
            text = f"{key}: {value}" if isinstance(value, str) else f"{key}: {json_dumps(value)}"
            memory_type = _infer_memory_type(key)
            await self._vec.store(
                text=text,
                memory_type=memory_type,
                metadata={"key": key, "value": value},
                ttl_days=ttl_days,
            )

    async def store_user_pref(self, user_id: str, pref_name: str, value: Any) -> None:
        """Memoriza una preferencia del usuario."""
        await self._kv.write_user_pref(user_id, pref_name, value)
        # También indexar en vectorial para recall semántico
        fact_key = f"user:{user_id}:pref:{pref_name}"
        memory_type = "user_preference"
        await self._vec.store(
            text=f"El usuario {user_id} prefiere {pref_name}={value}",
            memory_type=memory_type,
            metadata={"user_id": user_id, "pref": pref_name, "value": value},
            ttl_days=90,
        )

    async def store_episode(
        self,
        session_id: str,
        question: str,
        answer: str,
        sources: list[str],
        approved_facts: list[dict[str, Any]] | None = None,
    ) -> None:
        """Memoriza un resumen de sesión (episodio).

        El resumen es un dict con: pregunta, respuesta, fuentes usadas,
        hechos aprobados durante la interacción.
        """
        summary = {
            "question": question,
            "answer": answer,
            "sources": sources,
            "approved_facts": approved_facts or [],
            "timestamp": __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat(),
        }
        await self._kv.write_episode_summary(session_id, summary)

        # Indexar el resumen en vectorial para que el agente pueda
        # recuperar episodios anteriores por similitud semántica.
        await self._vec.store(
            text=f"[Resumen de sesión] Pregunta: {question} | Respuesta: {answer[:200]}",
            memory_type="episode",
            metadata={
                "session_id": session_id,
                "sources": sources,
                "n_facts": len(approved_facts or []),
            },
            ttl_days=30,
        )

    async def approve_fact(
        self,
        fact_key: str,
        fact_value: Any,
        user_id: str | None = None,
    ) -> None:
        """Memoriza un hecho aprobado explícitamente por el usuario.

        Solo los hechos aprobados explícitamente se almacenan como
        permanentes. El agente nunca memoriza información no verificada.
        """
        await self.store_fact(fact_key, fact_value, vectorize=True)
        if user_id:
            approval_key = f"user:{user_id}:approved:{fact_key.replace(':', '_')}"
            await self._kv.write(approval_key, {
                "fact_key": fact_key,
                "fact_value": fact_value,
                "approved_at": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
            })

    # ── Cancelación / olvido ─────────────────────────────────────────

    async def forget(self, key: str) -> bool:
        """Elimina un hecho memorizado.

        Returns:
            True si existía y se eliminó.
        """
        return await self._kv.delete(key)

    # ── Constructor de contexto para el agente ────────────────────────

    async def build_context(
        self,
        user_id: str | None = None,
        session_id: str | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Construye un bloque de contexto de memoria para el agente.

        Llama a este método al inicio de cada corrida para recuperar
        toda la memoria relevante antes de que el agente procese la
        pregunta.

        Returns:
            Dict con: ``prefs``, ``episode``, ``semantic_hits``, ``facts``.
        """
        context: dict[str, Any] = {}

        if user_id:
            context["prefs"] = await self.get_user_prefs(user_id)
        if session_id:
            context["episode"] = await self.get_episode(session_id)
        if query:
            context["semantic_hits"] = await self.recall(query, limit=3)

        return context

    # ── Limpieza ──────────────────────────────────────────────────────

    async def close(self) -> None:
        """Cierra las conexiones de ambos stores."""
        await self._kv.close()
        await self._vec.close()


def _infer_memory_type(key: str) -> str:
    """Infier el tipo de memoria a partir de la clave."""
    if key.startswith("person:role:"):
        return "person_role"
    if key.startswith("user:") and ":pref:" in key:
        return "user_preference"
    if key.startswith("session:") or key.startswith("episode:"):
        return "episode"
    if key.startswith("decision:"):
        return "decision"
    if key.startswith("fact:"):
        return "fact"
    return "fact"


def json_dumps(value: Any) -> str:
    """Serializa un valor a JSON para metadata."""
    import json
    return json.dumps(value, ensure_ascii=False, default=str)
