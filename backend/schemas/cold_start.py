# ruff: noqa: RUF002, RUF003
"""Cold-start distribution schemas — распределение SKU по WB-складам.

Используется для MVP «холодного старта» — расчёт куда лучше отправить новинку
(или товар без своей истории заказов): по долям ФО соседнего проекта-донора
или общероссийскому WB-фолбэку, с учётом исключённых складов и уже идущих
сборок.

Read-only расчёт (никаких записей в БД).
"""

from pydantic import BaseModel, ConfigDict, Field


class DistributeRequest(BaseModel):
    """Запрос на расчёт распределения по WB-складам для одного SKU.

    Attributes:
        nm_id: nomenclature.id (внутренний id артикула в проекте).
        total_qty: сколько штук распределять. None → весь rf_qty (ФФ-остаток).
        min_pack: минимальный размер партии в один ФО. ФО с qty<min_pack
            пулятся в крупнейший ФО. По умолчанию 5.
        window_days: окно для bench-данных (заказы за последние N дней).
            По умолчанию 30.
        bench_from_project_id: id соседнего проекта-донора bench-данных,
            если у текущего проекта < MIN_ORDERS_FOR_OWN_BENCH собственных
            заказов за окно. Опционально.
    """

    nm_id: int
    total_qty: int | None = None
    min_pack: int = Field(5, ge=1, le=1000)
    window_days: int = Field(30, ge=1, le=180)
    bench_from_project_id: int | None = None


class AllocationItem(BaseModel):
    """Одна строка плана распределения: ФО → склад → количество."""

    district_key: str
    district_label: str
    warehouse: str
    share_pct: float
    allocated: int
    already_in_assembly: int
    to_send: int


class SkuInfo(BaseModel):
    """Краткая информация о SKU."""

    nm_id: int
    article_seller: str | None = None
    subject: str | None = None
    brand: str | None = None
    rf_qty: int
    wb_qty: int


class DistributeMeta(BaseModel):
    """Параметры расчёта (для отображения в UI)."""

    min_pack: int
    window_days: int
    excluded_warehouses: list[str]


class DistributeResponse(BaseModel):
    """Ответ распределения cold-start.

    Attributes:
        sku: краткая информация об артикуле (rf_qty, wb_qty, brand, subject).
        total_qty: сколько штук фактически распределено.
        bench_source: источник бенчмарка ("own" | "neighbor:<id>" | "wb_fallback").
        bench_total_orders: заказов за window_days в bench-источнике.
        allocations: план по складам (только склады с allocated>0 или
            already_in_assembly>0).
        district_shares: доли по ФО, использованные в расчёте (debug).
        meta: параметры расчёта.
    """

    model_config = ConfigDict(from_attributes=True)

    sku: SkuInfo
    total_qty: int
    bench_source: str
    bench_total_orders: int
    allocations: list[AllocationItem]
    district_shares: dict[str, float]
    meta: DistributeMeta


class ColdStartTableRow(BaseModel):
    """Одна строка таблицы cold-start: SKU + per-warehouse allocation."""

    nm_id: int
    article_seller: str | None = None
    subject: str | None = None
    brand: str | None = None
    rf_qty: int
    wb_qty: int
    in_assembly_total: int
    sales_14d: int
    revenue_30d: float
    is_newcomer: bool  # first_sale_date IS NULL OR < 14 days
    allocations: dict[str, int]  # warehouse_name → qty
    total_allocated: int


class ColdStartTableResponse(BaseModel):
    """Ответ для таблицы cold-start: список SKU из сегмента + main warehouses."""

    model_config = ConfigDict(from_attributes=True)

    rows: list[ColdStartTableRow]
    main_warehouses: list[dict]  # [{district_key, district_label, warehouse, share_pct}]
    bench_source: str
    bench_total_orders: int
    meta: DistributeMeta
