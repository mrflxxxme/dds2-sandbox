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

#: 🔴 `wbStatus` — ВТОРАЯ, независимая ось статуса: `supplierStatus` говорит, что
#: сделал ПРОДАВЕЦ, а `wbStatus` — что случилось на стороне WB. Покупатель может
#: отказаться ДО того, как продавец собрал задание, и тогда `supplierStatus`
#: навсегда остаётся `new` — WB его уже не двинет. Ориентируясь только на
#: `supplierStatus`, мы считали такие задания живой очередью сборки: они
#: раздували «Новые» на карточках складов, не попадали во вкладку «Отменено»,
#: шли в выручку как живые и — дороже всего — ДЕРЖАЛИ ОСТАТОК через
#: `FBS_OPEN_STATUSES`, занижая то, что транслируется в WB (прод 26.07: 20 таких
#: заданий, из них 3 на складе «Пушкино»).
FBS_WB_CANCELLED_STATUSES: tuple[str, ...] = (
    "canceled",  # отмена
    "canceled_by_client",  # отмена покупателем
    "declined_by_client",  # отказ покупателя до получения
)

#: Подмножество отмен, где решение принял ПОКУПАТЕЛЬ (канон владельца
#: 30.07: отмены делятся «клиент отменил / мы отменили»). Всё остальное из
#: `FBS_WB_CANCELLED_STATUSES` + supplier cancel/cancel_carrier — наша сторона
#: (продавец / перевозчик).
FBS_WB_CLIENT_CANCEL_STATUSES: tuple[str, ...] = (
    "canceled_by_client",
    "declined_by_client",
)

#: `wbStatus`, на которых путь заказа ЗАКОНЧИЛСЯ доставкой: товар у покупателя
#: (или списан в брак). Сам `supplierStatus` навсегда остаётся `complete` и на
#: вопрос «что ещё в пути» не отвечает — эту ось знает только `wbStatus`.
FBS_WB_DELIVERED_STATUSES: tuple[str, ...] = (
    "sold",  # получено покупателем
    "defect",  # брак — до покупателя не доедет, но и в пути больше не числится
)

#: Отдельная ФАЗА между передачей и вручением: заказ ПРОШЁЛ сортировочный
#: центр WB (СЦ и дальше по цепочке — ПВЗ, отложенная доставка). Считать её
#: вместе с «едет к СЦ» нельзя — это разные этапы и разная зона
#: ответственности: пока задание не отсортировано, вопросы по нему ещё к нам,
#: после — уже к логистике WB. Пост-сортировочные статусы обязаны быть здесь:
#: чёрный список «complete минус sorted минус sold» возвращал `ready_for_pickup`
#: обратно в «едет к СЦ» — 168 заказов, лежащих в ПВЗ, светились «зависшими»
#: (прод 30.07.2026).
FBS_WB_SORTED_STATUSES: tuple[str, ...] = ("sorted", "ready_for_pickup", "postponed_delivery")

#: ДО-сортировочные `wbStatus`: заказ передан и физически едет к СЦ. Белый
#: список для фазы «в пути» и сигнала «зависло»: у алярма ложный пропуск
#: дешевле ложной тревоги, поэтому неизвестный новый статус WB по умолчанию
#: НЕ считается «едет к СЦ» (и не зажигает «зависло»). Пустой/NULL `wb_status`
#: остаётся «в пути» — строки до появления колонки. Наблюдаемое на проде
#: 30.07.2026: waiting 2386×complete; sent_to_carrier/accepted_by_carrier —
#: известные статусы WB того же этапа, живьём пока не встречались.
FBS_WB_PRE_SORT_STATUSES: tuple[str, ...] = ("waiting", "sent_to_carrier", "accepted_by_carrier")

#: Псевдо-статусы фильтра выдачи. Значениями `supplierStatus` не являются и в
#: зеркале не хранятся — только фильтр и счётчик (см. `orders_service`):
#:   `in_delivery` — передано в WB, ещё НЕ отсортировано;
#:   `sorted`      — принято сортировочным центром, ещё не у покупателя;
#:   `in_delivery_stuck` — подмножество `in_delivery`: передано давно (порог в
#:   `orders_service`), а СЦ так и не принял — зависло у перевозчика/на СЦ.
FBS_IN_DELIVERY_STATUS = "in_delivery"
FBS_SORTED_STATUS = "sorted"
FBS_IN_DELIVERY_STUCK_STATUS = "in_delivery_stuck"
#: «Завершённые» кабинета WB: `complete`, дошедшее до покупателя (sold/defect).
FBS_DELIVERED_STATUS = "delivered"
#: Разрез отмен: клиентские (FBS_WB_CLIENT_CANCEL_STATUSES) и наши
#: (supplier cancel/cancel_carrier + wb canceled без клиентского признака).
FBS_CANCEL_CLIENT_STATUS = "cancel_client"
FBS_CANCEL_SELLER_STATUS = "cancel_seller"


