"""
Warehouse models: Warehouse, InboundReceipt, OutboundShipment,
StockTransfer, StockMovement, WarehouseStock, StockAdjustment + item tables.
"""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

# ─── Enums ──────────────────────────────────────────────────────────────────


class WarehouseType(str, enum.Enum):
    EXTERNAL = "EXTERNAL"
    FULFILLMENT = "FULFILLMENT"


class InboundStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    EXPECTED = "EXPECTED"
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class OutboundStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


class TransferStatus(str, enum.Enum):
    """Статус переезда — ЗЕРКАЛО `AssemblyStatus` (канон юзера 31.07.2026).

    Переезд между складами ФФ ведётся как заявка на сборку: те же ступени, те же
    сигналы синка провайдера, машина внутри той же цепочки. Прежняя тонкая шкала
    DRAFT / IN_TRANSIT / COMPLETED легла на новую так: DRAFT → PENDING,
    IN_TRANSIT → SHIPPED, COMPLETED → DELIVERED (миграция `trv04`).

    Сток движется РОВНО в двух переходах:
      → SHIPPED   — списание со склада-источника (TRANSFER_OUT) + транзит на получателе;
      → DELIVERED — приход на получателе (TRANSFER_IN) и снятие транзита.
    Плюс → RETURNED: возврат на склад-источник, если получатель не принял.
    """

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"  # ФФ собирает переезд у себя
    READY = "READY"  # ФФ собрал (стадия «Собран») либо отмечено руками
    VEHICLE_ASSIGNED = "VEHICLE_ASSIGNED"
    SHIPPED = "SHIPPED"  # уехал: сток списан с источника, висит транзитом на получателе
    DELIVERED = "DELIVERED"  # принят получателем полностью
    RETURNED = "RETURNED"  # получатель не принял — товар вернулся на склад-источник
    CLOSED = "CLOSED"  # терминал после возврата
    CANCELLED = "CANCELLED"


#: Разрешённые переходы. Отличия от `ASSEMBLY_TRANSITIONS` ровно два, оба
#: осознанные:
#:  • PENDING → READY напрямую: у переезда БЕЗ связки с ФФ фазы «ФФ собирает» не
#:    существует (транзитные склады интеграции не имеют), и гонять человека через
#:    IN_PROGRESS ради одной лишней кнопки незачем.
#:  • SHIPPED → READY НЕТ (у заявки есть): сток уже списан, откат делает только
#:    RETURNED, который его возвращает. Иначе переезд «вернулся» бы в готовность,
#:    оставив единицы списанными.
TRANSFER_TRANSITIONS: dict["TransferStatus", set["TransferStatus"]] = {
    TransferStatus.PENDING: {
        TransferStatus.IN_PROGRESS,
        TransferStatus.READY,
        TransferStatus.CANCELLED,
    },
    TransferStatus.IN_PROGRESS: {TransferStatus.READY, TransferStatus.CANCELLED},
    TransferStatus.READY: {
        TransferStatus.VEHICLE_ASSIGNED,
        TransferStatus.IN_PROGRESS,
        TransferStatus.CANCELLED,
    },
    TransferStatus.VEHICLE_ASSIGNED: {
        TransferStatus.SHIPPED,
        TransferStatus.READY,
        TransferStatus.CANCELLED,
    },
    TransferStatus.SHIPPED: {
        TransferStatus.DELIVERED,
        TransferStatus.RETURNED,
        TransferStatus.CANCELLED,
    },
    TransferStatus.DELIVERED: {TransferStatus.RETURNED, TransferStatus.CLOSED},
    TransferStatus.RETURNED: {
        TransferStatus.READY,
        TransferStatus.CLOSED,
        TransferStatus.CANCELLED,
    },
    TransferStatus.CLOSED: set(),
    TransferStatus.CANCELLED: set(),
}

#: Статусы, в которых переезд ещё можно править и он НЕ держит движений стока.
TRANSFER_EDITABLE_STATUSES = frozenset(
    {TransferStatus.PENDING, TransferStatus.IN_PROGRESS, TransferStatus.READY}
)


