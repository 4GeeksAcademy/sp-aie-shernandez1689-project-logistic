"""Nodos de memoria para el grafo del agente RAG de TrackFlow.

Flujo de memoria (auto-evaluación + propuesta)
===============================================

Este módulo implementa el diseño de **memoria por propuesta explícita**:
el agente nunca escribe directamente a memoria. En su lugar:

  1. ``memorize_node`` auto-evalúa la interacción y, si hay algo memorable,
     genera una ``_memory_proposal`` que se incrusta como pregunta al final
     de la respuesta.
  2. En la **siguiente** interacción, ``remember_node`` clasifica el mensaje
     del usuario con una etiqueta estructurada para determinar si acepta,
     rechaza, edita o cambia de tema.
  3. Solo se persiste si hay confirmación explícita. El cambio de tema sin
     respuesta clara descarta la propuesta automáticamente.

Reglas de negocio clave
-----------------------
- **Clasificador estructurado**: ``_classify_proposal_intent()`` devuelve
  ``"accept" | "reject" | "edit" | "topic_change" | "ambiguous"``, no un
  booleano.
- **Una propuesta a la vez**: si ya hay una ``_memory_proposal`` pendiente
  sin resolver, ``memorize_node`` no genera una segunda.
- **Cambio de tema = descarte**: si el usuario no responde clara y directamente
  a la propuesta, se descarta (nunca se asume aprobación por silencio).
- **Auditoría**: cada propuesta, aceptación, rechazo, edición o descarte se
  registra en ``data/audit/memory_proposals.jsonl`` con marca de tiempo,
  mensaje origen y resultado.
- **Mensaje combinado**: si el usuario acepta/rechaza y además hace una nueva
  pregunta en el mismo mensaje, la propuesta se resuelve y la conversación
  continúa normalmente.

Nodos
-----
- ``remember_node``: Inicio de corrida. Carga memoria existente Y clasifica
  la intención del usuario frente a la propuesta pendiente.

- ``memorize_node``: Fin de corrida. Auto-evalúa y genera propuesta solo si
  no hay ya una pendiente.

Reglas de auto-evaluación (``memorize_node``)
-----------------------------------------------
1. Preferencias detectadas → proponer.
2. Hechos aprobados en respuesta → proponer.
3. Solicitud explícita del usuario → proponer.
4. Ninguna de las anteriores → ``{"type": "none"}`` (no se propone nada).

Ejemplos de NO memorización
----------------------------
1. "¿Cuál es el estado del ticket INC-123?" — Consulta operacional puntual.
2. "Necesito el stock del SKU CLT-SNK-W-42" — Consulta de inventario.
3. "¿Qué dice la política de devoluciones?" — Pregunta documental RAG.
4. "Gracias" / "ok" / "entendido" — Sin información nueva.
5. Preguntas sobre el clima o temas no relacionados.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from langgraph.config import get_config

from services.memory import MemoryService
from services.rag_agent import tracing

logger = logging.getLogger(__name__)

# Singleton del servicio — se inicializa bajo demanda.
_memory: MemoryService | None = None

# ── Ruta del log de auditoría ─────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
_AUDIT_LOG_PATH = REPO_ROOT / "data" / "audit" / "memory_proposals.jsonl"


# ── Tipos de intención para el clasificador ───────────────────────────

ProposalIntent = Literal["accept", "reject", "edit", "topic_change", "ambiguous"]


# ── Clasificador de intención explícita ──────────────────────────────
# En lugar de simples booleanos, produce una etiqueta estructurada.

# Patrones que indican ACEPTACIÓN explícita de la propuesta.
_ACCEPT_PATTERNS = re.compile(
    r"^\s*(s[ií]|si\s*guarda|ok|okay|de\s*[áa]cuerdo|confirmo|adelante|"
    r"claro|s[ií],?\s*gu[áa]rdalo|s[ií],?\s*recu[eé]rdalo|"
    r"good|yes|sure|go\s*ahead)\s*[\.!\?]*\s*$",
    re.IGNORECASE,
)

# Patrones que indican RECHAZO explícito.
_REJECT_PATTERNS = re.compile(
    r"^\s*(no|no\s*gracias|no\s*gu[áa]rdes|no\s*recuerdes|"
    r"nope|not\s*now|no\s*thanks|no\s*quiero)\s*[\.!\?]*\s*$",
    re.IGNORECASE,
)

# Patrones que indican EDICIÓN: el usuario quiere modificar el valor propuesto.
_EDIT_PATTERNS = re.compile(
    r"\b(?:en\s*realidad|cambia|corrige|mejor\s*di|edita|modifica|"
    r"no\s*es\s*eso|rectifica|realmente)\b",
    re.IGNORECASE,
)

# Umbral mínimo de longitud para considerar contenido real.
_MIN_QUESTION_LENGTH = 8

# Señales de que el usuario cambió de tema (no relacionado con la propuesta).
# Si una frase contiene estos términos Y no coincide con aceptación/rechazo,
# se clasifica como cambio de tema.
_TOPIC_CHANGE_SIGNALS = re.compile(
    r"\b(?:ticket|incidencia|stock|inventario|sku|pol[ií]tica|devoluci[oó]n|"
    r"cu[aá]l\s*es\s*el\s*estado|d[ií]m(e|a)\s*el|"
    r"qu[eé]\s*es|qu[eé]\s*significa|c[oó]mo\s*funciona|"
    r"reporte|informe|dashboard|gr[aá]fica|"
    r"qu[eé]\s*tal|buenas|hola|gracias)\b",
    re.IGNORECASE,
)


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
        return asyncio.run(coro)
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()


# ── Auditoría ────────────────────────────────────────────────────────
# Cada decisión sobre memoria se registra de forma auditable.


def _audit_proposal(
    event: str,
    proposal: dict[str, Any] | None,
    user_message: str | None,
    result: dict[str, Any] | None,
    thread_id: str,
    uid: str | None,
) -> None:
    """Registra un evento de auditoría en el log persistente.

    Args:
        event: Tipo de evento (proposed|accepted|rejected|edited|discarded|error).
        proposal: La propuesta implicada (o None).
        user_message: Mensaje del usuario que originó la decisión.
        result: Dict con detalles del resultado (errores, valores persistidos).
        thread_id: Identificador del hilo de conversación.
        uid: Identificador del usuario (puede ser None).
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "thread_id": thread_id,
        "user_id": uid,
        "proposal": proposal,
        "user_message": user_message,
        "result": result,
    }
    try:
        _AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("No se pudo escribir auditoría: %s", exc)


