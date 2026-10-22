"""Modelo SQLModel para la tabla de registro de ejecuciones (job_runs).

Usa el mismo patrón que ``TelemetryEventRecord`` en
``services/api/models.py``: SQLModel con metadatos para PostgreSQL,
con UUID nativo generado por la base de datos.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field, SQLModel


class JobRunStatus(str, Enum):
    """Estados posibles de un job_run.

    - ``pending``: creado, a la espera de ser ejecutado.
    - ``processing``: en ejecución activa.
    - ``success``: finalizó correctamente.
    - ``failed``: finalizó con error.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


# Estados terminales — una vez en success o failed no se cambian.
TERMINAL_STATUSES = frozenset({JobRunStatus.SUCCESS, JobRunStatus.FAILED})


def utcnow() -> datetime:
    """Devuelve el instante actual como datetime timezone-aware UTC."""
    return datetime.now(timezone.utc)


class JobRun(SQLModel, table=True):
    """Registro de una ejecución de pipeline.

    Cada fila representa una corrida: quién la inició, qué pipeline,
    cuándo empezó/terminó, cuántos eventos procesó, y si hubo error.

    La clave primaria es un ``UUID`` generado por PostgreSQL, no por la
    aplicación — así se garantiza unicidad incluso si varios workers
    insertan simultáneamente.
    """

    __tablename__ = "job_runs"  # type: ignore[assignment]

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(
            UUID(as_uuid=False),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
        ),
    )
    pipeline: str = Field(
        ...,
        sa_column=Column(Text, nullable=False, index=True),
        description="Nombre del pipeline (ej. weekly_warehouse_client_performance)",
    )
    status: JobRunStatus = Field(
        default=JobRunStatus.PENDING,
        sa_column=Column(Text, nullable=False, index=True),
    )
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    date_from: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="ISO date del inicio de la ventana procesada (inclusivo)",
    )
    date_to: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="ISO date del fin de la ventana procesada (exclusivo)",
    )
    processed_events: int = Field(default=0, ge=0)
    rows_upserted: int = Field(default=0, ge=0)
    error_message: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    created_at: Optional[datetime] = Field(
        default_factory=utcnow,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )


# Índice compuesto: (pipeline, status) para acelerar la comprobación de
# has_processing_lock y has_completed_for_date.
Index(
    "idx_job_runs_pipeline_status",
    JobRun.pipeline,
    JobRun.status,
)

# Índice compuesto: (pipeline, date_from) para acelerar la comprobación
# de has_completed_for_date.
Index(
    "idx_job_runs_pipeline_date",
    JobRun.pipeline,
    JobRun.date_from,
)


# ── Helpers de estado ───────────────────────────────────────────────


def is_terminal(status: JobRunStatus) -> bool:
    """``True`` si el estado es ``success`` o ``failed`` (terminal)."""
    return status in TERMINAL_STATUSES