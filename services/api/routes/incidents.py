from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from tinydb import Query as TinyQuery

from auth import get_current_user, require_roles
from database import get_incidents_table
from models import (
    Incident,
    IncidentBranch,
    IncidentCategory,
    IncidentCreate,
    IncidentOrigin,
    IncidentStatus,
    IncidentStatusUpdate,
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


@router.post("", response_model=Incident, status_code=201, dependencies=[require_editor])
def create_incident(payload: Any = Body(default=None)) -> Incident:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"field": "body", "message": "Se esperaba un objeto JSON."},
        )

    try:
        incident_data = IncidentCreate.model_validate(payload)
        incident = Incident(**incident_data.model_dump())
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error

    db, incidents_table = get_incidents_table()
    try:
        incidents_table.insert(incident.model_dump(mode="json"))
        return incident
    finally:
        db.close()


@router.get("", response_model=list[Incident])
def list_incidents(
    status: IncidentStatus | None = Query(default=None),
    origin: IncidentOrigin | None = Query(default=None),
    branch: IncidentBranch | None = Query(default=None),
    category: IncidentCategory | None = Query(default=None),
) -> list[Incident]:
    filters = {
        "status": status.value if status else None,
        "origin": origin.value if origin else None,
        "branch": branch.value if branch else None,
        "category": category.value if category else None,
    }

    db, incidents_table = get_incidents_table()
    try:
        documents = incidents_table.all()
        return [
            Incident.model_validate(dict(document))
            for document in documents
            if all(
                expected is None or document.get(field) == expected
                for field, expected in filters.items()
            )
        ]
    finally:
        db.close()


@router.get("/summary")
def summarize_incidents() -> dict[str, Any]:
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
        return summary
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
    payload: Any = Body(default=None),
) -> Incident:
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=400,
            detail={"field": "body", "message": "Se esperaba un objeto JSON."},
        )

    try:
        status_update = IncidentStatusUpdate.model_validate(payload)
    except ValidationError as error:
        raise RequestValidationError(error.errors()) from error

    db, incidents_table = get_incidents_table()
    try:
        document = incidents_table.get(TinyQuery().id == incident_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Incident not found")

        current_status = IncidentStatus(document["status"])
        next_status = status_update.status
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
        updated_document = incidents_table.get(doc_id=document.doc_id)
        return Incident.model_validate(dict(updated_document))
    finally:
        db.close()