# ── Clasificador de intención explícita ──────────────────────────────


def _classify_proposal_intent(
    user_text: str,
    proposal: dict[str, Any] | None,
) -> ProposalIntent:
    """Clasifica la intención del usuario frente a una propuesta pendiente.

    Clasificador estructurado que usa el texto completo del mensaje del
    usuario para determinar la intención. Devuelve una etiqueta explícita:

    - ``"accept"``: El usuario aprueba la propuesta de forma clara.
    - ``"reject"``: El usuario rechaza explícitamente.
    - ``"edit"``: El usuario corrige o modifica el valor propuesto.
    - ``"topic_change"``: El usuario ignora la propuesta y cambia de tema.
    - ``"ambiguous"``: No se puede determinar la intención.

    El clasificador usa el texto COMPLETO del mensaje, no solo el primer
    token, para poder detectar mensajes combinados como
    "sí, y dime el stock del SKU XYZ".
    """
    if not user_text or not proposal:
        return "ambiguous"

    text = user_text.strip()
    if not text:
        return "ambiguous"

    lower = text.lower()

    # 1. Detección de edición — tiene prioridad porque el usuario quiere
    #    corregir el valor propuesto, no simplemente rechazar.
    if _EDIT_PATTERNS.search(text):
        return "edit"

    # 2. Aceptación explícita (texto completo o comienzo del mensaje)
    #    "sí" / "sí, guarda eso" / "sí, ahora dime el stock"
    first_token = text.split()[0].lower().strip(",.!¡¿?")
    if first_token in ("sí", "si", "ok", "okay", "yes", "sure", "claro", "adelante"):
        return "accept"

    if _ACCEPT_PATTERNS.match(text):
        return "accept"

    # 3. Rechazo explícito
    if _REJECT_PATTERNS.match(text):
        return "reject"

    # Primer token de rechazo
    if first_token in ("no", "nope"):
        return "reject"

    # 4. Cambio de tema: el mensaje es largo y menciona términos operacionales
    if len(text) >= _MIN_QUESTION_LENGTH and _TOPIC_CHANGE_SIGNALS.search(text):
        return "topic_change"

    # 5. Mensajes muy cortos que no son claramente aceptación/rechazo
    if len(text) < _MIN_QUESTION_LENGTH:
        return "ambiguous"

    # 6. Por defecto, si no hay señales claras, es cambio de tema
    return "topic_change"


