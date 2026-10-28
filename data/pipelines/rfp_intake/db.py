"""Acceso a datos del pipeline de intake de RFPs (Hito 9, Parte 1).

Ticket, metadatos de la RFP y ``DepartmentSection.key_aspects`` se persisten
**únicamente** en PostgreSQL (Supabase) vía SQLModel, en el esquema
``rfp_intake`` (``migrations/001_rfp_intake.sql``). No hay fallback a
archivos locales (JSONL/TinyDB): sin ``DATABASE_URL`` las operaciones de
persistencia fallan con ``DatabaseNotConfiguredError``. Para validar el
flujo sin base de datos usa ``process_rfp_pdf(..., persist=False)``.

Mismo patrón de engine que
``data/pipelines/weekly_warehouse_client_performance/db.py`` y
``services/api/db_telemetry.py`` (psycopg, SSLMODE=require).

Regla de capas (Brifing-pipeline.md §5): este módulo pertenece al paquete
de pipeline en ``data/`` y no importa nada de ``services/``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.engine import Engine, create_engine, make_url
from sqlmodel import Session, SQLModel, select

from .config import REPO_ROOT
from .models import DepartmentSection, RFPMetadata, Ticket

# Mismo comportamiento que services/api/db_telemetry.py: carga el .env de la
# raíz del monorepo y el de services/api si existen (desarrollo).
for _env_candidate in (REPO_ROOT / ".env", REPO_ROOT / "services" / "api" / ".env"):
    if _env_candidate.exists():
        load_dotenv(_env_candidate, override=False)

_ENGINE: Engine | None = None

_DATETIME_FIELDS = ("created_at", "updated_at", "approved_at")


class DatabaseNotConfiguredError(RuntimeError):
    """``DATABASE_URL`` no está configurada; no hay dónde persistir."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _create_postgres_engine(database_url: str) -> Engine:
    """Crea un engine PostgreSQL a partir de una URL de conexión."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
    url = url.set(drivername="postgresql+psycopg")
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True)


def is_db_available() -> bool:
    """True si hay engine configurado (``DATABASE_URL`` o ``set_engine``)."""
    return _ENGINE is not None or bool(os.getenv("DATABASE_URL"))


def get_engine() -> Engine:
    """Devuelve (y cachea) el engine a PostgreSQL.

    Lanza ``DatabaseNotConfiguredError`` si ``DATABASE_URL`` no está definida.
    """
    global _ENGINE
    if _ENGINE is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise DatabaseNotConfiguredError(
                "DATABASE_URL no configurada: el intake de RFPs persiste en "
                "PostgreSQL (Supabase). Usa persist=False para validar sin BD."
            )
        _ENGINE = _create_postgres_engine(database_url)
    return _ENGINE


def set_engine(engine: Engine | None) -> None:
    """Sustituye el engine cacheado (tests / scripts de reproceso)."""
    global _ENGINE
    _ENGINE = engine


def create_tables(engine: Engine | None = None) -> None:
    """Crea las tablas del esquema ``rfp_intake`` (solo tests/desarrollo).

    En Supabase el esquema se aplica con ``migrations/001_rfp_intake.sql``.
    """
    tables = [m.__table__ for m in (Ticket, RFPMetadata, DepartmentSection)]  # type: ignore[attr-defined]
    SQLModel.metadata.create_all(engine or get_engine(), tables=tables)


# ── Conversión dict ⇄ modelo ────────────────────────────────────────────────


def _coerce_datetimes(record: dict[str, Any]) -> dict[str, Any]:
    """Convierte timestamps ISO 8601 (str) a ``datetime`` para las columnas TIMESTAMPTZ."""
    out = dict(record)
    for key in _DATETIME_FIELDS:
        value = out.get(key)
        if isinstance(value, str):
            out[key] = datetime.fromisoformat(value)
    return out


def _to_dict(row: SQLModel) -> dict[str, Any]:
    """Serializa una fila a dict con timestamps en ISO 8601 (contrato de la API)."""
    data = row.model_dump()
    for key in _DATETIME_FIELDS:
        value = data.get(key)
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    return data


def _build(model: type[SQLModel], record: dict[str, Any]) -> SQLModel:
    """Instancia el modelo con timestamps por defecto, ignorando claves ajenas."""
    record = dict(record)
    record.setdefault("created_at", _now())
    record.setdefault("updated_at", record["created_at"])
    fields = set(model.model_fields)
    return model(**{k: v for k, v in _coerce_datetimes(record).items() if k in fields})


def _upsert(*instances: SQLModel) -> None:
    """Inserta o actualiza filas por clave primaria en una sola transacción."""
    with Session(get_engine()) as session:
        for instance in instances:
            session.merge(instance)
            # El ticket debe existir antes que sus hijos (FK).
            session.flush()
        session.commit()


# ── Escritura ───────────────────────────────────────────────────────────────


def save_ticket(ticket: dict[str, Any]) -> None:
    """Persiste un Ticket en ``rfp_intake.tickets``."""
    _upsert(_build(Ticket, ticket))


def save_rfp_metadata(metadata: dict[str, Any]) -> None:
    """Persiste los metadatos de la RFP en ``rfp_intake.rfp_metadata``."""
    _upsert(_build(RFPMetadata, metadata))


def save_department_section(section: dict[str, Any]) -> None:
    """Persiste una sección de departamento (``key_aspects``, etc.)."""
    _upsert(_build(DepartmentSection, section))


def save_intake(
    ticket: dict[str, Any],
    metadata: dict[str, Any] | None,
    sections: list[dict[str, Any]],
) -> None:
    """Persiste Ticket + metadatos + secciones de forma atómica."""
    instances = [_build(Ticket, ticket)]
    if metadata is not None:
        instances.append(_build(RFPMetadata, metadata))
    instances.extend(_build(DepartmentSection, s) for s in sections)
    _upsert(*instances)


# ── Lectura ─────────────────────────────────────────────────────────────────


def list_tickets() -> list[dict[str, Any]]:
    """Lista los tickets (más reciente primero)."""
    with Session(get_engine()) as session:
        rows = session.exec(
            select(Ticket).order_by(Ticket.updated_at.desc())  # type: ignore[union-attr]
        ).all()
        return [_to_dict(row) for row in rows]


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    """Obtiene un ticket por su id."""
    with Session(get_engine()) as session:
        row = session.get(Ticket, ticket_id)
        return _to_dict(row) if row is not None else None


def get_rfp_metadata(ticket_id: str) -> dict[str, Any] | None:
    """Obtiene los metadatos de la RFP asociados a un ticket."""
    with Session(get_engine()) as session:
        row = session.exec(
            select(RFPMetadata).where(RFPMetadata.ticket_id == ticket_id)
        ).first()
        return _to_dict(row) if row is not None else None


def get_department_sections(ticket_id: str) -> list[dict[str, Any]]:
    """Obtiene las secciones de departamento de un ticket (orden por departamento)."""
    with Session(get_engine()) as session:
        rows = session.exec(
            select(DepartmentSection)
            .where(DepartmentSection.ticket_id == ticket_id)
            .order_by(DepartmentSection.department_id)
        ).all()
        return [_to_dict(row) for row in rows]


__all__ = [
    "DatabaseNotConfiguredError",
    "get_engine",
    "set_engine",
    "create_tables",
    "is_db_available",
    "save_ticket",
    "save_rfp_metadata",
    "save_department_section",
    "save_intake",
    "list_tickets",
    "get_ticket",
    "get_rfp_metadata",
    "get_department_sections",
]
