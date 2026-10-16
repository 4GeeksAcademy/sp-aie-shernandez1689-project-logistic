"""Router de telemetría (Fase 2 y Fase 3).

* ``POST /telemetry/events`` — ingesta real con validación parcial.
  Acepta un lote de eventos con el formato ``{"events": [...]}``, valida cada
  evento individualmente con ``TelemetryEvent.model_validate()``, rechaza los
  inválidos **sin cancelar el lote**, y persiste los válidos en Supabase en
  una única operación de bulk insert.

* ``GET /telemetry/report`` — endpoint de reporte técnico. Resuelve la ventana
  (por defecto últimos 7 días UTC, inicio inclusivo / fin exclusivo) **una
  sola vez**, llama al pipeline de ``services/telemetry`` y cachea el
  resultado 60 s (TTL) para no recalcular en cada request.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError
from sqlmodel import Session

# La raíz del monorepo es <repo>/services/api/routes/telemetry.py -> parents[3].
# Añadirla a sys.path permite importar el paquete services.telemetry
# (namespace package del monorepo).
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auth import get_current_user  # noqa: E402
from cache import (  # noqa: E402
    CACHE_KEY_TELEMETRY_REPORT,
    telemetry_report_cache,
)
from db_telemetry import engine as telemetry_engine  # noqa: E402
from models import TelemetryEvent, TelemetryEventRecord  # noqa: E402
from services.telemetry import build_telemetry_report  # noqa: E402


API_DIR = Path(__file__).resolve().parents[1]
load_dotenv(API_DIR / ".env")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


# ---------------------------------------------------------------------------
# Modelos de request / response
# ---------------------------------------------------------------------------


class TelemetryIngestRequest(BaseModel):
    """Body del endpoint POST /telemetry/events — validación laxa.

    Solo exige que ``events`` sea una lista **no vacía** de diccionarios
    Python. Cada diccionario se validará individualmente más abajo con
    ``TelemetryEvent.model_validate()``, permitiendo el rechazo parcial
    de eventos sin abortar todo el lote.
    """

    model_config = ConfigDict(extra="forbid")

    events: list[dict] = Field(
        ...,
        min_length=1,
        description="Lista de eventos crudos (dicts) para validar individualmente",
    )


class TelemetryIngestResponse(BaseModel):
    """Respuesta del endpoint: estadísticas de la ingesta."""

    received: int
    stored: int
    rejected: int


# ---------------------------------------------------------------------------
# Funciones auxiliares de mapeo envelope → columnas
# ---------------------------------------------------------------------------


def _parse_timestamp(ts_str: str) -> datetime:
    """Convierte un string ISO 8601 (ej. ``2026-10-16T14:30:00.000Z``) a
    :class:`datetime.datetime` timezone-aware."""
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str)


def _derive_level(event: TelemetryEvent) -> str:
    """Deriva el nivel de severidad según el tipo de evento
    (telemetry-plan.md §11.4).

    * ``info``: eventos de negocio, navegación, rendimiento,
      proveedores, incidencias.
    * ``warn``: umbrales, login fallido, edición rechazada, API 4xx.
    * ``error``: API 5xx.
    """
    et = event.event_type

    # Eventos que siempre son warn
    if et in {
        "stock_threshold_triggered",
        "login_failed",
        "direct_stock_edit_rejected",
    }:
        return "warn"

    # API response: distinguir 4xx (warn) de 5xx (error)
    if et == "api_response_error_recorded":
        status = event.properties.get("status_code")
        if status is not None and str(status).startswith("5"):
            return "error"
        return "warn"

    # info por defecto (eventos de negocio, navegación, etc.)
    return "info"


# Campos numéricos que pueden aparecer en event.properties (allowlist).
_NUMERIC_FIELDS = {
    "quantity",
    "variance",
    "current_stock",
    "available_quantity",
    "requested_quantity",
    "shortage",
    "threshold_value",
    "expected_quantity",
    "actual_quantity",
}


def _extract_value(event: TelemetryEvent) -> float | None:
    """Extrae el primer valor numérico relevante de ``properties``.

    Devuelve ``None`` si el evento no tiene ningún campo del allowlist
    numérico definido en §11 del plan de telemetría.
    """
    for field in _NUMERIC_FIELDS:
        val = event.properties.get(field)
        if val is not None:
            return float(val)
    return None


def _build_message(event: TelemetryEvent) -> str | None:
    """Genera un resumen legible para el evento, o ``None`` si no tiene
    sentido para ese tipo de evento."""
    et = event.event_type
    props = event.properties

    if et == "inbound_order_created":
        ref = props.get("reference", "N/A")
        qty = props.get("quantity", "N/A")
        return f"Inbound order created: {ref}, {qty} units"

    if et == "outbound_order_created":
        ref = props.get("reference", "N/A")
        qty = props.get("quantity", "N/A")
        return f"Outbound order created: {ref}, {qty} units"

    if et == "stock_threshold_triggered":
        pid = props.get("product_id", "N/A")
        return f"Stock threshold triggered for {pid}"

    if et == "login_failed":
        return "Login failed"

    if et == "direct_stock_edit_rejected":
        pid = props.get("product_id", "N/A")
        return f"Direct stock edit rejected for {pid}"

    # Eventos sin mensaje personalizado
    return None


def _build_tags(event: TelemetryEvent) -> dict[str, Any]:
    """Construye el JSON de la columna ``tags``.

    Incluye todas las ``properties`` del allowlist **más** los campos
    del envelope necesarios para trazabilidad: ``eventId``, ``sessionId``,
    ``userId``, ``schemaVersion``, ``requestId`` (telemetry-plan.md §11.3).
    """
    tags = dict(event.properties) if event.properties else {}
    tags["eventId"] = event.eventId
    tags["sessionId"] = event.sessionId
    tags["userId"] = event.userId
    tags["schemaVersion"] = event.schemaVersion
    tags["requestId"] = event.requestId
    return tags


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/events", response_model=TelemetryIngestResponse, status_code=200)
def ingest_telemetry_events(body: TelemetryIngestRequest) -> TelemetryIngestResponse:
    """Endpoint real de ingesta de telemetría.

    Pasos:
    1. Itera sobre cada evento crudo del lote.
    2. Valida individualmente con ``TelemetryEvent.model_validate()``.
    3. Los eventos **válidos** se transforman a :class:`TelemetryEventRecord`
       y se acumulan para el bulk insert.
    4. Los eventos **inválidos** se cuentan (``rejected``) pero **no
       cancelan el lote**.
    5. Si hay eventos válidos y hay conexión a Supabase, se insertan todos
       en una sola operación (``session.add_all`` + ``session.commit``).
    6. Devuelve ``{"received", "stored", "rejected"}``.
    """
    events_raw = body.events
    total = len(events_raw)

    valid_records: list[TelemetryEventRecord] = []
    rejected = 0

    for raw_event in events_raw:
        try:
            event = TelemetryEvent.model_validate(raw_event)
        except PydanticValidationError as exc:
            logger.warning("Event rejected: %s", exc)
            rejected += 1
            continue

        record = TelemetryEventRecord(
            timestamp=_parse_timestamp(event.timestamp),
            service="backoffice",
            event_type=event.event_type,
            level=_derive_level(event),
            value=_extract_value(event),
            message=_build_message(event),
            tags=_build_tags(event),
        )
        valid_records.append(record)

    stored = len(valid_records)

    # Bulk insert — una sola operación por lote
    if stored > 0 and telemetry_engine is not None:
        with Session(telemetry_engine) as session:
            session.add_all(valid_records)
            session.commit()

    # Log informativo
    if stored > 0:
        logger.info(
            "Telemetry batch received: %d event(s) | stored: %d | rejected: %d | %s",
            total,
            stored,
            rejected,
            ", ".join(r.event_type for r in valid_records),
        )
    else:
        logger.info(
            "Telemetry batch received: %d event(s) | stored: 0 | rejected: %d%s",
            total,
            rejected,
            " (DB not available)" if telemetry_engine is None else "",
        )

    return TelemetryIngestResponse(received=total, stored=stored, rejected=rejected)


# ---------------------------------------------------------------------------
# Reporte técnico de telemetría (Fase 2)
# ---------------------------------------------------------------------------


class TelemetryPeriod(BaseModel):
    """Ventana temporal resuelta del reporte (UTC, ISO 8601)."""

    from_: str = Field(..., alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True)


class TelemetryReportResponse(BaseModel):
    """Cuerpo de ``GET /telemetry/report``.

    Estructura fija del tele-report.md:
    ``{ "period": {"from", "to"}, "metrics": { <métrica>: [...] } }``.
    ``metrics`` es un dict abierto porque el catálogo de métricas del
    pipeline puede crecer sin romper el contrato del endpoint.
    """

    period: TelemetryPeriod
    metrics: dict[str, list[dict[str, Any]]]


def _round_to_minute(dt: datetime) -> datetime:
    """Trunca un datetime a la resolución del minuto completo.

    La ventana por defecto calcula ``now`` en cada request; sin truncar, cada
    request genera una clave de cache distinta (microsegundos) y el TTL de 60 s
    sería inútil. Truncando al minuto, la ventana por defecto es estable
    durante todo un minuto, alineada con el TTL del cache.
    """
    return dt.replace(second=0, microsecond=0)


def _resolve_period(
    start_date: datetime | None,
    end_date: datetime | None,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Resuelve la ventana del reporte.

    Reglas:
    * Si no se pasan fechas → últimos 7 días UTC: ``start = now - 7d``
      (inicio inclusivo), ``end = now`` (fin exclusivo).
    * ``now`` se trunca al minuto para que la ventana por defecto sea estable
      dentro del TTL del cache (60 s).
    * El usuario puede pasar solo una de las dos fechas; la otra se infiere.
    * Ambas se convierten a UTC; ``end`` nunca queda antes que ``start``.
    """
    now = now or _round_to_minute(datetime.now(timezone.utc))
    default_start = now - timedelta(days=7)

    a = start_date or default_start
    b = end_date or now

    if b <= a:
        raise ValueError("start_date debe ser anterior a end_date.")

    return a.astimezone(timezone.utc), b.astimezone(timezone.utc)


