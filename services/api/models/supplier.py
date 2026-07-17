from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, PositiveFloat, field_validator, model_validator


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
    pass


class SupplierResponse(SupplierBase):
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupplierCreatedResponse(SupplierResponse):
    id: int


class SupplierRateUpdate(BaseModel):
    rate_per_shipment: PositiveFloat


class SupplierStatusUpdate(BaseModel):
    status: SupplierStatus


Supplier = SupplierResponse
