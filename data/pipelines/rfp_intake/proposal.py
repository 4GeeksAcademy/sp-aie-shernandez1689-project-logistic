"""Parte 2 del intake de RFPs: generación + evaluación de la propuesta económica.

Consume el handoff de la Parte 1 (``rfp_metadata.handoff`` de un ticket en
``analisis_completo``, ``next_status == generando_borrador``) y las filas
``department_sections`` (``source_excerpts``). No vuelve a leer el PDF ni el
Markdown de la RFP.

Estados del ticket (BRIEFING §2.3)::

    analisis_completo ─► generando_borrador ─► en_evaluación ─┬─► esperando_aprobación
                                                               └─► needs_human_review

- ``generando_borrador`` al arrancar (generadores escribiendo).
- ``en_evaluación`` en cuanto el primer borrador pasa a los evaluadores; se
  mantiene durante el ciclo generador-evaluador.
- ``needs_human_review`` si alguna sección agota el límite de iteraciones
  (o la generación falla): el ticket no se descarta.
- ``esperando_aprobación`` si todas las secciones pasan: listas para la
  aprobación por departamento de la Parte 3.

Borradores y EvaluationResult se persisten en PostgreSQL en cada iteración
(``department_sections.drafts`` / ``evaluation_results``); el handoff hacia la
Parte 3 queda en ``rfp_metadata.proposal_handoff``.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

from . import db
from .config import (
    MAX_GENERATION_ITERATIONS,
    SECTION_EN_EVALUACION,
    SECTION_GENERANDO,
    SECTION_NEEDS_HUMAN_REVIEW,
    STATUS_ANALISIS_COMPLETO,
    STATUS_EN_EVALUACION,
    STATUS_ESPERANDO_APROBACION,
    STATUS_GENERANDO_BORRADOR,
    STATUS_NEEDS_HUMAN_REVIEW,
)
from .generator import build_section_briefs
from .pipeline import analyze_ticket
from .proposal_graph import run_proposal_graph

logger = logging.getLogger(__name__)

# Estados desde los que se puede (re)generar la propuesta. Los de la Parte 2
# permiten relanzar un ticket interrumpido o enviado a revisión humana.
GENERATABLE_STATUSES = frozenset({
    STATUS_ANALISIS_COMPLETO,
    STATUS_GENERANDO_BORRADOR,
    STATUS_EN_EVALUACION,
    STATUS_NEEDS_HUMAN_REVIEW,
})


class ProposalNotReadyError(RuntimeError):
    """El ticket no tiene un handoff de la Parte 1 utilizable."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def auto_proposal_enabled() -> bool:
    """``RFP_AUTO_PROPOSAL`` (1 por defecto): encadenar la Parte 2 tras el análisis."""
    return os.getenv("RFP_AUTO_PROPOSAL", "1").strip().lower() not in {"0", "false", "no"}