class DefectMarkStatus(str, enum.Enum):
    ACCEPTED = "ACCEPTED"
    CANCELLED = "CANCELLED"


class MovementType(str, enum.Enum):
    INBOUND = "INBOUND"
    INBOUND_CANCEL = "INBOUND_CANCEL"
    OUTBOUND = "OUTBOUND"
    OUTBOUND_CANCEL = "OUTBOUND_CANCEL"
    TRANSFER_IN = "TRANSFER_IN"
    TRANSFER_OUT = "TRANSFER_OUT"
    INBOUND_EDIT = "INBOUND_EDIT"
    ADJUSTMENT = "ADJUSTMENT"
    # Defective goods (брак)
    DEFECT_MARK = "DEFECT_MARK"
    DEFECT_RECEIVE = "DEFECT_RECEIVE"
    DEFECT_WRITEOFF = "DEFECT_WRITEOFF"
    DEFECT_RECOVER = "DEFECT_RECOVER"
    DEFECT_TRANSFER_OUT = "DEFECT_TRANSFER_OUT"
    DEFECT_TRANSFER_IN = "DEFECT_TRANSFER_IN"


# ─── Warehouse ──────────────────────────────────────────────────────────────


class Warehouse(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "warehouses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    warehouse_type: Mapped[str] = mapped_column(String(20), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(500))
    assembly_days: Mapped[int | None] = mapped_column(Integer)
    wb_acceptance_days: Mapped[int] = mapped_column(Integer, default=2)
    external_id: Mapped[str | None] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Counterparty link (Phase 1: counterparties-loans)
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)

    # Relationships
    receipts: Mapped[list["InboundReceipt"]] = relationship(back_populates="warehouse")
    shipments: Mapped[list["OutboundShipment"]] = relationship(back_populates="warehouse")

    __table_args__ = (
        Index("ix_warehouses_project_id", "project_id"),
        # Partial index created via CONCURRENTLY in migration:
        #   ix_warehouses_counterparty_id  (counterparty_id)
    )


class WarehouseCounterparty(Base, TimestampMixin):
    """Дополнительные контрагенты (юр. лица) склада-ФФ.

    Основной контрагент хранится в `Warehouse.counterparty_id` (блок «Реквизиты
    компании»); эта таблица — для ДОПОЛНИТЕЛЬНЫХ юр. лиц, которые тоже относятся
    к этому складу. Все их ИНН так же категоризируются в «Фулфилмент» при импорте
    выписок (см. `_upsert_counterparties` в `backend/etl/service.py`).

    Обычная link-таблица: отвязка — hard-delete (без SoftDeleteMixin).
    """

    __tablename__ = "warehouse_counterparty"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    counterparty_id: Mapped[int] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("warehouse_id", "counterparty_id", name="uq_warehouse_counterparty"),
        Index("ix_warehouse_counterparty_warehouse_id", "warehouse_id"),
        Index("ix_warehouse_counterparty_counterparty_id", "counterparty_id"),
        Index("ix_warehouse_counterparty_project_id", "project_id"),
    )


# ─── Inbound Receipt (Приёмка) ──────────────────────────────────────────────


class InboundReceipt(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "inbound_receipts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=InboundStatus.DRAFT, nullable=False)
    planned_date: Mapped[date | None] = mapped_column(Date)
    actual_date: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)  # JSON array
    cost_order_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("cost_orders.id"))
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # Возврат сборки: к какой заявке/попытке относится этот приём-возврат.
    # warehouse_id выше = склад, НА который вернули (может отличаться от склада-источника).
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    assembly_attempt_no: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # FF-портал: «взял в работу» — кто из операторов фулфилмента и когда начал приёмку.
    # Промежуточного статуса у приёмки нет (EXPECTED→ACCEPTED); claim хранится тут.
    assigned_to_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    work_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # FF-портал: архив приёмки оператором — прячет из активного списка
    # (ортогонально is_deleted/status).
    is_archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Relationships
    warehouse: Mapped["Warehouse"] = relationship(back_populates="receipts")
    items: Mapped[list["InboundReceiptItem"]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_inbound_receipts_project_id", "project_id"),
        Index("ix_inbound_receipts_warehouse_id", "warehouse_id"),
        Index("ix_inbound_receipts_assembly_request_id", "assembly_request_id"),
        Index("ix_inbound_receipts_is_archived", "is_archived"),
    )


