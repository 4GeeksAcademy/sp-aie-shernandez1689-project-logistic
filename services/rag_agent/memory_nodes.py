"""Nodos de memoria para el grafo del agente RAG de TrackFlow.

Estos nodos se integran en el grafo LangGraph para que el agente pueda
recordar hechos memorizados (leer) y memorizar nuevos hechos (escribir)
de forma explícita, sin acumular estado en el system prompt.

Nodos
-----
- ``remember_node``: Al inicio de la corrida, recupera memoria relevante
  (preferencias del usuario, hechos aprobados, episodios anteriores) y
  los inyecta en un campo ``memory_context`` del estado.

- ``memorize_node``: Después de generar la respuesta, evalúa si hay
  información nueva que merezca ser memorizada y la guarda en los stores
  KV y vectorial.

Reglas de negocio aplicadas
----------------------------
✅ Se memoriza:
  - Preferencias del usuario detectadas en la conversación.
  - Hechos aprobados explícitamente por el usuario.
  - Resúmenes de sesión (pregunta, fuentes, respuesta breve).
  - Mapeos persona→rol confirmados.

❌ NO se memoriza:
  - Estados crudos de incidencias o inventario.
  - Contenido de documentos RAG.
  - Datos personales de consumidores finales.
  - Credenciales o tokens.
  - Información no verificada/no aprobada.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.config import get_config

from services.memory import MemoryService
from services.rag_agent import tracing

logger = logging.getLogger(__name__)

# Singleton del servicio — se inicializa bajo demanda.
_memory: MemoryService | None = None


def _get_memory() -> MemoryService:
    global _memory
    if _memory is None:
        _memory = MemoryService()
    return _memory


def _thread_id() -> str:
    cfg = get_config()
    return cfg.get("configurable", {}).get("thread_id", "default")


def _user_id(state: dict[str, Any]) -> str | None:
    """Extrae el user_id del estado si existe."""
    return state.get("user_id") or state.get("configurable", {}).get("user_id")


# ── Nodo: remember ─────────────────────────────────────────────────────


async def remember_node(state: dict[str, Any]) -> dict[str, Any]:
    """Recupera memoria relevante al inicio de la corrida.

    Lee:
    - Preferencias del usuario (si hay user_id).
    - Resumen del episodio anterior (misma thread_id/session_id).
    - Hechos semánticos relacionados con la pregunta.

    Inyecta todo en ``memory_context``, que el nodo ``generate``
    usará como contexto adicional (nunca se añade al system prompt).
    """
    memory = _get_memory()
    question = state.get("question", "")
    uid = _user_id(state)
    thread_id = _thread_id()

    context: dict[str, Any] = {
        "prefs": {},
        "episode": None,
        "semantic_hits": [],
        "facts": [],
    }

    if uid:
        prefs = await memory.get_user_prefs(uid)
        if prefs:
            context["prefs"] = prefs

    if question:
        # Buscar hechos semánticos relacionados con la pregunta
        context["semantic_hits"] = await memory.recall(question, limit=3)

    # Episodios previos del mismo hilo (si el grafo reusa thread_id)
    episode = await memory.get_episode(thread_id)
    if episode:
        context["episode"] = episode

    out: dict[str, Any] = {
        "memory_context": context,
        "_memory_loaded": True,
    }
    tracing.add_step("remember", {
        "n_prefs": len(context.get("prefs", {})),
        "n_semantic_hits": len(context.get("semantic_hits", [])),
        "has_episode": context.get("episode") is not None,
    })

    logger.debug(
        "Memory loaded: %d prefs, %d semantic hits, episode=%s",
        len(context.get("prefs", {})),
        len(context.get("semantic_hits", [])),
        bool(context.get("episode")),
    )
    return out


# ── Nodo: memorize ────────────────────────────────────────────────────


async def memorize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Evalúa y memoriza información nueva después de generar la respuesta.

    Reglas de memorización:
    1. Si el usuario expresó una preferencia explícita → guardar.
    2. Si el usuario aprobó un hecho → guardar como approved_fact.
    3. Si es el final de la corrida → guardar resumen de sesión.
    4. NUNCA memorizar datos de tools (incidencias/inventario).

    Returns:
        Dict con metadatos de lo que se memorizó.
    """
    memory = _get_memory()
    thread_id = _thread_id()
    uid = _user_id(state)
    question = state.get("question", "")
    answer = state.get("answer", "")
    sources: list[str] = []

    # Extraer fuentes del estado
    if state.get("context"):
        for c in state["context"]:
            src = c.get("source_document")
            if src:
                sources.append(src)
    if state.get("tool_answers"):
        for c in state.get("tool_context") or []:
            src = c.get("source_document")
            if src:
                sources.append(src)
    sources = list(set(sources))

    memorized: dict[str, Any] = {
        "preferences": [],
        "approved_facts": [],
        "episode_saved": False,
    }

    # 1. Detectar preferencias del usuario en la conversación
    if uid and question:
        prefs = _extract_preferences(question)
        for pref_name, pref_value in prefs.items():
            try:
                await memory.store_user_pref(uid, pref_name, pref_value)
                memorized["preferences"].append({"name": pref_name, "value": pref_value})
            except ValueError as exc:
                logger.warning("Preferencia no memorizable: %s", exc)

    # 2. Detectar hechos aprobados en la respuesta
    approved = _extract_approved_facts(answer)
    for fact_key, fact_value in approved:
        try:
            await memory.approve_fact(fact_key, fact_value, user_id=uid)
            memorized["approved_facts"].append({"key": fact_key, "value": fact_value})
        except ValueError as exc:
            logger.warning("Hecho aprobado no memorizable: %s", exc)

    # 3. Guardar resumen de sesión
    try:
        await memory.store_episode(
            session_id=thread_id,
            question=question,
            answer=answer,
            sources=sources,
            approved_facts=[
                {"key": k, "value": v} for k, v in approved
            ],
        )
        memorized["episode_saved"] = True
    except ValueError as exc:
        logger.warning("Episodio no memorizable: %s", exc)

    out: dict[str, Any] = {
        "_memorized": memorized,
    }
    tracing.add_step("memorize", memorized)

    if memorized["preferences"] or memorized["approved_facts"]:
        logger.debug(
            "Memorized: %d prefs, %d facts, episode=%s",
            len(memorized["preferences"]),
            len(memorized["approved_facts"]),
            memorized["episode_saved"],
        )

    return out


