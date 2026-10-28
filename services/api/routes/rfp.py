"""Router HTTP del pipeline de intake de RFPs (Hito 9, Parte 1).

Extiende el backend existente de ``services/api`` (misma app / misma API)
con los endpoints del tramo de recepción de RFPs:

- ``POST /rfp/upload``    — sube un PDF de RFP (multipart): guarda el PDF en
  ``data/raw/rfp/``, crea el ticket en ``analizando`` y responde ``202`` al
  instante. El análisis corre en segundo plano (``BackgroundTasks``); la UI
  consulta ``GET /rfp/tickets/{ticket_id}`` hasta ``analisis_completo`` o
  ``descartado``.
- ``GET  /rfp/tickets``   — lista los tickets de intake (más recientes primero).
- ``GET  /rfp/tickets/{ticket_id}`` — detalle: metadatos, secciones por
  departamento (``key_aspects``, extractos, preguntas abiertas), resumen
  para Ventas y handoff hacia la Parte 2.

Regla estricta de capas (Brifing-pipeline.md §5): este router **importa
únicamente** funciones de ``data/pipelines/rfp_intake/`` (proceso, clasificador,
config). No contiene lógica de negocio ni SQL de agregación.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)

# ── Acceso al paquete data.pipelines (mismo patrón que routes/telemetry.py) ──
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auth import get_current_user  # noqa: E402
from data.pipelines.rfp_intake import analyze_ticket, db, register_upload  # noqa: E402

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
    background_tasks.add_task(analyze_ticket, ticket["ticket_id"])
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
    }


__all__ = ["router"]