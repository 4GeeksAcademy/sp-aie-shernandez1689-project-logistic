"""Tests de la Parte 2 del intake de RFPs: ciclo generador-evaluador y estado.

Cubren el grafo ``proposal_graph`` y ``proposal.generate_proposal``:

- Los tres evaluadores cuelgan del generador y corren en paralelo.
- Una sección que falla vuelve al generador con su ``feedback_for_generator``.
- Límite de iteraciones: último borrador + EvaluationResult conservados, la
  sección y el ticket quedan ``needs_human_review`` y viajan a la Parte 3.
- Persistencia: borradores/evaluaciones en ``department_sections``, estados
  del ticket ``generando_borrador → en_evaluación → final`` y
  ``proposal_handoff``; la entrada es el handoff, no el PDF.
- Router: ``POST /rfp/tickets/{id}/proposal``.

Los unitarios del generador y de cada evaluador viven en
``test_rfp_generation.py``.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import data.pipelines.rfp_intake.proposal_graph as proposal_graph_mod  # noqa: E402
from data.pipelines.rfp_intake import proposal  # noqa: E402
from data.pipelines.rfp_intake.generator import (  # noqa: E402
    TemplateDrafter,
    build_section_briefs,
)
from data.pipelines.rfp_intake.pipeline import process_rfp_pdf  # noqa: E402
from data.pipelines.rfp_intake.proposal_graph import (  # noqa: E402
    EVALUATOR_NODES,
    run_proposal_graph,
    section_graph,
)

SAMPLES_DIR = REPO_ROOT / "rfp-requests" / "trackflow"
SAMPLE_PDFS = {i: SAMPLES_DIR / f"trackflow-request-{i}.pdf" for i in (1, 2, 3)}

pytestmark = pytest.mark.skipif(
    not SAMPLE_PDFS[1].exists(), reason="Ejecuta primero scripts/generate_rfp_samples.py"
)

# Promesa prohibida por el BRIEFING §5 (devoluciones en menos de 48 h).
FAST_RETURNS = "\nProcesamos todas las devoluciones en 24 horas.\n"


class BadThenGoodDrafter(TemplateDrafter):
    """Primer borrador viola §5; al recibir el feedback lo corrige."""

    name = "bad-then-good"

    def __init__(self, bad_departments: set[str] | None = None, always_bad: bool = False):
        self.bad_departments = bad_departments
        self.always_bad = always_bad
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def draft(self, brief, previous_draft=None, evaluation=None):
        with self._lock:
            self.calls.append({
                "department_id": brief.department_id,
                "previous_draft": previous_draft,
                "evaluation": evaluation,
            })
        content = super().draft(brief, previous_draft, evaluation)
        targeted = self.bad_departments is None or brief.department_id in self.bad_departments
        if targeted and (self.always_bad or evaluation is None):
            content += FAST_RETURNS
        return content


def _briefs(idx: int, tmp_path):
    result = process_rfp_pdf(SAMPLE_PDFS[idx], tmp_path, persist=False)
    return build_section_briefs(result["handoff"], result["metadata"], result["departments"])


# ===================================================================
# Grafo
# ===================================================================


class TestProposalGraph:
    def test_evaluators_fan_out_from_generator(self):
        """generate → {readability, relevance, compliance} → aggregate."""
        edges = {(e.source, e.target) for e in section_graph.get_graph().edges}
        for node in EVALUATOR_NODES:
            assert ("generate", node) in edges
            assert (node, "aggregate") in edges

    def test_evaluators_run_in_parallel(self, tmp_path, monkeypatch):
        """Los tres evaluadores de una sección se solapan en el tiempo."""
        spans: dict[str, tuple[float, float]] = {}

        def slow(name, fn):
            def wrapper(*args):
                start = time.monotonic()
                time.sleep(0.3)
                out = fn(*args)
                spans[name] = (start, time.monotonic())
                return out
            return wrapper

        for name in EVALUATOR_NODES:
            attr = f"evaluate_{name}"
            monkeypatch.setattr(proposal_graph_mod, attr, slow(name, getattr(proposal_graph_mod, attr)))
        brief = _briefs(1, tmp_path)[0]
        run_proposal_graph([brief], drafter=TemplateDrafter())

        assert set(spans) == set(EVALUATOR_NODES)
        latest_start = max(s for s, _ in spans.values())
        earliest_end = min(e for _, e in spans.values())
        assert latest_start < earliest_end  # hay un instante en que corren los tres

    def test_one_section_node_per_department(self):
        nodes = set(proposal_graph_mod.proposal_graph.get_graph().nodes)
        assert {"section_warehouse", "section_lastmile", "section_reverse"} <= nodes

    def test_failing_department_does_not_take_down_the_others(self, tmp_path):
        class Crashes(TemplateDrafter):
            def draft(self, brief, previous_draft=None, evaluation=None):
                if brief.department_id == "reverse":
                    raise RuntimeError("boom")
                return super().draft(brief, previous_draft, evaluation)

        results = {r["department_id"]: r for r in run_proposal_graph(_briefs(1, tmp_path), drafter=Crashes())}
        assert results["warehouse"]["generation_status"] == "evaluacion_superada"
        assert results["reverse"]["generation_status"] == "needs_human_review"
        assert "boom" in results["reverse"]["error"]

    def test_slow_department_does_not_block_the_others(self, tmp_path):
        """warehouse termina su ciclo mientras reverse sigue generando."""
        class SlowReverse(TemplateDrafter):
            def draft(self, brief, previous_draft=None, evaluation=None):
                if brief.department_id == "reverse":
                    time.sleep(0.6)
                return super().draft(brief, previous_draft, evaluation)

        done: dict[str, float] = {}
        run_proposal_graph(
            _briefs(1, tmp_path), drafter=SlowReverse(),
            progress=lambda e, sid, p: done.setdefault(sid.rsplit("_", 1)[-1], time.monotonic())
            if e == "evaluated" else None,
        )
        assert done["warehouse"] + 0.3 < done["reverse"]

    @pytest.mark.parametrize("idx,departments", [
        (1, ["warehouse", "reverse"]), (2, ["warehouse", "lastmile"]),
    ])
    def test_template_sections_pass_first_iteration(self, tmp_path, idx, departments):
        results = run_proposal_graph(_briefs(idx, tmp_path), drafter=TemplateDrafter())
        assert [r["department_id"] for r in results] == departments
        for r in results:
            assert r["generation_status"] == "evaluacion_superada"
            assert r["iterations"] == 1
            assert r["evaluation"]["overall_pass"] is True

    def test_failed_section_returns_to_generator_with_feedback(self, tmp_path):
        drafter = BadThenGoodDrafter()
        brief = _briefs(1, tmp_path)[1]  # reverse
        [result] = run_proposal_graph([brief], drafter=drafter)

        assert result["iterations"] == 2
        assert result["generation_status"] == "evaluacion_superada"
        first, second = result["evaluation_results"]
        assert first["overall_pass"] is False
        assert "returns-min-48h" in first["compliance"]["rule_ids"]
        assert first["feedback_for_generator"]
        # El generador recibió el borrador anterior y ese mismo feedback.
        retry = drafter.calls[1]
        assert retry["previous_draft"] == result["drafts"][0]["content"]
        assert retry["evaluation"]["feedback_for_generator"] == first["feedback_for_generator"]
        assert second["overall_pass"] is True
        assert "24 horas" not in result["draft_content"]

    def test_iteration_limit_keeps_last_draft_for_human_review(self, tmp_path):
        drafter = BadThenGoodDrafter(always_bad=True)
        brief = _briefs(1, tmp_path)[1]
        [result] = run_proposal_graph([brief], drafter=drafter, max_iterations=3)

        assert result["iterations"] == 3
        assert len(drafter.calls) == 3  # el ciclo se corta en el límite
        assert result["generation_status"] == "needs_human_review"
        assert len(result["drafts"]) == len(result["evaluation_results"]) == 3
        assert result["draft_content"] == result["drafts"][-1]["content"]
        assert result["evaluation"] == result["evaluation_results"][-1]
        assert result["evaluation"]["overall_pass"] is False


# ===================================================================
# Pipeline con persistencia (SQLite con los modelos de PostgreSQL)
# ===================================================================


@pytest.fixture
def sqlite_db():
    from sqlalchemy.pool import StaticPool
    from sqlmodel import create_engine

    import data.pipelines.rfp_intake.db as db_mod

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    ).execution_options(schema_translate_map={"rfp_intake": None})
    db_mod.create_tables(engine)
    db_mod.set_engine(engine)
    yield db_mod
    db_mod.set_engine(None)


@pytest.fixture
def analyzed(sqlite_db, tmp_path, monkeypatch):
    """Ticket de la muestra 1 (ModaViva) en ``analisis_completo``."""
    import data.pipelines.rfp_intake.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
    ticket = pipeline_mod.register_upload(SAMPLE_PDFS[1].read_bytes())
    pipeline_mod.analyze_ticket(ticket["ticket_id"])
    assert sqlite_db.get_ticket(ticket["ticket_id"])["status"] == "analisis_completo"
    return ticket["ticket_id"]


def _track_statuses(sqlite_db, monkeypatch) -> list[str]:
    seen: list[str] = []
    original = sqlite_db.update_ticket

    def spy(ticket_id, **fields):
        if "status" in fields:
            seen.append(fields["status"])
        return original(ticket_id, **fields)

    monkeypatch.setattr(sqlite_db, "update_ticket", spy)
    return seen


class TestGenerateProposal:
    def test_all_sections_pass(self, sqlite_db, analyzed, monkeypatch):
        statuses = _track_statuses(sqlite_db, monkeypatch)
        handoff = proposal.generate_proposal(analyzed, drafter=TemplateDrafter())

        assert statuses == ["generando_borrador", "en_evaluación", "esperando_aprobación"]
        assert sqlite_db.get_ticket(analyzed)["status"] == "esperando_aprobación"
        assert handoff["needs_human_review"] == []
        assert handoff["avg_iterations"] == 1
        assert sqlite_db.get_rfp_metadata(analyzed)["proposal_handoff"] == handoff
        for section in sqlite_db.get_department_sections(analyzed):
            assert section["generation_status"] == "evaluacion_superada"
            assert section["draft_content"].startswith("## Propuesta económica")
            assert len(section["drafts"]) == len(section["evaluation_results"]) == 1
            result = section["evaluation_results"][0]
            assert {"readability", "relevance", "compliance", "overall_pass",
                    "feedback_for_generator"} <= set(result)
            assert result["section_id"] == section["id"]

    def test_exhausted_section_marks_ticket_needs_human_review(self, sqlite_db, analyzed, monkeypatch):
        statuses = _track_statuses(sqlite_db, monkeypatch)
        drafter = BadThenGoodDrafter(bad_departments={"reverse"}, always_bad=True)
        handoff = proposal.generate_proposal(analyzed, drafter=drafter, max_iterations=2)

        assert statuses == ["generando_borrador", "en_evaluación", "needs_human_review"]
        ticket = sqlite_db.get_ticket(analyzed)
        assert ticket["status"] == "needs_human_review"  # no se descarta
        sections = {s["department_id"]: s for s in sqlite_db.get_department_sections(analyzed)}
        reverse, warehouse = sections["reverse"], sections["warehouse"]
        assert reverse["generation_status"] == "needs_human_review"
        assert reverse["iterations"] == 2
        assert len(reverse["drafts"]) == len(reverse["evaluation_results"]) == 2
        assert reverse["evaluation_results"][-1]["overall_pass"] is False
        assert warehouse["generation_status"] == "evaluacion_superada"
        # Ambas secciones viajan a la Parte 3, la de revisión humana incluida.
        assert handoff["needs_human_review"] == [reverse["id"]]
        by_dep = {s["department_id"]: s for s in handoff["sections"]}
        assert set(by_dep) == {"warehouse", "reverse"}
        assert by_dep["reverse"]["draft_content"] == reverse["draft_content"]
        assert by_dep["reverse"]["evaluation"] == reverse["evaluation_results"][-1]

        # Provisional visible para Ventas: cabecera en el texto, bandera y resumen.
        assert reverse["draft_content"].startswith("> **BORRADOR PROVISIONAL**")
        assert "returns-min-48h" in reverse["draft_content"].splitlines()[0]
        assert "Sofía Ramos" in reverse["draft_content"].splitlines()[0]
        assert not reverse["drafts"][-1]["content"].startswith(">")  # historial intacto
        assert not warehouse["draft_content"].startswith(">")
        assert by_dep["reverse"]["provisional"] is True
        assert by_dep["warehouse"]["provisional"] is False
        proposal_summary = sqlite_db.get_rfp_metadata(analyzed)["sales_summary"]["proposal"]
        assert proposal_summary["status"] == "needs_human_review"
        [flagged] = proposal_summary["provisional_sections"]
        assert flagged["department_id"] == "reverse" and flagged["owner"] == "Sofía Ramos"
        assert "returns-min-48h" in flagged["pending"]

    def test_ticket_reason_shows_live_progress_per_section(self, sqlite_db, analyzed, monkeypatch):
        reasons: list[str] = []
        original = sqlite_db.update_ticket

        def spy(ticket_id, **fields):
            if "reason" in fields:
                reasons.append(fields["reason"])
            return original(ticket_id, **fields)

        monkeypatch.setattr(sqlite_db, "update_ticket", spy)
        drafter = BadThenGoodDrafter(bad_departments={"reverse"})
        proposal.generate_proposal(analyzed, drafter=drafter)

        assert any("reverse: borrador 1 no pasó la evaluación" in r for r in reasons)
        assert any("reverse: generando borrador 2" in r for r in reasons)
        assert any("warehouse: borrador 1 aprobado por los evaluadores" in r for r in reasons)

    def test_input_is_handoff_not_raw_pdf(self, sqlite_db, analyzed, tmp_path):
        """Sin PDF ni Markdown en disco la Parte 2 funciona igual."""
        for path in tmp_path.glob(f"{analyzed}.*"):
            path.unlink()
        proposal.generate_proposal(analyzed, drafter=TemplateDrafter())
        assert sqlite_db.get_ticket(analyzed)["status"] == "esperando_aprobación"

    def test_discarded_ticket_is_not_generated(self, sqlite_db, tmp_path, monkeypatch):
        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
        ticket = pipeline_mod.register_upload(SAMPLE_PDFS[3].read_bytes())
        pipeline_mod.analyze_ticket(ticket["ticket_id"])
        with pytest.raises(proposal.ProposalNotReadyError):
            proposal.generate_proposal(ticket["ticket_id"])

    def test_process_ticket_chains_part_2(self, sqlite_db, tmp_path, monkeypatch):
        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
        monkeypatch.setenv("RFP_GENERATOR", "template")
        ticket = pipeline_mod.register_upload(SAMPLE_PDFS[2].read_bytes())
        assert proposal.process_ticket(ticket["ticket_id"])["status"] == "esperando_aprobación"

        monkeypatch.setenv("RFP_AUTO_PROPOSAL", "0")
        other = pipeline_mod.register_upload(SAMPLE_PDFS[2].read_bytes())
        assert proposal.process_ticket(other["ticket_id"])["status"] == "analisis_completo"


class TestProposalRoute:
    @pytest.fixture
    def client(self, sqlite_db, monkeypatch):
        api_dir = REPO_ROOT / "services" / "api"
        if str(api_dir) not in sys.path:
            sys.path.insert(0, str(api_dir))
        from auth import CurrentUser, get_current_user
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setenv("RFP_RECOVERY_INTERVAL_SECONDS", "0")
        monkeypatch.setenv("RFP_GENERATOR", "template")
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def test_regenerate_proposal(self, client, analyzed):
        resp = client.post(f"/rfp/tickets/{analyzed}/proposal")
        assert resp.status_code == 202, resp.text
        detail = client.get(f"/rfp/tickets/{analyzed}").json()
        assert detail["ticket"]["status"] == "esperando_aprobación"
        assert detail["proposal_handoff"]["status"] == "esperando_aprobación"
        assert all(d["evaluation_results"] for d in detail["departments"])

    def test_missing_ticket_404(self, client):
        assert client.post("/rfp/tickets/rfp_nope/proposal").status_code == 404

    def test_ticket_without_handoff_409(self, client, sqlite_db):
        sqlite_db.save_ticket({"ticket_id": "rfp_x", "rfp_id": "rfp_x-rfp",
                               "status": "descartado", "raw_pdf_path": "/tmp/x.pdf"})
        assert client.post("/rfp/tickets/rfp_x/proposal").status_code == 409
