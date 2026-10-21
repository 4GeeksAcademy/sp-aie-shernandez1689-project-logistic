"""Módulo de reporting — capa HTTP del pipeline de desempeño semanal.

PIPELINE_DESIGN.md Fase 5 §5.1-§5.2:

- ``router.py``: 3 endpoints que delegan en ``data/pipelines/``.
- ``deps.py``: dependencias de autenticación (JWT) y token de corrida.
- ``schemas.py``: modelos pydantic de request/response.

Regla estricta: ``services/reporting/`` importa únicamente desde
``data/pipelines/weekly_warehouse_client_performance/``. Nunca contiene
SQL de agregación ni lógica de transformación.
"""

from services.reporting.router import router

__all__ = ["router"]