"""Схемы раздела «Биржа карточек товаров» (card-exchange showcase).

Наружу отдаём snake_case (конвенция проекта). WB-объявления приходят camelCase
(adID/nmID/imtID/totalPrice/feedbacks) — маппинг в сервисе card_exchange.showcase.
Раздел проксирует витрину/справочник/корзину биржи WB через WbPortalClient и
добавляет каскад по корневой категории (справочник Дениса) + фильтр по нашим товарам.
"""

from __future__ import annotations

from pydantic import BaseModel

# ─── справочники ──────────────────────────────────────────────────────────


class RootCategory(BaseModel):
    """Корневая категория из справочника + число предметов в ней."""

    category: str
    subject_count: int


# ─── витрина ──────────────────────────────────────────────────────────────


class ShowcaseCursor(BaseModel):
    """Курсор WB: id последней карточки + значение поля сортировки у неё."""

    last_ad_id: int
    last_value: float


class ShowcaseAd(BaseModel):
    """Одно объявление биржи (плоское, из WB `ads[].{meta,feedbacks,...}`)."""

    ad_id: int  # adID
    nm_id: int | None = None  # nmID (артикул WB)
    imt_id: int | None = None  # imtID (склейка)
    title: str | None = None
    brand: str | None = None
    supplier_name: str | None = None  # meta.supplierName
    imt_count: int | None = None  # meta.imtCount — вариантов товара
    stock_qty: int | None = None  # meta.stockQty — остатки
    photo: str | None = None
    contact_countries: list[str] | None = None  # meta.contactCountries — страны поставщика
    is_kiz: bool = False  # meta.isKiz
    total_price: int | None = None  # totalPrice — цена переноса, ₽
    rating: float = 0  # feedbacks.rating
    feedbacks_count: int = 0  # feedbacks.count
    has_in_cart: bool = False
    is_card_owner: bool = False
    is_ours: bool = False  # nmID входит в нашу номенклатуру (подсветка «наша карточка»)


class ShowcaseResponse(BaseModel):
    """Страница витрины + курсор следующей + диагностика справочника."""

    ads: list[ShowcaseAd]
    next_cursor: ShowcaseCursor | None = None
    has_more: bool = False
    # Имена предметов выбранных категорий, которых нет в справочнике WB (рассинхрон).
    unmatched_subjects: list[str] = []
    # Сколько страниц просканировано в режиме «точно наши nmID» и упёрлись ли в лимит.
    scanned_pages: int | None = None
    scan_truncated: bool = False


class ShowcaseQuery(BaseModel):
    """Запрос витрины. root_categories/our_mode резолвятся в subjectIDs фильтра WB."""

    search: str | None = None
    root_categories: list[str] | None = None
    # None — не фильтровать по нам; "categories" — предметы наших товаров; "exact" — только наши nmID.
    our_mode: str | None = None
    brands: list[str] | None = None
    supplier_ids: list[int] | None = None
    rating: float | None = None
    has_stocks: bool | None = None
    sort_field: str = "feedbacksCount"  # feedbacksCount | rating | totalPrice
    sort_order: str = "desc"  # asc | desc
    cursor: ShowcaseCursor | None = None


# ─── корзина ──────────────────────────────────────────────────────────────


class CartAdd(BaseModel):
    ad_id: int


class CartDelete(BaseModel):
    ad_ids: list[int]


class CartActionResult(BaseModel):
    ok: bool


# ─── сессия биржи (отдельный слот от сессии поставок) ─────────────────────


class ExchangeSessionStatus(BaseModel):
    status: str  # ACTIVE | EXPIRED | NONE
    updated_at: str | None = None
    # Кабинет WB, под которым собран доступ (кука x-supplier-id) — чтобы менеджер видел,
    # чей это кабинет, и подмена продавца не проходила незамеченной.
    supplier_id: str | None = None


class ExchangeSessionSet(BaseModel):
    authorizev3: str
