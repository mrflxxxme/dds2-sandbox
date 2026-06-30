# ruff: noqa: RUF001, RUF002, RUF003
"""Схемы домена «Ценообразование» (наценка по артикулам)."""

from datetime import datetime

from pydantic import BaseModel


class PricingRow(BaseModel):
    """Артикул: текущая цена ВБ + себестоимость + расходы ВБ → наценка."""

    nm_id: int
    vendor_code: str | None = None
    brand: str | None = None
    subject: str | None = None
    category: str
    size: str = ""  # размер из артикула (override → parse_size → «Без размера», + алиас)

    # Текущая цена витрины (WB API «Цены и скидки»)
    current_price: float | None = None  # discountedPrice — витрина (до СПП)
    base_price: float | None = None  # price — до seller-скидки
    discount: float | None = None  # seller-скидка %

    # Себестоимость единицы (override → avg по закупкам → склад)
    cost_price: float | None = None
    has_cost: bool = False
    has_price: bool = False

    # Наценка (на текущую цену)
    markup_coef: float | None = None  # цена / себест (множитель)
    markup_pct: float | None = None  # (цена − себест) / себест × 100
    cost_share_pct: float | None = None  # себест / цена × 100

    # СПП и цена для покупателя
    spp_rate: float = 0  # % (из воронки)
    buyer_price: float | None = None  # цена × (1 − СПП)

    # Юнит-экономика по факту за период (из воронки; 0 если продаж не было)
    orders_count: int = 0
    revenue: float = 0
    wb_expenses: float = 0  # все удержания ВБ (выручка − к перечислению)
    adv_sum: float = 0
    tax: float = 0
    cost_total: float = 0
    profit: float = 0
    margin_pct: float = 0  # прибыль / выручка × 100
    net_markup_pct: float | None = None  # прибыль / себестоимость × 100 (наценка после расходов ВБ)

    # Остатки ВБ и производные
    wb_stock: int = 0  # остаток на складах ВБ (quantity_full)
    own_stock: int = 0  # наш склад (WarehouseStock)
    assembly_stock: int = 0  # в активных сборках (до отгрузки)
    transit_stock: int = 0  # в пути на ВБ (отгружено, не принято)
    total_stock: int = 0  # весь товар = ВБ + наш склад + сборка + в пути
    is_new: bool = False  # новинка (first_sale_date пуст или недавняя) — не неликвид, а раскачка
    stock_value_cost: float | None = None  # остаток × себест — заморожено в товаре, ₽
    stock_potential_profit: float | None = None  # остаток × прибыль/ед — потенциальная прибыль остатка, ₽
    stock_potential_revenue: float | None = None  # остаток × цена витрины — потенциальная выручка, ₽
    days_left: float | None = None  # дней до исчерпания (по темпу продаж за период)
    sales_per_month: float | None = None  # оборачиваемость: продаж/мес (по темпу периода)
    anomaly: str | None = None  # метка аномалии (или None если строка нормальная)

    # Доп. метрики для решений по цене
    breakeven_price: float | None = None  # мин. цена витрины для нулевой прибыли (учёт СПП/комиссии/налога)
    safety_margin_pct: float | None = None  # запас прочности: (цена − безубыток)/цена × 100 — куда можно снижать
    drr: float = 0  # ДРР: реклама / выручка × 100
    cr: float = 0  # конверсия в заказ, % (клики→заказы) — диагностика «дорогой» рекламы
    gmroi: float | None = None  # валовая маржа / заморожено в остатке — отдача на ₽ в товаре (бенчмарк ≥3)
    sell_through_pct: float | None = None  # продано / (продано + остаток) × 100 — скорость распродажи
    elasticity: float | None = None  # эластичность спроса по цене (оценка по истории 90 дн, обычно < 0)
    elasticity_label: str = ""  # «эластичный» / «неэластичный» / «» (мало данных)
    optimal_price: float | None = None  # оценка цены под макс. прибыль (эластичность × точка безубыточности)
    abc: str | None = None  # ABC-класс по выручке (A/B/C)
    recommendation: str = ""  # авто-рекомендация по цене/остатку


class PricingGroup(BaseModel):
    """Группа по категории: агрегаты + дочерние артикулы."""

    category: str
    articles: int = 0
    priced_articles: int = 0
    # Портфельная наценка по группе (Σ цена / Σ себест по строкам с обоими)
    markup_coef: float | None = None
    markup_pct: float | None = None
    cost_share_pct: float | None = None
    revenue: float = 0
    profit: float = 0
    cost_total: float = 0
    wb_expenses: float = 0
    margin_pct: float = 0
    adv_sum: float = 0  # расходы на рекламу группы, ₽
    drr: float = 0  # ДРР группы: реклама / выручка × 100
    wb_stock: int = 0
    own_stock: int = 0
    assembly_stock: int = 0
    transit_stock: int = 0
    total_stock: int = 0
    stock_value_cost: float = 0
    children: list[PricingRow] = []
    subgroups: list["PricingGroup"] = []  # вложенные группы (размеры внутри категории)


class PricingSummary(BaseModel):
    total_articles: int = 0
    priced_articles: int = 0
    costed_articles: int = 0
    revenue: float = 0
    profit: float = 0
    cost_total: float = 0
    wb_expenses: float = 0
    markup_pct: float | None = None  # портфельная (Σ цена / Σ себест)
    cost_share_pct: float | None = None
    margin_pct: float = 0
    wb_stock_units: int = 0  # суммарный остаток ВБ (шт)
    total_stock_units: int = 0  # весь остаток по всем локациям (шт)
    stock_value_cost: float = 0  # заморожено в остатке по себест, ₽
    anomalies: int = 0  # число аномальных артикулов


class PricingResponse(BaseModel):
    group_by: str  # "category" | "sku"
    data_groups: list[PricingGroup] = []  # при group_by=category
    data_rows: list[PricingRow] = []  # при group_by=sku
    summary: PricingSummary
    price_synced_at: datetime | None = None
    has_bdr: bool = False
