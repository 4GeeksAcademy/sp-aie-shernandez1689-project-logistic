"""Nodos de memoria para el grafo del agente RAG de TrackFlow.

Flujo de memoria (auto-evaluación + propuesta)
===============================================

Este módulo implementa el diseño de **memoria por propuesta explícita**:
el agente nunca escribe directamente a memoria. En su lugar:

  1. ``generate_node`` produce la respuesta + una auto-evaluación de si
     hay algo nuevo que valga la pena recordar (``_memory_proposal``).
  2. Si hay propuesta, se incrusta como pregunta al final de la respuesta
     para que el usuario la acepte o rechace.
  3. ``remember_node`` (en la **siguiente** interacción) detecta si la
     nueva pregunta del usuario es una confirmación de la propuesta
     anterior y, de ser así, persiste el hecho.

Esto garantiza que:
  - Nunca se escribe a memoria sin consentimiento explícito del usuario.
  - No hace falta una segunda llamada al LLM ni un agente separado.
  - La mayoría de las interacciones se descartan sin propuesta.

Nodos
-----
- ``remember_node``: Inicio de corrida. Carga memoria existente Y
  procesa propuestas pendientes del checkpoint anterior.

- ``memorize_node``: Fin de corrida. Auto-evalúa y genera
  ``_memory_proposal`` si aplica.

Reglas de negocio
------------------

✅ MEMORIZABLE (se propone al usuario):
  - Preferencias de idioma ("en español", "in English").
  - Preferencias de formato / nivel de detalle ("solo resumen").
  - Hechos aprobados: mapeos persona→rol confirmados explícitamente.
  - Decisiones documentadas que el usuario pide recordar.

❌ NO MEMORIZABLE (se descarta sin propuesta):
  1. "¿Cuál es el estado del ticket INC-123?" — Consulta operacional puntual.
     Sin preferencias nuevas, sin hechos que confirmar → nada que recordar.
  2. "Necesito el stock del SKU CLT-SNK-W-42" — Consulta de inventario.
     Información transaccional que cambia constantemente.
  3. "¿Qué dice la política de devoluciones?" — Pregunta documental RAG.
     La respuesta está en la base de conocimiento, no es un hecho nuevo.
  4. "Gracias" / "ok" / "entendido" — Sin información nueva.
  5. Preguntas sobre el clima o temas no relacionados con TrackFlow.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from langgraph.config import get_config

from services.memory import MemoryService
from services.rag_agent import tracing

logger = logging.getLogger(__name__)

# Singleton del servicio — se inicializa bajo demanda.
_memory: MemoryService | None = None

# ── Patrones de detección de confirmación ─────────────────────────────

# Frases con las que un usuario ACEPTA una propuesta de memoria.
_ACCEPT_PATTERNS = re.compile(
    r"^\s*(s[ií]|si\s*guarda|ok|okay|de\s*[áa]cuerdo|confirmo|adelante|"
    r"claro|s[ií],?\s*gu[áa]rdalo|s[ií],?\s*recu[eé]rdalo|"
    r"good|yes|sure|go\s*ahead)\s*[\.!\?]*\s*$",
    re.IGNORECASE,
)

# Frases con las que un usuario RECHAZA explícitamente.
_REJECT_PATTERNS = re.compile(
    r"^\s*(no|no\s*gracias|no\s*gu[áa]rdes|no\s*recuerdes|"
    r"nope|not\s*now|no\s*thanks|no\s*quiero)\s*[\.!\?]*\s*$",
    re.IGNORECASE,
)

# Umbral mínimo de longitud para considerar una pregunta como "contenido real"
# (por debajo de esto, probablemente es sólo una respuesta de confirmación).
_MIN_QUESTION_LENGTH = 8


# ── Inicialización del servicio ────────────────────────────────────────


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
    user_id = state.get("user_id")
    if user_id:
        return user_id
    configurable = state.get("configurable") or {}
    return configurable.get("user_id")


# ── Helper síncrono para llamadas asíncronas ─────────────────────────


def _run_async(coro):
    """Ejecuta una corutina en un event loop existente o crea uno nuevo.

    Necesario porque los nodos del grafo son síncronos (graph.invoke),
    pero MemoryService usa redis.asyncio / httpx.AsyncClient.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No hay loop corriendo → crear uno nuevo
        return asyncio.run(coro)
    # Ya hay un loop (ej. Jupyter, tests con pytest-asyncio)
    # → crear una tarea y esperar
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()


