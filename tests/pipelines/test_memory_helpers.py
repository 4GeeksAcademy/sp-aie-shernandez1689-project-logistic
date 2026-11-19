"""Tests de los helpers puros de memoria (sin dependencias externas).

Cubre las funciones auxiliares de ``services.rag_agent.memory_nodes``
que no requieren Redis, Qdrant ni MemoryService:
  - ``_classify_proposal_intent`` — clasificador estructurado de intención.
  - ``_is_pure_confirmation`` — detección de mensaje solo-confirmación.
  - ``_extract_preferences`` — heurística de extracción de preferencias.
  - ``_extract_approved_facts`` — extracción de hechos confirmados.
  - ``_extract_explicit_remember_request`` — detección de solicitud explícita.
"""

from __future__ import annotations

import json

import pytest

from services.rag_agent.memory_nodes import (
    _audit_proposal,
    _classify_proposal_intent,
    _extract_approved_facts,
    _extract_explicit_remember_request,
    _extract_preferences,
    _is_pure_confirmation,
)

# ── Fixture compartido ─────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _backup_audit_path(monkeypatch):
    """Redirige el log de auditoría a un directorio temporal.

    Evita ensuciar ``data/audit/`` durante los tests y permite
    verificar el contenido del archivo en ``test_audit_proposal``.
    """
    import tempfile
    from pathlib import Path
    tmp = tempfile.mkdtemp()
    test_path = Path(tmp) / "memory_proposals.jsonl"
    monkeypatch.setattr("services.rag_agent.memory_nodes._AUDIT_LOG_PATH", test_path)
    return test_path


# ═══════════════════════════════════════════════════════════════════════
# Tests de _classify_proposal_intent
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyProposalIntent:
    """Cobertura: accept, reject, edit, topic_change, ambiguous."""

    PROPOSAL = {"type": "fact", "payload": {"facts": []}}

    # -- accept --

    @pytest.mark.parametrize("text", [
        "sí",
        "sí, guarda eso",
        "ok",
        "okay",
        "de acuerdo",
        "deacuerdo",
        "confirmo",
        "adelante",
        "claro",
        "sí, guárdalo",
        "sí, recuérdalo",
        "good",
        "yes",
        "sure",
        "go ahead",
        "Sí",
        "SI",
    ])
    def test_accept_explicit(self, text: str):
        assert _classify_proposal_intent(text, self.PROPOSAL) == "accept"

    def test_accept_with_extra_punctuation(self):
        assert _classify_proposal_intent("¡Sí!", self.PROPOSAL) == "accept"
        assert _classify_proposal_intent("Sí.", self.PROPOSAL) == "accept"
        assert _classify_proposal_intent("ok?", self.PROPOSAL) == "accept"

    # -- reject --

    @pytest.mark.parametrize("text", [
        "no",
        "no gracias",
        "no guardes",
        "no recuerdes",
        "nope",
        "not now",
        "no thanks",
        "no quiero",
        "No",
        "NO",
    ])
    def test_reject_explicit(self, text: str):
        assert _classify_proposal_intent(text, self.PROPOSAL) == "reject"

    # -- edit --

    @pytest.mark.parametrize("text", [
        "en realidad es otra cosa",
        "cambia eso por",
        "corrige el nombre",
        "mejor di que es gerente",
        "edita eso",
        "modifica el valor",
        "no es eso, es otra cosa",
        "rectifica por favor",
        "realmente es distinto",
    ])
    def test_edit_patterns(self, text: str):
        assert _classify_proposal_intent(text, self.PROPOSAL) == "edit"

    # -- topic_change --

    @pytest.mark.parametrize("text", [
        "¿cuál es el estado del ticket INC-123?",
        "dime el stock del SKU CLT-SNK-W-42",
        "¿qué es una incidencia?",
        "¿cómo funciona la política de devoluciones?",
        "gracias por la info",
        "hola, buenos días",
        "¿qué tal?",
    ])
    def test_topic_change_operational(self, text: str):
        assert _classify_proposal_intent(text, self.PROPOSAL) == "topic_change"

    def test_topic_change_long(self):
        """Mensajes largos sin palabras clave operacionales."""
        result = _classify_proposal_intent(
            "Prefiero no responder a eso ahora mismo, hablemos de otra cosa",
            self.PROPOSAL,
        )
        # "otra cosa" no es señal operacional → ambiguous o topic_change
        assert result in ("topic_change", "ambiguous")

    # -- ambiguous --

    @pytest.mark.parametrize("text", ["", "   ", "?", "ajá", "mmh"])
    def test_ambiguous_empty_or_short(self, text: str):
        assert _classify_proposal_intent(text, self.PROPOSAL) == "ambiguous"

    def test_ambiguous_no_proposal(self):
        assert _classify_proposal_intent("sí", None) == "ambiguous"
        assert _classify_proposal_intent("", None) == "ambiguous"

    # -- combined messages --

    def test_accept_combined_message(self):
        """Mensaje combinado: confirmación + nueva pregunta."""
        assert _classify_proposal_intent(
            "sí, y dime el stock del SKU X", self.PROPOSAL,
        ) == "accept"

    def test_reject_combined_message(self):
        assert _classify_proposal_intent(
            "no, mejor pregúntame después", self.PROPOSAL,
        ) == "reject"


