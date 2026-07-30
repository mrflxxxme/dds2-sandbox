"""
WB FBS schemas: склады продавца и привязки, превью/трансляция остатков,
сборочные задания, стикеры, поставки.

Этот файл — замороженный контракт домена: router, service и фронтовые типы
(`frontend-react/src/types/api.ts`, секция WB FBS) обязаны совпадать с ним.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.models.wb_fbs import (
    FBS_CANCEL_CLIENT_STATUS,
    FBS_CANCEL_SELLER_STATUS,
    FBS_DELIVERED_STATUS,
    FBS_IN_DELIVERY_STATUS,
    FBS_IN_DELIVERY_STUCK_STATUS,
    FBS_SORTED_STATUS,
)

# ─── Allowed enum values ─────────────────────────────────────────────────────

ALLOWED_STOCK_SOURCES = ["ledger", "ff_mirror", "min_of_both"]
ALLOWED_WAREHOUSE_MODES = ["observe", "translate"]
ALLOWED_FBS_MODES = ["safe", "sandbox", "prod"]
ALLOWED_SUPPLIER_STATUSES = ["new", "confirm", "complete", "cancel", "cancel_carrier"]
#: То же + псевдо-статусы фаз доставки — валидны в фильтре списка заданий,
#: но значениями `supplierStatus` не являются (`in_delivery` — ещё едет,
#: `sorted` — принято СЦ, `in_delivery_stuck` — передано давно, СЦ не принял).
ALLOWED_ORDER_STATUS_FILTERS = [
    *ALLOWED_SUPPLIER_STATUSES,
    FBS_IN_DELIVERY_STATUS,
    FBS_SORTED_STATUS,
    FBS_IN_DELIVERY_STUCK_STATUS,
    FBS_DELIVERED_STATUS,
    FBS_CANCEL_CLIENT_STATUS,
    FBS_CANCEL_SELLER_STATUS,
]
ALLOWED_STICKER_TYPES = ["svg", "zplv", "zplh", "png"]
#: Коды причин «не списано» — контракт с фронтом (словарь WRITEOFF_REASON_LABEL
#: в fbsShared.tsx, закреплено тестами с обеих сторон): `no_stock` — остатка
#: нет; `no_card` — нет карточки товара; `no_link` — склад продавца не привязан;
#: `queued` — остаток ЕСТЬ, задание просто ещё не подхвачено прогоном списания
#: (окно 5-минутного джоба / backlog) — это не проблема, а очередь.
ALLOWED_WRITEOFF_REASONS = ["no_stock", "no_card", "no_link", "queued"]
#: Производные состояния поставки (см. `models.wb_fbs.supply_status`) — своего
#: поля статуса Marketplace API не отдаёт.
ALLOWED_SUPPLY_STATUSES = ["active", "to_ship", "in_delivery", "rejected"]


# ─── Режим контура ───────────────────────────────────────────────────────────


class FbsModeOut(BaseModel):
    """Режим контура FBS для баннера на странице (GET /fbs/mode).

    `safe` — читаем боевой WB, но ничего в нём не меняем (кнопки записи гасятся);
    `sandbox` — работаем на тестовых данных песочницы (свой ключ scope Test);
    `prod` — боевой кабинет, запись разрешена.
    """

    mode: str
    write_enabled: bool
    api_base: str
    #: Есть ли активный боевой ключ «Маркетплейс» (`wb_marketplace`).
    has_key: bool
    #: Есть ли активный ключ песочницы (`wb_marketplace_sandbox`).
    has_sandbox_key: bool


# ─── Склады WB (offices) ─────────────────────────────────────────────────────


class FbsOfficeOut(BaseModel):
    """Склад WB (пункт приёма) — `GET /api/v3/offices`, не наша сущность."""

    id: int
    name: str | None = None
    address: str | None = None
    city: str | None = None
    cargo_type: int | None = None
    delivery_type: int | None = None
    federal_district: str | None = None
    selected: bool = False


# ─── Склады продавца WB + привязки ───────────────────────────────────────────


class FbsWarehouseLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    warehouse_id: int
    warehouse_name: str | None = None
    is_active: bool = True
    # Источник остатка определяется автоматически по самому складу — отдельной
    # настройки нет. Эти поля нужны UI, чтобы показать, что именно определилось.
    #: Есть зеркало WMS-провайдера → считаем min(наш учёт, зеркало).
    has_mirror: bool = False
    mirror_rows: int = 0
    mirror_synced_at: datetime | None = None
    mirror_provider: str | None = None
    #: Ключ интеграции склада активен. False при живом зеркале = данные могут протухнуть.
    integration_active: bool = False


class FbsWarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wb_warehouse_id: int
    name: str | None = None
    office_id: int | None = None
    office_name: str | None = None
    cargo_type: int | None = None
    delivery_type: int | None = None
    is_processing: bool = False
    is_deleting: bool = False
    # Наши настройки трансляции
    is_active: bool = False
    stock_source: str = "min_of_both"
    safety_stock_pct: Decimal = Decimal("0")
    safety_stock_abs: int = 0
    max_qty_per_sku: int = 0
    #: observe — только показываем расхождения, translate — пишем остатки в WB.
    mode: str = "observe"
    #: Порог FBO-остатка: отдаём в FBS, только если на складах WB осталось ≤ этого.
    #: None — на FBO не смотрим.
    fbo_max_qty: int | None = None
    #: Авто-учёт сборки FBS: заявки kind=fbs заводит и ведёт джоб (одна на
    #: поставку WB-GI), сток/резерв они не трогают.
    auto_assembly: bool = False
    synced_at: datetime | None = None
    links: list[FbsWarehouseLinkOut] = Field(default_factory=list)


class FbsWarehouseCreate(BaseModel):
    """Создать склад продавца НА СТОРОНЕ WB (`POST /api/v3/warehouses`)."""

    name: str = Field(..., min_length=1, max_length=200)
    office_id: int = Field(..., ge=1)


class FbsWarehouseRename(BaseModel):
    """`PUT /api/v3/warehouses/{id}` — WB разрешает смену офиса раз в сутки."""

    name: str = Field(..., min_length=1, max_length=200)
    office_id: int | None = Field(None, ge=1)


class FbsWarehouseSettingsUpdate(BaseModel):
    """Наши настройки трансляции (WB не трогаем). Все поля опциональны."""

    is_active: bool | None = None
    stock_source: str | None = None
    safety_stock_pct: Decimal | None = Field(None, ge=0, le=100)
    safety_stock_abs: int | None = Field(None, ge=0)
    max_qty_per_sku: int | None = Field(None, ge=0)
    mode: str | None = None
    #: -1 в запросе = «снять гейт» (None не отличить от «не передано»).
    fbo_max_qty: int | None = Field(None, ge=-1)
    #: Авто-учёт сборки FBS: WMS склада сам заводит заявки по FBS-заказам —
    #: мы зеркалим их учётными заявками kind=fbs (одна на поставку WB-GI).
    auto_assembly: bool | None = None
    #: Подтверждение рискованной комбинации «translate + ff_mirror», когда
    #: зеркало ФФ выше нашего учёта: без force сервис отвечает 409 с цифрами
    #: разрыва, с force — применяет как есть (решение человека).
    force: bool = False

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_WAREHOUSE_MODES:
            raise ValueError(f"mode must be one of: {ALLOWED_WAREHOUSE_MODES}")
        return v

    @field_validator("stock_source")
    @classmethod
    def validate_source(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_STOCK_SOURCES:
            raise ValueError(f"stock_source must be one of: {ALLOWED_STOCK_SOURCES}")
        return v


class FbsLinkCreate(BaseModel):
    """Привязать наш склад к складу продавца WB."""

    wb_warehouse_id: int
    warehouse_id: int


# ─── Превью и трансляция остатков ────────────────────────────────────────────


class FbsStockRow(BaseModel):
    """Одна позиция превью — с полной расшифровкой вычетов."""

    barcode: str
    chrt_id: int | None = None
    nomenclature_id: int | None = None
    article_seller: str | None = None
    nm_id: int | None = None
    # Слагаемые формулы
    qty_ledger: int = 0
    qty_ff_mirror: int | None = None
    #: Те же две стороны, но СВОБОДНЫЕ — после снятия резерва сборки (`_free_sides`).
    #: Именно их сравнивает подпись под «Остатком»: наш учёт держит товар до отгрузки,
    #: а WMS обычно снимает его при отборе под заявку, и сравнение сырых цифр
    #: читалось как расхождение там, где его нет. `qty_source` = один из них.
    qty_ledger_free: int = 0
    qty_ff_free: int | None = None
    #: Лежит у ФФ КОРОБАМИ — в остаток не входит: продать штуку из невскрытого
    #: короба нельзя, нужна поштучная приёмка. Это рабочий список «что вскрыть».
    qty_ff_boxed: int = 0
    ff_box_count: int = 0
    qty_source: int = 0
    #: Доступный остаток на складах WB (FBO), сумма по всем складам. None — не считали.
    fbo_qty: int | None = None
    reserved_assembly: int = 0
    fbs_open: int = 0
    defect: int = 0
    buffer: int = 0
    # Итог
    #: Расчётный доступный ДО применения ручного правила.
    qty_computed: int = 0
    qty_available: int = 0
    qty_sent: int | None = None
    qty_confirmed: int | None = None
    #: ЖИВОЙ остаток в кабинете WB (нетто — WB сам вычел заказанное).
    #: None = прочитать не удалось ИЛИ позиция без chrt_id; отличать от 0.
    qty_wb: int | None = None
    #: Ручное количество по этому товару (None — не задано). 0 = не отдавать.
    override_qty: int | None = None
    #: Разрезы для группировки и массового выделения в таблице.
    brand: str | None = None
    subject: str | None = None
    subcategory_id: int | None = None
    subcategory_name: str | None = None
    #: Причина, по которой позиция не транслируется (no_chrt / warehouse_processing / …)
    blocked_reason: str | None = None


# ─── Потоварная замена количества ────────────────────────────────────────────


class FbsOverrideItem(BaseModel):
    """Пара «товар → своё количество» для пер-товарного проставления."""

    nomenclature_id: int
    qty: int = Field(..., ge=0)


class FbsOverrideSet(BaseModel):
    """Задать количество вручную для товаров на складе продавца.

    `qty = 0` — не отдавать позицию вовсе. `qty > 0` — потолок: итог всегда
    `min(qty, рассчитанный доступный)`, поднять выше физического остатка нельзя.
    `qty = null` — снять ручное количество (вернуться к расчёту).

    Две формы тела, ровно одна за раз:
      • `nomenclature_ids` + `qty` — ОДНО число на весь список (ручной ввод);
      • `items` — КАЖДОМУ товару своё число («проставить из зеркала ФФ», где у
        каждой позиции свой остаток). Одним `qty` это не выразить, а слать по
        запросу на позицию нельзя: `rate_limit_write` режет такие серии, и
        полтысячи позиций не проставились бы вовсе.
    """

    wb_warehouse_id: int
    nomenclature_ids: list[int] = Field(default_factory=list, max_length=5000)
    qty: int | None = Field(None, ge=0)
    items: list[FbsOverrideItem] | None = Field(None, max_length=5000)

    @model_validator(mode="after")
    def check_one_form(self) -> "FbsOverrideSet":
        if self.items:
            if self.nomenclature_ids:
                raise ValueError("передайте либо nomenclature_ids + qty, либо items — не оба сразу")
            return self
        if not self.nomenclature_ids:
            raise ValueError("нужен непустой nomenclature_ids (или items)")
        return self


class FbsPushBreakdown(BaseModel):
    """Из чего сложилась разница «Доступно на складе» → «Штук к передаче».

    Цепочка сходится по построению: `ledger_free` минус все `cut_*` равно
    `total_units`, а `cut_other` — остаток, а не отдельный расчёт.
    """

    #: Наш свободный остаток по привязанным складам (ledger − резерв сборки) —
    #: ровно то, что карточка склада показывает как «Доступно».
    ledger_free: int = 0
    #: Срезало зеркало ФФ: провайдер видит меньше нашего учёта («Минимум из двух»).
    cut_by_mirror: int = 0
    #: Страховой буфер склада продавца (процент + абсолютный).
    cut_by_buffer: int = 0
    #: Позиции без chrtId — ключа методов остатков WB у них нет, уехать не могут.
    cut_no_chrt: int = 0
    #: Ручное «Кол-во»: работает как ПОТОЛОК (0 — не отдавать), поднять не может.
    cut_by_override: int = 0
    #: Открытые FBS-задания, потолок max_qty_per_sku, FBO-гейт.
    cut_other: int = 0


class FbsStockPreviewOut(BaseModel):
    wb_warehouse_id: int
    wb_warehouse_name: str | None = None
    warehouse_ids: list[int] = Field(default_factory=list)
    rows: list[FbsStockRow] = Field(default_factory=list)
    #: Удалось ли прочитать живой остаток кабинета. False → `qty_wb` у всех
    #: строк None, и колонку «В WB» рисуем прочерками, а не нулями.
    wb_stock_known: bool = False
    total_rows: int = 0
    total_units: int = 0
    rows_no_chrt: int = 0
    #: Почему `total_units` меньше «Доступно» на карточке склада.
    breakdown: FbsPushBreakdown = Field(default_factory=FbsPushBreakdown)
    is_processing: bool = False


class FbsMatrixCell(BaseModel):
    """Ячейка матрицы: что стоит на складе WB и что могли бы туда поставить."""

    #: Живой остаток кабинета. None — не прочитали (нет ключа / WB не ответил).
    wb: int | None = None
    #: Наш расчёт «сколько отдадим» по привязанным складам.
    can: int = 0
    #: Лежит у ФФ коробами — в `can` не входит (нужна поштучная приёмка).
    #: Реально бывает только у migfull; у прочих провайдеров всегда 0.
    boxed: int = 0
    boxes: int = 0


class FbsFboWarehouse(BaseModel):
    """Строка разбивки «где лежит остаток на FBO»."""

    name: str
    qty: int = 0


class FbsMatrixRow(BaseModel):
    nomenclature_id: int
    barcode: str | None = None
    article_seller: str | None = None
    nm_id: int | None = None
    brand: str | None = None
    subject: str | None = None
    #: Ключ — wb_warehouse_id строкой (JSON-объект не умеет int-ключи).
    cells: dict[str, FbsMatrixCell] = Field(default_factory=dict)
    total_wb: int = 0
    total_can: int = 0
    #: Сколько добавится к «можем» после поштучной приёмки коробов у ФФ.
    total_boxed: int = 0
    total_boxes: int = 0
    #: Остаток на складах WB (FBO). None — зеркала нет / судить нечем.
    fbo: int | None = None
    #: Где именно он лежит — раскрывается по клику. Без СЦ, транзита и
    #: возвратов «в пути», без исключённых и сгоревших складов.
    fbo_by_warehouse: list[FbsFboWarehouse] = Field(default_factory=list)
    #: Деньги за окно тренда — по КАРТОЧКЕ, по складам не делятся.
    revenue: Decimal = Decimal(0)
    profit: Decimal = Decimal(0)
    #: None — выручки не было; ноль означал бы «продавали в ноль».
    margin_pct: float | None = None
    sale_qty: int = 0
    avg_daily_qty: float = 0


class FbsMatrixWarehouse(BaseModel):
    wb_warehouse_id: int
    name: str | None = None


class FbsMatrixOut(BaseModel):
    warehouses: list[FbsMatrixWarehouse] = Field(default_factory=list)
    rows: list[FbsMatrixRow] = Field(default_factory=list)
    #: False → все `cells[*].wb` = None, рисуем прочерк, а не ноль.
    wb_stock_known: bool = False
    trend_days: int = 14


# ─── Сверка с кабинетом ──────────────────────────────────────────────────────


class FbsReconcileRow(BaseModel):
    """Расхождение между тем, что стоит в кабинете WB, и тем, что мы отдаём."""

    chrt_id: int
    barcode: str | None = None
    article_seller: str | None = None
    nm_id: int | None = None
    #: Что сейчас реально стоит в кабинете WB.
    wb_amount: int = 0
    #: Что отдаст наш расчёт (0 = не отдаём вовсе).
    our_amount: int = 0
    #: unmanaged — мы позицией не управляем, а она продаётся; mismatch — цифры разошлись.
    kind: str = "unmanaged"
    reason: str | None = None


class FbsReconcileOut(BaseModel):
    """Сверка склада продавца: что висит в WB помимо нашей трансляции.

    Нужна потому, что дельта-пуш не трогает позиции, которых он не знает: остаток,
    проставленный в кабинете руками ДО подключения, продолжит продаваться.
    """

    wb_warehouse_id: int
    wb_warehouse_name: str | None = None
    checked: int = 0
    unmanaged_positions: int = 0
    unmanaged_units: int = 0
    mismatch_positions: int = 0
    rows: list[FbsReconcileRow] = Field(default_factory=list)
    #: Позиции без chrtId — их состояние в WB проверить нечем.
    rows_no_chrt: int = 0


class FbsReconcileApply(BaseModel):
    """Применить сверку: обнулить в WB то, чем мы не управляем."""

    wb_warehouse_id: int
    #: Пусто — все `unmanaged`-позиции; иначе только эти chrtId (фильтр по свежей сверке).
    chrt_ids: list[int] = Field(default_factory=list)


# ─── Трансляция остатков ─────────────────────────────────────────────────────


class FbsStockPushRequest(BaseModel):
    #: Пусто — все активные склады продавца.
    wb_warehouse_ids: list[int] = Field(default_factory=list)
    #: True — слать все позиции, не только изменившиеся с прошлого пуша.
    force: bool = False
    #: Точечная отправка: только эти chrtId. Пусто — весь склад.
    #: Правка одной позиции не должна гонять 1200 строк и жечь лимит WB.
    chrt_ids: list[int] = Field(default_factory=list)


class FbsStockPushOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wb_warehouse_id: int | None = None
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_total: int = 0
    rows_changed: int = 0
    rows_sent: int = 0
    rows_no_chrt: int = 0
    rows_mismatch: int = 0
    error_msg: str | None = None


# ─── Сборочные задания ───────────────────────────────────────────────────────


class FbsOrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wb_order_id: int
    rid: str | None = None
    created_at_wb: datetime | None = None
    wb_warehouse_id: int | None = None
    office_name: str | None = None
    nm_id: int | None = None
    chrt_id: int | None = None
    barcode: str | None = None
    article: str | None = None
    subject: str | None = None
    price: Decimal | None = None
    sale_price: Decimal | None = None
    currency_code: str | None = None
    cargo_type: int | None = None
    is_zero_order: bool = False
    is_pickup_point_shipment_allowed: bool = False
    supplier_status: str
    wb_status: str | None = None
    is_cancellable: bool = False
    supply_id: str | None = None
    sticker_barcode: str | None = None
    sticker_part_a: str | None = None
    sticker_part_b: str | None = None
    ddate: date | None = None
    comment: str | None = None
    written_off_at: datetime | None = None
    synced_at: datetime
    #: Сколько дней задание едет: от передачи поставки (scan_dt → closed_at →
    #: written_off_at, что есть) до сейчас. Только у `complete`, ещё не принятых
    #: СЦ; у прочих фаз и без якоря — None.
    transit_days: int | None = None
    #: Фаза ПОСТАВКИ задания — для кабинетного статуса строки: закрыта ли
    #: (`done`) и отсканирован ли QR (`scan_dt`). Без них строка «complete +
    #: waiting» читалась как противоречие: закрыто у нас ≠ сдано WB.
    supply_done: bool | None = None
    supply_scan_dt: datetime | None = None
    #: Оценка штрафа WB за отмену (≈, см. `FbsCancelStats`) — заполняется ТОЛЬКО
    #: в корзине «Наша отмена», чтобы итог плашки был разложим по строкам.
    #: `None` = не корзина отмен либо нет ставки комиссии категории.
    penalty_est: Decimal | None = None
    #: Цена задания В РУБЛЯХ (канон `revenue_rub_expr`): рубль → sale_price →
    #: price; валюта СНГ → пересчёт WB (`converted_price`). None — заказ в
    #: чужой валюте, а пересчёт WB не прислал: показать номинал нельзя.
    price_rub: Decimal | None = None


class FbsCancelStats(BaseModel):
    """Сводка корзины отмен (`status=cancel_client|cancel_seller`) по ВСЕЙ
    выборке фильтра: потерянная выручка + штраф WB оценкой И фактом.

    Два числа по штрафу намеренно: оценка есть сразу, но она приблизительная
    (верхняя граница), факт точен до копейки, но садится в финотчёт WB примерно
    на пятый день после даты заказа. Подробности расчёта и почему оценка выше
    факта — в докстринге `services/wb_fbs/cancel_penalty.py`.
    """

    #: Потерянная выручка корзины: Σ coalesce(sale_price, price) по ВСЕЙ
    #: выборке фильтра, а не по видимой странице.
    revenue: Decimal = Decimal(0)
    #: Заданий в выборке (дубль `total` — чтобы плашка не зависела от пагинации).
    orders: int = 0

    #: Оценка штрафа по правилам WB. У клиентских отмен всегда 0: такой штраф
    #: («отмена покупателем из-за задержки продавца») отличить от обычного
    #: отказа нечем, и в финотчёте проекта он не встречался ни разу.
    penalty_est: Decimal = Decimal(0)
    #: По скольким заданиям оценка реально посчиталась.
    penalty_est_count: int = 0
    #: Заданий без ставки комиссии в `wb_tariffs` — по ним штраф НЕ выдуман, а
    #: пропущен; цифра объясняет расхождение `penalty_est_count` и `orders`.
    no_commission_count: int = 0
    #: Оценка усечена лимитом строк (очень широкое окно) — итог неполный.
    estimate_truncated: bool = False

    #: Факт: удержания «Штраф МП. Невыполненный заказ (отмена продавцом)» из
    #: финотчёта WB за то же окно дат заказа.
    penalty_fact: Decimal = Decimal(0)
    penalty_fact_count: int = 0
    #: До какой даты доехал финотчёт. Ноль факта по отменам свежее этой даты —
    #: не «штрафа нет», а «WB ещё не выставил».
    fact_covered_to: date | None = None
    #: Факт скрыт: включён фильтр склада WB, а у строк финотчёта склада нет —
    #: сумма по всем складам под заголовком одного вводила бы в заблуждение.
    fact_scoped_out: bool = False


class FbsOrderListOut(BaseModel):
    items: list[FbsOrderOut] = Field(default_factory=list)
    total: int = 0
    #: Счётчики по статусам для вкладок (new / confirm / complete / cancel).
    status_counts: dict[str, int] = Field(default_factory=dict)
    #: Две ФАЗЫ внутри `complete`: «ещё едет, не отсортировано» и «принято
    #: сортировочным центром». Отдельными полями, а не ключами `status_counts` —
    #: сумма счётчиков и так равна вкладке «Все».
    in_delivery_count: int = 0
    sorted_count: int = 0
    #: Зависшие в пути: переданы ≥ N дней назад, СЦ так и не принял. Окно
    #: ограничено сверху (см. сервис): у старых `complete` wb_status застывает
    #: (синк опрашивает не всё), и без потолка счётчик копил бы мёртвые строки.
    in_delivery_stuck_count: int = 0
    #: «Завершённые» кабинета WB: complete, дошедшее до покупателя (sold/defect).
    #: Окно периода применяется как к прочим фазам доставки.
    delivered_count: int = 0
    #: Разрез отмен (канон 30.07): клиент отменил / отменили мы (продавец или
    #: перевозчик). Сумма равна счётчику отмен эффективного статуса.
    cancel_client_count: int = 0
    cancel_seller_count: int = 0
    #: Сводка выручки/штрафа корзины отмен — ТОЛЬКО при фильтре
    #: status=cancel_client|cancel_seller (лишний агрегат прочим фазам не нужен).
    cancel_stats: FbsCancelStats | None = None


class FbsWarehouseQueueRow(BaseModel):
    """Очередь одного склада продавца по фазам жизни задания."""

    wb_warehouse_id: int
    #: Очередь сборки — БЕЗ периода: задание, созданное два месяца назад и до
    #: сих пор не собранное, обязано быть в цифре сборщика.
    new: int = 0
    confirm: int = 0
    #: Фазы доставки — за выбранный период (`complete` копится вечно).
    in_delivery: int = 0
    sorted: int = 0
    #: Зависшие в пути (передано ≥ N дней, СЦ не принял) — БЕЗ периода, как
    #: очередь сборки: пока не отсортировано — вопросы к нам, и зависшее
    #: обязано быть видно независимо от выбранного окна.
    in_delivery_stuck: int = 0


class FbsWarehouseSummaryOut(BaseModel):
    """Сводка «сколько на каждом складе и на какой фазе» одним запросом."""

    #: Границы окна для фаз доставки (включительно). None — окно не задано.
    date_from: date | None = None
    date_to: date | None = None
    warehouses: list[FbsWarehouseQueueRow] = Field(default_factory=list)
    #: Итог по всем складам — считает бэкенд, чтобы «Все склады» не расходились
    #: с суммой карточек при обрезке выдачи.
    totals: dict[str, int] = Field(default_factory=dict)


class FbsWriteoffIssueRow(BaseModel):
    """Переданные задания, которые НЕЧЕМ списать со склада (агрегат по товару).

    Списание идемпотентно ретраится каждые 5 минут, но пока причина жива,
    задание молча висит `written_off_at IS NULL` — единственным следом был
    warning в логе воркера. Эта строка делает отказ видимым.
    """

    wb_warehouse_id: int | None = None
    wb_warehouse_name: str | None = None
    #: Привязанный наш склад (первый активный) — куда списание пыталось попасть.
    warehouse_id: int | None = None
    warehouse_name: str | None = None
    nomenclature_id: int | None = None
    article: str | None = None
    barcode: str | None = None
    #: Сколько заданий не списано по этому товару на этом складе продавца.
    stuck: int = 0
    #: Самое старое непроведённое (created_at_wb) — возраст проблемы.
    oldest_at: datetime | None = None
    #: Остаток на привязанных складах: почему «нечем» (обычно 0).
    our_qty: int = 0
    our_defect: int = 0
    #: Россыпь в зеркале ФФ (None — зеркала нет): физически товар у провайдера
    #: может лежать — сигнал, что наш ledger отстал, а не что товара нет.
    ff_loose: int | None = None
    #: Код из `ALLOWED_WRITEOFF_REASONS`: no_stock — остатка нет | no_card —
    #: нет карточки товара | no_link — склад продавца не привязан | queued —
    #: остаток есть, ждёт ближайшего прогона списания (не проблема).
    reason: str = "no_stock"


class FbsWriteoffIssuesOut(BaseModel):
    """Сводка незакрытых списаний: `GET /fbs/orders/writeoff-issues`."""

    total_orders: int = 0
    rows: list[FbsWriteoffIssueRow] = Field(default_factory=list)
    #: Выдача срезана капом строк (total_orders считается ДО среза) — фронт
    #: обязан сказать «показаны первые N», а не молча спрятать хвост.
    truncated: bool = False


class FbsOrderTimelineEvent(BaseModel):
    """Строка таймлайна «Статус заказа» (модалка по заданию, как в кабинете WB).

    Смесь двух источников, оба уже отсортированы сервисом по времени:
    - `kind="anchor"` — синтетический якорь из ТОЧНОЙ даты (задание оформлено,
      поставка закрыта, QR отсканирован, списано в DDS);
    - `kind="event"` — переход из журнала `wb_fbs_order_events`; его время —
      момент ФИКСАЦИИ синком (точность = каденс, 5 мин), об этом говорит
      `approx=True`.
    """

    kind: str = "event"
    #: Человеческий ярлык рисует фронт по коду (словарь TIMELINE_LABEL) — здесь
    #: машинный код: created | assembling | assembled | scanned | written_off |
    #: wb:<status> | supplier:<status>.
    code: str
    at: datetime | None = None
    #: Время приблизительное (зафиксировано синком, не WB).
    approx: bool = False


class FbsOrderTimelineOut(BaseModel):
    """`GET /fbs/orders/{wb_order_id}/timeline`."""

    wb_order_id: int
    article: str | None = None
    subject: str | None = None
    nm_id: int | None = None
    events: list[FbsOrderTimelineEvent] = Field(default_factory=list)


class FbsOrderBackfillRequest(BaseModel):
    """Обратная загрузка истории заданий из `GET /api/v3/orders`.

    WB держит задания 3 месяца и отдаёт окном не больше 30 дней за запрос —
    сервис сам режет период на окна, здесь только глубина.
    """

    days: int = Field(default=90, ge=1, le=90)


class FbsOrderBackfillOut(BaseModel):
    """Итог бэкфилла. `written_off_marked` — сколько исторических заданий
    помечены уже списанными, чтобы не вычесть со склада продажи прошлых месяцев."""

    ok: bool = True
    fetched: int = 0
    upserted: int = 0
    written_off_marked: int = 0
    windows: int = 0
    message: str | None = None


# ─── Статистика заказов за период ────────────────────────────────────────────


class FbsOrderStatRow(BaseModel):
    """Строка разреза: ярлык → штуки и рубли. Доли считает фронт."""

    label: str
    orders_count: int = 0
    orders_sum: Decimal = Decimal("0")


class FbsOrderStatDay(BaseModel):
    """Точка дневного графика. День — МСК-сутки, как в воронке продаж."""

    day: date
    orders_count: int = 0
    orders_sum: Decimal = Decimal("0")


class FbsOrderStatsFunnel(BaseModel):
    """Весь объём заказов проекта по воронке WB — знаменатель доли FBS.

    Доля считается за ОДИНАКОВЫЕ дни с обеих сторон: воронка отстаёт от заданий,
    и полный период FBS, делённый на неполный период воронки, давал бы кратно
    завышенную долю. `fbs_*` — цифры FBS ровно за покрытый воронкой отрезок.
    """

    orders_count: int = 0
    orders_sum: Decimal = Decimal("0")
    #: false — воронка за период пуста (синк не проходил). Долю показывать нельзя:
    #: деление на ноль дало бы «100 % FBS» там, где данных просто нет.
    has_data: bool = False
    #: Реально покрытый воронкой отрезок внутри запрошенного периода.
    covered_from: date | None = None
    covered_to: date | None = None
    #: Покрывает ли воронка ВЕСЬ период. false — доля посчитана за подотрезок,
    #: и подпись обязана назвать какой: иначе цифра читается как «за весь период».
    full_period: bool = False
    #: Заказы FBS за тот же отрезок — числитель, сопоставимый со знаменателем.
    fbs_orders_count: int = 0
    fbs_orders_sum: Decimal = Decimal("0")


class FbsOrderStatsOut(BaseModel):
    """Сводка заказов FBS за период.

    `orders_count` включает отменённые — воронка WB их тоже не вычитает, а весь
    смысл блока в сопоставимости с ней. Отменённые вынесены отдельной парой полей.
    """

    date_from: date
    date_to: date
    wb_warehouse_id: int | None = None
    orders_count: int = 0
    orders_sum: Decimal = Decimal("0")
    cancelled_count: int = 0
    cancelled_sum: Decimal = Decimal("0")
    by_day: list[FbsOrderStatDay] = Field(default_factory=list)
    by_status: list[FbsOrderStatRow] = Field(default_factory=list)
    by_brand: list[FbsOrderStatRow] = Field(default_factory=list)
    by_subject: list[FbsOrderStatRow] = Field(default_factory=list)
    by_subcategory: list[FbsOrderStatRow] = Field(default_factory=list)
    funnel: FbsOrderStatsFunnel = Field(default_factory=FbsOrderStatsFunnel)


class FbsStickerRequest(BaseModel):
    """WB: максимум 100 заданий за запрос, только статусы confirm/complete."""

    order_ids: list[int] = Field(..., min_length=1, max_length=100)
    type: str = Field(default="png")
    width: int = Field(default=58)
    height: int = Field(default=40)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ALLOWED_STICKER_TYPES:
            raise ValueError(f"type must be one of: {ALLOWED_STICKER_TYPES}")
        return v

    @field_validator("width")
    @classmethod
    def validate_width(cls, v: int) -> int:
        if v not in (58, 40):
            raise ValueError("width must be 58 or 40")
        return v

    @field_validator("height")
    @classmethod
    def validate_height(cls, v: int) -> int:
        if v not in (40, 30):
            raise ValueError("height must be 40 or 30")
        return v


class FbsStickerOut(BaseModel):
    order_id: int
    part_a: str | None = None
    part_b: str | None = None
    barcode: str | None = None
    #: base64-содержимое файла в запрошенном формате.
    file: str | None = None


# ─── Поставки ────────────────────────────────────────────────────────────────


class FbsSupplyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wb_supply_id: str
    name: str | None = None
    done: bool = False
    created_at_wb: datetime | None = None
    closed_at: datetime | None = None
    scan_dt: datetime | None = None
    reject_dt: datetime | None = None
    #: Производное от `done`/`scan_dt`/`reject_dt` — см. `supply_status`.
    #: `done` остаётся в контракте: на нём завязаны кнопки «Передать»/«Удалить».
    status: str = "active"
    cargo_type: int | None = None
    cross_border_type: int | None = None
    is_b2b: bool = False
    destination_office_id: int | None = None
    #: Имя пункта приёма WB из справочника `/api/v3/offices` (кэш 10 мин).
    destination_office_name: str | None = None
    wb_warehouse_id: int | None = None
    #: Заданий в поставке ПО ДАННЫМ WB (`GET /supplies/{id}/order-ids`).
    #: None — состав ещё не спрашивали. Отличается от `orders_count`, который
    #: считает наши зеркальные задания: до бэкфилла истории он почти везде 0.
    wb_orders_count: int | None = None
    orders_count: int = 0
    qr_barcode: str | None = None


class FbsSupplyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class FbsSupplyAddOrders(BaseModel):
    """WB bulk: до 100 заданий за запрос; задания переходят в `confirm`."""

    order_ids: list[int] = Field(..., min_length=1, max_length=100)


class FbsSupplyPlanGroup(BaseModel):
    """Одна будущая поставка: WB запрещает смешивать склады и габариты.

    Габарит и трансграничность поставки фиксирует ПЕРВОЕ добавленное задание,
    поэтому группировать надо заранее, а не ловить 409 от WB.
    """

    wb_warehouse_id: int | None = None
    wb_warehouse_name: str | None = None
    cargo_type: int | None = None
    cross_border_type: int | None = None
    order_ids: list[int] = Field(default_factory=list)
    orders_count: int = 0
    #: Активная поставка той же группы — можно доложить вместо создания новой.
    existing_supply_id: str | None = None
    #: Почему группу нельзя отправить (напр. задание уже в другой поставке).
    blocked_reason: str | None = None


class FbsSupplyPlanOut(BaseModel):
    """Предпросмотр разбиения выделенных заданий на поставки."""

    groups: list[FbsSupplyPlanGroup] = Field(default_factory=list)
    total_orders: int = 0
    #: Сколько поставок получится (групп без blocked_reason).
    supplies_count: int = 0


class FbsSupplyBulkCreate(BaseModel):
    """Создать поставки по плану: одна на каждую группу «склад + габарит»."""

    order_ids: list[int] = Field(..., min_length=1, max_length=2000)
    #: Префикс имени: к нему добавится склад, чтобы поставки различались.
    name_prefix: str | None = Field(None, max_length=80)
    #: Докладывать в существующую активную поставку группы, если она есть.
    reuse_existing: bool = True


class FbsSupplyBulkOut(BaseModel):
    created: list[FbsSupplyOut] = Field(default_factory=list)
    #: Поставки, в которые доложили задания (не создавали новую).
    reused: list[FbsSupplyOut] = Field(default_factory=list)
    orders_attached: int = 0
    errors: list[str] = Field(default_factory=list)


class FbsPickListRow(BaseModel):
    """Строка листа подбора: что и сколько физически собрать со склада."""

    nomenclature_id: int | None = None
    barcode: str | None = None
    chrt_id: int | None = None
    nm_id: int | None = None
    article: str | None = None
    subject: str | None = None
    brand: str | None = None
    #: Сколько единиц этой позиции в поставке (заданий на неё).
    qty: int = 0
    #: Сумма `sale_price` заданий позиции (фолбэк — `price`, если пусто) — то же
    #: поле, что в колонке «Цена, ₽» списков заданий, для сверки при отгрузке.
    amount: Decimal | None = None
    #: Остаток на привязанных наших складах — сборщику видно, хватает ли.
    stock_available: int | None = None


class FbsPickListOut(BaseModel):
    """Лист подбора поставки — агрегат заданий по товару, а не по заказам."""

    wb_supply_id: str
    supply_name: str | None = None
    wb_warehouse_id: int | None = None
    wb_warehouse_name: str | None = None
    cargo_type: int | None = None
    done: bool = False
    created_at_wb: datetime | None = None
    orders_count: int = 0
    #: Число уникальных позиций (строк листа).
    positions_count: int = 0
    total_qty: int = 0
    total_amount: Decimal | None = None
    rows: list[FbsPickListRow] = Field(default_factory=list)


class FbsSupplyBarcodeOut(BaseModel):
    barcode: str | None = None
    file: str | None = None


# ─── Результаты действий ─────────────────────────────────────────────────────


class FbsActionOut(BaseModel):
    """Единый ответ write-действий: что реально произошло."""

    ok: bool = True
    message: str | None = None
    affected: int = 0
