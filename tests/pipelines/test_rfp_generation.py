"""Unitarios del agente generador y de los evaluadores (Hito 9, Parte 2).

Briefs construidos a partir del handoff de la Parte 1 de las muestras del
BRIEFING §4 (ModaViva → EUR, Luna Cosmetics → USD), sin PDF ni base de datos.
El ciclo generador-evaluador y la persistencia viven en ``test_rfp_proposal.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data.pipelines.rfp_intake.evaluators import (  # noqa: E402
    evaluate_compliance,
    evaluate_readability,
    evaluate_relevance,
    evaluate_section,
)
from data.pipelines.rfp_intake.generator import (  # noqa: E402
    LLMDrafter,
    TemplateDrafter,
    build_section_briefs,
    generate_section,
    section_headings,
)

MODAVIVA_META = {
    "client_name": "ModaViva Fashion S.L.", "client_country": "Spain", "currency": "EUR",
    "monthly_volume": 3000, "deadline": "2026-06-30", "budget_range": "EUR 50.000-80.000",
    "services_requested": ["warehouse", "reverse"],
}
LUNA_META = {
    "client_name": "Luna Cosmetics", "client_country": "US", "currency": "USD",
    "monthly_volume": 5000, "deadline": "2026-05-15", "budget_range": "USD 40.000",
    "services_requested": ["warehouse", "lastmile"],
}


def _handoff(ticket_id, meta, departments):
    return {
        "part": 2, "ticket_id": ticket_id, "rfp_id": f"{ticket_id}-rfp",
        "next_status": "generando_borrador", "currency": meta["currency"],
        "departments": [d for d, _ in departments],
        "section_ids": [f"{ticket_id}_{d}" for d, _ in departments],
        "sections": [
            {"department_id": d, "owner": None,
             "key_aspects": [f"Volumen mensual indicado en la RFP: {meta['monthly_volume']} pedidos/mes"],
             "open_questions": questions}
            for d, questions in departments
        ],
    }


@pytest.fixture
def modaviva():
    handoff = _handoff("rfp_mv", MODAVIVA_META, [
        ("warehouse", ["Número de SKUs y posiciones de pallet a almacenar."]),
        ("reverse", ["Tasa o volumen mensual de devoluciones esperado."]),
    ])
    rows = [{"department_id": "reverse", "id": "rfp_mv_reverse",
             "source_excerpts": ["Gestión de devoluciones y reacondicionamiento"]}]
    return {b.department_id: b for b in build_section_briefs(handoff, MODAVIVA_META, rows)}


@pytest.fixture
def luna():
    handoff = _handoff("rfp_luna", LUNA_META, [
        ("warehouse", ["Número de SKUs y posiciones de pallet a almacenar."]),
        ("lastmile", ["Plazo/SLA de entrega que espera el cliente."]),
    ])
    return {b.department_id: b for b in build_section_briefs(handoff, LUNA_META, [])}


# ===================================================================
# Agente generador
# ===================================================================


class TestGenerator:
    def test_brief_comes_from_handoff(self, modaviva):
        brief = modaviva["reverse"]
        assert brief.section_id == "rfp_mv_reverse"
        assert brief.owner == "Sofía Ramos"
        assert brief.currency == "EUR"
        assert brief.source_excerpts == ["Gestión de devoluciones y reacondicionamiento"]
        assert brief.open_questions == ["Tasa o volumen mensual de devoluciones esperado."]

    def test_template_section_follows_format_and_currency(self, modaviva):
        draft = TemplateDrafter().draft(modaviva["warehouse"])
        positions = [draft.index(h) for h in section_headings(modaviva["warehouse"])]
        assert positions == sorted(positions)  # encabezados en orden
        assert "€" in draft and "USD" not in draft
        assert "ModaViva" in draft and "3.000" in draft
        assert "Miguel Torres" in draft
        assert "Número de SKUs" in draft  # pregunta abierta trasladada

    def test_us_warehouse_never_quotes_eur_prices(self, luna):
        """La KB solo tiene tarifas de almacén en EUR: en USD quedan a cotizar."""
        draft = TemplateDrafter().draft(luna["warehouse"])
        assert "€" not in draft and "EUR" not in draft
        assert "a cotizar por Ana Whitfield" in draft

    def test_retry_declares_missing_aspects_as_pending(self, modaviva):
        evaluation = {"relevance": {"missing_aspects": ["tasa de reacondicionamiento"]},
                      "feedback_for_generator": "Pertinencia: falta tasa de reacondicionamiento."}
        draft = TemplateDrafter().draft(modaviva["reverse"], "borrador previo", evaluation)
        assert "Por confirmar con el cliente: tasa de reacondicionamiento." in draft

    def test_llm_retry_prompt_carries_feedback(self, modaviva):
        evaluation = {"feedback_for_generator": "Cumplimiento [returns-min-48h]: quita las 24 horas."}
        messages = LLMDrafter(api_key="x").messages(modaviva["reverse"], "borrador viejo", evaluation)
        user = messages[1]["content"]
        assert "quita las 24 horas" in user and "borrador viejo" in user
        assert "## Propuesta económica — Reverse Logistics" in messages[0]["content"]

    def test_llm_failure_falls_back_to_template(self, modaviva, monkeypatch):
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        result = generate_section(modaviva["reverse"], drafter=LLMDrafter(api_key=None))
        assert result["generator"].startswith("template (fallback")
        assert result["content"].startswith("## Propuesta económica")


# ===================================================================
# Evaluadores
# ===================================================================


class TestEvaluators:
    @pytest.mark.parametrize("sample,dep", [
        ("modaviva", "warehouse"), ("modaviva", "reverse"), ("luna", "warehouse"), ("luna", "lastmile"),
    ])
    def test_template_drafts_pass_all_evaluators(self, request, sample, dep):
        brief = request.getfixturevalue(sample)[dep]
        result = evaluate_section(TemplateDrafter().draft(brief), brief)
        assert result.overall_pass, result.feedback_for_generator
        assert result.feedback_for_generator == ""

    def test_compliance_fails_on_returns_under_48h(self, modaviva):
        """BRIEFING §5: ninguna propuesta promete devoluciones en menos de 48 horas."""
        brief = modaviva["reverse"]
        draft = TemplateDrafter().draft(brief) + "\nProcesamos todas las devoluciones en 24 horas.\n"
        check = evaluate_compliance(draft, brief)
        assert check.passed is False
        assert "returns-min-48h" in check.rule_ids

    def test_compliance_fails_on_wrong_currency(self, luna):
        brief = luna["warehouse"]
        draft = TemplateDrafter().draft(brief) + "\nAlmacenaje: 4,50 € por palet/día.\n"
        assert "currency-by-country" in evaluate_compliance(draft, brief).rule_ids

    def test_compliance_fails_on_carrier_rate(self, luna):
        brief = luna["lastmile"]
        draft = TemplateDrafter().draft(brief) + "\nUPS nos cobra 3,10 USD por envío.\n"
        assert "no-carrier-rates" in evaluate_compliance(draft, brief).rule_ids

    def test_relevance_lists_what_the_rfp_asks(self, modaviva):
        brief = modaviva["warehouse"]
        draft = "\n".join(section_headings(brief)) + "\nOfrecemos almacenamiento.\n"
        check = evaluate_relevance(draft, brief)
        assert check.passed is False
        joined = " ".join(check.missing_aspects)
        assert "onboarding" in joined and "3000" in joined

    def test_readability_fails_on_long_sentences(self):
        sentence = " ".join(["almacenamiento logístico integral con cobertura"] * 20)
        check = evaluate_readability(f"## T\n\n{sentence}.\n")
        assert check.passed is False

    def test_failed_evaluation_feedback_is_actionable(self, modaviva):
        brief = modaviva["reverse"]
        draft = TemplateDrafter().draft(brief) + "\nProcesamos todas las devoluciones en 24 horas.\n"
        result = evaluate_section(draft, brief).to_dict()
        assert result["overall_pass"] is False
        assert result["compliance"]["pass"] is False
        assert "returns-min-48h" in result["feedback_for_generator"]
