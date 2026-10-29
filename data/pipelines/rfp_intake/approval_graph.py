"""Grafos de aprobación humana por departamento (Hito 9, Parte 3).

Dos grafos LangGraph con checkpointer persistente
(``data/pipelines/checkpointer.py``: Postgres o SQLite):

**Rama por departamento** — un hilo por sección, ``rfp-{ticket_id}:{department}``::

    START ─┬─► prepare ─► await_approval ──┬─► approve        ─► END
           │                 ▲  (interrupt) ├─► reject         ─► END
           └─► reopen ───────┼──────────────┼─► limit_reached  ─► END
                             └──── revise ◄─┘ (request_changes, quedan rondas)

``await_approval`` llama a ``interrupt()``: el checkpointer guarda la rama
antes de pausar y solo esa rama queda esperando a su responsable (§2.1). Cada
departamento tiene su propio ``thread_id``, así que aprobar ``warehouse`` no
espera a ``reverse`` ni comparte checkpoint con otros tickets. El resume es la
entrada explícita ``Command(resume=decision)`` sobre el hilo de esa rama: el
grafo continúa en ``await_approval``, nunca vuelve a ``prepare``. ``reopen`` es
la segunda entrada explícita: reabre una rama ya cerrada cuando el arbitraje
fuerza cambios.

**Coordinador del ticket** — hilo ``rfp-{ticket_id}``::

    START ─┬─ Send ─► open_department (×N, en paralelo) ─┐
           └──────────────────────────────────────────────┴─► collect ─► detect_conflicts ─┬─► arbitrate ─► collect
                                                                                           ├─► arbitration_limit ─► collect
                                                                                           ├─► consolidate ─► finalize ─► END
                                                                                           └─► publish ─► END

``detect_conflicts`` compara hechos estructurados de las secciones
(``conflicts.py``); ``arbitrate`` aplica la regla fija del trigger con su
árbitro nombrado (BRIEFING §7) forzando ``request_changes`` en las ramas
ofensoras. Las rondas de arbitraje y las de cambios por sección tienen límite.

Cada ejecución de nodo añade al estado (``trace``) el agente, el input, el
output y el timestamp.
"""

from __future__ import annotations

import contextvars
import functools
import logging
import operator
import threading
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from ..checkpointer import get_checkpointer
from . import db
from .config import (
    APPROVAL_APROBADA,
    APPROVAL_CAMBIOS_SOLICITADOS,
    APPROVAL_DECISIONS,
    APPROVAL_PENDIENTE,
    APPROVAL_RECHAZADA,
    COMMERCIAL_DIRECTOR,
    CONFLICT_TRIGGERS,
    DECISION_APPROVE,
    DECISION_REJECT,
    DECISION_REQUEST_CHANGES,
    DEPARTMENT_IDS,
    DEPARTMENT_LABELS,
    DEPARTMENT_OWNERS,
    MAX_APPROVAL_ROUNDS,
    MAX_ARBITRATION_ROUNDS,
    STATUS_ESPERANDO_APROBACION,
    STATUS_TERMINADO,
    department_thread_id,
    ticket_thread_id,
)
from .conflicts import detect_conflicts, expected_currency, section_facts
from .evaluators import evaluate_section
from .generator import SectionBrief, build_section_briefs, format_volume, generate_section
from .proposal import provisional_banner

logger = logging.getLogger(__name__)

# Las ramas corren en hilos paralelos: las escrituras en BD se serializan.
_DB_LOCK = threading.RLock()
# Un lock por thread_id: dos resumes de la misma rama (o del coordinador)
# no compiten por el checkpoint; ramas distintas avanzan a la vez.
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def lock_for(thread_id: str) -> threading.Lock:
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(thread_id, threading.Lock())

PROVISIONAL_PREFIX = "> **BORRADOR PROVISIONAL**"
TRACE_TEXT_LIMIT = 280
TRACE_KEEP = 300
# Árbitros válidos de un resume con source=arbitration (BRIEFING §7).
ARBITERS = frozenset({COMMERCIAL_DIRECTOR, DEPARTMENT_OWNERS["reverse"]})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configurable(config: RunnableConfig | None) -> dict[str, Any]:
    return (config or {}).get("configurable", {}) or {}


def strip_provisional(draft: str | None) -> str:
    """Quita la cabecera "BORRADOR PROVISIONAL" de la Parte 2 (al aprobar)."""
    text = draft or ""
    if text.startswith(PROVISIONAL_PREFIX):
        _, _, rest = text.partition("\n\n")
        return rest
    return text


# ── Trazabilidad ────────────────────────────────────────────────────────────


