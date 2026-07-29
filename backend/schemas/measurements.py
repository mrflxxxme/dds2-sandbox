"""Schemas for WB warehouse measurements & dimension penalties (замеры)."""

from datetime import date, datetime
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
    """Удержание за габариты — строка «артикул × день начисления» из финотчёта."""

    model_config = ConfigDict(from_attributes=True)

    nm_id: int
    rr_dt: date | None = None       # дата начисления (финотчёт)
    subject_name: str | None = None
    brand: str | None = None
    penalty: Decimal                # начислено, ₽
    reversal: Decimal               # сторно, ₽ (≤0)
    net: Decimal                    # нетто, ₽
    # Сравнение литража: текущая карточка vs последний замер
    card_volume: Decimal | None = None
    meas_volume: Decimal | None = None
    deviation: Decimal | None = None  # (замер − карточка) / карточка · 100, %


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
    days_count: int                 # число дней начисления
    card_volume: Decimal | None = None
    meas_volume: Decimal | None = None
    deviation: Decimal | None = None


class PenaltyArticleSummaryResponse(BaseModel):
    items: list[PenaltyArticleSummaryRow]
    articles: int
    total_penalty: Decimal
    total_reversal: Decimal
    net: Decimal
