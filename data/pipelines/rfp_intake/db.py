"""Acceso a datos del pipeline de intake de RFPs (Hito 9, Parte 1).

Mismo patrón que ``data/pipelines/weekly_warehouse_client_performance/db.py``
y que ``services/api/db_telemetry.py``:

- Con ``DATABASE_URL`` configurada: engine SQLAlchemy/SQLModel hacia
  PostgreSQL (SSLMODE=require), donde se persisten Ticket, RFP metadata y
  DepartmentSection (BRIEFING §2.3 — "Persistir en PostgreSQL vía capa
  SQLModel").
- Sin ``DATABASE_URL`` (modo desarrollo/validación): fallback local que
  escribe el estado en ``data/eval/rfp_intake/`` como JSON Lines. Ese
  fallback **no sustituye** al contrato de producción; solo permite
  ejecutar y validar el flujo de punta a punta sin credenciales.

Regla de capas (Brifing-pipeline.md §5): este módulo pertenece al paquete
de pipeline en ``data/`` y no importa nada de ``services/``.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from sqlalchemy.engine import Engine, create_engine, make_url

from .config import DEFAULT_EVAL_RFP_DIR, REPO_ROOT

# Mismo comportamiento que services/api/db_telemetry.py: carga el .env de la
# raíz del monorepo y el de services/api si existen (desarrollo).
for _env_candidate in (REPO_ROOT / ".env", REPO_ROOT / "services" / "api" / ".env"):
    if _env_candidate.exists():
        load_dotenv(_env_candidate, override=False)

# ── Modo local: rutas de archivos de estado ─────────────────────────────────

TICKETS_PATH = DEFAULT_EVAL_RFP_DIR / "tickets.jsonl"
RFP_METADATA_PATH = DEFAULT_EVAL_RFP_DIR / "rfp_metadata.jsonl"
DEPARTMENT_SECTIONS_PATH = DEFAULT_EVAL_RFP_DIR / "department_sections.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_postgres_engine(database_url: str) -> Engine:
    """Crea un engine PostgreSQL a partir de una URL de conexión."""
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("DATABASE_URL must be a PostgreSQL connection string")
    url = url.set(drivername="postgresql+psycopg")
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True)


def get_engine() -> Engine | None:
    """Devuelve el engine a PostgreSQL si ``DATABASE_URL`` está configurada.

    Retorna ``None`` en modo local (sin base de datos).
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None
    return _create_postgres_engine(database_url)


def is_db_available() -> bool:
    """True si el pipeline puede operar en modo base de datos."""
    return bool(os.getenv("DATABASE_URL"))


@contextmanager
def session_scope() -> Iterator[Any]:
    """Context manager de sesión SQLModel; ``None`` en modo local."""
    engine = get_engine()
    if engine is None:
        yield None
        return
    from sqlmodel import Session

    with Session(engine) as session:
        yield session


# ── Modo local: helpers JSONL ───────────────────────────────────────────────


def ensure_local_dirs() -> None:
    """Crea el directorio de estado local de tickets si no existe."""
    DEFAULT_EVAL_RFP_DIR.mkdir(parents=True, exist_ok=True)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Añade un registro JSON a un archivo JSON Lines (modo local)."""
    ensure_local_dirs()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Lee todos los registros de un archivo JSON Lines (modo local)."""
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ── Parte 1: persistencia de Ticket/RFP metadata/DepartmentSection ──────────


def save_ticket(ticket: dict[str, Any]) -> None:
    """Persiste un Ticket.

    Con ``DATABASE_URL`` inserta vía SQLModel contra la tabla
    ``rfp_intake.tickets``; en modo local lo escribe en
    ``data/eval/rfp_intake/tickets.jsonl``.
    """
    ticket = dict(ticket)
    ticket.setdefault("created_at", _now_iso())
    ticket.setdefault("updated_at", ticket["created_at"])

    if is_db_available():
        _save_ticket_db(ticket)
    else:
        _append_jsonl(TICKETS_PATH, ticket)