# ═══════════════════════════════════════════════════════════════════════
# Tests de _is_pure_confirmation
# ═══════════════════════════════════════════════════════════════════════


class TestIsPureConfirmation:

    @pytest.mark.parametrize("text", [
        "sí", "no", "ok", "okay", "yes", "sure", "claro",
        "Sí.", "NO", "¡Sí!", "de acuerdo",
    ])
    def test_pure_confirmation(self, text: str):
        assert _is_pure_confirmation(text) is True

    @pytest.mark.parametrize("text", [
        "sí, y dime el stock",
        "no, pero cuéntame del ticket",
        "ok, ahora necesito el inventario",
        "sí guarda eso y dime el estado de INC-123",
    ])
    def test_combined_message(self, text: str):
        assert _is_pure_confirmation(text) is False

    def test_empty_string(self):
        assert _is_pure_confirmation("") is True


# ═══════════════════════════════════════════════════════════════════════
# Tests de _audit_proposal
# ═══════════════════════════════════════════════════════════════════════


class TestAuditProposal:

    def test_writes_jsonl_line(self, _backup_audit_path):
        _audit_proposal(
            event="accepted",
            proposal={"type": "fact", "payload": {"facts": []}},
            user_message="sí",
            result={"details": ["person:role:juan: gerente"]},
            thread_id="test-thread",
            uid="user-1",
        )
        assert _backup_audit_path.exists()
        lines = _backup_audit_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "accepted"
        assert entry["thread_id"] == "test-thread"
        assert entry["user_id"] == "user-1"
        assert entry["proposal"]["type"] == "fact"

    def test_multiple_events_append(self, _backup_audit_path):
        for evt in ("proposed", "accepted", "rejected", "discarded"):
            _audit_proposal(
                event=evt,
                proposal={"type": "preference"},
                user_message=f"msg-{evt}",
                result={},
                thread_id="t1",
                uid="u1",
            )
        lines = _backup_audit_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 4

    def test_no_error_on_empty_proposal(self, _backup_audit_path):
        # No debe lanzar excepción aunque proposal sea None
        _audit_proposal(
            event="none",
            proposal=None,
            user_message=None,
            result={"proposal_type": "none"},
            thread_id="t1",
            uid=None,
        )
        assert _backup_audit_path.exists()


# ═══════════════════════════════════════════════════════════════════════
# Tests de _extract_preferences
# ═══════════════════════════════════════════════════════════════════════


class TestExtractPreferences:

    @pytest.mark.parametrize("text,expected_lang", [
        ("en español, por favor", "es"),
        ("respóndeme en español", "es"),
        ("in english please", "en"),
        ("respondeme en ingles", "en"),
    ])
    def test_language_preference(self, text: str, expected_lang: str):
        assert _extract_preferences(text).get("language") == expected_lang

    @pytest.mark.parametrize("text,expected_detail", [
        ("solo el resumen", "summary"),
        ("resumido", "summary"),
        ("datos completos", "full"),
        ("máximo detalle", "full"),
        ("con todo detalle", "full"),
    ])
    def test_detail_level(self, text: str, expected_detail: str):
        assert _extract_preferences(text).get("detail_level") == expected_detail

    def test_no_preferences(self):
        assert _extract_preferences("¿cuál es el estado del ticket INC-123?") == {}


# ═══════════════════════════════════════════════════════════════════════
# Tests de _extract_approved_facts
# ═══════════════════════════════════════════════════════════════════════


class TestExtractApprovedFacts:

    def test_single_fact(self):
        facts = _extract_approved_facts(
            "Confirmado: Juan Pérez es Gerente de Ventas."
        )
        assert len(facts) == 1
        assert facts[0][0] == "person:role:juan pérez"
        assert "gerente" in facts[0][1].lower()

    def test_multiple_facts(self):
        facts = _extract_approved_facts(
            "Confirmado: Ana López es Directora de Marketing. "
            "Confirmamos: Carlos Ruiz lidera el equipo de IT."
        )
        assert len(facts) == 2

    def test_no_facts(self):
        assert _extract_approved_facts("¿Cuál es el clima?") == []

    def test_various_phrasings(self):
        facts = _extract_approved_facts(
            "Hecho: María García es la CEO."
        )
        assert len(facts) == 1
        assert facts[0][0] == "person:role:maría garcía"


# ═══════════════════════════════════════════════════════════════════════
# Tests de _extract_explicit_remember_request
# ═══════════════════════════════════════════════════════════════════════


class TestExtractExplicitRememberRequest:

    def test_recuerda_que(self):
        result = _extract_explicit_remember_request(
            "recuerda que Juan es gerente"
        )
        assert result is not None
        assert "juan" in result["key"]
        assert result["value"] == "gerente"

    def test_guarda_que(self):
        result = _extract_explicit_remember_request(
            "guarda que mi sucursal favorita es Viña del Mar"
        )
        assert result is not None
        assert result["value"] == "viña del mar"

    def test_toma_nota(self):
        result = _extract_explicit_remember_request(
            "toma nota: la reunión es el lunes"
        )
        assert result is not None
        assert "reunión" in result["value"]

    def test_no_request(self):
        assert _extract_explicit_remember_request(
            "¿cuál es el estado del ticket?"
        ) is None