def _build_cache_key(start_date: datetime, end_date: datetime) -> str:
    return (
        f"{CACHE_KEY_TELEMETRY_REPORT}"
        f"{start_date.isoformat()}|{end_date.isoformat()}"
    )


@router.get(
    "/report",
    response_model=TelemetryReportResponse,
    dependencies=[Depends(get_current_user)],
)
def get_telemetry_report(
    start_date: datetime | None = Query(
        default=None,
        description="Inicio de la ventana (ISO 8601). Por defecto now - 7d.",
    ),
    end_date: datetime | None = Query(
        default=None,
        description="Fin exclusivo de la ventana (ISO 8601). Por defecto now.",
    ),
) -> TelemetryReportResponse:
    """Reporte técnico de telemetría.

    El pipeline de análisis vive en ``services/telemetry``; este endpoint se
    limita a: resolver la ventana **una vez**, llamar al pipeline con ella y
    cachear el resultado 60 s para no recalcular en cada request.
    """
    if telemetry_engine is None:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL no está configurada; no se puede generar el reporte.",
        )

    try:
        start, end = _resolve_period(start_date, end_date)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cache_key = _build_cache_key(start, end)
    hit, cached = telemetry_report_cache.get(cache_key)
    if hit:
        return cached

    with Session(telemetry_engine) as session:
        metrics = build_telemetry_report(session, start, end)

    period = TelemetryPeriod(from_=start.isoformat(), to=end.isoformat())
    response = TelemetryReportResponse(period=period, metrics=metrics)
    telemetry_report_cache.set(cache_key, response)
    return response