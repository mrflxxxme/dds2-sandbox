# ruff: noqa: RUF003
"""Schemas для отчёта «Индекс локализации» (ИЛ + ИРП).

См. backend/services/localization_index_service.py.
"""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


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

    model_config = ConfigDict(from_attributes=True)


class LocalizationByPeriod(BaseModel):
    """Полный ответ: top-block + таблица по артикулам."""

    summary: LocalizationSummary
    rows: list[LocalizationSkuRow]
