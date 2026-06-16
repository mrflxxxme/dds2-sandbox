# ruff: noqa: RUF002, RUF003
"""
Fulfillment integration models: FF stock snapshots and request mirrors.

Generic layer for external fulfilment providers (skladbot.ru now, migfull
later). Data here is a read-only mirror of the provider state — it never
touches WarehouseStock / StockMovement, which stay document-driven.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.utils.time import utcnow


class FulfillmentProvider(str, enum.Enum):
    SKLADBOT = "skladbot"
    MIGFULL = "migfull"  # «Натали» — будущий провайдер
    WMSCELICOM = "wmscelicom"  # WMS Celicom («Целиком»), токен в URL-пути


class FfRequestKind(str, enum.Enum):
    ASSEMBLY = "assembly"  # доставка на склад МП (заявка на сборку)
    INBOUND = "inbound"  # приёмка на склад ФФ
    OTHER = "other"


class FulfillmentStock(Base):
    """Снапшот остатков фулфилмента; перезаписывается целиком при каждом синке."""

    __tablename__ = "fulfillment_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    barcode: Mapped[str] = mapped_column(String(100), nullable=False)
    nomenclature_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("nomenclature.id"))
    name: Mapped[str | None] = mapped_column(String(500))
    vendor_code: Mapped[str | None] = mapped_column(String(200))
    qty_good: Mapped[int] = mapped_column(Integer, default=0)
    qty_reserve: Mapped[int] = mapped_column(Integer, default=0)
    qty_defect: Mapped[int] = mapped_column(Integer, default=0)
    qty_nominal: Mapped[int] = mapped_column(Integer, default=0)
    external_product_id: Mapped[str | None] = mapped_column(String(100))
    # Короб → россыпь. base_barcode — ШК россыпи (EAN13), к которому относится
    # строка-короб (её собственный barcode — ШК короба, ITF14); units_per_box —
    # штук россыпи в одном коробе. Для россыпных строк base_barcode=NULL,
    # units_per_box=1. Остатки короба сводятся к россыпи (qty × units_per_box)
    # в list_stocks. migfull-специфика (авто-вывод: ITF14→EAN13 по GTIN-14 +
    # «короб N шт.» из названия); прочие провайдеры — дефолты.
    base_barcode: Mapped[str | None] = mapped_column(String(100))
    units_per_box: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "barcode", name="uq_ff_stock_wh_barcode"),
        Index("ix_fulfillment_stocks_project_wh", "project_id", "warehouse_id"),
        Index("ix_fulfillment_stocks_nomenclature_id", "nomenclature_id"),
    )


class FulfillmentBoxOverride(Base):
    """Ручное сопоставление короб→россыпь — когда авто-вывод не справился.

    Побеждает авто-вывод при синке (см. _normalize_migfull_stock): по ШК короба
    привязываем нашу номенклатуру (base_barcode = её ШК россыпи) и штук в коробе.
    Привязка только к существующей номенклатуре (nomenclature_id).
    """

    __tablename__ = "fulfillment_box_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    box_barcode: Mapped[str] = mapped_column(String(100), nullable=False)  # ШК короба у ФФ
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    base_barcode: Mapped[str] = mapped_column(String(100), nullable=False)  # ШК россыпи (= nomenclature.barcode)
    units_per_box: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "box_barcode", name="uq_ff_box_override_wh_barcode"),
        Index("ix_ff_box_overrides_project_wh", "project_id", "warehouse_id"),
    )


class FulfillmentRequest(Base):
    """Зеркало заявки фулфилмента (сборка/приёмка) + связь с нашими документами."""

    __tablename__ = "fulfillment_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)  # id заявки у ФФ
    number: Mapped[str | None] = mapped_column(String(100))  # WH-R-195847
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=FfRequestKind.OTHER.value)
    type_id: Mapped[int | None] = mapped_column(Integer)
    type_name: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str | None] = mapped_column(String(50))
    stage_code: Mapped[str | None] = mapped_column(String(100))
    stage_title: Mapped[str | None] = mapped_column(String(200))
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expired: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Локальный архив (пометка пользователя в DDS): синк его НЕ трогает,
    # в отличие от archived — зеркала провайдера, затираемого каждым синком
    local_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    local_archived_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Обогащение составом: skladbot — из деталки при синке, wmscelicom — из raw списка
    total_qty: Mapped[int | None] = mapped_column(Integer)  # заявлено всего, шт
    dest_warehouse: Mapped[str | None] = mapped_column(String(300))  # склад отгрузки МП
    external_created_at: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    # Связь с нашими документами (ручная в фазе 1, автоматическая при push в фазе 2)
    assembly_request_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("assembly_requests.id"))
    inbound_receipt_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("inbound_receipts.id"))

    __table_args__ = (
        UniqueConstraint("project_id", "provider", "external_id", name="uq_ff_request_external"),
        Index("ix_fulfillment_requests_project_wh", "project_id", "warehouse_id"),
        Index("ix_fulfillment_requests_assembly_request_id", "assembly_request_id"),
        Index("ix_fulfillment_requests_inbound_receipt_id", "inbound_receipt_id"),
    )


class FulfillmentStatusEvent(Base):
    """История смены статусов заявки ФФ — пишется синком при детекте изменения.

    Зеркало провайдера (FulfillmentRequest) хранит лишь ТЕКУЩЕЕ состояние и
    перетирается каждым синком. Эта таблица — журнал переходов: при каждом
    синке, если стадия/статус/флаги заявки изменились относительно зеркала,
    добавляется строка (когда, что → что). Первое появление заявки — событие
    `created`. Работает для всех провайдеров (в т.ч. wmscelicom/migfull, у
    которых живой истории стадий нет). Read-only журнал — append-only, без
    soft-delete; каскадно удаляется вместе с заявкой.
    """

    __tablename__ = "fulfillment_status_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    fulfillment_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fulfillment_requests.id", ondelete="CASCADE"), nullable=False
    )
    # Денормализация для отображения без JOIN (заявка переживёт смену number)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    number: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default=FfRequestKind.OTHER.value)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # created | changed
    old_status: Mapped[str | None] = mapped_column(String(50))
    new_status: Mapped[str | None] = mapped_column(String(50))
    old_stage_code: Mapped[str | None] = mapped_column(String(100))
    new_stage_code: Mapped[str | None] = mapped_column(String(100))
    old_stage_title: Mapped[str | None] = mapped_column(String(200))
    new_stage_title: Mapped[str | None] = mapped_column(String(200))
    old_is_completed: Mapped[bool | None] = mapped_column(Boolean)
    new_is_completed: Mapped[bool | None] = mapped_column(Boolean)
    old_archived: Mapped[bool | None] = mapped_column(Boolean)
    new_archived: Mapped[bool | None] = mapped_column(Boolean)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, server_default=func.now())

    __table_args__ = (
        Index("ix_ff_status_events_project_wh_changed", "project_id", "warehouse_id", "changed_at"),
        Index("ix_ff_status_events_request_id", "fulfillment_request_id"),
    )
