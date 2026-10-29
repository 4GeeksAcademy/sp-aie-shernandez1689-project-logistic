"""Tests de la Parte 3 del intake de RFPs: aprobación humana por departamento.

Cubren ``approval_graph`` (rama interrumpible por departamento + coordinador)
y ``approval`` (validación de la respuesta humana, resume, cierre):

- Interrupción por rama y resume como entrada explícita (no reinicio), también
  tras "reiniciar" el proceso con un checkpointer nuevo sobre el mismo fichero.
- Validación de la respuesta humana antes de entrar al grafo.
- Límites: rondas de cambios por sección y rondas de arbitraje por ticket.
- Arbitraje §7: ``returns-sla-breach``, ``currency-mismatch``,
  ``volume-vs-capacity`` con su árbitro fijo.
- Ramas paralelas: B se aprueba mientras A sigue interrumpida, y dos
  reescrituras se solapan en el tiempo.
- ``thread_id`` por ticket/departamento, trace por nodo, documento final.
- E2E Partes 1→3 con resumes simulados (pipeline y API HTTP).

Los PDF de prueba son los del BRIEFING §4 (``rfp-requests/trackflow``); los
departamentos aprueban de forma programática con ``submit_decision``.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from langgraph.types import Command  # noqa: E402

import data.pipelines.rfp_intake.approval as approval  # noqa: E402
from data.pipelines import checkpointer as ckpt  # noqa: E402
from data.pipelines.rfp_intake import proposal  # noqa: E402
from data.pipelines.rfp_intake.approval_graph import (  # noqa: E402
    department_approval_graph,
    department_config,
    ticket_approval_graph,
    ticket_config,
)
from data.pipelines.rfp_intake.conflicts import detect_conflicts, section_currencies  # noqa: E402
from data.pipelines.rfp_intake.generator import TemplateDrafter  # noqa: E402

SAMPLES_DIR = REPO_ROOT / "rfp-requests" / "trackflow"
SAMPLE_PDFS = {i: SAMPLES_DIR / f"trackflow-request-{i}.pdf" for i in (1, 2, 3)}

pytestmark = pytest.mark.skipif(
    not SAMPLE_PDFS[1].exists(), reason="Ejecuta primero scripts/generate_rfp_samples.py"
)

ANA, CARLOS, SOFIA, MIGUEL = "Ana Whitfield", "Carlos Vega", "Sofía Ramos", "Miguel Torres"
OWNERS = {"warehouse": ANA, "lastmile": CARLOS, "reverse": SOFIA}
FAST_RETURNS = "\nProcesamos todas las devoluciones en 24 horas.\n"


# ===================================================================
# Redactores de prueba (agentes generadores con fallos controlados)
# ===================================================================


class FastReturnsDrafter(TemplateDrafter):
    """reverse promete devoluciones en 24 h (viola §5) hasta recibir feedback."""

    def __init__(self, always: bool = False):
        self.always = always

    def draft(self, brief, previous_draft=None, evaluation=None):
        content = super().draft(brief, previous_draft, evaluation)
        if brief.department_id == "reverse" and (self.always or evaluation is None):
            content += FAST_RETURNS
        return content


class WrongCurrencyDrafter(TemplateDrafter):
    """warehouse cotiza en EUR una RFP de EE. UU. hasta que el árbitro fija la moneda."""

    def draft(self, brief, previous_draft=None, evaluation=None):
        content = super().draft(brief, previous_draft, evaluation)
        if brief.department_id == "warehouse" and not brief.approval_notes:
            content = content.replace("USD", "EUR")
        return content


class CapacityDrafter(TemplateDrafter):
    """warehouse compromete menos capacidad que el volumen que asume lastmile."""

    def __init__(self, capacity: str = "3.000"):
        self.capacity = capacity

    def draft(self, brief, previous_draft=None, evaluation=None):
        content = super().draft(brief, previous_draft, evaluation)
        if brief.department_id == "warehouse":
            content += f"\nCapacidad comprometida: {self.capacity} pedidos/mes.\n"
        return content


class SlowDrafter(TemplateDrafter):
    """Registra cuándo reescribe cada departamento (para medir solapamiento)."""

    def __init__(self, delay: float = 0.5):
        self.delay = delay
        self.spans: dict[str, tuple[float, float]] = {}

    def draft(self, brief, previous_draft=None, evaluation=None):
        start = time.monotonic()
        if evaluation is not None and brief.approval_notes:
            time.sleep(self.delay)
        content = super().draft(brief, previous_draft, evaluation)
        if brief.approval_notes:
            self.spans[brief.department_id] = (start, time.monotonic())
        return content


# ===================================================================
# Fixtures: PostgreSQL simulado (SQLite con los modelos) + checkpointer
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


def _saver(path: Path) -> SqliteSaver:
    saver = SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    saver.setup()
    return saver


@pytest.fixture
def checkpoint_file(tmp_path):
    """Checkpointer SQLite propio del test (fichero, para simular reinicios)."""
    path = tmp_path / "checkpoints.sqlite"
    previous = ckpt._SHARED
    ckpt.set_checkpointer(_saver(path))
    yield path
    ckpt.set_checkpointer(previous)


@pytest.fixture
def make_ticket(sqlite_db, checkpoint_file, tmp_path, monkeypatch):
    """Ticket de una muestra del BRIEFING con la Parte 2 hecha (sin abrir la Parte 3)."""
    import data.pipelines.rfp_intake.pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)

    def make(idx: int = 1, drafter=None, max_iterations: int | None = None) -> str:
        ticket = pipeline_mod.register_upload(SAMPLE_PDFS[idx].read_bytes())
        pipeline_mod.analyze_ticket(ticket["ticket_id"])
        proposal.generate_proposal(
            ticket["ticket_id"], drafter=drafter or TemplateDrafter(), max_iterations=max_iterations,
        )
        return ticket["ticket_id"]

    return make


def _approve(ticket_id: str, department_id: str, drafter=None):
    return approval.submit_decision(
        ticket_id, department_id, {"decision": "approve", "approver": OWNERS[department_id]},
        drafter=drafter,
    )


def _changes(ticket_id: str, department_id: str, comments: str, drafter=None):
    return approval.submit_decision(
        ticket_id, department_id,
        {"decision": "request_changes", "approver": OWNERS[department_id], "comments": comments},
        drafter=drafter,
    )


def _dept_state(ticket_id: str, department_id: str):
    return department_approval_graph().get_state(department_config(ticket_id, department_id))


def _nodes(ticket_id: str, department_id: str) -> list[str]:
    return [e["node"] for e in _dept_state(ticket_id, department_id).values["trace"]]


def _sections(sqlite_db, ticket_id: str) -> dict[str, dict]:
    return {s["department_id"]: s for s in sqlite_db.get_department_sections(ticket_id)}


# ===================================================================
# Interrupción y reanudación
# ===================================================================


class TestInterruptAndResume:
    def test_open_interrupts_each_department_in_its_own_thread(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        state = approval.open_approval(ticket_id)

        assert sqlite_db.get_ticket(ticket_id)["status"] == "esperando_aprobación"
        assert set(state["departments"]) == {"warehouse", "reverse"}
        for dep in ("warehouse", "reverse"):
            snapshot = _dept_state(ticket_id, dep)
            assert snapshot.next == ("await_approval",)  # pausada en el interrupt
            pending = approval.pending_interrupt(ticket_id, dep)
            assert pending["approver"] == OWNERS[dep]
            assert pending["allowed_decisions"] == ["approve", "reject", "request_changes"]
            assert state["departments"][dep]["awaiting"] is True
            assert state["departments"][dep]["thread_id"] == f"rfp-{ticket_id}:{dep}"
            assert _sections(sqlite_db, ticket_id)[dep]["approval_status"] == "pendiente"
        assert "lastmile" not in state["departments"]  # ModaViva no pide última milla
        assert state["thread_id"] == f"rfp-{ticket_id}"
        reason = sqlite_db.get_ticket(ticket_id)["reason"]
        assert "pendiente de aprobación" in reason and ANA in reason and SOFIA in reason

    def test_approve_resumes_at_the_interrupt_not_from_scratch(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        _approve(ticket_id, "warehouse")

        # Un solo prepare: el resume continúa en await_approval.
        assert _nodes(ticket_id, "warehouse") == ["prepare", "await_approval", "approve"]
        assert _dept_state(ticket_id, "warehouse").next == ()
        section = _sections(sqlite_db, ticket_id)["warehouse"]
        assert section["approval_status"] == "aprobada"
        assert section["approver"] == ANA
        assert section["approved_at"] is not None
        assert section["approval_history"][-1]["decision"] == "approve"

    def test_all_approved_generates_final_document_and_terminado(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        _approve(ticket_id, "reverse")
        assert sqlite_db.get_final_document(ticket_id) is None  # falta warehouse
        state = _approve(ticket_id, "warehouse")

        ticket = sqlite_db.get_ticket(ticket_id)
        assert ticket["status"] == "terminado"
        assert ticket["reason"] == "documento final generado con 2 secciones aprobadas"
        doc = approval.get_final_document(ticket_id)
        assert doc["currency"] == "EUR"
        assert [s["department_id"] for s in doc["sections"]] == ["warehouse", "reverse"]
        assert [s["approver"] for s in doc["sections"]] == [ANA, SOFIA]
        content = doc["content"]
        assert content.startswith("# Propuesta económica TrackFlow — ModaViva")
        assert "| Moneda | EUR |" in content
        assert "## Propuesta económica — Warehouse Operations" in content
        assert "## Propuesta económica — Reverse Logistics" in content
        assert "BORRADOR PROVISIONAL" not in content
        assert state["final_document"] is True and state["status"] == "terminado"

    def test_request_changes_rewrites_and_interrupts_again(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        before = _sections(sqlite_db, ticket_id)["reverse"]
        _changes(ticket_id, "reverse", "Detallar el proceso de inspección de devoluciones.")

        section = _sections(sqlite_db, ticket_id)["reverse"]
        assert section["approval_status"] == "cambios_solicitados"
        assert section["approval_rounds"] == 1
        assert len(section["drafts"]) == len(before["drafts"]) + 1
        assert section["drafts"][-1]["source"] == "approval"
        assert section["drafts"][-1]["requested_by"] == SOFIA
        assert len(section["evaluation_results"]) == len(before["evaluation_results"]) + 1
        # Vuelve a esperar a Sofía con la ronda 1.
        assert approval.pending_interrupt(ticket_id, "reverse")["round"] == 1
        assert _nodes(ticket_id, "reverse") == ["prepare", "await_approval", "revise"]

        _approve(ticket_id, "reverse")
        assert _sections(sqlite_db, ticket_id)["reverse"]["approval_status"] == "aprobada"

    def test_resume_after_restart_with_a_new_checkpointer(self, sqlite_db, make_ticket, checkpoint_file):
        """El checkpoint se persistió antes del interrupt: otro proceso puede reanudar."""
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        before = department_approval_graph()

        ckpt.set_checkpointer(_saver(checkpoint_file))  # "reinicio": conexión nueva
        assert department_approval_graph() is not before
        assert approval.pending_interrupt(ticket_id, "warehouse")["approver"] == ANA
        _approve(ticket_id, "warehouse")
        _approve(ticket_id, "reverse")
        assert sqlite_db.get_ticket(ticket_id)["status"] == "terminado"

    def test_open_approval_is_idempotent(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        _approve(ticket_id, "warehouse")
        approval.open_approval(ticket_id)  # no reabre ni reinicia las ramas
        assert _nodes(ticket_id, "warehouse").count("prepare") == 1
        assert _sections(sqlite_db, ticket_id)["warehouse"]["approval_status"] == "aprobada"

    def test_reject_blocks_the_final_document(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        approval.submit_decision(ticket_id, "reverse", {
            "decision": "reject", "approver": SOFIA,
            "comments": "No damos servicio de reacondicionamiento en Zaragoza.",
        })
        _approve(ticket_id, "warehouse")

        ticket = sqlite_db.get_ticket(ticket_id)
        assert ticket["status"] == "esperando_aprobación"
        assert "rechazadas (sin documento final): reverse" in ticket["reason"]
        assert sqlite_db.get_final_document(ticket_id) is None
        assert _sections(sqlite_db, ticket_id)["reverse"]["approval_status"] == "rechazada"

    def test_needs_human_review_ticket_enters_part_3_and_loses_banner_on_approval(
        self, sqlite_db, make_ticket, monkeypatch,
    ):
        """Parte 2 → 3: la sección provisional llega a su responsable y al aprobarla
        se quita la cabecera y el aviso de Ventas."""
        ticket_id = make_ticket(2, max_iterations=1, drafter=WrongCurrencyDrafter())
        assert sqlite_db.get_ticket(ticket_id)["status"] == "needs_human_review"
        statuses = _track_statuses(sqlite_db, monkeypatch)
        approval.open_approval(ticket_id, drafter=WrongCurrencyDrafter())
        assert sqlite_db.get_ticket(ticket_id)["status"] == "esperando_aprobación"
        _approve(ticket_id, "warehouse")
        _approve(ticket_id, "lastmile")

        assert sqlite_db.get_ticket(ticket_id)["status"] == "terminado"
        # needs_human_review → esperando_aprobación → terminado, sin saltos.
        assert statuses == ["esperando_aprobación", "terminado"]
        warehouse = _sections(sqlite_db, ticket_id)["warehouse"]
        assert not warehouse["draft_content"].startswith("> **BORRADOR PROVISIONAL**")
        sales = sqlite_db.get_rfp_metadata(ticket_id)["sales_summary"]["proposal"]
        assert sales["status"] == "terminado" and sales["provisional_sections"] == []


# ===================================================================
# Validación de la respuesta humana (antes de entrar al grafo)
# ===================================================================


class TestDecisionValidation:
    @pytest.fixture
    def opened(self, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        return ticket_id

    @pytest.mark.parametrize("payload", [
        {"decision": "maybe", "approver": ANA},
        {"decision": "approve"},
        {"decision": "request_changes", "approver": ANA},
        {"decision": "reject", "approver": ANA, "comments": "no"},
        # Un cliente no puede hacerse pasar por el árbitro.
        {"decision": "approve", "approver": ANA, "source": "arbitration"},
    ])
    def test_malformed_payload_never_reaches_the_graph(self, opened, payload):
        with pytest.raises(approval.ApprovalValidationError):
            approval.submit_decision(opened, "warehouse", payload)
        assert _nodes(opened, "warehouse") == ["prepare"]
        assert approval.pending_interrupt(opened, "warehouse") is not None

    def test_only_the_named_owner_decides(self, opened):
        """Sin escalera jerárquica: ni Miguel Torres ni otro responsable aprueban warehouse."""
        for other in (MIGUEL, SOFIA):
            with pytest.raises(approval.ApprovalValidationError, match=ANA):
                approval.submit_decision(opened, "warehouse", {"decision": "approve", "approver": other})
        assert approval.pending_interrupt(opened, "warehouse") is not None

    def test_section_not_waiting_is_a_conflict(self, opened):
        _approve(opened, "warehouse")
        with pytest.raises(approval.ApprovalConflictError, match="no espera decisión"):
            _approve(opened, "warehouse")

    def test_inactive_or_unknown_department_is_not_found(self, opened):
        with pytest.raises(approval.ApprovalNotFoundError):
            approval.submit_decision(opened, "lastmile", {"decision": "approve", "approver": CARLOS})
        with pytest.raises(approval.ApprovalNotFoundError):
            approval.submit_decision(opened, "finance", {"decision": "approve", "approver": ANA})
        with pytest.raises(approval.ApprovalNotFoundError):
            approval.submit_decision("rfp_nope", "warehouse", {"decision": "approve", "approver": ANA})

    def test_cannot_approve_a_section_with_an_open_conflict(self, sqlite_db, opened):
        reverse = _sections(sqlite_db, opened)["reverse"]
        sqlite_db.update_department_section(reverse["id"], draft_content=reverse["draft_content"] + FAST_RETURNS)
        with pytest.raises(approval.ApprovalConflictError, match="returns-sla-breach"):
            _approve(opened, "reverse")

    def test_graph_rejects_a_malformed_resume_even_bypassing_the_service(self, opened):
        with pytest.raises(ValueError, match="inválida"):
            department_approval_graph().invoke(
                Command(resume="sí, apruebo"), department_config(opened, "warehouse"),
            )

    def test_part_3_requires_part_2(self, sqlite_db, checkpoint_file):
        sqlite_db.save_ticket({"ticket_id": "rfp_x", "rfp_id": "rfp_x-rfp",
                               "status": "analisis_completo", "raw_pdf_path": "/tmp/x.pdf"})
        with pytest.raises(approval.ApprovalConflictError):
            approval.open_approval("rfp_x")


# ===================================================================
# Límites de iteración
# ===================================================================


class TestIterationLimits:
    def test_change_rounds_limit_rejects_the_section(self, sqlite_db, make_ticket, monkeypatch):
        monkeypatch.setattr(approval, "MAX_APPROVAL_ROUNDS", 2)
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        for i in range(2):
            _changes(ticket_id, "reverse", f"Cambio número {i + 1} en devoluciones.")
        assert approval.pending_interrupt(ticket_id, "reverse")["round"] == 2

        _changes(ticket_id, "reverse", "Tercer cambio fuera del límite.")
        section = _sections(sqlite_db, ticket_id)["reverse"]
        assert section["approval_status"] == "rechazada"
        assert section["approval_rounds"] == 2  # no hubo tercera reescritura
        assert "Límite de 2 rondas" in section["approval_comments"]
        assert _nodes(ticket_id, "reverse")[-1] == "limit_reached"
        _approve(ticket_id, "warehouse")
        assert sqlite_db.get_ticket(ticket_id)["status"] == "esperando_aprobación"
        assert sqlite_db.get_final_document(ticket_id) is None

    def test_arbitration_rounds_limit_rejects_offenders(self, sqlite_db, make_ticket, monkeypatch):
        """returns-sla-breach que el generador nunca corrige: el árbitro rechaza tras el límite."""
        monkeypatch.setattr(approval, "MAX_ARBITRATION_ROUNDS", 2)
        drafter = FastReturnsDrafter(always=True)
        ticket_id = make_ticket(1, drafter=drafter, max_iterations=1)
        state = approval.open_approval(ticket_id, drafter=drafter)

        outcomes = [a["outcome"] for a in state["arbitrations"]]
        assert outcomes == ["request_changes", "request_changes", "reject"]
        assert state["arbitration_rounds"] == 2
        section = _sections(sqlite_db, ticket_id)["reverse"]
        assert section["approval_status"] == "rechazada"
        assert section["approval_rounds"] == 2
        assert section["approver"] == SOFIA  # Sofía rechaza su propia sección (§7)
        assert approval.pending_interrupt(ticket_id, "warehouse") is not None  # warehouse sigue
        ticket = sqlite_db.get_ticket(ticket_id)
        assert ticket["status"] == "esperando_aprobación"
        assert "rechazadas" in ticket["reason"]


# ===================================================================
# Arbitraje (BRIEFING §7)
# ===================================================================


class TestArbitration:
    def test_returns_sla_breach_forces_changes_by_sofia(self, sqlite_db, make_ticket):
        drafter = FastReturnsDrafter()
        ticket_id = make_ticket(1, drafter=drafter, max_iterations=1)
        assert "24 horas" in _sections(sqlite_db, ticket_id)["reverse"]["draft_content"]
        state = approval.open_approval(ticket_id, drafter=drafter)

        [record] = state["arbitrations"]
        [conflict] = record["conflicts"]
        assert conflict["trigger"] == "returns-sla-breach"
        assert conflict["arbiters"] == {"reverse": SOFIA}
        assert record["decisions"]["reverse"]["decision"] == "request_changes"
        assert record["applied"] == {"reverse": "resume"}
        section = _sections(sqlite_db, ticket_id)["reverse"]
        assert "24 horas" not in section["draft_content"]
        assert section["approval_history"][0]["source"] == "arbitration"
        assert state["conflicts"] == []  # resuelto: espera de nuevo a Sofía
        assert approval.pending_interrupt(ticket_id, "reverse")["round"] == 1
        arbiter_entries = [e for e in state["trace"] if e["node"] == "arbitrate"]
        assert arbiter_entries[0]["agent"] == f"arbiter:{SOFIA}"

        _approve(ticket_id, "reverse")
        _approve(ticket_id, "warehouse")
        content = approval.get_final_document(ticket_id)["content"]
        assert "## Arbitrajes aplicados (BRIEFING §7)" in content
        assert "returns-sla-breach" in content

    def test_returns_breach_in_another_section_is_arbitrated_by_miguel(self):
        conflicts = detect_conflicts({
            "warehouse": {"draft": "Devoluciones procesadas en 24 horas.", "approval_status": "pendiente"},
        }, "EUR")
        [conflict] = [c for c in conflicts if c["trigger"] == "returns-sla-breach"]
        assert conflict["arbiters"] == {"warehouse": MIGUEL}

    def test_currency_mismatch_rewrites_to_client_currency(self, sqlite_db, make_ticket):
        drafter = WrongCurrencyDrafter()
        ticket_id = make_ticket(2, drafter=drafter, max_iterations=1)
        assert "EUR" in section_currencies(_sections(sqlite_db, ticket_id)["warehouse"]["draft_content"])
        state = approval.open_approval(ticket_id, drafter=drafter)

        [conflict] = state["arbitrations"][0]["conflicts"]
        assert conflict["trigger"] == "currency-mismatch"
        assert conflict["arbiters"] == {"warehouse": MIGUEL}
        assert state["arbitrations"][0]["decisions"]["warehouse"]["constraints"] == {"currency": "USD"}
        assert section_currencies(_sections(sqlite_db, ticket_id)["warehouse"]["draft_content"]) == ["USD"]
        assert state["conflicts"] == []
        assert approval.pending_interrupt(ticket_id, "lastmile")["round"] == 0  # lastmile no se tocó

    def test_volume_vs_capacity_caps_lastmile_volume(self, sqlite_db, make_ticket):
        drafter = CapacityDrafter("3.000")
        ticket_id = make_ticket(2, drafter=drafter)
        state = approval.open_approval(ticket_id, drafter=drafter)

        [record] = state["arbitrations"]
        [conflict] = record["conflicts"]
        assert conflict["trigger"] == "volume-vs-capacity"
        assert conflict["arbiters"] == {"lastmile": MIGUEL}
        assert record["decisions"]["lastmile"]["constraints"] == {"volume_cap": 3000}
        lastmile = _sections(sqlite_db, ticket_id)["lastmile"]["draft_content"]
        assert "se limita a 3.000 pedidos/mes" in lastmile
        assert "5.000 pedidos/mes" in lastmile  # el dato de la RFP no se pierde
        assert state["departments"]["lastmile"]["facts"]["assumed_volume"] == 3000
        assert state["conflicts"] == []

    def test_reopens_an_already_approved_section(self, sqlite_db, make_ticket):
        """Si el conflicto aparece tras aprobar, el árbitro reabre la rama (entrada ``reopen``)."""
        ticket_id = make_ticket(2)
        approval.open_approval(ticket_id)
        _approve(ticket_id, "lastmile")
        drafter = CapacityDrafter("2.000")
        approval.submit_decision(ticket_id, "warehouse", {
            "decision": "request_changes", "approver": ANA,
            "comments": "Solo podemos comprometer 2.000 pedidos al mes.",
        }, drafter=drafter)

        state = approval.approval_state(ticket_id)
        assert state["arbitrations"][-1]["applied"] == {"lastmile": "reopen"}
        assert _nodes(ticket_id, "lastmile")[-3:] == ["approve", "reopen", "revise"]
        assert _sections(sqlite_db, ticket_id)["lastmile"]["approval_status"] == "cambios_solicitados"
        assert "se limita a 2.000" in _sections(sqlite_db, ticket_id)["lastmile"]["draft_content"]
        assert sqlite_db.get_ticket(ticket_id)["status"] == "esperando_aprobación"


# ===================================================================
# Ramas paralelas, thread_id por ticket y trace
# ===================================================================


class TestParallelBranches:
    def test_b_is_approved_while_a_is_still_interrupted(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        _approve(ticket_id, "reverse")  # B

        assert _dept_state(ticket_id, "reverse").next == ()
        assert _sections(sqlite_db, ticket_id)["reverse"]["approval_status"] == "aprobada"
        assert _dept_state(ticket_id, "warehouse").next == ("await_approval",)  # A sigue
        assert approval.pending_interrupt(ticket_id, "warehouse") is not None
        reason = sqlite_db.get_ticket(ticket_id)["reason"]
        assert "pendiente de aprobación: warehouse" in reason and "aprobadas: reverse" in reason

        _approve(ticket_id, "warehouse")  # A
        trace = approval.approval_state(ticket_id)["trace"]
        approvals = [(e["thread_id"].rsplit(":", 1)[-1], e["timestamp"]) for e in trace if e["node"] == "approve"]
        assert [dep for dep, _ in approvals] == ["reverse", "warehouse"]
        assert sqlite_db.get_ticket(ticket_id)["status"] == "terminado"

    def test_two_owners_revise_at_the_same_time(self, sqlite_db, make_ticket):
        """Las reescrituras de warehouse y reverse se solapan: no hay serie fingida."""
        ticket_id = make_ticket(1)
        drafter = SlowDrafter(delay=0.6)
        approval.open_approval(ticket_id, drafter=drafter)
        errors: list[Exception] = []

        def decide(dep):
            try:
                _changes(ticket_id, dep, "Ajustar el alcance con el cliente.", drafter=drafter)
            except Exception as exc:  # pragma: no cover - se reporta abajo
                errors.append(exc)

        threads = [threading.Thread(target=decide, args=(d,)) for d in ("warehouse", "reverse")]
        started = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - started

        assert not errors, errors
        (s1, e1), (s2, e2) = drafter.spans["warehouse"], drafter.spans["reverse"]
        assert max(s1, s2) < min(e1, e2)  # hay un instante en que reescriben las dos
        assert elapsed < 2 * 0.6 + 0.4
        for dep in ("warehouse", "reverse"):
            assert approval.pending_interrupt(ticket_id, dep)["round"] == 1

    def test_concurrent_tickets_do_not_share_checkpoints(self, sqlite_db, make_ticket):
        first, second = make_ticket(1), make_ticket(1)
        approval.open_approval(first)
        approval.open_approval(second)
        _approve(first, "warehouse")

        assert department_config(first, "warehouse")["configurable"]["thread_id"] == f"rfp-{first}:warehouse"
        assert ticket_config(first)["configurable"]["thread_id"] == f"rfp-{first}"
        assert _dept_state(first, "warehouse").next == ()
        assert _dept_state(second, "warehouse").next == ("await_approval",)
        assert _sections(sqlite_db, second)["warehouse"]["approval_status"] == "pendiente"
        coordinator = ticket_approval_graph().get_state(ticket_config(second)).values
        assert coordinator["ticket_id"] == second


    def test_concurrent_tickets_running_at_once_keep_separate_checkpoints(self, sqlite_db, make_ticket):
        """Dos tickets abren y deciden a la vez en hilos distintos: cada uno su checkpoint."""
        tickets = [make_ticket(1), make_ticket(1)]
        errors: list[Exception] = []

        def run(ticket_id, decision):
            try:
                approval.open_approval(ticket_id)
                approval.submit_decision(ticket_id, "warehouse", decision)
            except Exception as exc:  # pragma: no cover - se reporta abajo
                errors.append(exc)

        decisions = [
            {"decision": "approve", "approver": ANA},
            {"decision": "reject", "approver": ANA, "comments": "Sin capacidad en Zaragoza este trimestre."},
        ]
        threads = [threading.Thread(target=run, args=args) for args in zip(tickets, decisions)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        first, second = tickets
        assert _sections(sqlite_db, first)["warehouse"]["approval_status"] == "aprobada"
        assert _sections(sqlite_db, second)["warehouse"]["approval_status"] == "rechazada"
        saver = ckpt.get_checkpointer()
        threads_seen = {c.config["configurable"]["thread_id"] for c in saver.list(None)}
        for ticket_id in tickets:
            assert {f"rfp-{ticket_id}", f"rfp-{ticket_id}:warehouse", f"rfp-{ticket_id}:reverse"} <= threads_seen
            for dep in ("warehouse", "reverse"):
                values = _dept_state(ticket_id, dep).values
                assert values["ticket_id"] == ticket_id  # ningún estado cruzado
                assert all(e["thread_id"] == f"rfp-{ticket_id}:{dep}" for e in values["trace"])


class TestTrace:
    def test_every_node_execution_records_agent_input_output_timestamp(self, sqlite_db, make_ticket):
        ticket_id = make_ticket(1)
        approval.open_approval(ticket_id)
        _changes(ticket_id, "reverse", "Añadir la ventana de 45 días en campaña.")
        _approve(ticket_id, "reverse")
        _approve(ticket_id, "warehouse")

        trace = approval.approval_state(ticket_id)["trace"]
        assert trace == sorted(trace, key=lambda e: e["timestamp"])
        for entry in trace:
            assert {"node", "agent", "input", "output", "timestamp", "thread_id"} <= set(entry)
        agents = {e["agent"] for e in trace}
        assert {f"human:{ANA}", f"human:{SOFIA}", "approval-orchestrator", "conflict-detector",
                "document-consolidator", "reverse-template"} <= agents
        nodes = [e["node"] for e in trace]
        assert nodes.count("open_department") == 2
        assert nodes[-2:] == ["consolidate", "finalize"]
        decision = next(e for e in trace if e["node"] == "await_approval" and e["agent"] == f"human:{ANA}")
        assert decision["output"]["decision"]["decision"] == "approve"
        # El trace también vive en el estado (checkpoint) de cada grafo.
        assert _dept_state(ticket_id, "warehouse").values["trace"][0]["node"] == "prepare"

    def test_every_graph_node_is_traced(self):
        """Ningún nodo escapa al registro: todos los nodos de ambos grafos usan @traced."""
        for graph in (department_approval_graph(), ticket_approval_graph()):
            for name, node in graph.builder.nodes.items():
                fn = node.runnable.func
                assert hasattr(fn, "__wrapped__"), f"{name} no está decorado con @traced"


# ===================================================================
# E2E: Partes 1 → 2 → 3 con resumes humanos simulados
# ===================================================================


def _track_statuses(sqlite_db, monkeypatch) -> list[str]:
    """Estados del ticket en orden, escritos por cualquiera de las tres partes."""
    seen: list[str] = []

    def record(status):
        if status and (not seen or seen[-1] != status):
            seen.append(status)

    update, save, save_intake = sqlite_db.update_ticket, sqlite_db.save_ticket, sqlite_db.save_intake

    def spy_update(ticket_id, **fields):
        record(fields.get("status"))
        return update(ticket_id, **fields)

    def spy_save(ticket):
        record(ticket.get("status"))
        return save(ticket)

    def spy_save_intake(ticket, metadata, sections):
        record(ticket.get("status"))
        return save_intake(ticket, metadata, sections)

    monkeypatch.setattr(sqlite_db, "update_ticket", spy_update)
    monkeypatch.setattr(sqlite_db, "save_ticket", spy_save)
    monkeypatch.setattr(sqlite_db, "save_intake", spy_save_intake)
    return seen


class TestEndToEnd:
    def test_rfp_through_the_three_parts(self, sqlite_db, checkpoint_file, tmp_path, monkeypatch):
        """Luna Cosmetics (US): recepción → generación → aprobación y cierre."""
        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
        monkeypatch.setenv("RFP_GENERATOR", "template")
        statuses = _track_statuses(sqlite_db, monkeypatch)
        ticket_id = pipeline_mod.register_upload(SAMPLE_PDFS[2].read_bytes())["ticket_id"]
        assert sqlite_db.get_ticket(ticket_id)["status"] == "analizando"

        assert proposal.process_ticket(ticket_id)["status"] == "esperando_aprobación"
        metadata = sqlite_db.get_rfp_metadata(ticket_id)
        handoff = metadata["proposal_handoff"]
        assert handoff["status"] == "esperando_aprobación"
        assert metadata["departments_needed"] == ["warehouse", "lastmile"]
        section_ids = {s["department_id"]: s["section_id"] for s in handoff["sections"]}

        _changes(ticket_id, "lastmile", "Indicar que el costo por envío se cotiza por zona.")
        _approve(ticket_id, "lastmile")
        assert sqlite_db.get_ticket(ticket_id)["status"] == "esperando_aprobación"
        _approve(ticket_id, "warehouse")

        assert statuses == [
            "analizando", "analisis_completo", "generando_borrador", "en_evaluación",
            "esperando_aprobación", "terminado",
        ]
        ticket = sqlite_db.get_ticket(ticket_id)
        assert ticket["status"] == "terminado"
        doc = approval.get_final_document(ticket_id)
        assert doc["currency"] == metadata["currency"] == "USD"
        assert {s["department_id"]: s["section_id"] for s in doc["sections"]} == section_ids
        assert doc["rfp_id"] == ticket["rfp_id"]
        assert "Luna" in doc["content"] and "| Moneda | USD |" in doc["content"]
        assert "€" not in doc["content"]
        sections = _sections(sqlite_db, ticket_id)
        for dep, section in sections.items():
            assert section["approval_status"] == "aprobada"
            assert section["approver"] == OWNERS[dep]
            # El documento lleva exactamente el texto aprobado de cada sección.
            assert section["draft_content"].strip() in doc["content"]
        assert sections["lastmile"]["approval_rounds"] == 1
        state = approval.approval_state(ticket_id)
        assert all(d["approval_minutes"] is not None for d in state["departments"].values())
        assert sqlite_db.get_rfp_metadata(ticket_id)["sales_summary"]["proposal"]["status"] == "terminado"

    def test_discarded_rfp_never_reaches_part_3(self, sqlite_db, checkpoint_file, tmp_path, monkeypatch):
        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
        ticket_id = pipeline_mod.register_upload(SAMPLE_PDFS[3].read_bytes())["ticket_id"]
        assert proposal.process_ticket(ticket_id)["status"] == "descartado"
        assert ticket_approval_graph().get_state(ticket_config(ticket_id)).values == {}
        with pytest.raises(approval.ApprovalConflictError):
            approval.open_approval(ticket_id)


class TestApprovalRoutes:
    @pytest.fixture
    def client(self, sqlite_db, checkpoint_file, tmp_path, monkeypatch):
        api_dir = REPO_ROOT / "services" / "api"
        if str(api_dir) not in sys.path:
            sys.path.insert(0, str(api_dir))
        from auth import CurrentUser, get_current_user
        from fastapi.testclient import TestClient
        from main import app

        import data.pipelines.rfp_intake.pipeline as pipeline_mod

        monkeypatch.setattr(pipeline_mod, "raw_rfp_dir", lambda: tmp_path)
        monkeypatch.setenv("RFP_RECOVERY_INTERVAL_SECONDS", "0")
        monkeypatch.setenv("RFP_GENERATOR", "template")
        monkeypatch.setenv(
            "RFP_APPROVER_EMAILS",
            "warehouse=admin@example.com;lastmile=admin@example.com;reverse=admin@example.com",
        )
        app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id="test-admin", email="admin@example.com", role="admin",
        )
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def test_upload_to_final_document_over_http(self, client):
        files = {"file": ("trackflow-request-1.pdf", SAMPLE_PDFS[1].read_bytes(), "application/pdf")}
        ticket_id = client.post("/rfp/upload", files=files).json()["ticket_id"]

        detail = client.get(f"/rfp/tickets/{ticket_id}").json()
        assert detail["ticket"]["status"] == "esperando_aprobación"
        assert detail["auto_approval"] is True
        assert set(detail["approval"]["departments"]) == {"warehouse", "reverse"}
        assert detail["final_document"] is None
        assert client.get(f"/rfp/tickets/{ticket_id}/final-document").status_code == 404

        url = f"/rfp/tickets/{ticket_id}/sections/{{dep}}/decision"
        bad = client.post(url.format(dep="warehouse"), json={"decision": "ok", "approver": ANA})
        assert bad.status_code == 422
        wrong = client.post(url.format(dep="warehouse"), json={"decision": "approve", "approver": MIGUEL})
        assert wrong.status_code == 422 and ANA in str(wrong.json()["detail"])

        resp = client.post(url.format(dep="reverse"), json={"decision": "approve", "approver": SOFIA})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "esperando_aprobación"
        resp = client.post(url.format(dep="warehouse"), json={"decision": "approve", "approver": ANA})
        assert resp.json()["status"] == "terminado"
        again = client.post(url.format(dep="warehouse"), json={"decision": "approve", "approver": ANA})
        assert again.status_code == 409

        doc = client.get(f"/rfp/tickets/{ticket_id}/final-document").json()
        assert doc["currency"] == "EUR" and len(doc["sections"]) == 2
        markdown = client.get(f"/rfp/tickets/{ticket_id}/final-document?format=markdown")
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert markdown.text.startswith("# Propuesta económica TrackFlow — ModaViva")
        detail = client.get(f"/rfp/tickets/{ticket_id}").json()
        assert detail["ticket"]["status"] == "terminado"
        assert detail["final_document"]["ticket_id"] == ticket_id

    def _login_as(self, client, email):
        from auth import CurrentUser, get_current_user

        client.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
            id=email.split("@")[0], email=email, role="user",
        )

    def _ticket_waiting_for_approval(self, client):
        files = {"file": ("trackflow-request-1.pdf", SAMPLE_PDFS[1].read_bytes(), "application/pdf")}
        ticket_id = client.post("/rfp/upload", files=files).json()["ticket_id"]
        return ticket_id, f"/rfp/tickets/{ticket_id}/sections/{{dep}}/decision"

    def test_approver_name_in_body_does_not_impersonate_the_owner(self, client, sqlite_db, monkeypatch):
        """G1 (informe NIST): escribir "Sofía Ramos" no basta para decidir su sección."""
        monkeypatch.setenv(
            "RFP_APPROVER_EMAILS",
            "warehouse=ana@trackflow.example;reverse=Sofia@TrackFlow.example",
        )
        ticket_id, url = self._ticket_waiting_for_approval(client)

        for intruder in ("intruso@trackflow.example", "ana@trackflow.example"):
            self._login_as(client, intruder)
            resp = client.post(url.format(dep="reverse"), json={"decision": "approve", "approver": SOFIA})
            assert resp.status_code == 403, resp.text
            assert "Reverse Logistics" in resp.json()["detail"]

        reverse = _sections(sqlite_db, ticket_id)["reverse"]
        assert reverse["approval_status"] != "aprobada"
        assert not reverse.get("approval_history")

        self._login_as(client, "sofia@trackflow.example")  # el email no distingue mayúsculas
        resp = client.post(url.format(dep="reverse"), json={"decision": "approve", "approver": SOFIA})
        assert resp.status_code == 200, resp.text
        reverse = _sections(sqlite_db, ticket_id)["reverse"]
        assert reverse["approval_status"] == "aprobada"
        assert reverse["approval_history"][-1]["approved_by"] == "sofia@trackflow.example"

    def test_department_without_configured_approvers_is_closed(self, client, monkeypatch):
        monkeypatch.setenv("RFP_APPROVER_EMAILS", "reverse=sofia@trackflow.example")
        _, url = self._ticket_waiting_for_approval(client)

        self._login_as(client, "admin@example.com")
        resp = client.post(url.format(dep="warehouse"), json={"decision": "approve", "approver": ANA})
        assert resp.status_code == 403, resp.text

    def test_open_approval_route(self, client, sqlite_db, monkeypatch):
        monkeypatch.setenv("RFP_AUTO_APPROVAL", "0")
        files = {"file": ("r.pdf", SAMPLE_PDFS[2].read_bytes(), "application/pdf")}
        ticket_id = client.post("/rfp/upload", files=files).json()["ticket_id"]
        assert client.get(f"/rfp/tickets/{ticket_id}").json()["approval"] is None

        resp = client.post(f"/rfp/tickets/{ticket_id}/approval")
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["approval"]["departments"]) == {"warehouse", "lastmile"}
        assert client.post("/rfp/tickets/rfp_nope/approval").status_code == 404
