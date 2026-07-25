"""
WB FBS (Fulfillment by Seller) models.

Контур продаж со склада продавца: справочник складов продавца WB, привязка
к нашим складам, журнал трансляции остатков, сборочные задания и поставки FBS.

Ключевые инварианты домена:
  • Ключ остатков в Marketplace API — `chrtId` (Nomenclature.chrt_id).
    Баркоды (sku) в методах остатков не принимаются с 09.02.2026.
  • Один наш склад может кормить максимум ОДИН склад продавца WB
    (partial unique index на wb_fbs_warehouse_links) — иначе один и тот же
    физический остаток уехал бы в два кабинета = двойная продажа.
  • Обратное направление разрешено: один склад WB может собираться из N наших
    складов (остатки суммируются).
  • Зеркала WB (заказы/поставки) — без SoftDelete: источник истины у WB,
    строки только upsert-ятся синком.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.mixins import TimestampMixin
from backend.utils.time import utcnow

# ─── Enums ──────────────────────────────────────────────────────────────────


class FbsSupplierStatus(str, enum.Enum):
    """`supplierStatus` — статус, которым управляет продавец."""

    NEW = "new"  # новое сборочное задание
    CONFIRM = "confirm"  # на сборке (добавлено в поставку)
    COMPLETE = "complete"  # в доставке (поставка передана)
    CANCEL = "cancel"  # отменено продавцом
    CANCEL_CARRIER = "cancel_carrier"  # отменено перевозчиком (трансграничные)


#: Задания в этих статусах ещё держат наш остаток — вычитаются из FBS-остатка
#: и из доступного под сборку (обратный гейт).
FBS_OPEN_STATUSES: tuple[str, ...] = (
    FbsSupplierStatus.NEW.value,
    FbsSupplierStatus.CONFIRM.value,
)

#: Терминальные статусы — синк статусов их больше не опрашивает.
FBS_TERMINAL_STATUSES: tuple[str, ...] = (
    FbsSupplierStatus.CANCEL.value,
    FbsSupplierStatus.CANCEL_CARRIER.value,
)


class FbsStockSource(str, enum.Enum):
    """Откуда берём физический остаток для трансляции на WB."""

    LEDGER = "ledger"  # WarehouseStock.quantity (наш документный ledger)
    FF_MIRROR = "ff_mirror"  # FulfillmentStock.qty_good (зеркало WMS провайдера)
    MIN_OF_BOTH = "min_of_both"  # min(ledger, зеркало) — консервативно, дефолт


class FbsWarehouseMode(str, enum.Enum):
    """Что система делает с остатками этого склада продавца."""

    #: Только читаем и показываем расхождение «в WB / у нас». В кабинет НЕ пишем.
    #: Дефолт: подключение к складу с ручными остатками не должно ничего перезаписать.
    OBSERVE = "observe"
    #: Транслируем наш расчёт в WB (дельта-пуш + верификация).
    TRANSLATE = "translate"


class FbsPushTrigger(str, enum.Enum):
    """Кто инициировал трансляцию остатков."""

    AUTO = "auto"  # фоновый джоб
    MANUAL = "manual"  # кнопка «Передать остатки»


class FbsPushStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    OK = "OK"
    PARTIAL = "PARTIAL"  # часть складов/чанков не прошла
    ERROR = "ERROR"


# ─── Склады продавца WB (справочник, синкается с GET /api/v3/warehouses) ─────


class WbFbsWarehouse(Base, TimestampMixin):
    """
    Склад продавца в кабинете WB (то, что WB называет warehouse, а не office).

    Строки создаёт синк `GET /api/v3/warehouses`; наши настройки трансляции
    (`is_active`, источник остатка, буфер) живут здесь же.
    """

    __tablename__ = "wb_fbs_warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # id склада продавца в WB — int64 (WB-side ID, как nmID/imtID).
    wb_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    # Склад WB (office), к которому привязан склад продавца.
    office_id: Mapped[int | None] = mapped_column(BigInteger)
    office_name: Mapped[str | None] = mapped_column(String(200))
    # 1 — МГТ, 2 — СГТ, 3 — КГТ+
    cargo_type: Mapped[int | None] = mapped_column(SmallInteger)
    # 1 — FBS, 2 — DBS, 3 — DBW, 5 — C&C, 6 — EDBS
    delivery_type: Mapped[int | None] = mapped_column(SmallInteger)
    # WB: при is_processing=true обновление и удаление остатков недоступно.
    is_processing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_deleting: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    # ─── Наши настройки трансляции ───
    #: Тумблер «транслировать остатки на этот склад».
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    stock_source: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=FbsStockSource.MIN_OF_BOTH.value,
        server_default=FbsStockSource.MIN_OF_BOTH.value,
    )
    #: Буфер безопасности: сначала вычитается процент, затем абсолют.
    safety_stock_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    safety_stock_abs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: Потолок остатка на одну позицию (0 — без потолка). Страховка на старте.
    max_qty_per_sku: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    #: observe — только показываем расхождения, translate — пишем в WB.
    mode: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=FbsWarehouseMode.OBSERVE.value,
        server_default=FbsWarehouseMode.OBSERVE.value,
    )
    #: Гейт по остатку на складах WB (FBO): отдаём позицию в FBS, только если её
    #: доступный FBO-остаток ≤ этого числа. NULL — на FBO не смотрим вовсе,
    #: 0 — классический сценарий «продаём со своего склада то, что кончилось на WB».
    fbo_max_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    synced_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_warehouse_id", name="uq_wb_fbs_warehouse"),
        Index("ix_wb_fbs_warehouses_project_id", "project_id"),
    )


class WbFbsWarehouseLink(Base, TimestampMixin):
    """
    Привязка: склад продавца WB ← наш склад (`warehouses.id`).

    N наших складов → 1 склад WB (остатки суммируются). Обратное запрещено
    partial unique index'ом `uq_wb_fbs_link_warehouse_active`.
    """

    __tablename__ = "wb_fbs_warehouse_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("project_id", "wb_warehouse_id", "warehouse_id", name="uq_wb_fbs_link"),
        Index("ix_wb_fbs_links_project_id", "project_id"),
        Index("ix_wb_fbs_links_warehouse_id", "warehouse_id"),
        # Один наш склад — максимум одна активная привязка (анти-двойная-продажа).
        Index(
            "uq_wb_fbs_link_warehouse_active",
            "project_id",
            "warehouse_id",
            unique=True,
            postgresql_where=text("is_active = true"),
        ),
    )


# ─── Потоварная замена количества ────────────────────────────────────────────


class WbFbsStockOverride(Base, TimestampMixin):
    """Ручное количество по конкретному товару на конкретном складе продавца.

    Максимально простая замена прежней системы правил (четыре уровня с
    приоритетами оказалась лишней): одна строка = один товар на одном складе.

      qty = 0   → не отдавать позицию вовсе (amount = 0)
      qty > 0   → потолок: итог = min(qty, рассчитанный доступный)

    Потолок именно ПОТОЛОК, а не фикс: физический свободный остаток всегда
    главнее, иначе WB продолжит продавать то, чего на складе нет.
    Убрать ограничение = удалить строку (SoftDelete здесь не нужен).
    """

    __tablename__ = "wb_fbs_stock_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_warehouse_id", "nomenclature_id", name="uq_wb_fbs_override"),
        Index("ix_wb_fbs_overrides_project_wh", "project_id", "wb_warehouse_id"),
        Index("ix_wb_fbs_overrides_nomenclature", "nomenclature_id"),
    )


# ─── Журнал трансляции остатков ─────────────────────────────────────────────


class WbFbsStockState(Base):
    """
    Последнее переданное на WB состояние остатка по (склад WB, chrtId).

    Нужен для дельта-пуша (не гонять 1000 позиций каждые 3 минуты) и для
    ловли «204-lie»: WB не валидирует имена полей и отвечает 204 даже когда
    остаток не обновился, поэтому после PUT читаем POST /stocks и пишем
    `qty_confirmed`.
    """

    __tablename__ = "wb_fbs_stock_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_warehouse_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chrt_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    barcode: Mapped[str | None] = mapped_column(String(50))
    nomenclature_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("nomenclature.id"))
    qty_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_confirmed: Mapped[int | None] = mapped_column(Integer)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_warehouse_id", "chrt_id", name="uq_wb_fbs_stock_state"),
        Index("ix_wb_fbs_stock_states_project_sent", "project_id", "sent_at"),
    )


class WbFbsStockPush(Base):
    """Один прогон трансляции остатков (для страницы «лог трансляции»)."""

    __tablename__ = "wb_fbs_stock_pushes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_warehouse_id: Mapped[int | None] = mapped_column(BigInteger)
    trigger: Mapped[str] = mapped_column(String(10), nullable=False, default=FbsPushTrigger.AUTO.value)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=FbsPushStatus.RUNNING.value)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    rows_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rows_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Позиции без `Nomenclature.chrt_id` — физически не транслируются, молчать нельзя.
    rows_no_chrt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Позиции, где верификация показала расхождение с тем, что мы отправили.
    rows_mismatch: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_msg: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_wb_fbs_stock_pushes_project_started", "project_id", "started_at"),)


# ─── Сборочные задания (заказы FBS) ─────────────────────────────────────────


class WbFbsOrder(Base, TimestampMixin):
    """
    Сборочное задание FBS — зеркало `GET /api/v3/orders/new` + `/orders/status`.

    Одно задание = одна единица товара (WB не агрегирует количество).
    """

    __tablename__ = "wb_fbs_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: `rid` == `srid` в отчётах — единственный надёжный ключ сшивки с финансами.
    rid: Mapped[str | None] = mapped_column(String(120))
    order_uid: Mapped[str | None] = mapped_column(String(120))
    created_at_wb: Mapped[datetime | None] = mapped_column(DateTime)

    wb_warehouse_id: Mapped[int | None] = mapped_column(BigInteger)
    office_id: Mapped[int | None] = mapped_column(BigInteger)
    office_name: Mapped[str | None] = mapped_column(String(200))

    nm_id: Mapped[int | None] = mapped_column(BigInteger)
    chrt_id: Mapped[int | None] = mapped_column(BigInteger)
    barcode: Mapped[str | None] = mapped_column(String(50))
    nomenclature_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("nomenclature.id"))
    article: Mapped[str | None] = mapped_column(String(100))
    subject: Mapped[str | None] = mapped_column(String(200))

    #: Цены приходят от WB в копейках — храним в рублях.
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    converted_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency_code: Mapped[str | None] = mapped_column(String(8))

    cargo_type: Mapped[int | None] = mapped_column(SmallInteger)
    cross_border_type: Mapped[int | None] = mapped_column(SmallInteger)
    is_zero_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_pickup_point_shipment_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    supplier_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=FbsSupplierStatus.NEW.value, server_default=FbsSupplierStatus.NEW.value
    )
    wb_status: Mapped[str | None] = mapped_column(String(30))
    is_cancellable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")

    supply_id: Mapped[str | None] = mapped_column(String(50))
    #: Стикер задания (кэш ответа POST /orders/stickers), файл — в MinIO.
    sticker_barcode: Mapped[str | None] = mapped_column(String(60))
    sticker_part_a: Mapped[str | None] = mapped_column(String(20))
    sticker_part_b: Mapped[str | None] = mapped_column(String(20))
    sticker_file_key: Mapped[str | None] = mapped_column(String(300))

    #: Только для СГТ (cargo_type = 2).
    ddate: Mapped[date | None] = mapped_column(Date)
    seller_date: Mapped[datetime | None] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(String(300))
    address: Mapped[dict | None] = mapped_column(JSONB)
    raw: Mapped[dict | None] = mapped_column(JSONB)

    #: Проставляется, когда задание списано из нашего ledger'а (StockMovement).
    written_off_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_order_id", name="uq_wb_fbs_order"),
        Index("ix_wb_fbs_orders_project_status", "project_id", "supplier_status"),
        Index("ix_wb_fbs_orders_project_supply", "project_id", "supply_id"),
        Index("ix_wb_fbs_orders_project_created", "project_id", "created_at_wb"),
        Index("ix_wb_fbs_orders_project_barcode", "project_id", "barcode"),
        # Горячий путь формулы остатка: открытые задания по складу WB.
        Index(
            "ix_wb_fbs_orders_open",
            "project_id",
            "wb_warehouse_id",
            "nomenclature_id",
            postgresql_where=text("supplier_status IN ('new', 'confirm')"),
        ),
    )


# ─── Поставки FBS ───────────────────────────────────────────────────────────


class WbFbsSupply(Base, TimestampMixin):
    """Поставка FBS (`WB-GI-…`) — контейнер сборочных заданий."""

    __tablename__ = "wb_fbs_supplies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    wb_supply_id: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    #: `done` в терминах WB — поставка закрыта (передана в доставку).
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at_wb: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    scan_dt: Mapped[datetime | None] = mapped_column(DateTime)

    #: Габаритный «залипон»: тип фиксируется первым добавленным заданием.
    cargo_type: Mapped[int | None] = mapped_column(SmallInteger)
    cross_border_type: Mapped[int | None] = mapped_column(SmallInteger)
    is_b2b: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_pickup_point_shipment_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    destination_office_id: Mapped[int | None] = mapped_column(BigInteger)
    recommended_wh_id: Mapped[int | None] = mapped_column(BigInteger)
    #: Склад продавца, с которого едет поставка (WB запрещает смешивать склады).
    wb_warehouse_id: Mapped[int | None] = mapped_column(BigInteger)

    orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: QR поставки — доступен только после deliver; файл кладём в MinIO.
    qr_barcode: Mapped[str | None] = mapped_column(String(60))
    qr_file_key: Mapped[str | None] = mapped_column(String(300))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "wb_supply_id", name="uq_wb_fbs_supply"),
        Index("ix_wb_fbs_supplies_project_done", "project_id", "done"),
    )
