# ruff: noqa: RUF002, RUF003
"""WB Orders — лента заказов с warehouse + geo атрибутами.

Зеркало WB Statistics API endpoint:
  https://statistics-api.wildberries.ru/api/v1/supplier/orders

Один ряд = одна единица товара (по `srid`). UPSERT идемпотентно по
`(project_id, srid)` — поля `is_cancel`, `cancel_date`, `last_change_date`
обновляются на каждой синхронизации.

Используется отчётом «Индекс локализации» (regional breakdown
local/non-local per nm × oblast_okrug) и любыми другими отчётами,
которым нужна гранулярная история заказов.

Не использует SoftDeleteMixin: WB не «удаляет» заказ, только меняет
`isCancel=true`. См. CLAUDE.md, правило #2 — к этой таблице не
применимо (нет колонки `is_deleted`). Скрипт check_conventions.sh
включает `wb_orders` в `SKIP_SOFT_DELETE_TABLES`.
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
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin
from backend.utils.time import utcnow


class WbOrder(Base, TimestampMixin):
    __tablename__ = "wb_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # ─── WB identifiers ────────────────────────────────────────────────────
    srid: Mapped[str] = mapped_column(String(120), nullable=False)
    g_number: Mapped[str | None] = mapped_column(String(40))
    sticker: Mapped[str | None] = mapped_column(String(40))
    income_id: Mapped[int | None] = mapped_column(BigInteger)

    # ─── Time ──────────────────────────────────────────────────────────────
    order_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_change_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ─── Product ───────────────────────────────────────────────────────────
    nm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(40))
    vendor_code: Mapped[str | None] = mapped_column(String(120))
    subject: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(200))
    tech_size: Mapped[str | None] = mapped_column(String(40))

    # ─── Source warehouse ──────────────────────────────────────────────────
    warehouse_name: Mapped[str | None] = mapped_column(String(120))
    warehouse_type: Mapped[str | None] = mapped_column(String(40))

    # ─── Delivery geography ────────────────────────────────────────────────
    country_name: Mapped[str | None] = mapped_column(String(80))
    oblast_okrug_name: Mapped[str | None] = mapped_column(String(120))
    region_name: Mapped[str | None] = mapped_column(String(200))

    # ─── Money ─────────────────────────────────────────────────────────────
    total_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    discount_percent: Mapped[int | None] = mapped_column(Integer)
    spp: Mapped[int | None] = mapped_column(Integer)
    finished_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    price_with_disc: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))

    # ─── Flags ─────────────────────────────────────────────────────────────
    is_cancel: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cancel_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_supply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_realization: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # ─── Sync ──────────────────────────────────────────────────────────────
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("project_id", "srid", name="uq_wb_orders_project_srid"),
        Index("ix_wb_orders_project_date", "project_id", "order_date"),
        Index("ix_wb_orders_project_nm_date", "project_id", "nm_id", "order_date"),
        Index(
            "ix_wb_orders_project_okrug_date",
            "project_id",
            "oblast_okrug_name",
            "order_date",
        ),
    )
