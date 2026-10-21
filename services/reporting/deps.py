"""Dependencias de autenticación para los endpoints de reporting.

PIPELINE_DESIGN.md Fase 5 §5.2:

- ``get_current_user``: reutiliza el mismo JWT Bearer de ``services/api/auth.py``
  (``get_current_user`` con ``require_roles``).
- ``reporting_run_token``: dependencia para ``POST /reporting/pipeline-runs``
  que verifica el token de ejecución (reporting-run-token).
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

# Reutilizar la misma autenticación JWT del API existente.
# Sigue el mismo patrón de services/api/auth.py.
from services.api.auth import get_current_user, require_roles

# Token opcional para autorizar corridas manuales (Brifing-pipeline.md §7).
# Si REPORTING_RUN_TOKEN no está configurada, se omite la verificación
# (modo desarrollo).
REPORTING_RUN_TOKEN = os.getenv("REPORTING_RUN_TOKEN", "")


async def verify_reporting_run_token(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Verifica el token de ejecución para POST /reporting/pipeline-runs.

    Si ``REPORTING_RUN_TOKEN`` no está configurado en el entorno, la
    verificación se salta (modo desarrollo/local).
    """
    if not REPORTING_RUN_TOKEN:
        # Modo desarrollo: no se requiere token
        return

    if authorization is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization scheme must be Bearer",
        )

    if token != REPORTING_RUN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid reporting run token",
        )