def load_handoff(ticket_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Ticket, metadatos, handoff de la Parte 1 y secciones; valida el contrato."""
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(f"Ticket no encontrado: {ticket_id}")
    if ticket["status"] not in GENERATABLE_STATUSES:
        raise ProposalNotReadyError(
            f"Ticket {ticket_id} en estado {ticket['status']}: la Parte 2 parte de analisis_completo."
        )
    metadata = db.get_rfp_metadata(ticket_id) or {}
    handoff = metadata.get("handoff") or {}
    if handoff.get("next_status") != STATUS_GENERANDO_BORRADOR or not handoff.get("sections"):
        raise ProposalNotReadyError(f"Ticket {ticket_id} sin handoff de la Parte 1 hacia generando_borrador.")
    return ticket, metadata, handoff, db.get_department_sections(ticket_id)


def generate_proposal(
    ticket_id: str,
    *,
    drafter: Any = None,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    """Genera y evalúa las secciones de un ticket y deja su estado final.

    Returns
    -------
    dict
        El ``proposal_handoff`` hacia la Parte 3.
    """
    ticket, metadata, handoff, sections = load_handoff(ticket_id)
    limit = max_iterations or MAX_GENERATION_ITERATIONS
    briefs = build_section_briefs(handoff, metadata, sections)

    db.update_ticket(ticket_id, status=STATUS_GENERANDO_BORRADOR,
                     reason=f"generando {len(briefs)} secciones")
    for brief in briefs:
        db.update_department_section(
            brief.section_id, generation_status=SECTION_GENERANDO, iterations=0,
            draft_content=None, drafts=[], evaluation_results=[],
        )

    # Los ciclos de cada sección corren en hilos paralelos: las escrituras se
    # serializan para no competir por la conexión.
    lock = threading.Lock()
    state = {"ticket_status": STATUS_GENERANDO_BORRADOR}

    def progress(event: str, section_id: str, payload: dict[str, Any]) -> None:
        with lock:
            if event == "drafting":
                db.update_department_section(section_id, generation_status=SECTION_GENERANDO)
            elif event == "evaluating":
                db.update_department_section(
                    section_id,
                    generation_status=SECTION_EN_EVALUACION,
                    draft_content=payload["content"],
                    iterations=payload["iteration"],
                )
                if state["ticket_status"] != STATUS_EN_EVALUACION:
                    db.update_ticket(ticket_id, status=STATUS_EN_EVALUACION,
                                     reason="ciclo generador-evaluador en curso")
                    state["ticket_status"] = STATUS_EN_EVALUACION
            elif event == "evaluated":
                db.update_department_section(
                    section_id,
                    draft_content=payload["draft"],
                    drafts=payload["drafts"],
                    evaluation_results=payload["evaluations"],
                )

    try:
        results = run_proposal_graph(briefs, drafter=drafter, progress=progress, max_iterations=limit)
    except Exception as exc:
        logger.exception("RFP propuesta: fallo generando %s", ticket_id)
        db.update_ticket(ticket_id, status=STATUS_NEEDS_HUMAN_REVIEW,
                         reason=f"error interno en la generación: {type(exc).__name__}")
        raise

    for result in results:
        db.update_department_section(
            result["section_id"],
            generation_status=result["generation_status"],
            iterations=result["iterations"],
            draft_content=result["draft_content"],
            drafts=result["drafts"],
            evaluation_results=result["evaluation_results"],
        )

    review = [r["section_id"] for r in results if r["generation_status"] == SECTION_NEEDS_HUMAN_REVIEW]
    final_status = STATUS_NEEDS_HUMAN_REVIEW if review else STATUS_ESPERANDO_APROBACION
    proposal_handoff = build_proposal_handoff(ticket_id, handoff, results, final_status, limit)
    db.update_rfp_metadata(ticket_id, proposal_handoff=proposal_handoff)
    reason = (
        f"{len(review)} de {len(results)} secciones agotaron {limit} iteraciones: revisión humana"
        if review
        else f"{len(results)} secciones superaron la evaluación; pendientes de aprobación"
    )
    db.update_ticket(ticket_id, status=final_status, reason=reason)
    return proposal_handoff


def build_proposal_handoff(
    ticket_id: str,
    handoff: dict[str, Any],
    results: list[dict[str, Any]],
    status: str,
    max_iterations: int,
) -> dict[str, Any]:
    """Contrato hacia la Parte 3: todas las secciones (también las de revisión humana)."""
    iterations = [r["iterations"] for r in results]
    return {
        "part": 3,
        "ticket_id": ticket_id,
        "rfp_id": handoff.get("rfp_id"),
        "status": status,
        "currency": handoff.get("currency"),
        "max_iterations": max_iterations,
        "avg_iterations": round(sum(iterations) / len(iterations), 2) if iterations else 0,
        "needs_human_review": [
            r["section_id"] for r in results if r["generation_status"] == SECTION_NEEDS_HUMAN_REVIEW
        ],
        "sections": [
            {
                "section_id": r["section_id"],
                "department_id": r["department_id"],
                "owner": r["owner"],
                "generation_status": r["generation_status"],
                "iterations": r["iterations"],
                "generator": r["generator"],
                "draft_content": r["draft_content"],
                "evaluation": r["evaluation"],
            }
            for r in results
        ],
        "generated_at": _now_iso(),
    }


def process_ticket(ticket_id: str) -> dict[str, Any]:
    """Tarea en segundo plano del upload: Parte 1 y, si acaba bien, Parte 2."""
    result = analyze_ticket(ticket_id)
    if result.get("status") != STATUS_ANALISIS_COMPLETO or not auto_proposal_enabled():
        return result
    try:
        generate_proposal(ticket_id)
    except Exception:
        # El ticket ya quedó en needs_human_review (o en analisis_completo si
        # el handoff no era válido); el análisis de la Parte 1 se conserva.
        logger.exception("RFP propuesta: no se pudo completar %s", ticket_id)
    return db.get_ticket(ticket_id) or result


__all__ = [
    "GENERATABLE_STATUSES",
    "ProposalNotReadyError",
    "auto_proposal_enabled",
    "load_handoff",
    "generate_proposal",
    "build_proposal_handoff",
    "process_ticket",
]
