"""Parte 3 del intake de RFPs: aprobación humana por departamento y cierre.

Puntos de entrada (los grafos viven en ``approval_graph.py``):

- ``open_approval(ticket_id)``: tras la Parte 2 (ticket en
  ``esperando_aprobación`` o ``needs_human_review``) abre una rama por
  departamento activo; cada una se interrumpe esperando a su responsable
  (BRIEFING §2.1). Idempotente: si ya está abierta solo resincroniza.
- ``submit_decision(ticket_id, department_id, payload)``: valida la respuesta
  humana (``validate_decision``) y SOLO si es válida reanuda la rama con
  ``Command(resume=...)``; después el coordinador recoge el estado, arbitra
  conflictos §7 y, si todo está aprobado, genera el documento final.

Estados del ticket (BRIEFING §2.3): ``esperando_aprobación`` mientras falte
alguna aprobación (o haya secciones rechazadas); ``terminado`` cuando el
documento final está guardado en ``rfp_intake.final_documents``.

Concurrencia: cada rama tiene su lock (validar + reanudar no compite con otro
resume de la misma rama) y el coordinador el suyo; dos responsables pueden
decidir a la vez y sus ramas avanzan en paralelo. Los locks son de proceso:
con varios workers, cada rama/ticket debe atenderse en uno solo. Tickets
distintos nunca comparten ``thread_id``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Literal

from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import db
from .approval_graph import (
    department_approval_graph,
    department_config,
    lock_for,
    refresh_trace,
    ticket_approval_graph,
    ticket_config,
)
from .config import (
    DECISION_APPROVE,
    DEPARTMENT_IDS,
    DEPARTMENT_LABELS,
    MAX_APPROVAL_ROUNDS,
    MAX_ARBITRATION_ROUNDS,
    STATUS_ESPERANDO_APROBACION,
    STATUS_NEEDS_HUMAN_REVIEW,
    department_thread_id,
    ticket_thread_id,
)
from .conflicts import detect_conflicts, expected_currency

logger = logging.getLogger(__name__)

# La Parte 3 arranca con lo que deja la Parte 2 (needs_human_review incluido).
APPROVABLE_STATUSES = frozenset({STATUS_ESPERANDO_APROBACION, STATUS_NEEDS_HUMAN_REVIEW})
MIN_COMMENT_CHARS = 10


class ApprovalError(RuntimeError):
    """Error de la Parte 3 con su código HTTP sugerido."""

    status_code = 409


class ApprovalNotFoundError(ApprovalError):
    status_code = 404


class ApprovalConflictError(ApprovalError):
    status_code = 409


class ApprovalValidationError(ApprovalError):
    status_code = 422

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.errors = errors or [{"msg": message}]


class ApprovalDecision(BaseModel):
    """Respuesta humana para reanudar una sección (se valida antes de entrar al grafo)."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    decision: Literal["approve", "reject", "request_changes"]
    approver: str = Field(min_length=3, max_length=120, description="Responsable del departamento (BRIEFING §2.1)")
    comments: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _comments_when_not_approving(self) -> "ApprovalDecision":
        if self.decision != DECISION_APPROVE and len(self.comments or "") < MIN_COMMENT_CHARS:
            raise ValueError(
                f"Rechazar o pedir cambios exige un comentario de al menos {MIN_COMMENT_CHARS} caracteres."
            )
        return self


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def auto_approval_enabled() -> bool:
    """``RFP_AUTO_APPROVAL`` (1 por defecto): abrir la Parte 3 al acabar la Parte 2."""
    return os.getenv("RFP_AUTO_APPROVAL", "1").strip().lower() not in {"0", "false", "no"}


def _require_ticket(ticket_id: str) -> dict[str, Any]:
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise ApprovalNotFoundError(f"Ticket no encontrado: {ticket_id}")
    return ticket


def open_approval(ticket_id: str, *, drafter: Any = None) -> dict[str, Any]:
    """Abre (o resincroniza) la aprobación por departamento de un ticket."""
    ticket = _require_ticket(ticket_id)
    graph = ticket_approval_graph()
    config = ticket_config(ticket_id, drafter)
    with lock_for(ticket_thread_id(ticket_id)):
        if graph.get_state(config).values:
            graph.invoke({"ticket_id": ticket_id, "entry": "sync"}, config)
            refresh_trace(ticket_id)
            return approval_state(ticket_id)
        if ticket["status"] not in APPROVABLE_STATUSES:
            raise ApprovalConflictError(
                f"Ticket {ticket_id} en estado {ticket['status']}: la Parte 3 parte de "
                "esperando_aprobación o needs_human_review."
            )
        handoff = (db.get_rfp_metadata(ticket_id) or {}).get("proposal_handoff") or {}
        departments = [s["department_id"] for s in handoff.get("sections", [])]
        if not departments:
            raise ApprovalConflictError(f"Ticket {ticket_id} sin proposal_handoff de la Parte 2.")
        graph.invoke({
            "ticket_id": ticket_id,
            "entry": "open",
            "departments": departments,
            "max_revisions": MAX_APPROVAL_ROUNDS,
            "max_arbitration_rounds": MAX_ARBITRATION_ROUNDS,
            "arbitration_rounds": 0,
            "arbitration_limit_applied": False,
            "arbitrations": [],
            "final_document": None,
        }, config)
        refresh_trace(ticket_id)
    return approval_state(ticket_id)