def save_rfp_metadata(metadata: dict[str, Any]) -> None:
    """Persiste los metadatos de la RFP (client, country, volumes, legibilidad)."""
    metadata = dict(metadata)
    metadata.setdefault("created_at", _now_iso())
    if is_db_available():
        _save_rfp_metadata_db(metadata)
    else:
        _append_jsonl(RFP_METADATA_PATH, metadata)


def save_department_section(section: dict[str, Any]) -> None:
    """Persiste una sección de departamento (key_aspects, etc.)."""
    section = dict(section)
    section.setdefault("created_at", _now_iso())
    if is_db_available():
        _save_department_section_db(section)
    else:
        _append_jsonl(DEPARTMENT_SECTIONS_PATH, section)


def list_tickets() -> list[dict[str, Any]]:
    """Lista los tickets conocidos (ordenados por actualización, más reciente primero)."""
    if is_db_available():
        return _list_tickets_db()
    records = read_jsonl(TICKETS_PATH)
    # devolver copia sin mutar el almacén
    return sorted(
        [dict(r) for r in records],
        key=lambda r: r.get("updated_at") or r.get("created_at") or "",
        reverse=True,
    )


def get_ticket(ticket_id: str) -> dict[str, Any] | None:
    """Obtiene un ticket por su id."""
    for ticket in list_tickets():
        if ticket.get("ticket_id") == ticket_id:
            return ticket
    return None


def get_rfp_metadata(ticket_id: str) -> dict[str, Any] | None:
    """Obtiene los metadatos de la RFP asociados a un ticket."""
    for record in read_jsonl(RFP_METADATA_PATH):
        if record.get("ticket_id") == ticket_id:
            return record
    return None


def get_department_sections(ticket_id: str) -> list[dict[str, Any]]:
    """Obtiene las secciones de departamento de un ticket."""
    sections = [
        dict(r)
        for r in read_jsonl(DEPARTMENT_SECTIONS_PATH)
        if r.get("ticket_id") == ticket_id
    ]
    return sorted(sections, key=lambda r: r.get("department_id", ""))


# ── Parte DB: SQLModel upsert (solo si DATABASE_URL está configurada) ───────


def _upsert_db(instance: Any) -> None:
    """Inserta o actualiza una instancia SQLModel en la BD (modo DB)."""
    from sqlmodel import Session, select

    engine = get_engine()
    if engine is None:
        return
    with Session(engine) as session:
        existing = session.get(type(instance), instance.id)  # type: ignore[attr-defined]
        if existing is not None:
            for key, value in instance.model_dump().items():
                setattr(existing, key, value)
            session.add(existing)
        else:
            session.add(instance)
        session.commit()


def _save_ticket_db(ticket: dict[str, Any]) -> None:
    from models import Ticket  # services/api/models.py — definido en la rama

    _upsert_db(Ticket(**ticket))


def _save_rfp_metadata_db(metadata: dict[str, Any]) -> None:
    from models import RFPRecord  # type: ignore[import-not-found]

    _upsert_db(RFPRecord(**metadata))


def _save_department_section_db(section: dict[str, Any]) -> None:
    from models import DepartmentSectionModel  # type: ignore[import-not-found]

    _upsert_db(DepartmentSectionModel(**section))


def _list_tickets_db() -> list[dict[str, Any]]:
    from sqlmodel import Session, select

    from models import Ticket  # type: ignore[import-not-found]

    engine = get_engine()
    if engine is None:
        return []
    with Session(engine) as session:
        rows = session.exec(
            select(Ticket).order_by(Ticket.updated_at.desc())  # type: ignore[attr-defined]
        ).all()
        return [row.model_dump() for row in rows]


__all__ = [
    "get_engine",
    "is_db_available",
    "session_scope",
    "save_ticket",
    "save_rfp_metadata",
    "save_department_section",
    "list_tickets",
    "get_ticket",
    "get_rfp_metadata",
    "get_department_sections",
    "ensure_local_dirs",
    "TICKETS_PATH",
    "RFP_METADATA_PATH",
    "DEPARTMENT_SECTIONS_PATH",
]