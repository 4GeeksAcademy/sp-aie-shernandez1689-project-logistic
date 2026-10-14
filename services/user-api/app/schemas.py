from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


Warehouse = Literal["LA", "ZGZ"]
Category = Literal["fashion", "electronics", "cosmetics"]
ExitType = Literal["dispatch", "loss"]


class SKUCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    client_name: str = Field(min_length=1)
    category: Category
    warehouse: Warehouse


class SKUResponse(SKUCreateRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(gt=0)
    current_stock: int = Field(ge=0)


class StockEntryCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku_id: int = Field(gt=0)
    quantity: int = Field(gt=0, strict=True)
    reference: str = Field(min_length=1)
    warehouse: Warehouse


class StockEntryResponse(StockEntryCreateRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(gt=0)
    created_at: datetime
    user_uuid: str = Field(min_length=1)


class StockExitCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sku_id: int = Field(gt=0)
    quantity: int = Field(gt=0, strict=True)
    exit_type: ExitType
    tracking_number: str | None = None
    warehouse: Warehouse

    @model_validator(mode="after")
    def validate_tracking_number(self) -> Self:
        if self.exit_type == "dispatch" and not self.tracking_number:
            raise ValueError("tracking_number is required for dispatch exits")
        if self.exit_type == "loss" and self.tracking_number is not None:
            raise ValueError("tracking_number must be null for loss exits")
        return self


class StockExitResponse(StockExitCreateRequest):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(gt=0)
    created_at: datetime
    user_uuid: str = Field(min_length=1)


class StockEntryOrderResponse(StockEntryResponse):
    order_type: Literal["inbound"] = "inbound"
    product: SKUResponse


class StockExitOrderResponse(StockExitResponse):
    order_type: Literal["outbound"] = "outbound"
    product: SKUResponse