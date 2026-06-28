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
    children: list[PricingRow] = []


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


class PricingResponse(BaseModel):
    group_by: str  # "category" | "sku"
    data_groups: list[PricingGroup] = []  # при group_by=category
    data_rows: list[PricingRow] = []  # при group_by=sku
    summary: PricingSummary
    price_synced_at: datetime | None = None
    has_bdr: bool = False
