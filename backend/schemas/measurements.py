"""Schemas for WB warehouse measurements & dimension penalties (замеры)."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class WarehouseMeasurementSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dim_id: int
    nm_id: int
    subject_name: str | None = None
    brand: str | None = None
    length: int | None = None
    width: int | None = None
    height: int | None = None
    volume: Decimal | None = None
    card_volume: Decimal | None = None  # текущий объём карточки WB (л), для сравнения с замером
    photo_urls: list[str] | None = None
    measured_at: datetime | None = None


class MeasurementPenaltySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    dim_id: int
    nm_id: int
    subject_name: str | None = None
    brand: str | None = None
    prc_over: Decimal | None = None
    # фактический замер WB
    act_length: int | None = None
    act_width: int | None = None
    act_height: int | None = None
    act_volume: Decimal | None = None
    # заявлено продавцом
    dec_length: int | None = None
    dec_width: int | None = None
    dec_height: int | None = None
    dec_volume: Decimal | None = None
    penalty_amount: Decimal | None = None
    reversal_amount: Decimal | None = None
    units_count: int | None = None
    is_valid: bool | None = None
    is_valid_at: datetime | None = None
    penalty_date: datetime | None = None
    photo_urls: list[str] | None = None


class WarehouseMeasurementListResponse(BaseModel):
    items: list[WarehouseMeasurementSchema]
    total: int


class MeasurementPenaltyListResponse(BaseModel):
    items: list[MeasurementPenaltySchema]
    total: int
    total_penalty: Decimal
    total_reversal: Decimal


class MeasurementSyncResult(BaseModel):
    warehouse: int
    penalties: int


class MeasurementFiltersResponse(BaseModel):
    brands: list[str]
    subjects: list[str]


class PenaltyArticleSummaryRow(BaseModel):
    nm_id: int
    subject_name: str | None = None
    brand: str | None = None
    total_penalty: Decimal
    total_reversal: Decimal
    net: Decimal
    penalties_count: int
    measurements_count: int


class PenaltyArticleSummaryResponse(BaseModel):
    items: list[PenaltyArticleSummaryRow]
    articles: int
    total_penalty: Decimal
    total_reversal: Decimal
    net: Decimal