# ── Nodo: remember (inicio de corrida) ────────────────────────────────


def remember_node(state: dict[str, Any]) -> dict[str, Any]:
    """Recupera memoria relevante al inicio de la corrida.

    Responsabilidades:
    1. Cargar preferencias, episodios y hechos semánticos previos.
    2. Detectar si la pregunta actual es una confirmación/rechazo de
       una propuesta de memoria del checkpoint anterior.
    3. Si es confirmación → persiste la memoria propuesta.
    4. Si es rechazo → descarta la propuesta.

    Returns:
        Dict con ``memory_context`` y opcionalmente ``answer`` si se
        procesó una confirmación.
    """
    memory = _get_memory()
    question = state.get("question", "").strip()
    uid = _user_id(state)
    thread_id = _thread_id()
    prev_proposal = state.get("_memory_proposal")

    context: dict[str, Any] = {
        "prefs": {},
        "episode": None,
        "semantic_hits": [],
        "facts": [],
    }
    out: dict[str, Any] = {
        "memory_context": context,
        "_memory_loaded": True,
        "_memory_proposal": None,  # se limpia siempre; si se genera de nuevo,
                                   # lo hará memorize_node al final de esta corrida
    }
    processed_proposal = False

    # ── Paso 1: Procesar propuesta pendiente del run anterior ──────
    if prev_proposal and prev_proposal.get("type") not in (None, "none") and question:
        proposal_type = prev_proposal.get("type")
        payload = prev_proposal.get("payload", {})

        if _is_acceptance(question):
            # ✅ Usuario aceptó → persistir
            accepted = _persist_proposal(memory, proposal_type, payload, uid, thread_id)
            out["_memory_proposal"] = None  # propuesta resuelta
            out["answer"] = _build_acceptance_response(accepted)
            processed_proposal = True
            logger.info("Propuesta ACEPTADA y persistida: %s", accepted)

        elif _is_rejection(question):
            # ❌ Usuario rechazó → descartar sin persistir
            out["_memory_proposal"] = None
            out["answer"] = "Entendido. No guardo nada."
            processed_proposal = True
            logger.info("Propuesta RECHAZADA por el usuario: %s", prev_proposal)

        elif len(question) < _MIN_QUESTION_LENGTH:
            # Pregunta muy corta que no es claramente aceptación ni rechazo
            # → tratar como respuesta ambigua (no hacer nada, mantener propuesta)
            logger.debug(
                "Respuesta ambigua a propuesta (long=%d): %r",
                len(question), question,
            )

    # ── Paso 2: Cargar memoria existente ───────────────────────────
    if not processed_proposal:
        if uid:
            prefs = _run_async(memory.get_user_prefs(uid))
            if prefs:
                context["prefs"] = prefs

        if question and len(question) >= _MIN_QUESTION_LENGTH:
            context["semantic_hits"] = _run_async(memory.recall(question, limit=3))

        episode = _run_async(memory.get_episode(thread_id))
        if episode:
            context["episode"] = episode

    tracing.add_step("remember", {
        "n_prefs": len(context.get("prefs", {})),
        "n_semantic_hits": len(context.get("semantic_hits", [])),
        "has_episode": context.get("episode") is not None,
        "processed_proposal": processed_proposal,
    })

    logger.debug(
        "Memory loaded: %d prefs, %d semantic hits, episode=%s, "
        "proposal_processed=%s",
        len(context.get("prefs", {})),
        len(context.get("semantic_hits", [])),
        bool(context.get("episode")),
        processed_proposal,
    )
    return out


# ── Nodo: memorize (fin de corrida) ────────────────────────────────────