def _compact(value: Any, depth: int = 0) -> Any:
    """Recorta textos largos para que el trace no duplique los borradores."""
    if isinstance(value, str):
        if len(value) <= TRACE_TEXT_LIMIT:
            return value
        return value[:TRACE_TEXT_LIMIT] + f"… ({len(value)} caracteres)"
    if isinstance(value, dict):
        if depth >= 3:
            return "{…}"
        return {k: _compact(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        items = [_compact(v, depth + 1) for v in list(value)[:10]]
        return items + (["…"] if len(value) > 10 else [])
    return value


def traced(node: str, agent: str | Callable[[dict, dict], str], inputs: tuple[str, ...]):
    """Registra en ``trace`` cada ejecución del nodo: agente, input, output, timestamp."""

    def decorator(fn: Callable[[dict, RunnableConfig], dict[str, Any]]):
        @functools.wraps(fn)
        def wrapper(state: dict, config: RunnableConfig) -> dict[str, Any]:
            output = dict(fn(state, config) or {})
            # ``_trace``: datos del resultado que no son claves del estado.
            extra = output.pop("_trace", {})
            entry = {
                "node": node,
                "agent": agent(state, output) if callable(agent) else agent,
                "thread_id": _configurable(config).get("thread_id"),
                "input": _compact({k: state.get(k) for k in inputs if k in state}),
                "output": _compact({**{k: v for k, v in output.items() if k != "trace"}, **extra}),
                "timestamp": _now_iso(),
            }
            return {**output, "trace": [entry]}

        return wrapper

    return decorator


def _actor(decision: dict[str, Any] | None) -> str:
    if not decision:
        return "human"
    role = "arbiter" if decision.get("source") == "arbitration" else "human"
    return f"{role}:{decision.get('approver')}"


def _isolated(fn: Callable[..., Any], *args: Any) -> Any:
    """Ejecuta otro grafo fuera del contexto del nodo actual.

    Sin esto LangGraph trataría la rama como subgrafo del coordinador y su
    ``interrupt`` pausaría también al coordinador (y a las demás ramas).
    """
    return contextvars.Context().run(fn, *args)


# ── Rama de aprobación por departamento ────────────────────────────────────


class DepartmentApprovalState(TypedDict, total=False):
    ticket_id: str
    department_id: str
    section_id: str
    owner: str
    brief: dict[str, Any]
    draft: str
    generator: str | None
    evaluation: dict[str, Any] | None
    revisions: int
    max_revisions: int
    approval_status: str
    ready_at: str
    decision: dict[str, Any] | None
    decisions: list[dict[str, Any]]
    reopen: dict[str, Any] | None
    trace: Annotated[list[dict[str, Any]], operator.add]


def _section_row(ticket_id: str, department_id: str) -> dict[str, Any]:
    with _DB_LOCK:
        rows = db.get_department_sections(ticket_id)
    for row in rows:
        if row["department_id"] == department_id:
            return row
    raise KeyError(f"Sección {department_id} no encontrada en {ticket_id}")


def assert_valid_resume(response: Any, owner: str) -> dict[str, Any]:
    """Comprobación estructural del valor de resume dentro del grafo.

    La validación completa (formato, responsable, conflictos abiertos) la
    hace ``approval.validate_decision`` antes de reanudar; esto impide que un
    resume malformado avance aunque llegue por otra vía.
    """
    if not isinstance(response, dict) or response.get("decision") not in APPROVAL_DECISIONS:
        raise ValueError(f"Respuesta de aprobación inválida: {response!r}")
    approver = response.get("approver")
    if response.get("source") == "arbitration":
        if not any(name in str(approver) for name in ARBITERS):
            raise ValueError(f"Árbitro no reconocido: {approver!r}")
    elif approver != owner:
        raise ValueError(f"Solo {owner} decide esta sección, no {approver!r}")
    return response


def route_entry(state: DepartmentApprovalState) -> str:
    """Entrada explícita: abrir la rama (``prepare``) o reabrirla (``reopen``)."""
    return "reopen" if state.get("reopen") else "prepare"


@traced("prepare", "approval-orchestrator", ("ticket_id", "department_id", "max_revisions"))
def prepare(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Carga la sección de la Parte 2 y la deja pendiente de su responsable."""
    ticket_id, dep = state["ticket_id"], state["department_id"]
    with _DB_LOCK:
        metadata = db.get_rfp_metadata(ticket_id) or {}
        rows = db.get_department_sections(ticket_id)
    row = next(r for r in rows if r["department_id"] == dep)
    brief = next(
        b for b in build_section_briefs(metadata.get("handoff") or {}, metadata, rows)
        if b.department_id == dep
    )
    owner = row.get("department_owner") or DEPARTMENT_OWNERS[dep]
    evaluations = row.get("evaluation_results") or []
    with _DB_LOCK:
        db.update_department_section(
            row["id"], approval_status=APPROVAL_PENDIENTE, approver=owner, approved_at=None,
            approval_comments=None, approval_rounds=0, approval_history=[],
        )
    return {
        "section_id": row["id"],
        "owner": owner,
        "brief": brief.to_dict(),
        "draft": row.get("draft_content") or "",
        "generator": None,
        "evaluation": evaluations[-1] if evaluations else None,
        "revisions": 0,
        "max_revisions": state.get("max_revisions") or MAX_APPROVAL_ROUNDS,
        "approval_status": APPROVAL_PENDIENTE,
        "ready_at": _now_iso(),
        "decision": None,
        "decisions": [],
    }


@traced("await_approval", lambda s, out: _actor(out.get("decision")),
        ("section_id", "owner", "revisions", "approval_status"))
def await_approval(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Punto de interrupción: pausa SOLO esta rama hasta la decisión del responsable."""
    evaluation = state.get("evaluation") or {}
    response = interrupt({
        "ticket_id": state["ticket_id"],
        "department_id": state["department_id"],
        "section_id": state["section_id"],
        "approver": state["owner"],
        "round": state.get("revisions", 0),
        "max_rounds": state.get("max_revisions") or MAX_APPROVAL_ROUNDS,
        "allowed_decisions": list(APPROVAL_DECISIONS),
        "evaluation_passed": evaluation.get("overall_pass"),
        "provisional": (state.get("draft") or "").startswith(PROVISIONAL_PREFIX),
        "ready_at": state.get("ready_at"),
    })
    decision = assert_valid_resume(response, state["owner"])
    return {"decision": decision, "decisions": [*state.get("decisions", []), decision]}


@traced("reopen", lambda s, out: _actor(s.get("reopen")), ("section_id", "approval_status", "reopen"))
def reopen(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Reabre una rama cerrada con la decisión forzada por el arbitraje."""
    decision = assert_valid_resume(state["reopen"], state["owner"])
    return {"decision": decision, "decisions": [*state.get("decisions", []), decision], "reopen": None}


def route_decision(state: DepartmentApprovalState) -> str:
    choice = state["decision"]["decision"]
    if choice == DECISION_APPROVE:
        return "approve"
    if choice == DECISION_REJECT:
        return "reject"
    limit = state.get("max_revisions") or MAX_APPROVAL_ROUNDS
    return "revise" if state.get("revisions", 0) < limit else "limit_reached"


@traced("approve", lambda s, out: _actor(s.get("decision")), ("section_id", "decision"))
def approve(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Registra la aprobación; la sección aprobada ya no lleva cabecera provisional."""
    decision = state["decision"]
    clean = strip_provisional(state.get("draft"))
    with _DB_LOCK:
        db.update_department_section(
            state["section_id"], approval_status=APPROVAL_APROBADA, approver=decision["approver"],
            approved_at=decision["decided_at"], approval_comments=decision.get("comments"),
            draft_content=clean, approval_history=state["decisions"],
        )
    return {"approval_status": APPROVAL_APROBADA, "draft": clean}


def _close_rejected(state: DepartmentApprovalState, comments: str) -> dict[str, Any]:
    decision = state["decision"]
    with _DB_LOCK:
        db.update_department_section(
            state["section_id"], approval_status=APPROVAL_RECHAZADA, approver=decision["approver"],
            approved_at=None, approval_comments=comments, approval_history=state["decisions"],
        )
    return {"approval_status": APPROVAL_RECHAZADA}


@traced("reject", lambda s, out: _actor(s.get("decision")), ("section_id", "decision"))
def reject(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    return _close_rejected(state, state["decision"].get("comments") or "")


@traced("limit_reached", "approval-orchestrator", ("section_id", "revisions", "max_revisions", "decision"))
def limit_reached(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Se agotaron las rondas de cambios: la sección queda rechazada."""
    limit = state.get("max_revisions") or MAX_APPROVAL_ROUNDS
    comments = (
        f"Límite de {limit} rondas de cambios agotado. Última petición "
        f"({state['decision']['approver']}): {state['decision'].get('comments') or ''}"
    )
    return _close_rejected(state, comments)


def _apply_constraints(brief: SectionBrief, decision: dict[str, Any]) -> SectionBrief:
    constraints = decision.get("constraints") or {}
    if constraints.get("currency"):
        brief.currency = constraints["currency"]
    if constraints.get("volume_cap") is not None:
        brief.volume_cap = int(constraints["volume_cap"])
    brief.approval_notes = [*brief.approval_notes, f"{decision['approver']}: {decision.get('comments') or ''}"]
    return brief


@traced("revise", lambda s, out: out.get("generator") or "generator",
        ("section_id", "revisions", "decision"))
def revise(state: DepartmentApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """request_changes: el agente generador del departamento reescribe y se reevalúa."""
    decision = state["decision"]
    brief = _apply_constraints(SectionBrief(**state["brief"]), decision)
    previous = strip_provisional(state.get("draft"))
    evaluation = state.get("evaluation") or {}
    feedback = f"Cambios pedidos en la aprobación por {decision['approver']}: {decision.get('comments') or ''}"
    if brief.volume_cap is not None:
        feedback += f"\nLimita el volumen de la propuesta a {format_volume(brief.volume_cap)} pedidos/mes."
    if evaluation.get("feedback_for_generator"):
        feedback += "\n" + evaluation["feedback_for_generator"]
    result = generate_section(
        brief, drafter=_configurable(config).get("drafter"),
        previous_draft=previous, evaluation={**evaluation, "feedback_for_generator": feedback},
    )
    row = _section_row(state["ticket_id"], state["department_id"])
    history = list(row.get("evaluation_results") or [])
    iteration = len(history) + 1
    new_eval = evaluate_section(result["content"], brief, iteration=iteration).to_dict()
    draft = result["content"]
    if not new_eval["overall_pass"]:
        draft = provisional_banner({"evaluation": new_eval, "iterations": iteration, "owner": state["owner"]}) + draft
    revisions = state.get("revisions", 0) + 1
    entry = {
        "iteration": iteration, "content": result["content"], "generator": result["generator"],
        "generated_at": _now_iso(), "source": "approval", "requested_by": decision["approver"],
    }
    with _DB_LOCK:
        db.update_department_section(
            state["section_id"], draft_content=draft,
            drafts=[*(row.get("drafts") or []), entry], evaluation_results=[*history, new_eval],
            approval_status=APPROVAL_CAMBIOS_SOLICITADOS, approver=decision["approver"],
            approval_comments=decision.get("comments"), approval_rounds=revisions,
            approval_history=state["decisions"],
        )
    return {
        "brief": brief.to_dict(),
        "draft": draft,
        "generator": result["generator"],
        "evaluation": new_eval,
        "revisions": revisions,
        "approval_status": APPROVAL_CAMBIOS_SOLICITADOS,
        "ready_at": _now_iso(),
    }


_DECISION_TARGETS = ["approve", "reject", "revise", "limit_reached"]

_department = StateGraph(DepartmentApprovalState)
_department.add_node("prepare", prepare)
_department.add_node("await_approval", await_approval)
_department.add_node("reopen", reopen)
_department.add_node("approve", approve)
_department.add_node("reject", reject)
_department.add_node("limit_reached", limit_reached)
_department.add_node("revise", revise)
_department.add_conditional_edges(START, route_entry, ["prepare", "reopen"])
_department.add_edge("prepare", "await_approval")
_department.add_conditional_edges("await_approval", route_decision, _DECISION_TARGETS)
_department.add_conditional_edges("reopen", route_decision, _DECISION_TARGETS)
_department.add_edge("revise", "await_approval")
for _node in ("approve", "reject", "limit_reached"):
    _department.add_edge(_node, END)


# ── Coordinador del ticket ──────────────────────────────────────────────────


class TicketApprovalState(TypedDict, total=False):
    ticket_id: str
    entry: str
    department_id: str  # solo en el Send de open_department
    departments: list[str]
    max_revisions: int
    max_arbitration_rounds: int
    sections: dict[str, dict[str, Any]]
    conflicts: list[dict[str, Any]]
    arbitration_rounds: int
    arbitration_limit_applied: bool
    arbitrations: list[dict[str, Any]]
    final_document: dict[str, Any] | None
    status: str
    trace: Annotated[list[dict[str, Any]], operator.add]


def department_config(ticket_id: str, department_id: str, config: RunnableConfig | None = None) -> RunnableConfig:
    """Config del hilo de una rama (hereda el ``drafter`` del coordinador)."""
    return {
        "configurable": {
            "thread_id": department_thread_id(ticket_id, department_id),
            "drafter": _configurable(config).get("drafter"),
        },
        "recursion_limit": 50,
    }


def ticket_config(ticket_id: str, drafter: Any = None) -> RunnableConfig:
    return {
        "configurable": {"thread_id": ticket_thread_id(ticket_id), "drafter": drafter},
        "recursion_limit": 100,
    }


def route_ticket_entry(state: TicketApprovalState) -> list[Send] | str:
    """Entrada ``open``: una rama por departamento, en paralelo. ``sync``: recoger."""
    if state.get("entry") == "open":
        return [
            Send("open_department", {
                "ticket_id": state["ticket_id"],
                "department_id": dep,
                "max_revisions": state.get("max_revisions"),
            })
            for dep in state.get("departments", [])
        ] or "collect"
    return "collect"


@traced("open_department", "approval-orchestrator", ("ticket_id", "department_id"))
def open_department(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Arranca la rama de un departamento hasta su interrupción (idempotente)."""
    dep = state["department_id"]
    cfg = department_config(state["ticket_id"], dep, config)
    graph = department_approval_graph()
    if not graph.get_state(cfg).values:
        _isolated(graph.invoke, {
            "ticket_id": state["ticket_id"],
            "department_id": dep,
            "max_revisions": state.get("max_revisions"),
        }, cfg)
    snapshot = graph.get_state(cfg)
    return {"_trace": {"department_id": dep, "awaiting": bool(snapshot.interrupts),
                       "department_thread_id": cfg["configurable"]["thread_id"]}}


def _minutes_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None

    def parse(value: str) -> datetime:
        moment = datetime.fromisoformat(value)
        # SQLite devuelve TIMESTAMPTZ sin zona: se guardó en UTC.
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    return round((parse(end) - parse(start)).total_seconds() / 60, 2)


@traced("collect", "approval-orchestrator", ("ticket_id", "departments", "arbitration_rounds"))
def collect(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Estado de cada rama: decisión, rondas, si espera al responsable y hechos §7."""
    ticket_id = state["ticket_id"]
    with _DB_LOCK:
        rows = {r["department_id"]: r for r in db.get_department_sections(ticket_id)}
    graph = department_approval_graph()
    sections: dict[str, dict[str, Any]] = {}
    for dep in state.get("departments", []):
        row = rows.get(dep, {})
        snapshot = graph.get_state(department_config(ticket_id, dep, config))
        values = snapshot.values or {}
        approved_at = row.get("approved_at")
        sections[dep] = {
            "department_id": dep,
            "section_id": row.get("id"),
            "owner": row.get("department_owner") or DEPARTMENT_OWNERS[dep],
            "approval_status": row.get("approval_status", APPROVAL_PENDIENTE),
            "approver": row.get("approver"),
            "approved_at": approved_at,
            "comments": row.get("approval_comments"),
            "revisions": values.get("revisions", 0),
            "max_revisions": values.get("max_revisions") or MAX_APPROVAL_ROUNDS,
            "awaiting": bool(snapshot.interrupts),
            "ready_at": values.get("ready_at"),
            "approval_minutes": _minutes_between(values.get("ready_at"), approved_at),
            "draft": row.get("draft_content") or "",
            "facts": section_facts(dep, row.get("draft_content") or ""),
            "decisions": values.get("decisions", []),
            "thread_id": department_thread_id(ticket_id, dep),
        }
    return {"sections": sections}


@traced("detect_conflicts", "conflict-detector", ("sections",))
def detect(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Triggers §7 sobre los hechos estructurados de las secciones."""
    with _DB_LOCK:
        metadata = db.get_rfp_metadata(state["ticket_id"]) or {}
    currency = expected_currency(metadata.get("client_country"), metadata.get("currency"))
    conflicts = detect_conflicts(
        {dep: {"draft": s["draft"], "approval_status": s["approval_status"]}
         for dep, s in state.get("sections", {}).items()},
        currency,
    )
    return {"conflicts": conflicts}


def route_after_detect(state: TicketApprovalState) -> str:
    sections = state.get("sections", {})
    if state.get("conflicts"):
        limit = state.get("max_arbitration_rounds") or MAX_ARBITRATION_ROUNDS
        if state.get("arbitration_rounds", 0) < limit:
            return "arbitrate"
        return "publish" if state.get("arbitration_limit_applied") else "arbitration_limit"
    if sections and all(s["approval_status"] == APPROVAL_APROBADA for s in sections.values()):
        return "consolidate"
    return "publish"


def _force(ticket_id: str, department_id: str, decision: dict[str, Any], config: RunnableConfig) -> str:
    """Aplica una decisión del árbitro a una rama: resume si espera, reopen si estaba cerrada."""
    graph = department_approval_graph()
    cfg = department_config(ticket_id, department_id, config)
    with lock_for(cfg["configurable"]["thread_id"]):
        snapshot = graph.get_state(cfg)
        if snapshot.interrupts:
            _isolated(graph.invoke, Command(resume=decision), cfg)
            return "resume"
        if (snapshot.values or {}).get("approval_status") == APPROVAL_RECHAZADA:
            return "skipped"
        _isolated(graph.invoke, {"reopen": decision}, cfg)
        return "reopen"


def _forced_decisions(conflicts: list[dict[str, Any]], choice: str, comment: Callable[[dict], str]) -> dict[str, dict[str, Any]]:
    """Una decisión por sección ofensora, fundiendo todos sus conflictos."""
    forced: dict[str, dict[str, Any]] = {}
    for conflict in conflicts:
        for dep in conflict["offenders"]:
            decision = forced.setdefault(dep, {
                "decision": choice, "approver": "", "comments": "", "source": "arbitration",
                "triggers": [], "constraints": {}, "decided_at": _now_iso(),
            })
            arbiter = conflict["arbiters"][dep]
            if arbiter not in decision["approver"].split(" / "):
                decision["approver"] = " / ".join(filter(None, [decision["approver"], arbiter]))
            decision["triggers"].append(conflict["trigger"])
            decision["comments"] = " ".join(filter(None, [decision["comments"], comment(conflict)]))
            decision["constraints"].update(conflict["constraints"].get(dep, {}))
    return forced


def _arbiters(state: dict, output: dict) -> str:
    names = {a for c in state.get("conflicts", []) for a in c["arbiters"].values()}
    return "arbiter:" + " / ".join(sorted(names))


@traced("arbitrate", _arbiters, ("conflicts", "arbitration_rounds"))
def arbitrate(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Nodo de arbitraje: regla fija del trigger + árbitro nombrado (BRIEFING §7)."""
    round_ = state.get("arbitration_rounds", 0) + 1
    forced = _forced_decisions(
        state["conflicts"], DECISION_REQUEST_CHANGES,
        lambda c: f"[{c['trigger']}] {c['rule']} Detalle: {c['detail']}",
    )
    applied = {dep: _force(state["ticket_id"], dep, d, config) for dep, d in forced.items()}
    record = {
        "round": round_,
        "outcome": DECISION_REQUEST_CHANGES,
        "conflicts": [{k: c[k] for k in ("trigger", "arbiters", "offenders", "detail", "rule")}
                      for c in state["conflicts"]],
        "decisions": forced,
        "applied": applied,
        "decided_at": _now_iso(),
    }
    return {"arbitration_rounds": round_, "arbitrations": [*state.get("arbitrations", []), record]}


@traced("arbitration_limit", _arbiters, ("conflicts", "arbitration_rounds"))
def arbitration_limit(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Conflicto sin resolver tras el límite: el árbitro rechaza las secciones ofensoras."""
    limit = state.get("max_arbitration_rounds") or MAX_ARBITRATION_ROUNDS
    forced = _forced_decisions(
        state["conflicts"], DECISION_REJECT,
        lambda c: f"[{c['trigger']}] Sin resolver tras {limit} rondas de arbitraje. {c['rule']}",
    )
    applied = {dep: _force(state["ticket_id"], dep, d, config) for dep, d in forced.items()}
    record = {
        "round": state.get("arbitration_rounds", 0),
        "outcome": DECISION_REJECT,
        "conflicts": [{k: c[k] for k in ("trigger", "arbiters", "offenders", "detail", "rule")}
                      for c in state["conflicts"]],
        "decisions": forced,
        "applied": applied,
        "decided_at": _now_iso(),
    }
    return {"arbitration_limit_applied": True, "arbitrations": [*state.get("arbitrations", []), record]}


def render_final_document(
    ticket: dict[str, Any],
    metadata: dict[str, Any],
    sections: list[dict[str, Any]],
    currency: str | None,
    arbitrations: list[dict[str, Any]],
    generated_at: str,
) -> str:
    """Markdown del documento final: cabecera del cliente + aprobaciones + secciones."""
    client = metadata.get("client_name") or "cliente"
    volume = metadata.get("monthly_volume")
    services = ", ".join(DEPARTMENT_LABELS[s["department_id"]] for s in sections)
    lines = [
        f"# Propuesta económica TrackFlow — {client}",
        "",
        "| Campo | Valor |",
        "|---|---|",
        f"| Ticket | {ticket['ticket_id']} |",
        f"| RFP | {ticket.get('rfp_id') or '—'} |",
        f"| Cliente | {client} ({metadata.get('client_country') or 'país sin determinar'}) |",
        f"| Moneda | {currency or '—'} |",
        f"| Servicios | {services} |",
        f"| Volumen mensual (RFP) | {format_volume(volume) + ' pedidos/mes' if volume else '—'} |",
        f"| Fecha límite | {metadata.get('deadline') or '—'} |",
        f"| Generado | {generated_at} |",
        "",
        "## Aprobaciones por departamento",
        "",
        "| Departamento | Responsable | Aprobado por | Fecha |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {DEPARTMENT_LABELS[s['department_id']]} | {s['owner']} | {s['approver']} | {s['approved_at']} |"
        for s in sections
    ]
    for section in sections:
        lines += ["", section["content"].strip()]
    if arbitrations:
        lines += ["", "## Arbitrajes aplicados (BRIEFING §7)", "",
                  "| Ronda | Trigger | Árbitro | Secciones | Resolución |", "|---|---|---|---|---|"]
        for record in arbitrations:
            for c in record["conflicts"]:
                arbiters = " / ".join(sorted(set(c["arbiters"].values())))
                lines.append(
                    f"| {record['round']} | {c['trigger']} | {arbiters} | "
                    f"{', '.join(c['offenders'])} | {record['outcome']} |"
                )
    lines += [
        "",
        "---",
        f"Documento generado con las secciones aprobadas por cada responsable. "
        f"Precios en {currency or 'la moneda del país del cliente'} según el país del cliente.",
    ]
    return "\n".join(lines) + "\n"


@traced("consolidate", "document-consolidator", ("sections", "arbitrations"))
def consolidate(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Todas las secciones aprobadas y sin conflictos: arma el documento final."""
    ticket_id = state["ticket_id"]
    with _DB_LOCK:
        ticket = db.get_ticket(ticket_id) or {"ticket_id": ticket_id}
        metadata = db.get_rfp_metadata(ticket_id) or {}
    currency = expected_currency(metadata.get("client_country"), metadata.get("currency"))
    by_dep = state["sections"]
    sections = [
        {
            "department_id": dep,
            "section_id": by_dep[dep]["section_id"],
            "owner": by_dep[dep]["owner"],
            "approver": by_dep[dep]["approver"],
            "approved_at": by_dep[dep]["approved_at"],
            "content": by_dep[dep]["draft"],
        }
        for dep in DEPARTMENT_IDS
        if dep in by_dep
    ]
    generated_at = _now_iso()
    content = render_final_document(ticket, metadata, sections, currency,
                                    state.get("arbitrations", []), generated_at)
    return {"final_document": {
        "ticket_id": ticket_id,
        "rfp_id": ticket.get("rfp_id"),
        "currency": currency,
        "sections": sections,
        "content": content,
        "generated_at": generated_at,
    }}


def merged_trace(ticket_id: str) -> list[dict[str, Any]]:
    """Trace del coordinador + el de cada rama (desde sus checkpoints), en orden cronológico."""
    coordinator = ticket_approval_graph().get_state(ticket_config(ticket_id)).values or {}
    entries = list(coordinator.get("trace", []))
    for dep in coordinator.get("departments", []):
        values = department_approval_graph().get_state(department_config(ticket_id, dep)).values or {}
        entries += values.get("trace", [])
    entries.sort(key=lambda e: e["timestamp"])
    return entries[-TRACE_KEEP:]


def refresh_trace(ticket_id: str) -> None:
    """Persiste en ``rfp_metadata.approval.trace`` el trace completo tras cada ejecución."""
    with _DB_LOCK:
        approval = (db.get_rfp_metadata(ticket_id) or {}).get("approval")
        if approval is not None:
            db.update_rfp_metadata(ticket_id, approval={**approval, "trace": merged_trace(ticket_id)})


def _reason(sections: dict[str, dict[str, Any]], conflicts: list[dict[str, Any]], status: str) -> str:
    if status == STATUS_TERMINADO:
        return f"documento final generado con {len(sections)} secciones aprobadas"
    groups: dict[str, list[str]] = {}
    for dep, s in sections.items():
        who = s["owner"] if s["approval_status"] in (APPROVAL_PENDIENTE, APPROVAL_CAMBIOS_SOLICITADOS) else s["approver"]
        revised = f", borrador revisado {s['revisions']}/{s['max_revisions']}" if s["revisions"] else ""
        groups.setdefault(s["approval_status"], []).append(f"{dep} ({who}{revised})")
    parts = []
    pending = groups.get(APPROVAL_PENDIENTE, []) + groups.get(APPROVAL_CAMBIOS_SOLICITADOS, [])
    if pending:
        parts.append("pendiente de aprobación: " + ", ".join(pending))
    if groups.get(APPROVAL_APROBADA):
        parts.append("aprobadas: " + ", ".join(groups[APPROVAL_APROBADA]))
    if groups.get(APPROVAL_RECHAZADA):
        parts.append("rechazadas (sin documento final): " + ", ".join(groups[APPROVAL_RECHAZADA]))
    if conflicts:
        parts.append("conflictos abiertos: " + ", ".join(sorted({c["trigger"] for c in conflicts})))
    return "; ".join(parts) or "esperando decisiones"


def _publish(state: TicketApprovalState, config: RunnableConfig, status: str) -> dict[str, Any]:
    """Persiste estado del ticket, resumen de aprobación y avisos de Ventas."""
    ticket_id = state["ticket_id"]
    sections = state.get("sections", {})
    conflicts = state.get("conflicts", []) if status != STATUS_TERMINADO else []
    summary = {
        "status": status,
        "thread_id": ticket_thread_id(ticket_id),
        "departments": {dep: {k: v for k, v in s.items() if k != "draft"} for dep, s in sections.items()},
        "conflicts": conflicts,
        "arbitrations": state.get("arbitrations", []),
        "arbitration_rounds": state.get("arbitration_rounds", 0),
        "max_arbitration_rounds": state.get("max_arbitration_rounds") or MAX_ARBITRATION_ROUNDS,
        "final_document": status == STATUS_TERMINADO,
        "triggers": CONFLICT_TRIGGERS,
        "updated_at": _now_iso(),
        # Se completa con refresh_trace() al acabar la ejecución del coordinador.
        "trace": merged_trace(ticket_id),
    }
    reason = _reason(sections, conflicts, status)
    with _DB_LOCK:
        metadata = db.get_rfp_metadata(ticket_id) or {}
        fields: dict[str, Any] = {"approval": summary}
        sales = metadata.get("sales_summary")
        if sales and sales.get("proposal"):
            # Una sección provisional deja de serlo cuando su responsable la aprueba.
            approved = {d for d, s in sections.items() if s["approval_status"] == APPROVAL_APROBADA}
            proposal = {
                **sales["proposal"],
                "status": status,
                "provisional_sections": [
                    p for p in sales["proposal"].get("provisional_sections", [])
                    if p["department_id"] not in approved
                ],
            }
            fields["sales_summary"] = {**sales, "proposal": proposal}
        db.update_rfp_metadata(ticket_id, **fields)
        db.update_ticket(ticket_id, status=status, reason=reason)
    return {"status": status}


@traced("finalize", "document-consolidator", ("final_document",))
def finalize(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Guarda el documento final y cierra el ticket en ``terminado``."""
    with _DB_LOCK:
        db.save_final_document(state["final_document"])
    return _publish(state, config, STATUS_TERMINADO)


@traced("publish", "approval-orchestrator", ("sections", "conflicts"))
def publish(state: TicketApprovalState, config: RunnableConfig) -> dict[str, Any]:
    """Alguna aprobación sigue pendiente (o bloqueada): ``esperando_aprobación``."""
    return _publish(state, config, STATUS_ESPERANDO_APROBACION)


_ticket = StateGraph(TicketApprovalState)
_ticket.add_node("open_department", open_department)
_ticket.add_node("collect", collect)
_ticket.add_node("detect_conflicts", detect)
_ticket.add_node("arbitrate", arbitrate)
_ticket.add_node("arbitration_limit", arbitration_limit)
_ticket.add_node("consolidate", consolidate)
_ticket.add_node("finalize", finalize)
_ticket.add_node("publish", publish)
_ticket.add_conditional_edges(START, route_ticket_entry, ["open_department", "collect"])
_ticket.add_edge("open_department", "collect")
_ticket.add_edge("collect", "detect_conflicts")
_ticket.add_conditional_edges(
    "detect_conflicts", route_after_detect,
    ["arbitrate", "arbitration_limit", "consolidate", "publish"],
)
_ticket.add_edge("arbitrate", "collect")
_ticket.add_edge("arbitration_limit", "collect")
_ticket.add_edge("consolidate", "finalize")
_ticket.add_edge("finalize", END)
_ticket.add_edge("publish", END)


# ── Compilación con el checkpointer compartido ──────────────────────────────

_COMPILED: dict[str, tuple[Any, Any]] = {}
_COMPILE_LOCK = threading.Lock()


def _compiled(name: str, builder: StateGraph) -> Any:
    """Compila una vez por checkpointer (``set_checkpointer`` fuerza recompilar)."""
    saver = get_checkpointer()
    with _COMPILE_LOCK:
        cached = _COMPILED.get(name)
        if cached is None or cached[0] is not saver:
            _COMPILED[name] = (saver, builder.compile(checkpointer=saver))
        return _COMPILED[name][1]


def department_approval_graph() -> Any:
    return _compiled("department", _department)


def ticket_approval_graph() -> Any:
    return _compiled("ticket", _ticket)


__all__ = [
    "PROVISIONAL_PREFIX",
    "DepartmentApprovalState",
    "TicketApprovalState",
    "assert_valid_resume",
    "department_approval_graph",
    "department_config",
    "lock_for",
    "merged_trace",
    "refresh_trace",
    "render_final_document",
    "strip_provisional",
    "ticket_approval_graph",
    "ticket_config",
]
