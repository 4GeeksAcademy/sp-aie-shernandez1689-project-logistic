"""Router de consulta de estado de tareas Celery asíncronas.

``GET /tasks/{task_id}`` — consulta el estado de una tarea encolada vía Celery
(backend Redis) y devuelve ``{"task_id": ..., "status": ..., "result": ...}``.

Mapeo de estados
----------------
Los estados internos de Celery se traducen a una nomenclatura estable del API:

================  ==========
Celery            API
================  ==========
PENDING           pending
RECEIVED          pending
STARTED           started
SUCCESS           success
FAILURE           failure
RETRY             pending
REVOKED           failure
================  ==========
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from celery import AsyncResult
from fastapi import APIRouter, Depends

# ── Asegurar que raíz del monorepo y services/api están en sys.path ──
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from auth import get_current_user  # noqa: E402
from services.celery_app import celery_app  # noqa: E402


router = APIRouter(prefix="/tasks", tags=["tasks"])

# Mapeo de estados internos de Celery → nomenclatura pública del API
_STATUS_MAP: dict[str, str] = {
    "PENDING": "pending",
    "RECEIVED": "pending",
    "STARTED": "started",
    "SUCCESS": "success",
    "FAILURE": "failure",
    "RETRY": "pending",
    "REVOKED": "failure",
}


@router.get("/{task_id}")
def get_task_status(
    task_id: str,
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Consulta el estado de una tarea Celery por su UUID.

    Lee el resultado directamente del backend Redis de Celery
    (``AsyncResult``) sin pasar por la base de datos del proyecto.

    Args:
        task_id: UUID de la tarea Celery (devuelto por el endpoint que
                 encola la tarea).

    Returns:
        Diccionario con la estructura::

            {
                "task_id": "uuid-de-la-tarea",
                "status": "pending|started|success|failure",
                "result": <valor devuelto por la tarea si success,
                          o {"error": "..."} si failure>
            }

        El campo ``result`` solo aparece cuando ``status`` es
        ``success`` o ``failure``.
    """
    result = AsyncResult(task_id, app=celery_app)
    state = result.state
    api_status = _STATUS_MAP.get(state, "pending")

    response: dict[str, Any] = {
        "task_id": task_id,
        "status": api_status,
    }

    if api_status == "success":
        try:
            response["result"] = result.result
        except Exception:
            response["result"] = None
    elif api_status == "failure":
        try:
            exc = result.result
            response["result"] = {"error": str(exc)} if exc is not None else None
        except Exception:
            response["result"] = None

    return response