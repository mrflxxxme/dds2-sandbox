"""Schemas for WB tariffs."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class WbTariffSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    subject_name: str
    commission_rate: Decimal


class WbTariffUploadResult(BaseModel):
    inserted: int
    replaced: int
