from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from tinydb import Query as TinyQuery

from auth import get_current_user, require_roles
from cache import (
    CACHE_KEY_INCIDENTS_LIST,
    CACHE_KEY_INCIDENTS_SUMMARY,
    incident_cache,
)
from database import get_incidents_table
from models import (
    Incident,
    IncidentBranch,
    IncidentCategory,
    IncidentCreate,
    IncidentListResponse,
    IncidentOrigin,
    IncidentStatus,
    IncidentStatusUpdate,
    IncidentSummaryResponse,
)


router = APIRouter(
    prefix="/api/incidents",
    tags=["incidents"],
    dependencies=[Depends(get_current_user)],
)
require_editor = Depends(require_roles("admin", "manager"))

VALID_TRANSITIONS = {
    IncidentStatus.OPEN: {IncidentStatus.IN_PROGRESS, IncidentStatus.DISCARDED},
    IncidentStatus.IN_PROGRESS: {IncidentStatus.RESOLVED, IncidentStatus.DISCARDED},
    IncidentStatus.RESOLVED: set(),
    IncidentStatus.DISCARDED: set(),
}


def _invalidate_incident_caches() -> None:
    """Invalida todas las claves de incidencias al escribir."""
    incident_cache.invalidate_prefix(CACHE_KEY_INCIDENTS_LIST)
    incident_cache.invalidate(CACHE_KEY_INCIDENTS_SUMMARY)


def _build_list_cache_key(
    status: IncidentStatus | None,
    origin: IncidentOrigin | None,
    branch: IncidentBranch | None,
    category: IncidentCategory | None,
) -> str:
    parts = [CACHE_KEY_INCIDENTS_LIST]
    for name, value in [
        ("status", status),
        ("origin", origin),
        ("branch", branch),
        ("category", category),
    ]:
        parts.append(f"{name}={value.value if value else ''}")
    return "|".join(parts)


@router.post("", response_model=Incident, status_code=201, dependencies=[require_editor])
def create_incident(payload: IncidentCreate) -> Incident:
    incident = Incident(**payload.model_dump())

    db, incidents_table = get_incidents_table()
    try:
        incidents_table.insert(incident.model_dump(mode="json"))
        _invalidate_incident_caches()
        return incident
    finally:
        db.close()


@router.get("", response_model=list[IncidentListResponse])
def list_incidents(
    status: IncidentStatus | None = Query(default=None),
    origin: IncidentOrigin | None = Query(default=None),
    branch: IncidentBranch | None = Query(default=None),
    category: IncidentCategory | None = Query(default=None),
) -> list[IncidentListResponse]:
    """
    Cachea listados hasta 60 s porque:
    - Coste: O(n) con filtrado O(n) en memoria y re-hidratación Pydantic.
    - Frecuencia: alta (dashboard, recargas de página).
    - Estabilidad: los datos cambian solo con escrituras (crear o
      cambiar estado), que son poco frecuentes.
    - Los datos son globales/compartidos, no personalizados por usuario.
    """
    cache_key = _build_list_cache_key(status, origin, branch, category)
    hit, cached_result = incident_cache.get(cache_key)
    if hit:
        return cached_result

    filters = {
        "status": status.value if status else None,
        "origin": origin.value if origin else None,
        "branch": branch.value if branch else None,
        "category": category.value if category else None,
    }

    db, incidents_table = get_incidents_table()
    try:
        documents = incidents_table.all()
        result = [
            IncidentListResponse.model_validate(dict(document))
            for document in documents
            if all(
                expected is None or document.get(field) == expected
                for field, expected in filters.items()
            )
        ]
        incident_cache.set(cache_key, result)
        return result
    finally:
        db.close()


@router.get("/summary", response_model=IncidentSummaryResponse)
def summarize_incidents() -> IncidentSummaryResponse:
    """
    Cachea el sumario hasta 60 s porque:
    - Coste: O(n · m) (n=documentos, m=dimensiones) con bucles anidados
      y contadores. Es el endpoint más costoso de incidencias.
    - Frecuencia: alta (dashboard principal se refresca a menudo).
    - Estabilidad: igual que list_incidents.
    - Los datos son agregados compartidos, no hay riesgo de fuga.
    """
    hit, cached_result = incident_cache.get(CACHE_KEY_INCIDENTS_SUMMARY)
    if hit:
        return cached_result

    dimensions = {
        "status": IncidentStatus,
        "category": IncidentCategory,
        "origin": IncidentOrigin,
        "branch": IncidentBranch,
    }

    db, incidents_table = get_incidents_table()
    try:
        documents = incidents_table.all()
        summary: dict[str, Any] = {"total": len(documents)}
        for field, enum_type in dimensions.items():
            totals = {value.value: 0 for value in enum_type}
            for document in documents:
                value = document.get(field)
                if value in totals:
                    totals[value] += 1
            summary[field] = totals
        result = IncidentSummaryResponse.model_validate(summary)
        incident_cache.set(CACHE_KEY_INCIDENTS_SUMMARY, result)
        return result
    finally:
        db.close()


@router.get("/{incident_id}", response_model=Incident)
def get_incident(incident_id: str) -> Incident:
    db, incidents_table = get_incidents_table()
    try:
        document = incidents_table.get(TinyQuery().id == incident_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return Incident.model_validate(dict(document))
    finally:
        db.close()


@router.patch(
    "/{incident_id}/status",
    response_model=Incident,
    dependencies=[require_editor],
)
def update_incident_status(
    incident_id: str,
    payload: IncidentStatusUpdate,
) -> Incident:
    db, incidents_table = get_incidents_table()
    try:
        document = incidents_table.get(TinyQuery().id == incident_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Incident not found")

        current_status = IncidentStatus(document["status"])
        next_status = payload.status
        if next_status not in VALID_TRANSITIONS[current_status]:
            raise HTTPException(
                status_code=400,
                detail=(
                    {
                        "field": "status",
                        "message": (
                            f"No se permite cambiar de {current_status.value} "
                            f"a {next_status.value}."
                        ),
                    }
                ),
            )

        incidents_table.update(
            {
                "status": next_status.value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            doc_ids=[document.doc_id],
        )
        _invalidate_incident_caches()
        updated_document = incidents_table.get(doc_id=document.doc_id)
        return Incident.model_validate(dict(updated_document))
    finally:
        db.close()