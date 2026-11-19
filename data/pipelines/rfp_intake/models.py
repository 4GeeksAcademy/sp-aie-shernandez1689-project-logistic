"""Modelos SQLModel del pipeline de intake de RFPs (Hito 9, Parte 1).

Reflejan 1:1 las tablas de ``migrations/001_rfp_intake.sql`` (esquema
``rfp_intake`` en PostgreSQL/Supabase):

- ``Ticket``            → ``rfp_intake.tickets``
- ``RFPMetadata``       → ``rfp_intake.rfp_metadata``
- ``FinalDocument``     → ``rfp_intake.final_documents`` (Parte 3)
- ``DepartmentSection`` → ``rfp_intake.department_sections`` (``key_aspects``,
  ``source_excerpts``, ``open_questions``; Parte 2: ``drafts``,
  ``evaluation_results``, ``generation_status``, ``iterations``)

Viven en ``data/pipelines/`` (no en ``services/api/models.py``) para respetar
la regla de capas: el pipeline no importa nada de ``services/``.

Las columnas JSON usan ``JSONB`` en PostgreSQL y ``JSON`` genérico en otros
dialectos (solo para tests con SQLite en memoria).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

SCHEMA = "rfp_intake"

_JSON = JSON().with_variant(JSONB(), "postgresql")


def _json_column(default: Any) -> Any:
    return Field(default_factory=lambda: type(default)(), sa_column=Column(_JSON, nullable=False))


def _ts_column(nullable: bool = False) -> Any:
    return Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=nullable),
    )


class Ticket(SQLModel, table=True):
    """Estado del documento recibido (BRIEFING §2.3)."""

    __tablename__ = "tickets"  # type: ignore
    __table_args__ = {"schema": SCHEMA}

    ticket_id: str = Field(primary_key=True)
    rfp_id: str
    status: str
    raw_pdf_path: str
    reason: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # Intentos de análisis iniciados (lo usa el barrido de tickets atascados).
    attempts: int = 0
    created_at: datetime | None = _ts_column()
    updated_at: datetime | None = _ts_column()


class RFPMetadata(SQLModel, table=True):
    """Metadatos extraídos de la RFP: cliente, país/moneda, volumen, legibilidad."""

    __tablename__ = "rfp_metadata"  # type: ignore
    __table_args__ = {"schema": SCHEMA}

    id: str = Field(primary_key=True)
    ticket_id: str = Field(foreign_key=f"{SCHEMA}.tickets.ticket_id", index=True)
    rfp_id: str
    doc_type: str | None = None
    client_name: str | None = None
    client_country: str | None = None
    services_requested: list[str] = _json_column([])
    monthly_volume: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    deadline: str | None = None
    budget_range: str | None = None
    departments_needed: list[str] = _json_column([])
    currency: str | None = None
    num_pages: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    markdown_path: str | None = None
    converter: str | None = None
    estimated_tokens: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    processing_cost: str | None = None
    readability: dict[str, Any] = _json_column({})
    sales_summary: dict[str, Any] | None = Field(default=None, sa_column=Column(_JSON, nullable=True))
    handoff: dict[str, Any] | None = Field(default=None, sa_column=Column(_JSON, nullable=True))
    # Parte 2 → Parte 3: secciones con su último borrador + EvaluationResult.
    proposal_handoff: dict[str, Any] | None = Field(
        default=None, sa_column=Column(_JSON, nullable=True)
    )
    # Parte 3: estado de la aprobación (departamentos, conflictos, arbitrajes, trace).
    approval: dict[str, Any] | None = Field(default=None, sa_column=Column(_JSON, nullable=True))
    created_at: datetime | None = _ts_column()
    updated_at: datetime | None = _ts_column()


class DepartmentSection(SQLModel, table=True):
    """Sección por departamento activo (Parte 1: ``key_aspects``; Parte 2: borradores)."""

    __tablename__ = "department_sections"  # type: ignore
    __table_args__ = {"schema": SCHEMA}

    id: str = Field(primary_key=True)
    ticket_id: str = Field(foreign_key=f"{SCHEMA}.tickets.ticket_id", index=True)
    rfp_id: str
    department_id: str
    department_label: str | None = None
    department_owner: str | None = None
    key_aspects: list[str] = _json_column([])
    source_excerpts: list[str] = _json_column([])
    open_questions: list[str] = _json_column([])
    # Parte 2: último borrador + historial (un borrador y un EvaluationResult
    # por iteración del ciclo generador-evaluador).
    draft_content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    drafts: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(_JSON, nullable=True))
    evaluation_results: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(_JSON, nullable=True)
    )
    generation_status: str | None = None
    iterations: int = 0
    # Parte 3: decisión del responsable (o del árbitro) y su historial.
    approval_status: str = "pendiente"
    approver: str | None = None
    approved_at: datetime | None = _ts_column(nullable=True)
    approval_comments: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    approval_rounds: int = 0
    approval_history: list[dict[str, Any]] | None = Field(
        default=None, sa_column=Column(_JSON, nullable=True)
    )
    created_at: datetime | None = _ts_column()
    updated_at: datetime | None = _ts_column()


class FinalDocument(SQLModel, table=True):
    """Documento final consolidado con las secciones aprobadas (BRIEFING §2.3)."""

    __tablename__ = "final_documents"  # type: ignore
    __table_args__ = {"schema": SCHEMA}

    ticket_id: str = Field(primary_key=True, foreign_key=f"{SCHEMA}.tickets.ticket_id")
    rfp_id: str
    currency: str | None = None
    sections: list[dict[str, Any]] = _json_column([])
    content: str = Field(sa_column=Column(Text, nullable=False))
    generated_at: datetime | None = _ts_column()
    created_at: datetime | None = _ts_column()
    updated_at: datetime | None = _ts_column()


__all__ = ["SCHEMA", "Ticket", "RFPMetadata", "DepartmentSection", "FinalDocument"]