class InboundReceiptItem(Base):
    __tablename__ = "inbound_receipt_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    receipt_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("inbound_receipts.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    expected_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_qty: Mapped[int] = mapped_column(Integer, default=0)
    # FF-портал: брак по позиции при приёмке (Хамза вводит факт + брак отдельно).
    # actual_qty = годный принятый; defect_qty = брак; недовоз = expected − (actual+defect).
    defect_qty: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # Relationships
    receipt: Mapped["InboundReceipt"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_inbound_receipt_items_project_id", "project_id"),
        Index("ix_inbound_receipt_items_receipt_id", "receipt_id"),
        Index("ix_inbound_receipt_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Outbound Shipment (Отгрузка) ──────────────────────────────────────────


class OutboundShipment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "outbound_shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=OutboundStatus.DRAFT, nullable=False)
    destination: Mapped[str | None] = mapped_column(String(200))
    wb_supply_id: Mapped[str | None] = mapped_column(String(100))
    shipped_date: Mapped[date | None] = mapped_column(Date)
    comment: Mapped[str | None] = mapped_column(Text)
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # ─── Assembly shipping attempt (цепочка попыток отгрузки) ──────────────
    # Долговечная 1:N связь заявка→отгрузки (на AssemblyRequest хранится лишь
    # последняя попытка-зеркало и затирается при переотгрузке). Каждая отгрузка
    # одной заявки = одна попытка с собственным водителем/FBW/складом WB/стоимостью.
    assembly_request_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )
    wb_fbo_supply_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("wb_fbo_supplies.id", ondelete="SET NULL"), nullable=True
    )
    # ─── Забор ВНУТРЕННЕГО ПЕРЕЕЗДА (перемещение между складами) ───────────
    # Отгрузка создаётся при send_transfer как НОСИТЕЛЬ ЛОГИСТИКИ И ДЕНЕГ:
    # снимок машины/перевозчика/стоимости + связка с заявкой на оплату
    # (PaymentRequestShipment: N заборов → 1 платёж, кейс «одна машина везёт
    # три документа») и с банковской выпиской (etl/sync_shipment_payments).
    # СТОК ЭТОТ ЗАБОР НЕ ДВИГАЕТ — списание уже сделал сам перемещение
    # (MovementType.TRANSFER_OUT, reference_type='TRANSFER'). Второй раз
    # списывать нельзя: движения остаются за перемещением, забор — только
    # логистика и деньги.
    # ВАЖНО для читателей outbound_shipments: строка с непустым
    # stock_transfer_id — это ПЕРЕЕЗД МЕЖДУ НАШИМИ СКЛАДАМИ, а не отгрузка на
    # маркетплейс. Отчёты, считающие отгрузку/выручку/списание, обязаны её
    # исключать (фильтр stock_transfer_id IS NULL).
    stock_transfer_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stock_transfers.id", ondelete="SET NULL"), nullable=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    # Снимок логистики попытки на момент отгрузки — на AssemblyRequest эти поля
    # перезатираются при назначении нового водителя; здесь сохраняются по-попыточно.
    pickup_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    vehicle_info: Mapped[str | None] = mapped_column(String(300), nullable=True)
    vehicle_brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True
    )
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pickup_time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pallets_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pallet_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    # Единица поставки — снимок с заявки при отгрузке (False = паллеты, True = короба).
    # Чтобы «История отправок»/«Оплаты» показывали единицу забора (коробов vs паллет).
    shipped_as_boxes: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # ─── Связка забора с банковской выпиской ───────────────────────────────
    # «Этот забор оплачен транзакцией X» БЕЗ заявки на оплату: авто (матчер
    # etl/sync_shipment_payments по ИНН перевозчика + pickup_cost, 1:1) ИЛИ вручную
    # (привязка нескольких заборов к одной агрегированной оплате — N заборов → 1 txn).
    # Защита от двойного авто-матча — consumed-set матчера (не БД-уникальность).
    matched_transaction_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    matched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Relationships
    warehouse: Mapped["Warehouse"] = relationship(back_populates="shipments")
    items: Mapped[list["OutboundShipmentItem"]] = relationship(
        back_populates="shipment",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_outbound_shipments_project_id", "project_id"),
        Index("ix_outbound_shipments_warehouse_id", "warehouse_id"),
        Index("ix_outbound_shipments_assembly_request_id", "assembly_request_id"),
        # Один переезд — РОВНО ОДИН живой забор. Инвариант держался только на
        # row-lock при DRAFT → IN_TRANSIT; на уровне БД два забора на один
        # stock_transfer_id ничем не запрещались, а это прямой путь к двойной
        # заявке на оплату одной перевозки. Переотправки у переезда нет
        # (в отличие от заявки с её attempt_no), поэтому уникальность честная.
        Index(
            "uq_outbound_shipments_stock_transfer",
            "stock_transfer_id",
            unique=True,
            postgresql_where=text("stock_transfer_id IS NOT NULL AND is_deleted = false"),
        ),
        Index("ix_outbound_shipments_wb_fbo_supply_id", "wb_fbo_supply_id"),
        Index("ix_outbound_shipments_counterparty_id", "counterparty_id"),
        Index("ix_outbound_shipments_matched_transaction_id", "matched_transaction_id"),
        # Partial-unique (matched_transaction_id WHERE not null AND not deleted) — в миграции.
    )


