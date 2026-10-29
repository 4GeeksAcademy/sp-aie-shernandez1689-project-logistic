"""Router HTTP del pipeline de intake de RFPs (Hito 9, Parte 1).

Extiende el backend existente de ``services/api`` (misma app / misma API)
con los endpoints del tramo de recepción de RFPs:

- ``POST /rfp/upload``    — sube un PDF de RFP (multipart): guarda el PDF en
  ``data/raw/rfp/``, crea el ticket en ``analizando`` y responde ``202`` al
  instante. El análisis corre en segundo plano (``BackgroundTasks``); la UI
  recibe cada cambio de estado por ``GET /rfp/events`` (SSE).
- ``GET  /rfp/tickets``   — lista los tickets de intake (más recientes primero).
- ``GET  /rfp/tickets/{ticket_id}`` — detalle: metadatos, secciones por
  departamento (``key_aspects``, extractos, preguntas abiertas; Parte 2:
  borradores y EvaluationResult), resumen para Ventas y handoffs.
- ``POST /rfp/tickets/{ticket_id}/proposal`` — (re)lanza la Parte 2 en
  segundo plano (``202``). Tras el upload se encadena sola salvo
  ``RFP_AUTO_PROPOSAL=0``; al acabar abre la Parte 3 salvo
  ``RFP_AUTO_APPROVAL=0``.
- ``POST /rfp/tickets/{ticket_id}/approval`` — abre (o resincroniza) la
  aprobación por departamento (Parte 3).
- ``POST /rfp/tickets/{ticket_id}/sections/{department_id}/decision`` — el
  responsable del departamento aprueba, rechaza o pide cambios. La respuesta
  se valida (422/409) antes de reanudar la rama interrumpida del grafo.
- ``GET  /rfp/tickets/{ticket_id}/final-document`` — documento final
  (``?format=markdown`` lo devuelve como ``text/markdown``).
- ``GET  /rfp/events`` — stream SSE (``text/event-stream``, mismo JWT) con
  ``rfp_ticket_created`` / ``rfp_ticket_updated`` en cada alta o cambio de
  estado del ticket; admite ``Last-Event-ID`` para recuperar lo perdido
  (ver ``rfp_events.py``). Sustituye el polling de la UI.

Regla estricta de capas (Brifing-pipeline.md §5): este router **importa
únicamente** funciones de ``data/pipelines/rfp_intake/`` (proceso, clasificador,
config). No contiene lógica de negocio ni SQL de agregación.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi.concurrency import run_in_threadpool
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import PlainTextResponse, StreamingResponse

# ── Acceso al paquete data.pipelines (mismo patrón que routes/telemetry.py) ──
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auth import get_current_user  # noqa: E402
from data.pipelines.rfp_intake import (  # noqa: E402
    ApprovalDecision,
    ApprovalError,
    ProposalNotReadyError,
    db,
    get_final_document,
    open_approval,
    process_ticket,
    recover_stale_tickets,
    register_upload,
    run_proposal_and_approval,
    submit_decision,
)
from data.pipelines.rfp_intake.approval import auto_approval_enabled  # noqa: E402
from data.pipelines.rfp_intake.config import STATUS_TERMINADO  # noqa: E402
from data.pipelines.rfp_intake.proposal import (  # noqa: E402
    auto_proposal_enabled,
    load_handoff,
)
from rfp_events import ticket_events  # noqa: E402

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rfp", tags=["RFP Intake"])

# Extensiones admitidas al upload (rechazo temprano).
_ALLOWED_EXTENSIONS = {".pdf"}
# Cabecera mágica de todo PDF.
_PDF_MAGIC = b"%PDF-"

# Campos de metadatos que la UI de backoffice muestra en la tabla de tickets.
_DISPLAY_FIELDS = (
    "doc_type",
    "client_name",
    "client_country",
    "services_requested",
    "monthly_volume",
    "deadline",
    "budget_range",
    "departments_needed",
    "currency",
)


def _enrich_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    """Funde los metadatos de la RFP (client, moneda, depts…) en el ticket.

    El ticket persistido solo guarda id/status/reason; los campos de
    visualización viven en el registro de metadatos. Este helper los copia
    sobre la fila para que la tabla del frontend no muestre guiones.
    """
    row = dict(ticket)
    metadata = db.get_rfp_metadata(ticket.get("ticket_id", "")) or {}
    return {**row, **{k: metadata.get(k) for k in _DISPLAY_FIELDS}}


def _publish_ticket_event(ticket: dict[str, Any], created: bool) -> None:
    """Observador del pipeline: emite por SSE el ticket con sus metadatos."""
    ticket_events.publish_ticket(_enrich_ticket(ticket), created)


# Cada alta/cambio de ticket que persiste el pipeline se emite por SSE.
db.add_ticket_listener(_publish_ticket_event)


def _db_unavailable(exc: Exception) -> HTTPException:
    """503 cuando el backend no tiene PostgreSQL configurado."""
    logger.error("RFP intake sin base de datos: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Persistencia de RFPs no disponible (DATABASE_URL no configurada).",
    )


def _ensure_pdf(upload: UploadFile) -> None:
    """Valida que el archivo subido parezca un PDF (nombre + head bytes)."""
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Formato no admitido: '{suffix or 'sin extensión'}'. Esperado: PDF.",
        )


@router.post("/upload", status_code=status.HTTP_202_ACCEPTED, response_model=None)
async def upload_rfp(
    background_tasks: BackgroundTasks,
    file: Annotated[UploadFile, File(description="PDF de la RFP recibida")],
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Recibe un PDF de RFP y crea su ticket en ``analizando`` (modo ticket).

    Guarda el PDF en ``data/raw/rfp/<ticket_id>.pdf`` y responde de inmediato;
    el pipeline de intake corre en segundo plano y deja el ticket en
    ``analisis_completo`` o ``descartado``.
    """
    _ensure_pdf(file)

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El archivo adjunto está vacío.",
        )
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El archivo no es un PDF válido.",
        )

    try:
        ticket = register_upload(content)
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc

    # Función síncrona → Starlette la ejecuta en el threadpool tras responder.
    # Parte 1 (análisis) y, si acaba en analisis_completo, Parte 2.
    background_tasks.add_task(process_ticket, ticket["ticket_id"])
    logger.info("RFP %s recibida como %s", file.filename, ticket["ticket_id"])
    return {**ticket, "filename": file.filename}


