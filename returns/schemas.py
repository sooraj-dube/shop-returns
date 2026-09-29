from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from shop_platform.events import ItemCondition

from returns.domain import ReturnReason, ReturnStatus


class RequestedLineIn(BaseModel):
    line_id: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    reason: ReturnReason


class CreateReturnIn(BaseModel):
    order_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    lines: list[RequestedLineIn] = Field(min_length=1)


class LineInspectionIn(BaseModel):
    line_id: str = Field(min_length=1)
    condition: ItemCondition


class InspectReturnIn(BaseModel):
    lines: list[LineInspectionIn] = Field(min_length=1)


class ReturnLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_id: str
    sku: str
    quantity: int
    unit_price_cents: int
    reason: ReturnReason
    condition: ItemCondition | None
    restock: bool | None
    refund_cents: int | None


class ReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    return_id: str
    rma_code: str
    order_id: str
    customer_id: str
    status: ReturnStatus
    currency: str
    refund_cents: int | None
    lines: list[ReturnLineOut]
    created_at: datetime
    received_at: datetime | None
    inspected_at: datetime | None


class ReturnListOut(BaseModel):
    returns: list[ReturnOut]