def memorize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Auto-evalúa la interacción y genera una propuesta de memoria.

    NO persiste nada. En su lugar, produce un campo ``_memory_proposal``
    con la información que *podría* memorizarse, y una pregunta en lenguaje
    natural al final de la respuesta para que el usuario confirme.

    El criterio de auto-evaluación es:
    1. Si se detectaron preferencias nuevas en la pregunta del usuario → proponer.
    2. Si la respuesta contiene hechos confirmados explícitamente → proponer.
    3. Si el usuario pidió explícitamente recordar algo → proponer.
    4. En cualquier otro caso → ``_memory_proposal = {"type": "none"}``.

    Returns:
        Dict con ``_memory_proposal`` y opcionalmente ``answer`` modificada.
    """
    question = state.get("question", "")
    answer = state.get("answer", "")
    uid = _user_id(state)

    # --- Auto-evaluación ---
    proposal: dict[str, Any] = {"type": "none", "payload": {}}
    proposal_text: str | None = None

    # 1. Preferencias del usuario
    detected_prefs = _extract_preferences(question)
    if uid and detected_prefs:
        proposal = {
            "type": "preference",
            "payload": {"user_id": uid, "preferences": detected_prefs},
        }
        pref_descriptions = []
        for k, v in detected_prefs.items():
            if k == "language":
                lang_name = {"es": "español", "en": "inglés"}.get(v, v)
                pref_descriptions.append(f"prefieres las respuestas en {lang_name}")
            elif k == "detail_level":
                if v == "summary":
                    pref_descriptions.append("prefieres solo el resumen")
                else:
                    pref_descriptions.append("prefieres el máximo detalle")
        if pref_descriptions:
            proposal_text = "📝 ¿Quieres que recuerde que " + \
                " y ".join(pref_descriptions) + "?"

    # 2. Hechos aprobados (solo si no hay ya una propuesta de preferencia)
    if proposal["type"] == "none":
        approved = _extract_approved_facts(answer)
        if approved:
            payload_facts = [{"key": k, "value": v} for k, v in approved]
            proposal = {
                "type": "fact",
                "payload": {
                    "facts": payload_facts,
                    "user_id": uid,
                },
            }
            descriptions = [f"«{v}»" for _, v in approved[:2]]
            if len(approved) > 2:
                descriptions.append(f"y {len(approved) - 2} más")
            proposal_text = "📝 ¿Quieres que recuerde que " + \
                ", ".join(descriptions) + "?"

    # 3. Solicitud explícita de recordar
    if proposal["type"] == "none":
        explicit = _extract_explicit_remember_request(question)
        if explicit:
            proposal = {
                "type": "fact",
                "payload": {
                    "facts": [{"key": explicit["key"], "value": explicit["value"]}],
                    "user_id": uid,
                },
            }
            proposal_text = f"📝 ¿Quieres que recuerde esto: {explicit['value']}?"

    # --- Construir salida ---
    out: dict[str, Any] = {
        "_memory_proposal": proposal,
        "_memorized": {
            "proposal_type": proposal["type"],
            "proposed": proposal["type"] != "none",
        },
    }

    # Si hay propuesta, añadir la pregunta al final de la respuesta
    if proposal_text and answer and not answer.endswith(proposal_text):
        # No duplicar si ya se incluyó
        if "¿Quieres que recuerde" not in answer:
            out["answer"] = answer.rstrip() + "\n\n" + proposal_text

    tracing.add_step("memorize", {
        "proposal_type": proposal["type"],
        "proposal_text": proposal_text,
    })

    logger.debug(
        "Memory proposal: type=%s, text=%s",
        proposal["type"],
        proposal_text,
    )
    return out


# ── Helpers de detección ──────────────────────────────────────────────


def _is_acceptance(text: str) -> bool:
    """Determina si el texto del usuario es una confirmación."""
    return bool(_ACCEPT_PATTERNS.match(text.strip()))


def _is_rejection(text: str) -> bool:
    """Determina si el texto del usuario es un rechazo."""
    return bool(_REJECT_PATTERNS.match(text.strip()))


def _persist_proposal(
    memory: MemoryService,
    proposal_type: str,
    payload: dict[str, Any],
    uid: str | None,
    thread_id: str,
) -> dict[str, Any]:
    """Persiste una propuesta aceptada en los stores de memoria.

    Returns:
        Dict con resumen de lo persistido.
    """
    accepted: dict[str, Any] = {"type": proposal_type, "details": []}

    if proposal_type == "preference":
        prefs = payload.get("preferences", {})
        for pref_name, pref_value in prefs.items():
            _run_async(memory.store_user_pref(uid, pref_name, pref_value))
            accepted["details"].append(f"{pref_name}={pref_value}")

    elif proposal_type == "fact":
        facts = payload.get("facts", [])
        for fact in facts:
            _run_async(memory.approve_fact(
                fact["key"], fact["value"], user_id=uid,
            ))
            accepted["details"].append(f"{fact['key']}: {fact['value']}")

    return accepted


def _build_acceptance_response(accepted: dict[str, Any]) -> str:
    """Construye la respuesta tras aceptar una propuesta."""
    if accepted["type"] == "preference":
        return (
            "✅ Preferencia guardada. " +
            "La tendré en cuenta en futuras respuestas."
        )
    elif accepted["type"] == "fact":
        return (
            "✅ Hecho recordado. Lo tendré presente "
            "cuando sea relevante."
        )
    return "✅ Entendido."


# ── Helpers de extracción (compartidos con generate_node) ─────────────


def _extract_preferences(text: str) -> dict[str, str]:
    """Extrae preferencias expresadas por el usuario.

    Conservadora: solo detecta patrones explícitos y claros.
    NO requiere llamada al LLM — es puramente heurístico.

    Returns:
        Dict nombre_pref → valor_pref. Vacío si no detecta nada.
    """
    prefs: dict[str, str] = {}
    lower = text.lower()

    # Idioma
    if "en español" in lower or "en castellano" in lower or "respóndeme en español" in lower:
        prefs["language"] = "es"
    elif "in english" in lower or "in spanish" in lower:
        if "in spanish" in lower:
            prefs["language"] = "es"
    if "respondeme en ingles" in lower or "respóndeme en inglés" in lower:
        prefs["language"] = "en"

    # Nivel de detalle
    summary_phrases = [
        "solo el resumen", "solo resumen", "resumen corto",
        "en resumen", "resumido", "abreviado",
    ]
    if any(p in lower for p in summary_phrases):
        prefs["detail_level"] = "summary"

    full_phrases = [
        "datos completos", "todos los datos", "máximo detalle",
        "con todo detalle", "sin resumir", "completo", "detallado",
    ]
    if any(p in lower for p in full_phrases):
        prefs["detail_level"] = "full"

    return prefs


def _extract_approved_facts(text: str) -> list[tuple[str, Any]]:
    """Extrae hechos aprobados de la respuesta del agente.

    Detecta patrones como "Confirmado: [Nombre] es [Rol]".
    Conservadora: solo extrae estructuras claras de aprobación.
    """
    facts: list[tuple[str, Any]] = []

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


def _extract_explicit_remember_request(text: str) -> dict[str, Any] | None:
    """Detecta si el usuario pide explícitamente recordar algo.

    Patrones:
    - "recuerda que X es Y"
    - "guarda que Z"
    - "para que lo recuerdes: ..."
    - "toma nota: ..."

    Returns:
        Dict con ``key`` y ``value``, o None si no detecta nada.
    """
    lower = text.lower()

    # "recuerda que [algo] es/está [valor]"
    m = re.search(
        r"recuerda\s+que\s+(.+?)\s+(?:es|está|trabaja como|dirige)\s+(.+)",
        lower,
    )
    if m:
        key = m.group(1).strip().replace(" ", "_").lower()
        return {"key": f"fact:{key}", "value": m.group(2).strip()}

    # "guarda que [algo] = [valor]"
    m = re.search(r"guarda\s+que\s+(.+?)\s*(?:=|[eé]s|:)\s*(.+)", lower)
    if m:
        key = m.group(1).strip().replace(" ", "_").lower()
        return {"key": f"fact:{key}", "value": m.group(2).strip()}

    # "toma nota: [algo]"
    m = re.search(r"toma\s+nota\s*:?\s*(.+)", lower)
    if m:
        note = m.group(1).strip().capitalize()
        return {"key": "fact:user_note", "value": note}

    return None
