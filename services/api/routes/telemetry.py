"""Telemetry ingestion router (Fase 1 — endpoint stub).

Este router es TEMPORAL y solo para verificación del envelope. Su único
propósito es confirmar que el payload de eventos llega con el formato
correcto. En la Fase 3 se sustituirá por la implementación real con
validación completa por event_type y persistencia en Supabase.

El endpoint acepta un lote de eventos con la forma {"events": [...]} y
responde 200 con {"received": N}. No persiste nada todavía.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter
from pydantic import BaseModel

from models import TelemetryBatch


API_DIR = Path(__file__).resolve().parents[1]
load_dotenv(API_DIR / ".env")

# URL del endpoint de destino para la Fase 3. Todavía no se usa para
# redirigir tráfico; se lee desde el arranque para establecer el patrón.
TELEMETRY_ENDPOINT = os.getenv("TELEMETRY_ENDPOINT", "").rstrip("/")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


class TelemetryIngestResponse(BaseModel):
    """Respuesta del stub: número de eventos aceptados en el lote."""

    received: int


@router.post("/events", response_model=TelemetryIngestResponse, status_code=200)
def ingest_telemetry_events(payload: TelemetryBatch) -> TelemetryIngestResponse:
    """Endpoint temporal de verificación del envelope de telemetría.

    Registra en log la cantidad de eventos recibidos y el ``event_type``
    de cada uno, y responde 200 OK con la cantidad de eventos del lote.
    """
    event_types = [event.event_type for event in payload.events]
    logger.info(
        "Telemetry batch received: %d event(s) | %s",
        len(payload.events),
        ", ".join(event_types),
    )
    return TelemetryIngestResponse(received=len(payload.events))