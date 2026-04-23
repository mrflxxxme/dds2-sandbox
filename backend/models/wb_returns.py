# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
WB Goods Returns — отчёт по возвратам и перемещению товаров WB.

Зеркалит endpoint https://seller-analytics-api.wildberries.ru/api/v1/analytics/goods-return
и связывает единицу возврата с `InboundReceipt` когда пользователь оформляет приёмку.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow


class WbGoodsReturn(Base, TimestampMixin, SoftDeleteMixin):
    """
    Возврат товара с ПВЗ.
    `status`, `is_status_active`, `completed_dt` обновляются только через WB API sync.
    `inbound_receipt_id` выставляется когда пользователь оформляет приёмку на физ.склад.
    """

    __tablename__ = "wb_goods_returns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)

    # ─── WB API fields (mirror) ─────────────────────────────────────────────
    srid: Mapped[str] = mapped_column(String(64), nullable=False)  # unique identifier of return
    shk_id: Mapped[str | None] = mapped_column(String(32))
    sticker_id: Mapped[str | None] = mapped_column(String(32))
    barcode: Mapped[str | None] = mapped_column(String(50))
    nm_id: Mapped[int | None] = mapped_column(BigInteger)
    subject_name: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200))
    tech_size: Mapped[str | None] = mapped_column(String(50))
    order_id: Mapped[int | None] = mapped_column(BigInteger)
    order_dt: Mapped[date | None] = mapped_column(Date)
    ready_to_return_dt: Mapped[datetime | None] = mapped_column(DateTime)
    expired_dt: Mapped[datetime | None] = mapped_column(DateTime)
    completed_dt: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str | None] = mapped_column(String(100))  # raw WB status
    return_type: Mapped[str | None] = mapped_column(String(100))  # e.g. "Возврат брака"
    reason: Mapped[str | None] = mapped_column(Text)
    is_status_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dst_office_id: Mapped[int | None] = mapped_column(Integer)
    dst_office_address: Mapped[str | None] = mapped_column(String(500))

    # ─── Our fields ─────────────────────────────────────────────────────────
    inbound_receipt_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("inbound_receipts.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Ручная архивация «забрали без оформления» — возврат списан без приёмки.
    # Не соответствует soft_delete (WB-sync его restore-ит). Пока archived_at
    # выставлен — запись показывается только на вкладке «История».
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    # Relationships — lazy="raise_on_sql" чтобы не триггерить неявный SQL в async;
    # всегда подгружаем через selectinload() явно.
    inbound_receipt = relationship(
        "InboundReceipt",
        foreign_keys=[inbound_receipt_id],
        lazy="raise_on_sql",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "srid", name="uq_wb_goods_returns_project_srid"),
        Index("ix_wb_goods_returns_project_id", "project_id"),
        Index("ix_wb_goods_returns_inbound_receipt_id", "inbound_receipt_id"),
        Index("ix_wb_goods_returns_nm_id", "nm_id"),
        Index("ix_wb_goods_returns_is_status_active", "is_status_active"),
    )
