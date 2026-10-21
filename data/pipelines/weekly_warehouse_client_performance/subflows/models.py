"""Modelos Pydantic para inputs/outputs explícitos de los subflows.

Cada subflow define su contrato de entrada y salida mediante modelos
Pydantic, garantizando que no haya dependencias implícitas entre etapas
ni variables globales compartidas.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Extract ──────────────────────────────────────────────────────────────────


class ExtractInput(BaseModel):
    """Input del subflow de extracción.

    Parameters
    ----------
    start : datetime
        Inicio de la ventana de extracción (UTC, inclusivo).
    end : datetime
        Fin de la ventana de extracción (UTC, exclusivo).
    """

    start: datetime = Field(..., description="Inicio de la ventana UTC (inclusivo)")
    end: datetime = Field(..., description="Fin de la ventana UTC (exclusivo)")


class ExtractOutput(BaseModel):
    """Output del subflow de extracción.

    Parameters
    ----------
    raw_events : list[dict]
        Lista de eventos extraídos con ``timestamp`` (ISO UTC str),
        ``event_type`` y ``tags`` (dict).
    event_count : int
        Número total de eventos extraídos.
    """

    raw_events: list[dict[str, Any]] = Field(
        ..., description="Eventos extraídos con timestamp, event_type y tags"
    )
    event_count: int = Field(..., ge=0, description="Número total de eventos extraídos")


# ── Transform ────────────────────────────────────────────────────────────────


class TransformInput(BaseModel):
    """Input del subflow de transformación.

    Parameters
    ----------
    raw_events : list[dict]
        Lista de eventos crudos devuelta por el subflow de extracción.
    """

    raw_events: list[dict[str, Any]] = Field(
        ..., description="Eventos crudos del subflow de extracción"
    )


class TransformOutput(BaseModel):
    """Output del subflow de transformación.

    Parameters
    ----------
    kpi_rows : list[dict]
        Filas de KPIs agregadas por (warehouse, client_id, week_start).
        Cada fila contiene los 5 KPIs del Briefing §5.
    row_count : int
        Número de filas KPI generadas.
    """

    kpi_rows: list[dict[str, Any]] = Field(
        ..., description="KPIs semanales agregados por almacén, cliente y semana"
    )
    row_count: int = Field(..., ge=0, description="Número de filas KPI generadas")


# ── Load ─────────────────────────────────────────────────────────────────────


class LoadInput(BaseModel):
    """Input del subflow de carga.

    Parameters
    ----------
    kpi_rows : list[dict]
        Filas de KPIs generadas por el subflow de transformación.
    """

    kpi_rows: list[dict[str, Any]] = Field(
        ..., description="Filas KPI a persistir (upsert en base de datos o JSONL local)"
    )


class LoadOutput(BaseModel):
    """Output del subflow de carga.

    Parameters
    ----------
    upserted_count : int
        Número de filas insertadas o actualizadas.
    """

    upserted_count: int = Field(
        ..., ge=0, description="Número de filas KPI persistidas (upserted)"
    )


# ── Export (opcional) ────────────────────────────────────────────────────────


class ExportInput(BaseModel):
    """Input del subflow opcional de exportación de snapshot.

    Parameters
    ----------
    kpi_rows : list[dict]
        Filas de KPIs a incluir en el snapshot.
    run_id : str
        UUID de la corrida del pipeline para trazabilidad.
    iso_week : str
        Etiqueta ISO de la semana (ej. "2026-W28").
    week_start : str | None
        Fecha ISO del lunes de la semana (opcional).
    """

    kpi_rows: list[dict[str, Any]] = Field(
        ..., description="KPIs calculados a incluir en el snapshot"
    )
    run_id: str = Field(..., description="UUID de la corrida del pipeline")
    iso_week: str = Field(..., description="Etiqueta ISO de la semana (ej. 2026-W28)")
    week_start: str | None = Field(None, description="Fecha ISO del lunes de la semana")


class ExportOutput(BaseModel):
    """Output del subflow opcional de exportación de snapshot.

    Parameters
    ----------
    path : str
        Ruta del archivo de snapshot generado.
    rows_count : int
        Número de filas incluidas en el snapshot.
    run_id : str
        UUID de la corrida asociada.
    """

    path: str = Field(..., description="Ruta del archivo snapshot exportado")
    rows_count: int = Field(..., ge=0, description="Número de filas en el snapshot")
    run_id: str = Field(..., description="UUID de la corrida del pipeline")