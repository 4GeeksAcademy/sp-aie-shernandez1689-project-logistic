"""Orquestador del pipeline de intake de RFPs (Hito 9, Parte 1).

Flujo de punta a punta (BRIEFING §2.2–§2.4). En la API se parte en dos:
``register_upload()`` (síncrono, rápido: PDF + ticket ``analizando``) y
``analyze_ticket()`` (en segundo plano: el resto del flujo).

    upload ──► register_upload()        PDF → data/raw/rfp/<ticket_id>.pdf
                                        ticket ``analizando``
           ──► convert_pdf_to_markdown() PDF → Markdown (MarkItDown, obligatorio)
                                        → data/raw/rfp/<ticket_id>.md
           ──► classify(markdown)       ¿es RFP de TrackFlow? metadatos,
                                        departamentos, país/moneda
           ──► compute_readability()    legibilidad + costo de procesamiento
           ──► persist                  Ticket + RFP metadata + DepartmentSection
           ──┴  estado final: ``analisis_completo`` | ``descartado``

Parte 1 (BRIEFING §6): el ticket identifica correctamente si el documento es
una RFP de TrackFlow, extrae metadatos (incluido `client_country`) y enruta el
análisis solo a los departamentos que el alcance requiere.

Regla de capas (Brifing-pipeline.md §5): este orquestador vive en
``data/pipelines/`` y expone funciones puras que `services/` (router HTTP)
y `scripts/` (CLI) invocan. No importa nada de `services/`.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import db
from .classifier import ClassificationResult, classify
from .config import (
    COUNTRY_CURRENCY,
    DEPARTMENT_LABELS,
    DEPARTMENT_OWNERS,
    REPO_ROOT,
    STATUS_ANALISIS_COMPLETO,
    STATUS_ANALIZANDO,
    STATUS_DESCARTADO,
    raw_rfp_dir,
)
from .extract import convert_pdf_to_markdown, save_markdown
from .readability_metrics import compute_readability_metrics

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_ticket_id() -> str:
    """Identificador legible de ticket: ``rfp_<año><mes><día>_<uuid8>``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"rfp_{stamp}_{uuid.uuid4().hex[:8]}"


def resolve_currency(client_country: str | None) -> str:
    """Moneda de la propuesta según el país del cliente (BRIEFING §5).

    EE. UU. → USD · España → EUR · desconocido → ``None`` (se pide confirmar).
    """
    if not client_country:
        return None  # type: ignore[return-value]
    return COUNTRY_CURRENCY.get(client_country)


def store_pdf(pdf_path: Path, ticket_id: str, raw_dir: Path | None = None) -> Path:
    """Copia el PDF recibido a ``data/raw/rfp/<ticket_id>.pdf`` (BRIEFING §2.4)."""
    target_dir = raw_dir or raw_rfp_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{ticket_id}{pdf_path.suffix.lower() or '.pdf'}"
    shutil.copyfile(str(pdf_path), str(target))
    return target


# ── Bloque de metadatos por departamento (Parte 1: key_aspects) ─────────────


def _build_key_aspects_for(
    department_id: str,
    result: ClassificationResult,
    currency: str,
) -> list[str]:
    """Aspectos clave iniciales de un departamento para esta RFP (Parte 1).

    Se rellenan con datos reales del documento (servicio, país, volumen,
    moneda). La redacción del borrador (draft_content) es de la Parte 2.
    """
    aspects = [f"Servicio solicitado: {department_id}"]
    if result.client_country:
        aspects.append(f"País del cliente: {result.client_country}")
    if result.monthly_volume is not None:
        aspects.append(f"Volumen mensual estimado: {result.monthly_volume} pedidos/mes")
    if currency:
        aspects.append(f"Moneda de la propuesta: {currency}")
    if result.deadline:
        aspects.append(f"Fecha límite: {result.deadline}")
    return aspects


