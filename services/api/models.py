from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, ClassVar, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveFloat, field_validator, model_validator
from sqlalchemy import Column, DateTime, Index, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlmodel import Field as SQLField, SQLModel

VALID_CATEGORIES: list[str] = [
    "carrier_last_mile",
    "carrier_international",
    "warehouse_supplies",
    "packaging_materials",
    "reverse_logistics",
    "fleet_maintenance",
    "it_and_wms_software",
    "cleaning_and_facilities",
]

VALID_STATUSES: list[str] = ["active", "suspended"]


class IncidentCategory(str, Enum):
    LOST_PARCEL = "lost_parcel"
    DELIVERY_FAILURE = "delivery_failure"
    INVENTORY_DISCREPANCY = "inventory_discrepancy"
    CARRIER_ISSUE = "carrier_issue"
    RETURNS_ISSUE = "returns_issue"
    WAREHOUSE_INCIDENT = "warehouse_incident"
    SYSTEM_FAILURE = "system_failure"
    CLIENT_COMPLAINT = "client_complaint"
    OTHER = "other"


class IncidentStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    DISCARDED = "discarded"


class IncidentOrigin(str, Enum):
    CUSTOMER = "customer"
    BRANCH = "branch"
    INTERNAL = "internal"


class IncidentBranch(str, Enum):
    CENTRAL = "central"
    LA_WAREHOUSE = "la_warehouse"
    LA_OFFICE = "la_office"
    ZARAGOZA_WAREHOUSE = "zaragoza_warehouse"
    ZARAGOZA_OFFICE = "zaragoza_office"


class IncidentFields(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1)
    category: IncidentCategory
    status: IncidentStatus = IncidentStatus.OPEN
    origin: IncidentOrigin
    branch: IncidentBranch

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Field must not be empty")
        return normalized

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Field must not be empty")
        return value


class Incident(IncidentFields):
    id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class IncidentCreate(IncidentFields):
    model_config = ConfigDict(extra="forbid")


class IncidentListResponse(IncidentFields):
    id: str
    created_at: datetime


class IncidentSummaryResponse(BaseModel):
    total: NonNegativeInt
    status: dict[IncidentStatus, NonNegativeInt]
    category: dict[IncidentCategory, NonNegativeInt]
    origin: dict[IncidentOrigin, NonNegativeInt]
    branch: dict[IncidentBranch, NonNegativeInt]


class IncidentStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: IncidentStatus


class SupplierStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class SupplierBase(BaseModel):
    name: str = Field(..., min_length=1)
    country: Literal["USA", "Spain"]
    categories: list[str] = Field(..., min_length=1)
    rate_per_shipment: PositiveFloat
    currency: Literal["USD", "EUR"]
    status: SupplierStatus
    service_zone: str | None = None
    contact_email: str | None = None
    notes: str | None = None

    _valid_categories: ClassVar[set[str]] = set(VALID_CATEGORIES)

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, values: list[str]) -> list[str]:
        invalid = [value for value in values if value not in cls._valid_categories]
        if invalid:
            allowed = ", ".join(sorted(cls._valid_categories))
            raise ValueError(f"Invalid categories: {invalid}. Allowed values: {allowed}")
        return values

    @field_validator("rate_per_shipment")
    @classmethod
    def validate_rate_per_shipment(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("rate_per_shipment must be greater than 0")
        return value

    @model_validator(mode="after")
    def validate_currency_by_country(self) -> "SupplierBase":
        if self.country == "USA" and self.currency != "USD":
            raise ValueError("Currency must be USD when country is USA")
        if self.country == "Spain" and self.currency != "EUR":
            raise ValueError("Currency must be EUR when country is Spain")
        return self

    @field_validator("status", mode="before")
    @classmethod
    def validate_status(cls, value: str | SupplierStatus) -> str | SupplierStatus:
        if isinstance(value, SupplierStatus):
            return value
        if not isinstance(value, str):
            raise ValueError("Invalid status type")

        normalized = value.strip().lower()
        if normalized == "activo":
            return SupplierStatus.ACTIVE
        if normalized in {"suspendido", "suspendiudo"}:
            return SupplierStatus.SUSPENDED
        if normalized in VALID_STATUSES:
            return normalized

        allowed = "active, suspended, activo, suspendido"
        raise ValueError(f"Invalid status: {value}. Allowed values: {allowed}")


class SupplierCreate(SupplierBase):
    model_config = ConfigDict(extra="forbid")


class SupplierResponse(SupplierBase):
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupplierCreatedResponse(SupplierResponse):
    id: int


class SupplierListResponse(BaseModel):
    id: int
    name: str
    country: Literal["USA", "Spain"]
    categories: list[str]
    rate_per_shipment: PositiveFloat
    currency: Literal["USD", "EUR"]
    status: SupplierStatus


class SupplierRateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rate_per_shipment: PositiveFloat


class SupplierStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SupplierStatus


Supplier = SupplierResponse


# --- Telemetry -----------------------------------------------------------------


class TelemetryEvent(BaseModel):
    """Envelope estándar de un evento de telemetría (docs/telemetry/telemetry-plan.md §4).

    Este modelo se reutilizará sin cambios en la Fase 3 (validación completa y
    persistencia en Supabase). Definir aquí los campos permite que todo el
    pipeline (captura -> almacenamiento -> reporte) dependa de un único contrato.
    """

    model_config = ConfigDict(extra="forbid")

    eventId: str = Field(..., min_length=1)
    timestamp: str = Field(..., min_length=1)
    sessionId: str = Field(..., min_length=1)
    userId: str = Field(..., min_length=1)
    event_type: str = Field(..., pattern=r"^[a-z0-9]+(?:_[a-z0-9]+)+$")
    schemaVersion: str = Field(..., pattern=r"^\d+\.\d+\.\d+$")
    requestId: str = Field(..., min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    """Body del endpoint POST /telemetry/events: un lote de N eventos."""

    model_config = ConfigDict(extra="forbid")

    events: list[TelemetryEvent] = Field(..., min_length=1)


# --- Telemetry Storage (SQLModel for Supabase/PostgreSQL) ------------------


class TelemetryEventRecord(SQLModel, table=True):
    """Tabla de almacenamiento de eventos de telemetría en Supabase (Fase 1).

    Ocho columnas fijas que reflejan el envelope estándar del evento más el
    payload semiestructurado en ``tags`` (JSONB). Las columnas ``event_type``,
    ``timestamp`` y ``service`` sostienen las consultas analíticas del proyecto
    de reporte; ``tags`` guarda ``event.properties`` (solo claves del allowlist).

    Invariantes:
    - Solo escritura (INSERT). Nunca UPDATE ni DELETE.
    - Los eventos son hechos inmutables una vez registrados.
    - El ``id`` se genera en la base de datos con ``gen_random_uuid()``.
    """

    __tablename__ = "telemetry_events"  # type: ignore

    __table_args__ = (
        Index("idx_telemetry_events_timestamp", "timestamp", postgresql_using="btree"),
        Index("idx_telemetry_events_event_type", "event_type", postgresql_using="btree"),
        Index("idx_telemetry_events_tags", "tags", postgresql_using="gin"),
    )

    id: str | None = SQLField(
        default=None,
        sa_column=Column(
            UUID(as_uuid=False),
            primary_key=True,
            server_default=text("gen_random_uuid()"),
            comment="Identificador único del registro",
        ),
    )
    timestamp: datetime = SQLField(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            comment="Timestamp del evento en ISO 8601",
        ),
    )
    service: str = SQLField(
        sa_column=Column(Text, nullable=False, comment="Origen del evento (backoffice, api)"),
    )
    event_type: str = SQLField(
        sa_column=Column(Text, nullable=False, comment="Tipo de evento en formato entidad_acción"),
    )
    level: str = SQLField(
        sa_column=Column(
            Text,
            nullable=False,
            server_default=text("'info'"),
            comment="Severidad: info, warn, error",
        ),
    )
    value: float | None = SQLField(
        default=None,
        sa_column=Column(Numeric, nullable=True, comment="Valor numérico asociado al evento, si aplica"),
    )
    message: str | None = SQLField(
        default=None,
        sa_column=Column(Text, nullable=True, comment="Descripción legible del evento"),
    )
    tags: dict[str, Any] | None = SQLField(
        sa_column=Column(
            JSONB,
            nullable=False,
            server_default=text("'{}'::jsonb"),
            comment="properties del envelope (solo claves del allowlist)",
        ),
    )


# --- Celery Dead Letter Queue ----------------------------------------------


class CeleryTaskFailure(SQLModel, table=True):
    """Registro de tareas de Celery que agotaron todos los reintentos.

    Cada fila representa una tarea que falló definitivamente tras superar
    ``max_retries``. Se inserta desde el hook ``on_failure`` de la clase base
    ``DatabaseTask``.

    Invariantes:
    - Solo escritura (INSERT). Nunca UPDATE ni DELETE.
    - ``failed_at`` se genera en la aplicación (UTC).
    - ``task_name`` + ``task_id`` identifican unívocamente el fallo.
    """

    __tablename__ = "celery_task_failures"  # type: ignore

    id: int | None = SQLField(
        default=None,
        primary_key=True,
        sa_column_kwargs={"comment": "Identificador autoincremental"},
    )
    task_id: str = SQLField(
        sa_column=Column(
            Text,
            nullable=False,
            comment="ID único de la tarea Celery (UUID)",
        ),
    )
    task_name: str = SQLField(
        sa_column=Column(
            Text,
            nullable=False,
            comment="Nombre registrado de la tarea (ej. generate_telemetry_report)",
        ),
    )
    attempt: int = SQLField(
        sa_column=Column(
            Integer,
            nullable=False,
            comment="Número de intento en el que falló (max_retries + 1)",
        ),
    )
    error: str = SQLField(
        sa_column=Column(
            Text,
            nullable=False,
            comment="Mensaje de la excepción que causó el fallo definitivo",
        ),
    )
    traceback: str | None = SQLField(
        default=None,
        sa_column=Column(
            Text,
            nullable=True,
            comment="Traceback completo de la excepción, si está disponible",
        ),
    )
    failed_at: datetime = SQLField(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            comment="Timestamp UTC del fallo definitivo",
        ),
    )