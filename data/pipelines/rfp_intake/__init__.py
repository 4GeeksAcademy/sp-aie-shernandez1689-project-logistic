"""Paquete del pipeline de intake de RFPs (TrackFlow, Hito 9 - Parte 1).

Flujo (Parte 1): upload (``register_upload``: PDF en data/raw/ + ticket
``analizando``) → en segundo plano ``analyze_ticket``: ``extract``
(PDF → Markdown con MarkItDown) (PDF → texto) → ``classify``
(RFP de TrackFlow? metadatos, departamentos) → legibilidad → persistir
ticket en PostgreSQL. Estado final ``analisis_completo`` o ``descartado``.

Parte 2 (``proposal``): handoff de la Parte 1 → generador por departamento →
evaluadores en paralelo (legibilidad, pertinencia, cumplimiento §5) → ciclo
con límite de iteraciones → ``esperando_aprobación`` o ``needs_human_review``.

Parte 3 (``approval``): una rama interrumpible por departamento (hilo
``rfp-{ticket_id}:{department}``) espera la decisión de su responsable;
el coordinador (hilo ``rfp-{ticket_id}``) arbitra los conflictos §7 y, con
todo aprobado, guarda el documento final → ``terminado``.
"""

from __future__ import annotations

from .classifier import ClassificationResult, classify
from .config import (
    COUNTRY_CURRENCY,
    DEPARTMENT_IDS,
    DEPARTMENT_LABELS,
    DEPARTMENT_OWNERS,
    DEFAULT_RAW_RFP_DIR,
    PIPELINE_NAME,
    SERVICE_TO_DEPARTMENT,
    STATUS_ANALISIS_COMPLETO,
    STATUS_ANALIZANDO,
    STATUS_DESCARTADO,
    raw_rfp_dir,
    samples_dir,
)
from .extract import ConversionUnavailableError, convert_pdf_to_markdown
from .graph import department_graph, run_department_graph
from .proposal import (
    ProposalNotReadyError,
    generate_proposal,
    process_ticket,
    run_proposal_and_approval,
)
from .approval import (
    ApprovalDecision,
    ApprovalError,
    get_final_document,
    open_approval,
    submit_decision,
    validate_decision,
)
# Sin re-exportar ``proposal_graph`` (el grafo): taparía el submódulo homónimo.
from .proposal_graph import run_proposal_graph, section_graph
from .pipeline import (
    analyze_ticket,
    process_rfp_pdf,
    recover_stale_tickets,
    register_upload,
    resolve_currency,
    store_pdf,
)

__all__ = [
    # clasificador / extractor
    "ClassificationResult",
    "classify",
    "ConversionUnavailableError",
    "convert_pdf_to_markdown",
    # grafo orchestrator → workers → synthesizer
    "department_graph",
    "run_department_graph",
    # Parte 2: generación + evaluación
    "ProposalNotReadyError",
    "generate_proposal",
    "process_ticket",
    "section_graph",
    "run_proposal_graph",
    "run_proposal_and_approval",
    # Parte 3: aprobación por departamento + documento final
    "ApprovalDecision",
    "ApprovalError",
    "open_approval",
    "validate_decision",
    "submit_decision",
    "get_final_document",
    # orquestador
    "register_upload",
    "analyze_ticket",
    "recover_stale_tickets",
    "process_rfp_pdf",
    "resolve_currency",
    "store_pdf",
    # config
    "PIPELINE_NAME",
    "DEPARTMENT_IDS",
    "DEPARTMENT_LABELS",
    "DEPARTMENT_OWNERS",
    "SERVICE_TO_DEPARTMENT",
    "COUNTRY_CURRENCY",
    "STATUS_ANALIZANDO",
    "STATUS_DESCARTADO",
    "STATUS_ANALISIS_COMPLETO",
    "DEFAULT_RAW_RFP_DIR",
    "raw_rfp_dir",
    "samples_dir",
]