def _extract_department_sections(
    result: ClassificationResult,
    currency: str,
    rfp_id: str,
    ticket_id: str,
) -> list[dict[str, Any]]:
    """Genera los DepartmentSection de la RFP (solo departamentos activos)."""
    sections: list[dict[str, Any]] = []
    for dep_id in result.departments_needed:
        sections.append(
            {
                "id": f"{ticket_id}_{dep_id}",
                "ticket_id": ticket_id,
                "rfp_id": rfp_id,
                "department_id": dep_id,
                "department_label": DEPARTMENT_LABELS.get(dep_id, dep_id),
                "department_owner": DEPARTMENT_OWNERS.get(dep_id),
                "key_aspects": _build_key_aspects_for(dep_id, result, currency),
                "draft_content": None,  # Parte 2 generará el borrador
                "evaluation_results": None,  # Parte 2/3 evaluará el borrador
                "approval_status": "pendiente",  # Parte 3 flujo de aprobación
                "approver": None,
                "approved_at": None,
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )
    return sections


# ── Orquestador principal ───────────────────────────────────────────────────


def _require_db() -> None:
    """Falla antes de escribir el PDF: sin BD no hay dónde dejar el ticket."""
    if not db.is_db_available():
        raise db.DatabaseNotConfiguredError(
            "DATABASE_URL no configurada: no se puede persistir el ticket."
        )


def register_upload(content: bytes, raw_dir: Path | None = None) -> dict[str, Any]:
    """Recepción rápida (modo ticket): guarda el PDF y crea el ticket ``analizando``.

    Escribe ``data/raw/rfp/<ticket_id>.pdf`` (BRIEFING §2.4) y persiste el
    ticket. El análisis se lanza aparte con ``analyze_ticket(ticket_id)``.
    """
    _require_db()
    ticket_id = _new_ticket_id()
    target_dir = raw_dir or raw_rfp_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    raw_pdf = target_dir / f"{ticket_id}.pdf"
    raw_pdf.write_bytes(content)

    ticket = _build_ticket(
        ticket_id=ticket_id,
        rfp_id=f"{ticket_id}-rfp",
        status=STATUS_ANALIZANDO,
        raw_pdf_path=str(raw_pdf),
        reason="análisis en curso",
    )
    db.save_ticket(ticket)
    return ticket


def analyze_ticket(ticket_id: str) -> dict[str, Any]:
    """Analiza el PDF de un ticket ``analizando`` y persiste el estado final.

    Pensado para ejecutarse en segundo plano tras ``register_upload``. Un
    error inesperado deja el ticket ``descartado`` con el motivo, para que
    nunca quede colgado en ``analizando``.
    """
    ticket = db.get_ticket(ticket_id)
    if ticket is None:
        raise KeyError(f"Ticket no encontrado: {ticket_id}")

    ids = {
        "ticket_id": ticket_id,
        "rfp_id": ticket["rfp_id"],
        "raw_pdf_path": ticket["raw_pdf_path"],
        "created_at": ticket.get("created_at"),
    }
    try:
        return _analyze(Path(ticket["raw_pdf_path"]), persist=True, **ids)
    except Exception as exc:
        logger.exception("RFP intake: fallo analizando %s", ticket_id)
        failed = _build_ticket(
            status=STATUS_DESCARTADO,
            reason=f"error interno durante el análisis: {type(exc).__name__}",
            **ids,
        )
        db.save_ticket(failed)
        return failed


def process_rfp_pdf(
    pdf_path: str | Path,
    raw_dir: Path | None = None,
    *,
    persist: bool = True,
) -> dict[str, Any]:
    """Ejecuta el intake completo de una RFP de forma síncrona (CLI / tests).

    Parameters
    ----------
    pdf_path
        Ruta al PDF recibido.
    raw_dir
        Directorio destino de la copia (por defecto ``data/raw/rfp``).
    persist
        Si ``False``, no escribe estado (útil en tests/validación en memoria).

    Returns
    -------
    dict
        Estado completo del ticket: ``ticket_id``, ``status``, ``pdf``,
        ``classification``, ``metadata`` (con legibilidad), ``departments``.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists() or not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    if persist:
        ticket = register_upload(pdf_path.read_bytes(), raw_dir)
        return analyze_ticket(ticket["ticket_id"])

    ticket_id = _new_ticket_id()
    raw_pdf = store_pdf(pdf_path, ticket_id, raw_dir)
    return _analyze(
        pdf_path,
        ticket_id=ticket_id,
        rfp_id=f"{ticket_id}-rfp",
        raw_pdf_path=str(raw_pdf),
        persist=False,
    )


def _analyze(
    pdf_path: Path,
    *,
    ticket_id: str,
    rfp_id: str,
    raw_pdf_path: str,
    created_at: str | None = None,
    persist: bool,
) -> dict[str, Any]:
    """Markdown → clasificación → metadatos → secciones → estado final.

    Nada a partir de aquí lee el PDF: clasificador, métricas y (Parte 2)
    agentes trabajan sobre el Markdown convertido.
    """
    ids = {
        "ticket_id": ticket_id,
        "rfp_id": rfp_id,
        "raw_pdf_path": raw_pdf_path,
        "created_at": created_at,
    }

    # 1) Conversión PDF → Markdown (antes de que nadie lea el documento)
    extraction = convert_pdf_to_markdown(pdf_path)
    markdown = extraction["markdown"]
    extraction["markdown_path"] = str(
        save_markdown(markdown, Path(raw_pdf_path).with_suffix(".md"))
    )
    if not markdown:
        ticket = _build_ticket(
            status=STATUS_DESCARTADO, reason="documento sin texto extraíble", **ids
        )
        if persist:
            _persist(ticket, metadata=None, sections=[])
        return ticket

    # 2) Clasificación (determinista, Parte 1)
    classification = classify(markdown)

    # 3) ¿RFP de TrackFlow?
    if not classification.is_rfp:
        ticket = _build_ticket(status=STATUS_DESCARTADO, reason=classification.reason, **ids)
        if persist:
            _persist(ticket, metadata=None, sections=[])
        return ticket

    # 4) Metadatos enriquecidos + legibilidad + moneda
    currency = resolve_currency(classification.client_country)
    metadata = _build_metadata(classification, extraction, currency, rfp_id, ticket_id)

    # 5) Secciones por departamento (solo activos)
    sections = _extract_department_sections(classification, currency, rfp_id, ticket_id)

    # 6) Ticket final (analizando → analisis_completo)
    ticket = _build_ticket(
        status=STATUS_ANALISIS_COMPLETO, reason=classification.reason, **ids
    )

    if persist:
        _persist(ticket, metadata=metadata, sections=sections)

    return _compose_response(
        ticket=ticket,
        classification=classification,
        metadata=metadata,
        sections=sections,
        currency=currency,
        extraction=extraction,
    )


# ── Helpers de construcción ─────────────────────────────────────────────────


def _build_ticket(
    *,
    ticket_id: str,
    rfp_id: str,
    status: str,
    raw_pdf_path: str,
    reason: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "ticket_id": ticket_id,
        "rfp_id": rfp_id,
        "status": status,
        "raw_pdf_path": raw_pdf_path,
        "reason": reason,
        "created_at": created_at or now,
        "updated_at": now,
    }


def _build_metadata(
    classification: ClassificationResult,
    extraction: dict[str, Any],
    currency: str,
    rfp_id: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Construye el bloque de metadatos de la RFP (BRIEFING §2.3)."""
    now = _now_iso()
    readability = compute_readability_metrics(extraction["markdown"])
    return {
        "id": f"{ticket_id}-meta",
        "ticket_id": ticket_id,
        "rfp_id": rfp_id,
        "doc_type": classification.doc_type,
        "client_name": classification.client_name,
        "client_country": classification.client_country,
        "services_requested": classification.services_requested,
        "monthly_volume": classification.monthly_volume,
        "deadline": classification.deadline,
        "budget_range": classification.budget_range,
        "departments_needed": classification.departments_needed,
        "currency": currency,
        "num_pages": extraction.get("num_pages"),
        "markdown_path": extraction.get("markdown_path"),
        "converter": extraction.get("converter"),
        "estimated_tokens": readability["estimated_tokens"],
        "processing_cost": readability["processing_cost"],
        "readability": readability,
        "created_at": now,
        "updated_at": now,
    }


def _persist(
    ticket: dict[str, Any],
    metadata: dict[str, Any] | None,
    sections: list[dict[str, Any]],
) -> None:
    """Persiste ticket + metadatos + secciones (Parte 1: key_aspects) en PostgreSQL."""
    db.save_intake(ticket, metadata, sections)


def _compose_response(
    *,
    ticket: dict[str, Any],
    classification: ClassificationResult,
    metadata: dict[str, Any],
    sections: list[dict[str, Any]],
    currency: str,
    extraction: dict[str, Any],
) -> dict[str, Any]:
    """Compone la respuesta completa del ticket para la UI/CLI."""
    return {
        "ticket_id": ticket["ticket_id"],
        "status": ticket["status"],
        "reason": ticket["reason"],
        "raw_pdf_path": ticket["raw_pdf_path"],
        "classification": classification.to_dict(),
        "metadata": metadata,
        "departments": sections,
        "currency": currency,
        "source_pdf": {
            "filename": extraction.get("filename"),
            "num_pages": extraction.get("num_pages"),
            "markdown_path": extraction.get("markdown_path"),
        },
    }


__all__ = [
    "register_upload",
    "analyze_ticket",
    "process_rfp_pdf",
    "resolve_currency",
    "store_pdf",
    "STATUS_ANALIZANDO",
    "STATUS_DESCARTADO",
    "STATUS_ANALISIS_COMPLETO",
    "REPO_ROOT",
]