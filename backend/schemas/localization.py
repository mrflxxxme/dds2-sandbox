# ruff: noqa: RUF002, RUF003
"""Schemas для отчёта «Индекс локализации» (ИЛ + ИРП).

См. backend/services/localization_index_service.py.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class DistrictBreakdown(BaseModel):
    """Локально/нелокально по одному федеральному округу.

    Используется в составе LocalizationSkuRow.districts (per nm_id) и
    в LocalizationSummary.district_totals (агрегаты по всем артикулам).
    """

    district: str  # ключ ФО: central / northwest / south_caucasus / volga / ural / far_east_siberia / abroad / unknown
    label: str  # человекочитаемая подпись («Центральный»)
    local: int  # заказы из склада того же ФО, что и доставка
    non_local: int  # заказы из склада другого ФО
    total: int  # local + non_local
    local_pct: Decimal  # local / total × 100, 2 знака

    model_config = ConfigDict(from_attributes=True)


class LocalizationSummary(BaseModel):
    """Top-block (агрегаты по периоду) для отчёта локализации."""

    # Главные KPI
    localization_index: Decimal  # средневзвешенный КТР (по orders_count)
    irp_percent: Decimal  # средневзвешенный КРП, %, к цене товара

    # Заказы
    local_orders: int  # доля_локализации × orders, агрегировано
    non_local_orders: int  # orders - local_orders
    total_orders: int

    # Доля локализации в среднем за период (взвешенно по orders)
    loc_pct_overall: Decimal

    # Артикулы
    articles_count: int  # уникальных nm_id
    articles_local_count: int  # с КРП = 0 (loc ≥ 60%)
    articles_critical_count: int  # с КРП > 0

    # Региональная разбивка (по всем артикулам). Список упорядочен
    # как DISTRICT_ORDER из warehouse_district.py.
    district_totals: list[DistrictBreakdown] = []

    model_config = ConfigDict(from_attributes=True)


class LocalizationSkuRow(BaseModel):
    """Строка таблицы по артикулу для отчёта локализации."""

    nm_id: int
    vendor_code: str | None = None
    title: str | None = None  # human-friendly: vendor_code or subject
    subject: str | None = None
    brand: str | None = None

    total: int  # суммарные заказы за период
    local: int  # «местных» заказов
    non_local: int  # «не местных»

    loc_pct: Decimal  # средневзвеш. localization_percent (0..100)
    ktr: Decimal  # коэффициент по таблице КТР
    krp: Decimal  # %, по таблице КРП

    contribution: Decimal  # orders × ktr — вклад в индекс локализации
    status: str  # excellent / neutral / weak / critical

    # Дата первой продажи (из Nomenclature.first_sale_date) — UI вычисляет
    # «Новинка / Активный / Без продаж» через computeProductStatus().
    first_sale_date: date | None = None

    # Per-district breakdown (упорядочено как DISTRICT_ORDER).
    # Пустой список если sync wb_orders ещё не запускался для проекта.
    districts: list[DistrictBreakdown] = []

    model_config = ConfigDict(from_attributes=True)


class LocalizationByPeriod(BaseModel):
    """Полный ответ: top-block + таблица по артикулам."""

    summary: LocalizationSummary
    rows: list[LocalizationSkuRow]


class LocalizationDailyPoint(BaseModel):
    """Одна точка графика «Динамика ИЛ/ИРП по дням».

    Логика расчёта — та же что в LocalizationSummary, но для одного дня:
    взвешенное среднее КТР/КРП по orders_count за день. Поддерживает
    фильтрацию по subject/brand (для реактивности UI-фильтров).
    """

    date: str  # ISO-дата (YYYY-MM-DD)
    localization_index: Decimal  # средневзвешенный КТР за день
    irp_percent: Decimal  # средневзвешенный КРП за день
    total_orders: int  # объём заказов за день (для tooltip / прозрачности)
    articles_count: int  # уникальных nm_id за день

    model_config = ConfigDict(from_attributes=True)
