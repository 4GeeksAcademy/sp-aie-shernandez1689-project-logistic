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
  (o la generación falla): el ticket no se descarta. Esas secciones llevan
  una cabecera "BORRADOR PROVISIONAL" en ``draft_content`` y figuran en
  ``sales_summary.proposal.provisional_sections`` para Ventas.
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
    departments = {b.section_id: b.department_id for b in briefs}
    # Última novedad por sección: el motivo del ticket la muestra en vivo.
    live: dict[str, str] = {b.section_id: "en cola" for b in briefs}

    def progress(event: str, section_id: str, payload: dict[str, Any]) -> None:
        with lock:
            iteration = payload["iteration"]
            if event == "drafting":
                db.update_department_section(section_id, generation_status=SECTION_GENERANDO)
                live[section_id] = f"generando borrador {iteration}"
            elif event == "evaluating":
                db.update_department_section(
                    section_id,
                    generation_status=SECTION_EN_EVALUACION,
                    draft_content=payload["content"],
                    iterations=iteration,
                )
                live[section_id] = f"evaluando borrador {iteration}"
            elif event == "evaluated":
                db.update_department_section(
                    section_id,
                    draft_content=payload["draft"],
                    drafts=payload["drafts"],
                    evaluation_results=payload["evaluations"],
                )
                passed = payload["evaluation"]["overall_pass"]
                live[section_id] = (
                    f"borrador {iteration} aprobado por los evaluadores" if passed
                    else f"borrador {iteration} no pasó la evaluación"
                )
            reason = "; ".join(f"{departments[s]}: {msg}" for s, msg in live.items())
            if event == "evaluating" and state["ticket_status"] != STATUS_EN_EVALUACION:
                state["ticket_status"] = STATUS_EN_EVALUACION
                db.update_ticket(ticket_id, status=STATUS_EN_EVALUACION, reason=reason)
            else:
                db.update_ticket(ticket_id, reason=reason)

    try:
        results = run_proposal_graph(briefs, drafter=drafter, progress=progress, max_iterations=limit)
    except Exception as exc:
        logger.exception("RFP propuesta: fallo generando %s", ticket_id)
        db.update_ticket(ticket_id, status=STATUS_NEEDS_HUMAN_REVIEW,
                         reason=f"error interno en la generación: {type(exc).__name__}")
        raise

    for result in results:
        if result["generation_status"] == SECTION_NEEDS_HUMAN_REVIEW:
            mark_provisional(result)
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
    db.update_rfp_metadata(
        ticket_id,
        proposal_handoff=proposal_handoff,
        sales_summary=_sales_summary_with_proposal(metadata.get("sales_summary"), results, final_status),
    )
    reason = (
        f"{len(review)} de {len(results)} secciones agotaron {limit} iteraciones: revisión humana"
        if review
        else f"{len(results)} secciones superaron la evaluación; pendientes de aprobación"
    )
    db.update_ticket(ticket_id, status=final_status, reason=reason)
    return proposal_handoff


def failed_checks(evaluation: dict[str, Any] | None) -> list[str]:
    """Qué no superó la sección: ``rule_ids`` de cumplimiento + relevance/readability."""
    if evaluation is None:  # el ciclo falló antes de evaluar
        return ["error"]
    checks = list(evaluation["compliance"].get("rule_ids") or [])
    for name in ("relevance", "readability"):
        if not evaluation[name]["pass"]:
            checks.append(name)
    return checks


def provisional_banner(result: dict[str, Any]) -> str:
    """Cabecera que viaja con el texto: quien copie el borrador ve que es provisional."""
    checks = ", ".join(failed_checks(result["evaluation"])) or "evaluación"
    return (
        f"> **BORRADOR PROVISIONAL** — no superó la evaluación tras {result['iterations']} "
        f"iteraciones (pendiente: {checks}). Revisar con {result['owner']} antes de "
        "enviarlo al cliente.\n\n"
    )


def mark_provisional(result: dict[str, Any]) -> None:
    """Marca una sección ``needs_human_review``: cabecera en ``draft_content`` + bandera.

    El historial ``drafts`` conserva el texto tal como salió del generador;
    la aprobación de la Parte 3 debe quitar la cabecera.
    """
    result["provisional"] = True
    result["draft_content"] = provisional_banner(result) + (result["draft_content"] or "")


def _sales_summary_with_proposal(
    summary: dict[str, Any] | None, results: list[dict[str, Any]], status: str
) -> dict[str, Any] | None:
    """Añade al resumen de Ventas el estado de la propuesta y qué secciones son provisionales."""
    if summary is None:
        return None
    provisional = [
        {
            "department_id": r["department_id"],
            "owner": r["owner"],
            "iterations": r["iterations"],
            "pending": failed_checks(r["evaluation"]),
        }
        for r in results
        if r.get("provisional")
    ]
    return {**summary, "proposal": {"status": status, "provisional_sections": provisional}}


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
                "provisional": bool(r.get("provisional")),
                "iterations": r["iterations"],
                "generator": r["generator"],
                "draft_content": r["draft_content"],
                "evaluation": r["evaluation"],
                "error": r.get("error"),
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
    "failed_checks",
    "mark_provisional",
    "process_ticket",
]