@router.get("/tickets")
def list_tickets(
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Lista los tickets de intake (más recientes primero)."""
    try:
        enriched = [_enrich_ticket(t) for t in db.list_tickets()]
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc
    return {"tickets": enriched, "count": len(enriched)}


@router.get("/events", response_class=StreamingResponse)
async def ticket_event_stream(
    request: Request,
    _user: Any = Depends(get_current_user),
) -> StreamingResponse:
    """Stream SSE de altas y cambios de estado de los tickets de intake.

    Con ``Last-Event-ID`` (reconexión) reenvía primero lo que el cliente se
    perdió, o ``rfp_resync`` si ya no está en el buffer del servidor.
    """
    return StreamingResponse(
        ticket_events.stream(
            request.is_disconnected,
            last_event_id=request.headers.get("last-event-id"),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tickets/{ticket_id}")
def get_ticket_detail(
    ticket_id: str,
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Detalle de un ticket: metadatos de la RFP + secciones por departamento."""
    try:
        ticket = db.get_ticket(ticket_id)
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket no encontrado: {ticket_id}",
        )

    metadata = db.get_rfp_metadata(ticket_id) or {}

    return {
        "ticket": _enrich_ticket(ticket),
        "metadata": metadata,
        "departments": db.get_department_sections(ticket_id),
        # Synthesizer: qué pedir a quién (Ventas) + handoff a la Parte 2.
        "sales_summary": metadata.get("sales_summary"),
        "handoff": metadata.get("handoff"),
        # Parte 2 → Parte 3: borradores evaluados (needs_human_review incluidas).
        "proposal_handoff": metadata.get("proposal_handoff"),
        # Parte 3: decisiones por departamento, conflictos §7, arbitrajes, trace.
        "approval": metadata.get("approval"),
        # Solo existe al cerrar el ticket (todas las secciones aprobadas).
        "final_document": (
            db.get_final_document(ticket_id) if ticket["status"] == STATUS_TERMINADO else None
        ),
        # La UI sabe si analisis_completo es transitorio (Parte 2 encadenada)
        # y si la Parte 3 se abre sola al acabar la Parte 2.
        "auto_proposal": auto_proposal_enabled(),
        "auto_approval": auto_approval_enabled(),
    }