# ── Helpers de extracción ─────────────────────────────────────────────


def _extract_preferences(text: str) -> dict[str, str]:
    """Extrae preferencias expresadas por el usuario en la pregunta.

    Ejemplos:
    - "respondeme en español" → {"language": "es"}
    - "prefiero datos en bruto" → {"detail_level": "raw"}
    - "dame solo el resumen" → {"detail_level": "summary"}

    Returns:
        Dict nombre_pref → valor_pref. Vacío si no detecta nada.
    """
    prefs: dict[str, str] = {}
    lower = text.lower()

    # Idioma
    lang_map = {
        "español": "es",
        "spanish": "es",
        "inglés": "en",
        "english": "en",
    }
    for phrase, code in lang_map.items():
        if f"en {phrase}" in lower or f"en {code}" in lower or (
            f"en idioma {phrase}" in lower
        ):
            prefs["language"] = code
            break
        if f"respóndeme en {phrase}" in lower or f"respondeme en {phrase}" in lower:
            prefs["language"] = code
            break
        if f"en {phrase}" in lower and "hablar" not in lower:
            # Podría ser preferencia de idioma
            pass

    # Nivel de detalle
    if any(p in lower for p in ["solo el resumen", "solo resumen", "resumen corto"]):
        prefs["detail_level"] = "summary"
    elif any(p in lower for p in ["datos completos", "todos los datos", "máximo detalle"]):
        prefs["detail_level"] = "full"

    return prefs


def _extract_approved_facts(text: str) -> list[tuple[str, Any]]:
    """Extrae hechos aprobados de la respuesta del agente.

    Un hecho aprobado es una confirmación explícita de que algo es cierto.
    Ejemplo: la respuesta dice "Confirmado: Ana es Head of Warehouse".

    Returns:
        Lista de (fact_key, fact_value). Vacía si no detecta nada.
    """
    facts: list[tuple[str, Any]] = []

    # Esta función es conservadora: solo extrae hechos que tienen
    # una estructura clara de aprobación. En una versión futura esto
    # podría hacerse con un LLM para mayor precisión.
    import re

    # Patrón: "X es Y" donde X es un nombre conocido
    person_pattern = re.compile(
        r"(?:Confirmado|Confirmada|Confirmamos?|Hecho)\s*:\s*"
        r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)\s+"
        r"(?:es|está|trabaja como|dirige|lidera|es el|es la)\s+"
        r"(.+?)(?:\.|,|$)",
        re.IGNORECASE,
    )
    for match in person_pattern.finditer(text):
        name = match.group(1).strip().lower()
        role = match.group(2).strip()
        facts.append((f"person:role:{name}", role))

    return facts