class OutboundShipmentItem(Base):
    __tablename__ = "outbound_shipment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    shipment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("outbound_shipments.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    shipment: Mapped["OutboundShipment"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_outbound_shipment_items_project_id", "project_id"),
        Index("ix_outbound_shipment_items_shipment_id", "shipment_id"),
        Index("ix_outbound_shipment_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Stock Transfer (Перемещение) ──────────────────────────────────────────


class StockTransfer(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "stock_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    from_warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    to_warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TransferStatus.PENDING, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    is_defect: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    defect_reason: Mapped[str | None] = mapped_column(Text)

    # ─── Машина и логистика переезда ───────────────────────────────────────
    # Зеркало блока «Назначить машину» у заявки на сборку (AssemblyRequest):
    # переезд между ФФ везёт та же наёмная машина и оплачивается так же.
    # Отличия от заявки: НЕТ WB-пропуска (переезд не едет на маркетплейс) и
    # НЕТ гарда Газельки (агрегатор возит только сборки на WB).
    # Статусную цепочку DRAFT → IN_TRANSIT → COMPLETED машина НЕ трогает:
    # ступень VEHICLE_ASSIGNED не вводим (её читают авто-приём ФФ и отчёты —
    # см. _collect_transfer_fact_candidates), назначенная машина показывается
    # бейджем на черновике.
    #
    # vehicle_info — госномер (как на заявке: у старых записей может лежать
    # свободный текст «номер, водитель, ТК», читатели обязаны терпеть оба).
    vehicle_info: Mapped[str | None] = mapped_column(String(300), nullable=True)
    vehicle_brand: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # ondelete=SET NULL — как у counterparty_id заявки и забора: жёсткое
    # удаление контрагента не должно ронять переезд. Слияние контрагентов
    # перецепляет эту ссылку (counterparty_service.merge), иначе забор с мёртвым
    # id никогда не сматчился бы с выпиской.
    counterparty_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("counterparty.id", ondelete="SET NULL"), nullable=True
    )
    # Логистику оказывает склад забора: перевозчик берётся из
    # Warehouse.counterparty_id склада-ИСТОЧНИКА, а не из введённого ИНН.
    # Флаг — чтобы UI помнил режим при переоткрытии (симметрично заявке).
    logistics_by_warehouse: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    pickup_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    pickup_time_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Вехи цепочки — зеркало AssemblyRequest: «когда ФФ собрал» и «когда уехал».
    # Нужны сводному списку логиста, где переезды идут вперемешку с заявками и
    # обязаны заполнять те же колонки; вывести их из статуса нельзя — статус
    # хранит только ТЕКУЩЕЕ состояние.
    actual_ready_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Транспортная единица переезда — 1:1 с заявкой на сборку (AssemblyRequest):
    # shipped_as_boxes=False → паллеты (по умолчанию), True → короба. Флаг меняет
    # только ЕДИНИЦУ измерения pallets_count/pallet_weight_kg и подписи в UI
    # («Короба» / «Вес 1 короба» vs «Палеты»). При конвертации заявки в переезд
    # единица и её количество переносятся из заявки — иначе переезд терял бы
    # паллеты (у всех шести заявок кейса «транзит Питер/ЕКБ» они проставлены),
    # а ₽/паллета по переездам было бы не посчитать.
    # Nullable (в отличие от заявки): переезд можно завести и без транспортной
    # оценки — например когда машину ещё не считали.
    pallets_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pallet_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    shipped_as_boxes: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Стоимость забора. Деньги хранятся ЗДЕСЬ только как план переезда; фактом
    # оплаты владеет OutboundShipment (снимок логистики + связка с выпиской и
    # заявкой на оплату), который создаётся при отправке перемещения.
    pickup_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    vehicle_assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Переезд, созданный ИЗ заявки на сборку («Переделать в перемещение»).
    # Кейс: ФФ собрал заявку, но товар едет не на WB, а на транзитный склад
    # (в т.ч. после возврата «WB не принял» — ASM-807 → возврат IN-232 →
    # переезд). Заявка остаётся со своей историей и своими зеркалами ФФ,
    # перемещение живёт рядом. Конвертация РАЗРЕШЕНА только когда нетто-сток
    # заявки не списан (движения ASSEMBLY компенсированы возвратом) — иначе
    # отправка переезда списала бы те же единицы второй раз.
    converted_from_assembly_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    items: Mapped[list["StockTransferItem"]] = relationship(
        back_populates="transfer",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_stock_transfers_project_id", "project_id"),
        Index("ix_stock_transfers_counterparty_id", "counterparty_id"),
        # Одна заявка — один живой переезд: проверка «уже сконвертирована» в
        # сервисе не атомарна, двойной клик дал бы два переезда по одному
        # составу и двойное списание при отправке.
        Index(
            "uq_stock_transfers_converted_from_assembly",
            "converted_from_assembly_id",
            unique=True,
            postgresql_where=text("converted_from_assembly_id IS NOT NULL AND is_deleted = false"),
        ),
    )


class StockTransferStatusHistory(Base):
    """Журнал смены статусов переезда — один в один `AssemblyStatusHistory`.

    Раз переезд ведётся как заявка, у него должен быть и тот же ответ на вопрос
    «кто и когда это сделал»: статус двигают трое — человек кнопкой, синк ФФ по
    стадии провайдера (`changed_by='ff_sync'`) и авто-переходы (`'system'`).
    Без журнала разбор «почему сток уехал» упирался бы в пустоту: сам переезд
    хранит только ТЕКУЩИЙ статус.

    Append-only, без soft-delete; каскадно удаляется вместе с переездом.
    """

    __tablename__ = "stock_transfer_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    stock_transfer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False
    )
    old_status: Mapped[str | None] = mapped_column(String(20))
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(50))  # user | ff_sync | system
    comment: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_stock_transfer_status_history_project_id", "project_id"),
        Index("ix_stock_transfer_status_history_transfer_id", "stock_transfer_id"),
    )