def _run_proposal(ticket_id: str) -> None:
    try:
        run_proposal_and_approval(ticket_id)
    except Exception:
        logger.exception("RFP propuesta: falló la generación de %s", ticket_id)


@router.post("/tickets/{ticket_id}/proposal", status_code=status.HTTP_202_ACCEPTED)
def regenerate_proposal(
    ticket_id: str,
    background_tasks: BackgroundTasks,
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """(Re)lanza la generación + evaluación de la propuesta (Parte 2)."""
    try:
        ticket, *_ = load_handoff(ticket_id)
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Ticket no encontrado: {ticket_id}") from exc
    except ProposalNotReadyError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    background_tasks.add_task(_run_proposal, ticket_id)
    return {"ticket_id": ticket_id, "status": ticket["status"], "queued": True}


def _approval_http_error(exc: ApprovalError) -> HTTPException:
    detail: Any = getattr(exc, "errors", None) or str(exc)
    return HTTPException(status_code=exc.status_code, detail=detail)


@router.post("/tickets/{ticket_id}/approval")
def start_approval(
    ticket_id: str,
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Abre la aprobación por departamento (Parte 3) o la resincroniza."""
    try:
        return {"ticket_id": ticket_id, "approval": open_approval(ticket_id)}
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc
    except ApprovalError as exc:
        raise _approval_http_error(exc) from exc


@router.post("/tickets/{ticket_id}/sections/{department_id}/decision")
def record_decision(
    ticket_id: str,
    department_id: str,
    decision: ApprovalDecision,
    _user: Any = Depends(get_current_user),
) -> dict[str, Any]:
    """Decisión del responsable: aprobar, rechazar o pedir cambios.

    FastAPI valida el formato (422) y el pipeline valida el contexto
    (responsable correcto, sección esperando decisión, conflictos abiertos)
    antes de reanudar la rama interrumpida.
    """
    try:
        approval = submit_decision(ticket_id, department_id, decision)
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc
    except ApprovalError as exc:
        raise _approval_http_error(exc) from exc
    ticket = db.get_ticket(ticket_id) or {}
    return {"ticket_id": ticket_id, "status": ticket.get("status"), "approval": approval}


@router.get("/tickets/{ticket_id}/final-document", response_model=None)
def final_document(
    ticket_id: str,
    format: str = Query("json", pattern="^(json|markdown)$"),
    _user: Any = Depends(get_current_user),
) -> Any:
    """Documento final consolidado (solo con todas las secciones aprobadas)."""
    try:
        document = get_final_document(ticket_id)
    except db.DatabaseNotConfiguredError as exc:
        raise _db_unavailable(exc) from exc
    except ApprovalError as exc:
        raise _approval_http_error(exc) from exc
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"El ticket {ticket_id} aún no tiene documento final.",
        )
    if format == "markdown":
        return PlainTextResponse(document["content"], media_type="text/markdown; charset=utf-8")
    return document


async def rfp_recovery_loop() -> None:
    """Barrido periódico de tickets atascados en ``analizando``.

    El análisis corre en BackgroundTasks dentro de este proceso: si el
    backend se reinicia a mitad, la tarea se pierde. Este bucle (arrancado
    desde el lifespan de ``main.py``) corre al iniciar y cada
    ``RFP_RECOVERY_INTERVAL_SECONDS`` (300 por defecto; 0 lo desactiva) y
    reanaliza o descarta esos tickets. La lógica vive en el pipeline.
    """
    interval = int(os.getenv("RFP_RECOVERY_INTERVAL_SECONDS", "300"))
    if interval <= 0:
        return
    while True:
        if db.is_db_available():
            try:
                summary = await run_in_threadpool(recover_stale_tickets)
                if any(summary.values()):
                    logger.warning("RFP intake: barrido de tickets atascados %s", summary)
            except Exception:
                logger.exception("RFP intake: falló el barrido de tickets atascados")
        await asyncio.sleep(interval)


__all__ = ["router", "rfp_recovery_loop"]