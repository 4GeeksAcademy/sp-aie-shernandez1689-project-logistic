"""Tool: consulta de tickets de soporte (incidencias).

Contrato tipado de entrada/salida
---------------------------------
Entrada (``IncidentQuery``):
  - ``ticket_id``  : id de ticket concreto (GET /api/incidents/{id}).
  - filtros        : status, category, origin, branch — igual que los
                     query params de GET /api/incidents.

Salida (``IncidentLookupResult``):
  - ``ticket_id``, ``status``, ``category``, ``origin``, ``branch``,
    ``title``, ``description``, ``created_at``, ``updated_at``,
    ``resolved`` — los MISMOOS campos que expone la API de incidencias
    (models.Incident / IncidentListResponse).

Transporte
----------
Lee de forma **en-proceso** el gestor de incidencias real del monorepo
(``services/api``): reutiliza la misma función de repositorio
(``database.get_incidents_table``) y los mismos modelos Pydantic
(``models.Incident``, ``models.IncidentListResponse``) que sirven los
endpoints GET /api/incidents y GET /api/incidents/{id}. No existe un
dataset paralelo: es la misma tabla TinyDB que expone la API.

Solo lectura
------------
La tool únicamente consulta: nunca crea, actualiza ni elimina tickets.
No hay ninguna rama de escritura en este módulo (a diferencia del
gestor, que sí expone POST/PATCH — no se tocan aquí).

Timeout y fallback
------------------
``INCIDENT_TIMEOUT_SECONDS`` (default 4.0 s) limita la operación de
lectura; si el gestor no responde a tiempo, falla o el ticket no existe,
el resultado se entrega con ``resolved=False`` y ``error`` poblado para
que el agente responda algo honesto ("no pude confirmar el estado de ese
ticket ahora mismo") en lugar de inventar un estado.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

from services.rag_agent.tools._timeout import run_with_timeout

# ── Identificadores de ticket: formato libre o UUIDv4 ---------------------
# Se aceptan ambos porque la API asigna uuid4 pero los datos de prueba de
# TrackFlow usan códigos legibles como "INC-..." (ver incidents.CSV).
_TICKET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# Raíz del monorepo: services/rag_agent/tools/incident_lookup.py -> parents[3].
_REPO_ROOT = Path(__file__).resolve().parents[3]

# Timeout explícito y numérico de la llamada (segundos). El grafo nunca queda
# colgado si el gestor de incidencias no responde: se aplica con SIGALRM sobre
# el hilo principal (el grafo se ejecuta en el hilo principal, sin asyncio).
INCIDENT_TIMEOUT_SECONDS: float = float(os.getenv("INCIDENT_LOOKUP_TIMEOUT", "4.0"))


class IncidentQuery(BaseModel):
    """Entrada tipada de la tool (contrato de entrada)."""

    ticket_id: str | None = Field(default=None, description="ID de ticket (o filtro exacto).")
    status: str | None = Field(default=None, description="Filtro: estado del incidente.")
    category: str | None = Field(default=None, description="Filtro: categoría del incidente.")
    origin: str | None = Field(default=None, description="Filtro: origen del incidente.")
    branch: str | None = Field(default=None, description="Filtro: sucursal del incidente.")

    @field_validator("ticket_id")
    @classmethod
    def _validate_ticket_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("ticket_id no puede ser vacío")
        if not (_UUID_RE.fullmatch(value) or _TICKET_ID_RE.fullmatch(value)):
            raise ValueError("ticket_id tiene un formato no válido")
        return value

    @field_validator("status", "category", "origin", "branch")
    @classmethod
    def _validate_filters(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip()


class IncidentLookupResult(BaseModel):
    """Salida tipada de la tool (contrato de salida).

    Mismos campos que models.Incident / IncidentListResponse + metadatos
    de la consulta (``resolved``/``error``) para que el nodo del grafo
    decida el fallback.
    """

    ticket_id: str | None = None
    status: str | None = None
    category: str | None = None
    origin: str | None = None
    branch: str | None = None
    title: str | None = None
    description: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # metadatos de la consulta (no son campos del gestionador)
    resolved: bool = False
    error: str | None = None
    matched_filters: list[str] = Field(default_factory=list)


def _normalise_filters(query: IncidentQuery) -> tuple[dict[str, Any], list[str]]:
    """Devuelve el filtro normalizado y la lista de campos aplicados."""
    filters: dict[str, Any] = {}
    for field in ("status", "category", "origin", "branch"):
        value = getattr(query, field)
        if value:
            filters[field] = value
    return filters, sorted(filters.keys())


def _fallback(message: str) -> IncidentLookupResult:
    """Resultado honesto: el grafo no sabe el estado, no lo inventa."""
    return IncidentLookupResult(resolved=False, error=message)


def _incident_to_result(
    incident: Any,
    matched_filters: list[str],
    requested_ticket: str | None,
) -> IncidentLookupResult:
    """Convierte un incidente (models.Incident / dict) a IncidentLookupResult.

    Lee solo: construye el resultado con los mismos campos que expone la
    API de incidencias.
    """
    data = incident.model_dump(mode="json") if hasattr(incident, "model_dump") else dict(incident)
    return IncidentLookupResult(
        ticket_id=requested_ticket or data.get("id"),
        status=data.get("status"),
        category=data.get("category"),
        origin=data.get("origin"),
        branch=data.get("branch"),
        title=data.get("title"),
        description=data.get("description"),
        created_at=str(data.get("created_at")) if data.get("created_at") else None,
        updated_at=str(data.get("updated_at")) if data.get("updated_at") else None,
        resolved=True,
        matched_filters=matched_filters,
    )


def _import_incident_manager() -> tuple[Callable[[], tuple[Any, Any]], type, type]:
    """Importa en-proceso el gestor de incidencias real de services/api.

    Devuelve (get_incidents_table, Incident, IncidentListResponse).
    Fallo de import -> ImportError legible (nunca datos simulados).
    """
    try:
        api_dir = _REPO_ROOT / "services" / "api"
        if str(api_dir) not in sys.path:
            sys.path.insert(0, str(api_dir))
        from database import get_incidents_table  # type: ignore[import-not-found]
        from models import Incident, IncidentListResponse  # type: ignore[import-not-found]
        return get_incidents_table, Incident, IncidentListResponse
    except Exception as exc:  # pragma: no cover - defensivo
        raise ImportError(
            "No se pudo importar el gestor de incidencias de services/api: "
            f"{exc}"
        ) from exc


class _QueryReader:
    """Sobrecarga el _deadline del módulo — facilita tests sin fabricar datos."""

    def __init__(
        self,
        get_incidents_table: Callable[[], tuple[Any, Any]],
        Incident: type,
        IncidentListResponse: type,
    ):
        self.get_incidents_table = get_incidents_table
        self.Incident = Incident
        self.IncidentListResponse = IncidentListResponse

    def read(self, query: IncidentQuery) -> IncidentLookupResult:
        """Lee de la tabla de incidencias (solo lectura)."""
        query = IncidentQuery.model_validate(query.model_dump())
        filters, matched = _normalise_filters(query)
        requested = query.ticket_id

        db, table = self.get_incidents_table()
        try:
            documents = table.all()
        finally:
            db.close()

        # 1. Consulta por id exacto (GET /api/incidents/{id}).
        if requested:
            for doc in documents:
                if str(doc.get("id")) == requested:
                    return _incident_to_result(doc, matched, requested)
            return _fallback(f"el ticket '{requested}' no existe")

        # 2. Consulta con filtros (GET /api/incidents?status=&...).
        if filters:
            matches = [
                doc
                for doc in documents
                if all(doc.get(f) == v for f, v in filters.items())
            ]
            if not matches:
                joined = ", ".join(f"{k}={v}" for k, v in filters.items())
                return _fallback(f"no hay incidencias con: {joined}")
            if len(matches) > 1:
                joined = ", ".join(f"{k}={v}" for k, v in filters.items())
                return _fallback(f"hay {len(matches)} incidencias con: {joined} — especifica un ticket_id")
            return _incident_to_result(matches[0], matched, requested)

        # 3. Sin ticket_id ni filtros: toda la lista (GET /api/incidents).
        return IncidentLookupResult(
            ticket_id=None,
            title=None,
            description=f"{len(documents)} incidencias registradas",
            resolved=True,
            matched_filters=[],
        )


# Sistema de lectura del módulo (import lazy en primera llamada).
_reader: _QueryReader | None = None


def _get_reader() -> _QueryReader:
    global _reader
    if _reader is None:
        get_incidents_table, Incident, IncidentListResponse = _import_incident_manager()
        _reader = _QueryReader(get_incidents_table, Incident, IncidentListResponse)
    return _reader


def reset_reader() -> None:
    """Reinicia el lector perezoso (útil en tests)."""
    global _reader
    _reader = None


def lookup_incidents(
    query: IncidentQuery | dict[str, Any] | str,
    *,
    timeout: float | None = None,
) -> IncidentLookupResult:
    """Forma canónica de la tool: consulta tipada con timeout explícito.

    Acepta un IncidentQuery, un dict con sus campos o un ticket_id suelto.
    Aplica el timeout numérico (default INCIDENT_TIMEOUT_SECONDS) y
    convierte cualquier fallo a un IncidentLookupResult no resuelto.

    Solo lectura: esta función no escribe nada.
    """
    if isinstance(query, str):
        query = IncidentQuery(ticket_id=query)
    try:
        if isinstance(query, dict):
            query = IncidentQuery.model_validate(query)
        timeout_s = timeout if timeout is not None else INCIDENT_TIMEOUT_SECONDS
        return run_with_timeout(lambda: _get_reader().read(query), timeout_s)
    except ValidationError as exc:
        return _fallback(f"entrada inválida: {exc.errors()[0]['msg']}")
    except TimeoutError:
        return _fallback(
            f"el gestor de incidencias no respondió en {timeout_s:.1f}s — no pude "
            "confirmar el estado de ese ticket ahora mismo"
        )
    except ImportError as exc:
        return _fallback(str(exc))
    except Exception as exc:  # pragma: no cover - defensivo
        return _fallback(f"fallo inesperado consultando incidencias: {exc}")