class StockTransferItem(Base):
    __tablename__ = "stock_transfer_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    transfer_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stock_transfers.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    transfer: Mapped["StockTransfer"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_stock_transfer_items_project_id", "project_id"),
        Index("ix_stock_transfer_items_transfer_id", "transfer_id"),
        Index("ix_stock_transfer_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Defect Mark Operation (Пометка годных как брак) ─────────────────────


class DefectMarkOperation(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "defect_mark_operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=DefectMarkStatus.ACCEPTED, nullable=False)
    actual_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["DefectMarkOperationItem"]] = relationship(
        back_populates="operation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_defect_mark_operations_project_id", "project_id"),
        Index("ix_defect_mark_operations_warehouse_id", "warehouse_id"),
    )


class DefectMarkOperationItem(Base):
    __tablename__ = "defect_mark_operation_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    operation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("defect_mark_operations.id", ondelete="CASCADE"), nullable=False
    )
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    operation: Mapped["DefectMarkOperation"] = relationship(back_populates="items")

    __table_args__ = (
        Index("ix_defect_mark_operation_items_project_id", "project_id"),
        Index("ix_defect_mark_operation_items_operation_id", "operation_id"),
        Index("ix_defect_mark_operation_items_nomenclature_id", "nomenclature_id"),
    )