def pending_interrupt(ticket_id: str, department_id: str) -> dict[str, Any] | None:
    """Payload de la interrupción que espera en la rama, o ``None``."""
    snapshot = department_approval_graph().get_state(department_config(ticket_id, department_id))
    return snapshot.interrupts[0].value if snapshot.interrupts else None


def validate_decision(ticket_id: str, department_id: str, payload: Any) -> dict[str, Any]:
    """Valida la respuesta humana; solo una respuesta válida entra al grafo.

    - formato (``ApprovalDecision``: decisión admitida, comentario obligatorio
      al rechazar o pedir cambios);
    - la rama existe y está interrumpida esperando decisión;
    - decide el responsable nombrado del departamento (§2.1), nadie más;
    - no se aprueba una sección ofensora de un conflicto §7 abierto.
    """
    _require_ticket(ticket_id)
    if department_id not in DEPARTMENT_IDS:
        raise ApprovalNotFoundError(f"Departamento desconocido: {department_id}")
    snapshot = department_approval_graph().get_state(department_config(ticket_id, department_id))
    values = snapshot.values or {}
    if not values:
        raise ApprovalNotFoundError(f"La sección {department_id} de {ticket_id} no tiene aprobación abierta.")
    if not snapshot.interrupts:
        raise ApprovalConflictError(
            f"La sección {department_id} no espera decisión (estado: {values.get('approval_status')})."
        )
    try:
        decision = payload if isinstance(payload, ApprovalDecision) else ApprovalDecision.model_validate(payload)
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ApprovalValidationError("; ".join(e["msg"] for e in errors), errors) from exc
    owner = values["owner"]
    if decision.approver != owner:
        raise ApprovalValidationError(
            f"Solo {owner}, responsable de {DEPARTMENT_LABELS[department_id]}, decide esta sección.",
            [{"loc": ["approver"], "msg": f"se esperaba {owner}"}],
        )
    if decision.decision == DECISION_APPROVE:
        metadata = db.get_rfp_metadata(ticket_id) or {}
        sections = {
            s["department_id"]: {"draft": s.get("draft_content"), "approval_status": s.get("approval_status")}
            for s in db.get_department_sections(ticket_id)
        }
        currency = expected_currency(metadata.get("client_country"), metadata.get("currency"))
        for conflict in detect_conflicts(sections, currency):
            if department_id in conflict["offenders"]:
                raise ApprovalConflictError(
                    f"No se puede aprobar {department_id}: conflicto {conflict['trigger']} abierto "
                    f"({conflict['detail']})."
                )
    return {
        "decision": decision.decision,
        "approver": decision.approver,
        "comments": decision.comments,
        "source": "human",
        "round": values.get("revisions", 0),
        "decided_at": _now_iso(),
    }


def submit_decision(
    ticket_id: str,
    department_id: str,
    payload: Any,
    *,
    drafter: Any = None,
) -> dict[str, Any]:
    """Reanuda la rama del departamento con la decisión validada y resincroniza el ticket."""
    config = ticket_config(ticket_id, drafter)
    with lock_for(department_thread_id(ticket_id, department_id)):
        decision = validate_decision(ticket_id, department_id, payload)
        department_approval_graph().invoke(
            Command(resume=decision), department_config(ticket_id, department_id, config),
        )
    with lock_for(ticket_thread_id(ticket_id)):
        ticket_approval_graph().invoke({"ticket_id": ticket_id, "entry": "sync"}, config)
        refresh_trace(ticket_id)
    logger.info("RFP %s: %s registró %s en %s", ticket_id, decision["approver"],
                decision["decision"], department_id)
    return approval_state(ticket_id)


def approval_state(ticket_id: str) -> dict[str, Any] | None:
    """Resumen persistido de la Parte 3 (``rfp_metadata.approval``)."""
    return (db.get_rfp_metadata(ticket_id) or {}).get("approval")


def get_final_document(ticket_id: str) -> dict[str, Any] | None:
    _require_ticket(ticket_id)
    return db.get_final_document(ticket_id)


__all__ = [
    "APPROVABLE_STATUSES",
    "ApprovalDecision",
    "ApprovalError",
    "ApprovalNotFoundError",
    "ApprovalConflictError",
    "ApprovalValidationError",
    "auto_approval_enabled",
    "open_approval",
    "pending_interrupt",
    "validate_decision",
    "submit_decision",
    "approval_state",
    "get_final_document",
]