class FbsSupplyStatus(str, enum.Enum):
    """Состояние поставки в терминах кабинета WB.

    Собственного поля статуса Marketplace API не отдаёт: в payload'е поставки
    есть только `done`, `scanDt` и `rejectDt` — кабинет рисует свои ярлыки сам.
    Раскладываем ту же тройку в те же четыре состояния, иначе весь `done=true`
    схлопывается в одну строку и наш экран расходится с кабинетом (52
    «переданных» против «В доставке 44»).
    """

    ACTIVE = "active"  # `done=false` — черновик, задания ещё докладываются
    TO_SHIP = "to_ship"  # закрыта, QR не отсканирован — «Отгрузите поставку»
    IN_DELIVERY = "in_delivery"  # закрыта, QR отсканирован — «Поставка в обработке»
    REJECTED = "rejected"  # `rejectDt` — WB отклонил приёмку


def supply_status(done: bool, scan_dt: datetime | None, reject_dt: datetime | None) -> str:
    """`done`/`scanDt`/`rejectDt` → состояние поставки.

    Порядок проверок значим: `rejectDt` перебивает всё (отклонённая поставка
    остаётся `done=true` со сканом), а `scanDt` различает «лежит у нас, не
    отгружена» и «уехала в WB» — это и есть граница вкладок кабинета.

    Приёмка («Завершена») из этой тройки не выводится: факта приёмки в payload'е
    поставки нет вообще. Он виден только по заданиям поставки — см. DOMAIN_WB_FBS.
    """
    if reject_dt is not None:
        return FbsSupplyStatus.REJECTED.value
    if not done:
        return FbsSupplyStatus.ACTIVE.value
    return FbsSupplyStatus.TO_SHIP.value if scan_dt is None else FbsSupplyStatus.IN_DELIVERY.value


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
    #: Авто-учёт сборки FBS: у склада WMS провайдера сам заводит свои заявки по
    #: FBS-заказам — наша система зеркалит их учётными заявками kind=fbs
    #: (одна на поставку WB-GI-…, статусы ведёт джоб). Сток/резерв зеркало не
    #: трогает: списание остаётся за `writeoff_completed_orders`.
    auto_assembly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

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


# ─── Журнал переходов статусов заданий ──────────────────────────────────────


class WbFbsOrderEvent(Base):
    """Переход статуса задания FBS — строка таймлайна «Статус заказа».

    WB Marketplace API истории НЕ отдаёт (только текущие статусы), поэтому
    журнал пишет наш синк В МОМЕНТ ОБНАРУЖЕНИЯ перехода: `changed_at` — время
    фиксации (точность = каденс синка, 5 мин), не время события у WB. Точные
    даты прошлого дают синтетические якоря модалки (created_at_wb задания,
    closed_at/scan_dt поставки, written_off_at) — их в журнал не пишем, чтобы
    не дублировать источники истины.

    Append-only, без SoftDelete (как `FulfillmentStatusEvent`): каскадно
    умирает с заданием, повторный синк без изменений событий не плодит.
    """

    __tablename__ = "wb_fbs_order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wb_fbs_orders.id", ondelete="CASCADE"), nullable=False
    )
    #: Какая ось сменилась: supplier_status | wb_status.
    axis: Mapped[str] = mapped_column(String(20), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(30), nullable=True)
    new_value: Mapped[str] = mapped_column(String(30), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (
        # Таймлайн одного задания — единственный читатель журнала.
        Index("ix_wb_fbs_order_events_order", "project_id", "order_id", "changed_at"),
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
    #: `rejectDt` — WB отклонил приёмку поставки. Вместе с `done`/`scan_dt`
    #: раскладывается в `FbsSupplyStatus` (см. `supply_status`).
    reject_dt: Mapped[datetime | None] = mapped_column(DateTime)

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
