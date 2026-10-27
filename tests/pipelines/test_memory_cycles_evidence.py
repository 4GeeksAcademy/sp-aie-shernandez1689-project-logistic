"""Tests de evidencia: ciclos completos de memoria por propuesta explícita.

Propósito
---------
Documentar y verificar el flujo completo de memoria en dos ciclos:

1. **Ciclo ACEPTACIÓN** — el usuario responde afirmativamente a una propuesta
   de memoria, y el hecho persiste y se refleja en una interacción futura.

2. **Ciclo RECHAZO** — el usuario rechaza explícitamente la propuesta, la
   memoria NO se persiste, y en una interacción futura el agente no tiene
   el hecho disponible.

Estructura de cada ciclo
------------------------
Cada ciclo recorre 3 turnos de conversación:

   Turno 1 (memorize)
     - El agente detecta algo memorable y genera:
       * ``_memory_proposal`` con type="preference" o "fact"
       * pregunta al final de la respuesta ("📝 ¿Quieres que recuerde...?")
       * evento ``proposed`` en el audit log

   Turno 2 (remember → memorize)
     - El usuario responde a la propuesta.
     - ``remember_node`` clasifica la intención (accept/reject).
       - Si accept: persiste vía ``_persist_proposal()``
       - Si reject: descarta sin persistir
     - ``memorize_node`` limpia la propuesta (ya resuelta).
     - Se registran eventos ``accepted`` o ``rejected`` en el audit log.

   Turno 3 (remember → respuesta)
     - El usuario hace una pregunta donde el hecho sería relevante.
     - ``remember_node`` carga la memoria existente.
     - Si fue aceptado: el hecho aparece en ``memory_context``.
     - Si fue rechazado: el hecho NO aparece en ``memory_context``.

Este archivo usa el grafo real (graph.invoke) con dependencias externas
mockeadas para aislar el flujo de memoria.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.rag_agent import tracing
from services.rag_agent.graph import graph
from services.rag_agent.mcp_client import reset_mcp_client

# ---------------------------------------------------------------------------
# Ayudantes
# ---------------------------------------------------------------------------

_AUDIT_LOG = (
    Path(__file__).resolve().parents[2] / "data" / "audit" / "memory_proposals.jsonl"
)


def _read_audit_log() -> list[dict]:
    """Retorna todas las entradas del audit log (vacío si no existe)."""
    if not _AUDIT_LOG.exists():
        return []
    with open(_AUDIT_LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _last_audit_entry() -> dict | None:
    entries = _read_audit_log()
    return entries[-1] if entries else None


def _audit_events() -> list[str]:
    return [e.get("event") for e in _read_audit_log()]


# ---------------------------------------------------------------------------
# Fixture: grafo limpio con dependencias mockeadas
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_audit_log():
    """Elimina el audit log antes de cada test para tener un estado limpio."""
    if _AUDIT_LOG.exists():
        _AUDIT_LOG.unlink()
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    yield
    # Limpieza opcional al final


@pytest.fixture
def mock_rag():
    """Mockea retrieve, generate_answer, MCP tools y consolidación."""
    with patch("services.rag_agent.graph.retrieve", return_value=[]) as ret:
        with patch("services.rag_agent.graph.generate_answer", return_value="respuesta simulada") as gen:
            with patch("services.rag_agent.mcp_client.lookup_incidents_mcp", return_value={}) as mcp_inc:
                with patch("services.rag_agent.mcp_client.lookup_inventory_mcp", return_value={
                    "product": None,
                    "description": None,
                    "resolved": False,
                    "error": None,
                }) as mcp_inv:
                    with patch("services.rag_agent.memory_nodes._get_consolidation") as mock_cons:
                        # ConsolidationService mockeado: consolidate() es un AsyncMock
                        # que devuelve un objeto con los atributos que consolidate_node espera
                        mock_cons_obj = MagicMock()
                        mock_cons_obj.consolidate = AsyncMock(return_value=MagicMock(
                            synced_orphans=0,
                            dedup_removed=0,
                            dedup_kept=0,
                            episodes_compressed=0,
                            low_relevance_pruned=0,
                            duration_ms=0.0,
                            errors=[],
                        ))
                        mock_cons.return_value = mock_cons_obj
                        yield ret, gen, mcp_inc, mcp_inv, mock_cons


@pytest.fixture(autouse=True)
def reset_mcp():
    """Reinicia cliente MCP entre tests para evitar contaminación."""
    reset_mcp_client()


@pytest.fixture
def mock_memory_service():
    """MemoryService mockeado sin Redis/Qdrant.

    Devuelve valores controlados para que el flujo de memoria pueda
    ejercitarse sin dependencias externas.
    """
    mem = MagicMock()
    # KV store (Redis) — siempre devuelve listas vacías
    mem._kv = MagicMock()
    mem._kv.search_keys = AsyncMock(return_value=[])
    mem._kv.read = AsyncMock(return_value=None)
    mem.get_user_prefs = AsyncMock(return_value={})
    mem.get_episode = AsyncMock(return_value=None)
    mem.recall = AsyncMock(return_value=[])
    mem.store_user_pref = AsyncMock(return_value=None)
    mem.approve_fact = AsyncMock(return_value=None)
    return mem


def _invoke(question: str, thread_id: str = "evidence-test", user_id: str = "usr_evidence"):
    """Ejecuta el grafo una corrida y retorna el estado final.

    Pasa ``user_id`` en ``configurable`` para que sobreviva a la
    serialización del checkpointer (AgentState no declara ``user_id``).
    """
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if user_id:
        configurable["user_id"] = user_id
    return graph.invoke(
        {"question": question},
        {"configurable": configurable},
    )


# ===================================================================
# CICLO 1: ACEPTACIÓN — usuario confirma y el hecho se recuerda luego
# ===================================================================


class TestFullAcceptanceCycle:
    """Ciclo completo de aceptación: proponer → aceptar → recordar."""

    def test_acceptance_flow(self, clean_audit_log, mock_rag, mock_memory_service):
        """Verifica el flujo completo de aceptación en 3 turnos.

        Turno 1: El usuario dice "recuerda que ..." → memorize_node
                 genera propuesta de tipo fact.
        Turno 2: El usuario responde "sí" → remember_node clasifica
                 como accept, persiste el hecho.
        Turno 3: El usuario pregunta algo nuevo → remember_node
                 carga memoria, el hecho aparece en semantic_hits.
        """
        thread_id = "evidence-acceptance"
        user_id = "usr_evidence_accept"

        # ── Parche global del MemoryService ──────────────────────────
        patcher_mem = patch(
            "services.rag_agent.memory_nodes._get_memory",
            return_value=mock_memory_service,
        )
        patcher_mem.start()

        # ── Turno 1: El usuario pide recordar algo explícitamente ────
        msg1 = "recuerda que el límite de stock es 100 unidades"
        state1 = _invoke(msg1, thread_id=thread_id, user_id=user_id)

        # Verificar que se generó una propuesta de hecho (fact)
        proposal1 = state1.get("_memory_proposal", {})
        assert proposal1.get("type") == "fact", (
            f"Se esperaba propuesta 'fact', obtuvo '{proposal1.get('type')}'. "
            f"Estado: proposal={proposal1}"
        )
        payload = proposal1.get("payload", {})
        facts = payload.get("facts", [])
        assert any("stock" in str(f).lower() for f in facts), (
            f"Debe detectar el hecho sobre stock, facts={facts}"
        )

        # La respuesta debe incluir la pregunta de confirmación
        answer1 = state1.get("answer", "")
        assert "📝" in answer1, (
            "La respuesta debe contener el emoji 📝 de propuesta"
        )

        # Audit log: debe haber evento 'proposed'
        events_after_t1 = _audit_events()
        assert "proposed" in events_after_t1, (
            f"Debe haber evento 'proposed' en audit log, obtuvo: {events_after_t1}"
        )

        # Verificar que la última entrada del audit contiene el detalle
        last_entry = _last_audit_entry()
        assert last_entry is not None
        assert last_entry.get("event") in ("proposed",)
        assert last_entry.get("user_id") == user_id

        # ── Turno 2: El usuario ACEPTA la propuesta ──────────────────
        state2 = _invoke("sí", thread_id=thread_id, user_id=user_id)

        # La propuesta debe haber sido procesada (aceptada) y limpiada
        proposal2 = state2.get("_memory_proposal")
        assert proposal2 is None or proposal2.get("type") in (None, "none"), (
            "La propuesta debe limpiarse tras la aceptación. "
            f"_memory_proposal={proposal2!r}"
        )

        # La intención debe ser 'accept'
        assert state2.get("_memory_intent_classification") == "accept", (
            f"Clasificación debe ser 'accept', obtuvo "
            f"'{state2.get('_memory_intent_classification')}'"
        )

        # Verificar que la persistencia fue llamada en el MemoryService mockeado
        # (approve_fact para hechos, store_user_pref para preferencias)
        mock_memory_service.store_user_pref.assert_not_called()
        assert mock_memory_service.approve_fact.call_count >= 1, (
            "Se debe haber llamado a approve_fact al menos una vez"
        )

        # Audit log: debe haber evento 'accepted'
        events_after_t2 = _audit_events()
        assert "accepted" in events_after_t2, (
            f"Debe haber evento 'accepted' en audit log, obtuvo: {events_after_t2}"
        )
        # Verificar la entrada específica de accepted
        for entry in _read_audit_log():
            if entry.get("event") == "accepted":
                assert entry.get("user_id") == user_id
                break
        else:
            pytest.fail("No se encontró entrada 'accepted' en audit log")

        # ── Turno 3: El usuario hace una nueva pregunta —
        #              el agente debe recuperar el hecho ──────────────
        # Configurar el mock para que devuelva el hecho guardado
        mock_memory_service.recall = AsyncMock(return_value=[
            {
                "id": "fact-001",
                "text": "límite de stock es 100 unidades",
                "memory_type": "fact",
                "metadata": {"key": "fact:limite_de_stock"},
                "score": 0.92,
                "created_at": "2026-10-27T10:00:00",
            }
        ])
        mock_memory_service.get_user_prefs = AsyncMock(return_value={})
        mock_memory_service.get_episode = AsyncMock(return_value=None)

        state3 = _invoke(
            "¿Cuál es el límite de stock?",
            thread_id=thread_id,
            user_id=user_id,
        )

        # La memoria debe incluir el hecho recuperado
        mem_ctx3 = state3.get("memory_context", {})
        semantic_hits = mem_ctx3.get("semantic_hits", [])
        assert len(semantic_hits) > 0, (
            "El agente debe recuperar hits semánticos del hecho aceptado, "
            f"pero semantic_hits está vacío. memory_context={mem_ctx3}"
        )
        hit_texts = [h.get("text", "") for h in semantic_hits]
        assert any("stock" in t.lower() for t in hit_texts), (
            f"Ningún hit semántico contiene 'stock': {hit_texts}"
        )

        patcher_mem.stop()


# ===================================================================
# CICLO 2: RECHAZO — usuario rechaza y la memoria NO cambia
# ===================================================================


class TestFullRejectionCycle:
    """Ciclo completo de rechazo: proponer → rechazar → no recordar."""

    def test_rejection_flow(self, clean_audit_log, mock_rag, mock_memory_service):
        """Verifica el flujo completo de rechazo en 3 turnos.

        Turno 1: El usuario pide recordar algo → memorize_node
                 genera propuesta de tipo fact.
        Turno 2: El usuario responde "no" → remember_node clasifica
                 como reject, NO persiste nada.
        Turno 3: El usuario pregunta el mismo tema → el agente NO
                 tiene el hecho disponible (semantic_hits vacío).
        """
        thread_id = "evidence-rejection"
        user_id = "usr_evidence_reject"

        patcher_mem = patch(
            "services.rag_agent.memory_nodes._get_memory",
            return_value=mock_memory_service,
        )
        patcher_mem.start()

        # ── Turno 1: El usuario pide recordar algo explícitamente ────
        state1 = _invoke(
            "recuerda que Juan Pérez es el gerente de logística",
            thread_id=thread_id,
            user_id=user_id,
        )

        # Verificar que se generó una propuesta de hecho
        proposal1 = state1.get("_memory_proposal", {})
        assert proposal1.get("type") == "fact", (
            f"Se esperaba propuesta 'fact', obtuvo '{proposal1.get('type')}'. "
            f"proposal={proposal1}"
        )

        # La respuesta debe incluir la pregunta de confirmación
        answer1 = state1.get("answer", "")
        assert "📝" in answer1, (
            "La respuesta debe contener el emoji 📝 de propuesta"
        )

        # Audit log: debe haber evento 'proposed'
        events_after_t1 = _audit_events()
        assert "proposed" in events_after_t1, (
            f"Debe haber evento 'proposed' en audit log, obtuvo: {events_after_t1}"
        )

        # Contar propuestas hasta ahora
        proposed_count_before_accept = sum(
            1 for e in events_after_t1 if e == "proposed"
        )
        assert proposed_count_before_accept >= 1

        # ── Turno 2: El usuario RECHAZA la propuesta ─────────────────
        state2 = _invoke("no", thread_id=thread_id, user_id=user_id)

        # La propuesta debe haber sido descartada (no persistida)
        proposal2 = state2.get("_memory_proposal")
        assert proposal2 is None or proposal2.get("type") in (None, "none"), (
            f"La propuesta debe limpiarse tras el rechazo. proposal={proposal2!r}"
        )

        # La intención debe ser 'reject'
        assert state2.get("_memory_intent_classification") == "reject", (
            f"Clasificación debe ser 'reject', obtuvo "
            f"'{state2.get('_memory_intent_classification')}'"
        )

        # Verificar que NO se persistió nada
        mock_memory_service.approve_fact.assert_not_called()
        mock_memory_service.store_user_pref.assert_not_called()

        # Audit log: debe haber evento 'rejected'
        events_after_t2 = _audit_events()
        assert "rejected" in events_after_t2, (
            f"Debe haber evento 'rejected' en audit log, obtuvo: {events_after_t2}"
        )
        # Y NO debe haber 'accepted'
        assert "accepted" not in events_after_t2, (
            f"No debe haber 'accepted' en ciclo de rechazo: {events_after_t2}"
        )

        # ── Turno 3: El usuario pregunta por el mismo tema —
        #              el agente NO debe tener el hecho ───────────────
        # El mock devuelve vacío (nunca se persistió nada)
        mock_memory_service.recall = AsyncMock(return_value=[])
        mock_memory_service.get_user_prefs = AsyncMock(return_value={})
        mock_memory_service.get_episode = AsyncMock(return_value=None)

        state3 = _invoke(
            "¿Quién es el gerente de logística?",
            thread_id=thread_id,
            user_id=user_id,
        )

        # La memoria NO debe contener información sobre Juan Pérez
        mem_ctx3 = state3.get("memory_context", {})
        semantic_hits = mem_ctx3.get("semantic_hits", [])
        assert len(semantic_hits) == 0, (
            "El agente NO debe recuperar hits semánticos porque el hecho "
            f"fue rechazado. semantic_hits={semantic_hits}"
        )

        patcher_mem.stop()


# ===================================================================
# VERIFICACIÓN CRUZADA: audit log refleja la decisión correcta
# ===================================================================


class TestAuditTrailEvidence:
    """El audit log debe reflejar fielmente cada decisión del ciclo."""

    def test_audit_acceptance_has_correct_events(
        self, clean_audit_log, mock_rag, mock_memory_service,
    ):
        """Verifica que el audit log del ciclo de aceptación tenga:
        proposed → accepted, sin rejected."""
        thread_id = "audit-acceptance"
        user_id = "usr_audit_accept"

        patcher_mem = patch(
            "services.rag_agent.memory_nodes._get_memory",
            return_value=mock_memory_service,
        )
        patcher_mem.start()

        _invoke(
            "recuerda que el límite de stock es 100 unidades",
            thread_id=thread_id,
            user_id=user_id,
        )
        _invoke("sí, guárdalo", thread_id=thread_id, user_id=user_id)

        events = _audit_events()

        # Debe haber al menos un 'proposed' y un 'accepted'
        assert "proposed" in events, f"Falta 'proposed': {events}"
        assert "accepted" in events, f"Falta 'accepted': {events}"
        # No debe haber 'rejected'
        assert "rejected" not in events, f"No debe haber 'rejected': {events}"

        patcher_mem.stop()

    def test_audit_rejection_has_correct_events(
        self, clean_audit_log, mock_rag, mock_memory_service,
    ):
        """Verifica que el audit log del ciclo de rechazo tenga:
        proposed → rejected, sin accepted."""
        thread_id = "audit-rejection"
        user_id = "usr_audit_reject"

        patcher_mem = patch(
            "services.rag_agent.memory_nodes._get_memory",
            return_value=mock_memory_service,
        )
        patcher_mem.start()

        _invoke(
            "recuerda que mi umbral de alerta es 50",
            thread_id=thread_id,
            user_id=user_id,
        )
        _invoke("no", thread_id=thread_id, user_id=user_id)

        events = _audit_events()

        # Debe haber al menos un 'proposed' y un 'rejected'
        assert "proposed" in events, f"Falta 'proposed': {events}"
        assert "rejected" in events, f"Falta 'rejected': {events}"
        # No debe haber 'accepted'
        assert "accepted" not in events, f"No debe haber 'accepted': {events}"

        patcher_mem.stop()