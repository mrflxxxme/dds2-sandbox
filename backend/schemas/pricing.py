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

    # СПП и цена для покупателя (СПП — самый свежий из BDR, «на данный момент»)
    spp_rate: float = 0  # % СПП сейчас (последний BDR, фолбэк — средний воронки)
    buyer_price: float | None = None  # АКТУАЛЬНАЯ цена с СПП = цена × (1 − СПП сейчас)

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
    breakeven_with_adv: float | None = None  # то же + средняя реклама категории (себест+Расх.WB+налог+ср.ДРР)
    safety_margin_pct: float | None = None  # запас прочности: (цена − безубыток)/цена × 100 — куда можно снижать
    drr: float = 0  # ДРР: реклама / выручка × 100
    cr: float = 0  # конверсия в заказ, % (клики→заказы) — диагностика «дорогой» рекламы
    ctr: float = 0  # CTR: клики / показы × 100 (кликабельность)
    cpc: float = 0  # CPC: реклама / клики (цена клика, ₽)
    adv_views: int = 0  # показы рекламы за период
    adv_clicks: int = 0  # клики по рекламе за период
    gmroi: float | None = None  # валовая маржа / заморожено в остатке — отдача на ₽ в товаре (бенчмарк ≥3)
    sell_through_pct: float | None = None  # продано / (продано + остаток) × 100 — скорость распродажи
    elasticity: float | None = None  # эластичность спроса по цене (оценка по истории 90 дн, обычно < 0)
    elasticity_label: str = ""  # «эластичный» / «неэластичный» / «» (мало данных)
    optimal_price: float | None = None  # оценка цены под макс. прибыль (эластичность × точка безубыточности)
    abc: str | None = None  # ABC-класс по выручке (A/B/C)
    recommendation: str = ""  # авто-рекомендация по цене/остатку

    # Склейка (imt_id — группа вариантов «цвет/размер» под одной карточкой WB).
    # Доли и роль считаются ОТНОСИТЕЛЬНО склейки при group_by=imt (иначе пустые).
    imt_id: int | None = None  # WB imtID карточки-склейки
    sklejka: str = ""  # имя склейки (алиас → «Склейка {imt}» → «Без склейки»)
    rev_share_pct: float | None = None  # доля варианта в выручке склейки, %
    adv_share_pct: float | None = None  # доля варианта в рекламе склейки, %
    sklejka_role: str = ""  # роль по рекламе внутри склейки: «якорь» / «донор» / «»


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
    ctr: float = 0  # CTR группы: Σклики / Σпоказы × 100
    cpc: float = 0  # CPC группы: Σреклама / Σклики, ₽
    adv_views: int = 0  # показы рекламы группы
    adv_clicks: int = 0  # клики по рекламе группы
    wb_stock: int = 0
    own_stock: int = 0
    assembly_stock: int = 0
    transit_stock: int = 0
    total_stock: int = 0
    stock_value_cost: float = 0
    # Склейка (при group_by=imt): id карточки + охват рекламой/продажами вариантов
    imt_id: int | None = None  # imtID склейки
    advertised_variants: int = 0  # вариантов с рекламой (adv_sum > 0)
    converting_variants: int = 0  # вариантов с продажами (orders > 0)
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


# ─── Карта СПП: категория × уровень цены → СПП ────────────────────────────


class SppLevelItem(BaseModel):
    """Конкретный артикул на уровне цены — из чего сложилась медиана."""

    nm_id: int
    vendor_code: str | None = None
    price: float
    spp: float
    buyer_price: float


class SppLevel(BaseModel):
    """Уровень цены внутри категории и живой СПП на нём."""

    price: float  # уровень цены (наша цена до СПП, округлённая до шага сетки)
    spp: float  # медиана СПП на уровне, %
    spp_min: float  # разброс: не все товары уровня получают одинаковый СПП
    spp_max: float
    buyer_price: float  # медиана цены, которую платит клиент
    n: int  # сколько артикулов стоит на этом уровне
    items: list[SppLevelItem] = []  # сами артикулы — раскрывается в таблице


class SppCliff(BaseModel):
    """Обрыв: между соседними уровнями СПП резко падает вверх по цене."""

    keep_below: float  # последний «хороший» уровень
    breaks_at: float  # уровень, на котором СПП рушится
    spp_below: float
    spp_above: float
    drop: float  # насколько падает, п.п.
    seller_gives: float  # сколько ₽ уступаем мы, переходя вниз
    buyer_gains: float  # сколько ₽ выигрывает клиент
    leverage: float | None = None  # buyer_gains / seller_gives — ради чего всё
    n_below: int = 0
    n_above: int = 0


class SppCategory(BaseModel):
    category: str
    nm_count: int = 0
    levels: list[SppLevel] = []
    cliffs: list[SppCliff] = []
    gaps: list[int] = []  # уровни сетки без единого товара — что проверить пробой


class SppMapStats(BaseModel):
    source: str = "card"  # card (витрина) | orders (с кошельком покупателя)
    days: int = 1
    step: int = 100
    points: int = 0
    categories_count: int = 0
    with_cliffs: int = 0
    last_snapshot_on: str | None = None


class SppMapResponse(BaseModel):
    categories: list[SppCategory] = []
    stats: SppMapStats
