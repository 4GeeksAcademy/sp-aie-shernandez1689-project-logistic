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
           ──► run_department_graph()   orchestrator → worker × depto → synthesizer
                                        key_aspects por depto + resumen Ventas
                                        + handoff a la Parte 2
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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db
from .classifier import ClassificationResult, classify
from .config import (
    COUNTRY_CURRENCY,
    REPO_ROOT,
    STATUS_ANALISIS_COMPLETO,
    STATUS_ANALIZANDO,
    STATUS_DESCARTADO,
    raw_rfp_dir,
)
from .extract import convert_pdf_to_markdown, save_markdown
from .graph import run_department_graph
from .readability_metrics import compute_readability_metrics

logger = logging.getLogger(__name__)

# Barrido de tickets atascados: un análisis normal dura segundos.
STALE_ANALYSIS_AFTER = timedelta(minutes=10)
MAX_ANALYSIS_ATTEMPTS = 3


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
    # Reclama el ticket: registra el intento y renueva updated_at antes de
    # trabajar, para que el barrido sepa que hubo un intento si el proceso
    # muere a mitad (y no lo reprocese mientras sigue activo).
    attempts = int(ticket.get("attempts") or 0) + 1
    db.save_ticket(
        _build_ticket(
            status=STATUS_ANALIZANDO,
            reason=f"análisis en curso (intento {attempts})",
            attempts=attempts,
            **ids,
        )
    )
    ids["attempts"] = attempts
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


def recover_stale_tickets(
    stale_after: timedelta = STALE_ANALYSIS_AFTER,
    max_attempts: int = MAX_ANALYSIS_ATTEMPTS,
) -> dict[str, list[str]]:
    """Barrido de tickets atascados en ``analizando`` (el proceso murió a mitad).

    Para cada ticket sin actividad desde hace más de ``stale_after``:

    - PDF ausente en ``data/raw/`` → ``descartado`` con el motivo.
    - ``attempts >= max_attempts`` → ``descartado`` para revisión manual (un
      PDF que tumba el proceso no debe reintentarse en bucle).
    - En otro caso → se reanaliza con ``analyze_ticket`` (idempotente:
      upsert por clave primaria).

    Returns
    -------
    dict
        ``requeued``, ``abandoned`` y ``missing_pdf`` con los ``ticket_id``.
    """
    cutoff = datetime.now(timezone.utc) - stale_after
    summary: dict[str, list[str]] = {"requeued": [], "abandoned": [], "missing_pdf": []}

    for ticket in db.list_tickets_by_status(STATUS_ANALIZANDO):
        if _as_utc(ticket.get("updated_at")) > cutoff:
            continue  # análisis reciente: puede seguir en marcha
        ticket_id = ticket["ticket_id"]
        attempts = int(ticket.get("attempts") or 0)
        ids = {
            "ticket_id": ticket_id,
            "rfp_id": ticket["rfp_id"],
            "raw_pdf_path": ticket["raw_pdf_path"],
            "created_at": ticket.get("created_at"),
            "attempts": attempts,
        }
        if not Path(ticket["raw_pdf_path"]).is_file():
            db.save_ticket(_build_ticket(
                status=STATUS_DESCARTADO,
                reason="análisis interrumpido y PDF no encontrado en data/raw",
                **ids,
            ))
            summary["missing_pdf"].append(ticket_id)
        elif attempts >= max_attempts:
            db.save_ticket(_build_ticket(
                status=STATUS_DESCARTADO,
                reason=f"análisis interrumpido {attempts} veces; revisar manualmente",
                **ids,
            ))
            summary["abandoned"].append(ticket_id)
        else:
            logger.warning("RFP intake: reanalizando ticket atascado %s", ticket_id)
            analyze_ticket(ticket_id)
            summary["requeued"].append(ticket_id)
    return summary


def _as_utc(value: Any) -> datetime:
    """``updated_at`` (ISO o datetime, con o sin tz) → datetime UTC."""
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


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
    attempts: int = 0,
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
        "attempts": attempts,
    }

    # 1) Conversión PDF → Markdown (antes de que nadie lea el documento)
    extraction = convert_pdf_to_markdown(pdf_path)
    markdown = extraction["markdown"]
    extraction["markdown_path"] = str(
        save_markdown(markdown, Path(raw_pdf_path).with_suffix(".md"))
    )
    # 2) Clasificación (determinista, Parte 1); sin texto = documento vacío
    classification = classify(markdown)

    # 3) ¿RFP de TrackFlow? Un descarte también guarda sus metadatos de
    #    conversión y legibilidad: todo documento procesado deja su registro.
    if not classification.is_rfp:
        reason = classification.reason if markdown else "documento sin texto extraíble"
        ticket = _build_ticket(status=STATUS_DESCARTADO, reason=reason, **ids)
        metadata = _build_metadata(classification, extraction, None, rfp_id, ticket_id)
        if persist:
            _persist(ticket, metadata=metadata, sections=[])
        return {**ticket, "metadata": metadata}

    # 4) Metadatos enriquecidos + legibilidad + moneda
    currency = resolve_currency(classification.client_country)
    metadata = _build_metadata(classification, extraction, currency, rfp_id, ticket_id)

    # 5) Grafo orchestrator → workers (uno por departamento activo) → synthesizer
    synthesis = run_department_graph(
        ticket_id=ticket_id,
        rfp_id=rfp_id,
        markdown=markdown,
        metadata=metadata,
        markdown_path=extraction["markdown_path"],
    )
    sections = synthesis["sections"]
    metadata["sales_summary"] = synthesis["sales_summary"]
    metadata["handoff"] = synthesis["handoff"]

    # 6) Ticket final (analizando → analisis_completo); handoff a la Parte 2
    #    en metadata["handoff"] (next_status = generando_borrador).
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
    attempts: int = 0,
) -> dict[str, Any]:
    now = _now_iso()
    return {
        "ticket_id": ticket_id,
        "rfp_id": rfp_id,
        "status": status,
        "raw_pdf_path": raw_pdf_path,
        "reason": reason,
        "attempts": attempts,
        "created_at": created_at or now,
        "updated_at": now,
    }


def _build_metadata(
    classification: ClassificationResult,
    extraction: dict[str, Any],
    currency: str | None,
    rfp_id: str,
    ticket_id: str,
) -> dict[str, Any]:
    """Metadatos del documento (BRIEFING §2.3): clasificación + conversión + legibilidad.

    Se construye también para documentos descartados (campos de cliente
    vacíos, sin departamentos): sus métricas quedan registradas igual.
    """
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
        "sales_summary": metadata.get("sales_summary"),
        "handoff": metadata.get("handoff"),
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
    "recover_stale_tickets",
    "process_rfp_pdf",
    "resolve_currency",
    "store_pdf",
    "STATUS_ANALIZANDO",
    "STATUS_DESCARTADO",
    "STATUS_ANALISIS_COMPLETO",
    "REPO_ROOT",
]