# ─── Stock Movements (Журнал движений — аудит) ────────────────────────────


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # +приход / -расход
    defect_delta: Mapped[int] = mapped_column(Integer, default=0)  # +приход / -расход дефектов
    reference_type: Mapped[str] = mapped_column(String(30), nullable=False)  # RECEIPT/SHIPMENT/TRANSFER/ADJUSTMENT
    reference_id: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_stock_movements_project_id", "project_id"),
        Index("ix_stock_movements_warehouse_id", "warehouse_id"),
        Index("ix_stock_movements_nomenclature_id", "nomenclature_id"),
        Index("ix_stock_movements_created_at", "created_at"),
    )


# ─── Warehouse Stock (Материализованный баланс) ───────────────────────────


class WarehouseStock(Base):
    __tablename__ = "warehouse_stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    in_transit: Mapped[int] = mapped_column(Integer, default=0)
    defect_quantity: Mapped[int] = mapped_column(Integer, default=0)
    defect_in_transit: Mapped[int] = mapped_column(Integer, default=0)
    cost_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "nomenclature_id", name="uq_warehouse_stock"),
        Index("ix_warehouse_stock_project_id", "project_id"),
        Index("ix_warehouse_stock_warehouse_id", "warehouse_id"),
        Index("ix_warehouse_stock_nomenclature_id", "nomenclature_id"),
    )


# ─── Stock Adjustment (Корректировка / Инвентаризация) ─────────────────────


class StockAdjustment(Base, TimestampMixin):
    __tablename__ = "stock_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    nomenclature_id: Mapped[int] = mapped_column(Integer, ForeignKey("nomenclature.id"), nullable=False)
    barcode: Mapped[str] = mapped_column(String(50), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)  # +излишек / -недостача
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_stock_adjustments_project_id", "project_id"),
        Index("ix_stock_adjustments_nomenclature_id", "nomenclature_id"),
    )


# ─── Warehouse Delivery Times (Время доставки до WB) ─────────────────────


class WarehouseDeliveryTime(Base, TimestampMixin):
    __tablename__ = "warehouse_delivery_times"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    warehouse_id: Mapped[int] = mapped_column(Integer, ForeignKey("warehouses.id"), nullable=False)
    wb_warehouse_name: Mapped[str] = mapped_column(String(255), nullable=False)
    delivery_days: Mapped[int] = mapped_column(Integer, default=3)

    __table_args__ = (
        UniqueConstraint("project_id", "warehouse_id", "wb_warehouse_name", name="uq_wh_delivery_time"),
        Index("ix_warehouse_delivery_times_project_id", "project_id"),
        Index("ix_warehouse_delivery_times_warehouse_id", "warehouse_id"),
    )
