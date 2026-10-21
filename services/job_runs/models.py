"""Modelos de datos para la tabla job_runs.

Define el modelo SQLModel de base de datos y los modelos Pydantic
de request/response para el control de ejecuciones de procesos en
segundo plano (DEV-53).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import Column, Date, DateTime, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlmodel import Field as SQLField, SQLModel


class JobRunStatus(str, Enum):
    """Máquina de estados de una ejecución.

    Transiciones válidas:
        pending → processing → completed
                             ↘ failed
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRun(SQLModel, table=True):
    """Control de ejecuciones de procesos en segundo plano (DEV-53).

    Cada fila representa un intento de ejecución de un job para una
    fecha objetivo concreta (target_date).

    Capa: orquestación nocturna (No confundir con ``reporting.pipeline_runs``).
    """

    __tablename__ = "job_runs"  # type: ignore[assignment]

    __table_args__ = (
        Index("idx_job_runs_name_date", "job_name", "target_date"),
        Index("idx_job_runs_target_date", "target_date", postgresql_using="btree"),
        Index("idx_job_runs_status", "status"),
    )

    id: str | None = SQLField(
        default=None,
        sa_column=Column(
            UUID(as_uuid=False),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
            comment="Identificador único de la ejecución",
        ),
    )
    job_name: str = SQLField(
        sa_column=Column(
            Text,
            nullable=False,
            comment="Nombre del job (ej. nightly_export). Identifica el proceso orquestado.",
        ),
    )
    target_date: date = SQLField(
        sa_column=Column(
            Date,
            nullable=False,
            comment="Fecha objetivo del procesamiento en UTC. Clave de idempotencia con job_name.",
        ),
    )
    status: str = SQLField(
        default="pending",
        sa_column=Column(
            Text,
            nullable=False,
            server_default=text("'pending'"),
            comment="Estado: pending | processing | completed | failed",
        ),
    )
    started_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=True,
            comment="Timestamp UTC del paso a processing (inicio real)",
        ),
    )
    finished_at: datetime | None = SQLField(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=True,
            comment="Timestamp UTC de finalización (completed o failed)",
        ),
    )
    error_message: str | None = SQLField(
        default=None,
        sa_column=Column(
            Text,
            nullable=True,
            comment="Mensaje de excepción si status = failed",
        ),
    )
    created_at: datetime = SQLField(
        default_factory=_utcnow,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            comment="Timestamp UTC de creación del registro",
        ),
    )


# ── Modelos Pydantic para uso en API ──────────────────────────────


class JobRunCreate(BaseModel):
    """Datos para crear un nuevo registro de ejecución en estado pending."""

    job_name: str = Field(..., description="Nombre del job")
    target_date: str = Field(..., description="Fecha objetivo en formato YYYY-MM-DD (UTC)")
    status: str = Field(default="pending", description="Estado inicial")


class JobRunResponse(BaseModel):
    """Respuesta con los datos de una ejecución."""

    id: str
    job_name: str
    target_date: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    created_at: str

    @classmethod
    def from_orm_row(cls, row: dict[str, Any]) -> JobRunResponse:
        """Construye una respuesta desde una fila de base de datos (dict)."""
        return cls(
            id=str(row["id"]),
            job_name=row["job_name"],
            target_date=str(row["target_date"]),
            status=row["status"],
            started_at=str(row.get("started_at")) if row.get("started_at") else None,
            finished_at=str(row.get("finished_at")) if row.get("finished_at") else None,
            error_message=row.get("error_message"),
            created_at=str(row["created_at"]),
        )