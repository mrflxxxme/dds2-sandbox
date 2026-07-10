"""
WB warehouse measurements & dimension penalties.

Источник — WB Analytics API:
- GET /api/analytics/v1/warehouse-measurements — контрольные замеры товаров на складах
- GET /api/analytics/v1/measurement-penalties — удержания за занижение габаритов

Оба набора project-scoped и наполняются идемпотентным upsert по естественному ключу.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class WbWarehouseMeasurement(Base):
    """Контрольный замер одного товара на складе WB (data.reports[] из warehouse-measurements)."""

    __tablename__ = "wb_warehouse_measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    dim_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # WB dimId — id самого замера
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)   # WB nmID — артикул
    subject_name: Mapped[str | None] = mapped_column(String(300))

    # Габариты по факту замера (см) и объём (л)
    length: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    photo_urls: Mapped[list | None] = mapped_column(JSONB)  # ссылки на фото замеров (3 стороны)
    measured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # WB dt

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "dim_id", name="uq_wb_wh_measurement_project_dim"),
        Index("ix_wb_wh_measurement_project_nm", "project_id", "nm_id"),
        Index("ix_wb_wh_measurement_project_measured", "project_id", "measured_at"),
    )


class WbMeasurementPenalty(Base):
    """Удержание за занижение габаритов (data.reports[] из measurement-penalties).

    Габариты в двух наборах: `act_*` — фактический замер WB, `dec_*` — заявленные
    продавцом (WB-поля с суффиксом Sup). `prc_over` — % превышения по объёму.
    """

    __tablename__ = "wb_measurement_penalties"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    dim_id: Mapped[int] = mapped_column(BigInteger, nullable=False)  # WB dimId — id замера
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subject_name: Mapped[str | None] = mapped_column(String(300))

    prc_over: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # % превышения габаритов

    # Фактический замер WB (act) — WB-поля volume/width/length/height
    act_length: Mapped[int | None] = mapped_column(Integer)
    act_width: Mapped[int | None] = mapped_column(Integer)
    act_height: Mapped[int | None] = mapped_column(Integer)
    act_volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    # Заявлено продавцом (dec) — WB-поля *Sup
    dec_length: Mapped[int | None] = mapped_column(Integer)
    dec_width: Mapped[int | None] = mapped_column(Integer)
    dec_height: Mapped[int | None] = mapped_column(Integer)
    dec_volume: Mapped[Decimal | None] = mapped_column(Numeric(12, 3))

    # WB шлёт по строке на КАЖДУЮ единицу товара → суммируем по (замер, день).
    penalty_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))   # Σ penaltyAmount за день, ₽
    reversal_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))  # Σ reversalAmount за день, ₽
    units_count: Mapped[int | None] = mapped_column(Integer)  # сколько строк-единиц WB прислал за день
    is_valid: Mapped[bool | None] = mapped_column(Boolean)
    is_valid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # isValidDt
    penalty_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # dtBonus

    photo_urls: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        # Грейн WB — одна запись на (замер × день начисления/сторно). dim_id
        # повторяется по дням (dtBonus), поэтому дата входит в ключ.
        UniqueConstraint(
            "project_id", "dim_id", "penalty_date",
            name="uq_wb_measurement_penalty_project_dim_date",
        ),
        Index("ix_wb_measurement_penalty_project_nm", "project_id", "nm_id"),
        Index("ix_wb_measurement_penalty_project_date", "project_id", "penalty_date"),
    )