# ── Nodo: remember (inicio de corrida) ────────────────────────────────


def remember_node(state: dict[str, Any]) -> dict[str, Any]:
    """Recupera memoria relevante al inicio de la corrida.

    Responsabilidades:
    1. Cargar preferencias, episodios y hechos semánticos previos.
    2. Clasificar la intención del mensaje actual frente a la propuesta
       pendiente del checkpoint anterior usando ``_classify_proposal_intent``.
    3. Según la clasificación:
       - ``accept`` → persistir la memoria propuesta.
       - ``reject`` → descartar sin persistir.
       - ``edit`` → descartar y registrar que el usuario quiere editar.
       - ``topic_change`` → descartar por omisión.
       - ``ambiguous`` → mantener propuesta pendiente.
    4. Si el usuario además hace una nueva pregunta tras resolver la
       propuesta, se carga la memoria existente para continuar.

    Returns:
        Dict con ``memory_context``, opcionalmente ``answer`` si solo se
        procesó la propuesta, y ``_memory_intent_classification`` con la
        etiqueta de clasificación.
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
        "_memory_proposal": None,
        "_memory_intent_classification": None,
    }
    processed_proposal = False

    # ── Paso 1: Clasificar intención y procesar propuesta pendiente ──
    if prev_proposal and prev_proposal.get("type") not in (None, "none") and question:
        intent = _classify_proposal_intent(question, prev_proposal)
        out["_memory_intent_classification"] = intent

        if intent == "accept":
            # ✅ Usuario aceptó → persistir
            accepted = _persist_proposal(memory, prev_proposal, uid, thread_id)
            out["_memory_proposal"] = None
            _audit_proposal(
                event="accepted",
                proposal=prev_proposal,
                user_message=question,
                result=accepted,
                thread_id=thread_id,
                uid=uid,
            )
            logger.info("Propuesta ACEPTADA y persistida: %s", accepted)

            # Si el mensaje solo es aceptación (no tiene más contenido),
            # responder directamente. Si tiene más contenido, continuar.
            if _is_pure_confirmation(question):
                out["answer"] = _build_acceptance_response(accepted)
                processed_proposal = True
            else:
                # Mensaje combinado: "sí, y dime el stock..."
                # La aceptación se procesa, pero seguimos para responder
                # a la nueva pregunta. Se añade un prefacio a la memoria.
                processed_proposal = False
                context["_proposal_result"] = _build_acceptance_response(accepted)

        elif intent == "reject":
            # ❌ Usuario rechazó → descartar
            out["_memory_proposal"] = None
            _audit_proposal(
                event="rejected",
                proposal=prev_proposal,
                user_message=question,
                result={"discarded": True},
                thread_id=thread_id,
                uid=uid,
            )
            logger.info("Propuesta RECHAZADA por el usuario: %s", prev_proposal)

            if _is_pure_confirmation(question):
                out["answer"] = "Entendido. No guardo nada."
                processed_proposal = True
            else:
                # Rechazo + nueva pregunta → continuar
                processed_proposal = False
                context["_proposal_result"] = "Entendido. No guardo nada."

        elif intent == "edit":
            # ✏️ Usuario quiere editar → descartar propuesta actual
            out["_memory_proposal"] = None
            _audit_proposal(
                event="edited",
                proposal=prev_proposal,
                user_message=question,
                result={"discarded": True, "intent": "user_wants_to_edit"},
                thread_id=thread_id,
                uid=uid,
            )
            logger.info("Propuesta EDITADA (descartada): %s", prev_proposal)
            # El usuario hará una nueva solicitud explícita; continuar
            processed_proposal = False
            context["_proposal_result"] = (
                "Entendido. Descarto la propuesta anterior. "
                "Dime exactamente qué quieres que recuerde."
            )

        elif intent in ("topic_change", "ambiguous"):
            # 🔀 Cambio de tema o ambigüedad → descartar por defecto
            out["_memory_proposal"] = None
            _audit_proposal(
                event="discarded",
                proposal=prev_proposal,
                user_message=question,
                result={"intent": intent, "discarded": True},
                thread_id=thread_id,
                uid=uid,
            )
            logger.info(
                "Propuesta DESCARTADA por %s: %r",
                intent, question[:80],
            )
            processed_proposal = False
            context["_proposal_result"] = None  # fluye sin mención

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
        "intent_classification": out.get("_memory_intent_classification"),
    })

    logger.debug(
        "Memory loaded: %d prefs, %d semantic hits, episode=%s, "
        "proposal_processed=%s, intent=%s",
        len(context.get("prefs", {})),
        len(context.get("semantic_hits", [])),
        bool(context.get("episode")),
        processed_proposal,
        out.get("_memory_intent_classification"),
    )
    return out


# ── Nodo: memorize (fin de corrida) ────────────────────────────────────


def memorize_node(state: dict[str, Any]) -> dict[str, Any]:
    """Auto-evalúa la interacción y genera una propuesta de memoria.

    NO persiste nada. En su lugar, produce un campo ``_memory_proposal``
    con la información que *podría* memorizarse, y una pregunta en lenguaje
    natural al final de la respuesta para que el usuario confirme.

    Reglas:
    - Si ya hay una propuesta pendiente sin resolver (del checkpoint anterior),
      NO se genera una segunda. Solo puede haber una a la vez.
    - Si no se detecta contenido memorable, ``_memory_proposal = {"type": "none"}``.

    Returns:
        Dict con ``_memory_proposal``, ``_audit_memory`` (registro de la
        decisión), y opcionalmente ``answer`` modificada.
    """
    question = state.get("question", "")
    answer = state.get("answer", "")
    uid = _user_id(state)
    thread_id = _thread_id()

    # ── Verificar si ya hay una propuesta pendiente ────────────────
    existing_proposal = state.get("_memory_proposal")
    if existing_proposal and existing_proposal.get("type") not in (None, "none"):
        # Ya hay una propuesta sin resolver → no generar otra
        _audit_proposal(
            event="skipped",
            proposal=None,
            user_message=question[:200],
            result={"reason": "existing_pending_proposal"},
            thread_id=thread_id,
            uid=uid,
        )
        logger.debug(
            "Propuesta saltada: ya hay una pendiente (type=%s)",
            existing_proposal.get("type"),
        )
        return {
            "_memory_proposal": existing_proposal,  # mantener la pendiente
            "_memorized": {
                "proposal_type": existing_proposal["type"],
                "proposed": False,
                "skipped_reason": "existing_pending_proposal",
            },
        }

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

    # 2. Hechos aprobados (solo si no hay ya una propuesta)
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

    # --- Auditoría de la propuesta generada ---
    _audit_proposal(
        event="proposed" if proposal["type"] != "none" else "none",
        proposal=proposal,
        user_message=question[:200],
        result={
            "proposal_type": proposal["type"],
            "proposal_text": proposal_text,
        },
        thread_id=thread_id,
        uid=uid,
    )

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
        if "📝 ¿Quieres que recuerde" not in answer:
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


# ── Helpers de clasificación ──────────────────────────────────────────


def _is_pure_confirmation(text: str) -> bool:
    """Determina si el mensaje es solo una confirmación sin contenido adicional.

    Es decir, el usuario solo respondió "sí" o "no" sin hacer una nueva
    pregunta. Esto permite distinguir entre:
    - "sí" → solo confirmación
    - "sí, y dime el stock" → confirmación + nueva pregunta
    """
    if not text:
        return True
    stripped = text.strip().strip(",.!¡¿?")
    # Si después de limpiar puntuación queda una palabra corta, es confirmación pura
    words = stripped.split()
    if len(words) <= 3:
        lower = stripped.lower().strip()
        accept_words = {"sí", "si", "ok", "okay", "yes", "sure", "no", "nope", "claro", "adelante", "de acuerdo", "deacuerdo"}
        if lower in accept_words or any(lower.startswith(w) for w in ("sí", "si", "no", "ok")):
            return True
    return False


# ── Helpers de persistencia ──────────────────────────────────────────


def _persist_proposal(
    memory: MemoryService,
    proposal: dict[str, Any],
    uid: str | None,
    thread_id: str,
) -> dict[str, Any]:
    """Persiste una propuesta aceptada en los stores de memoria.

    Args:
        memory: Instancia de MemoryService.
        proposal: La propuesta a persistir (debe tener ``type`` y ``payload``).
        uid: Identificador del usuario.
        thread_id: Identificador del hilo.

    Returns:
        Dict con resumen de lo persistido.
    """
    proposal_type = proposal.get("type")
    payload = proposal.get("payload", {})
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
            "✅ Preferencia guardada. "
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
    elif "in english" in lower:
        prefs["language"] = "en"
    elif "in spanish" in lower:
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
