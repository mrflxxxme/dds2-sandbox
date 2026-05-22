# ruff: noqa: RUF001, RUF002
"""Pydantic-схемы ответов API «Приоритет складов WB по скорости доставки».

Все эндпоинты лежат под префиксом `/api/v1/warehouse/speed/*` и обслуживают
страницу с тремя задачами:
  • показать метаданные источника (`meta`),
  • показать топ anchor/stealer складов по ФО (`okrug-info`),
  • оценить «корзину» загруженных складов (`evaluate`),
  • дать сырой список priority-цепочек городов (`cities`).

Источник данных — `backend/services/warehouse_speed.py` (pure functions
поверх `backend/data/wb_warehouse_speed.json`). Ни одного запроса к БД,
поэтому `project_id`-фильтр не применим.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# ─── /meta ───────────────────────────────────────────────────────────────────


class SpeedMeta(BaseModel):
    """Метаданные источника таблицы скоростей."""

    version: str = Field(..., description="Дата выгрузки, например 2026-05-14")
    source: str = Field(..., description="Откуда выгрузили (бот/файл)")
    cities_count: int = Field(..., ge=0, description="Сколько городов в таблице")
    okrug_keys: list[str] = Field(
        default_factory=list,
        description="Все коды ФО, встречающиеся в данных",
    )


# ─── /okrug-info ─────────────────────────────────────────────────────────────


class WarehouseCount(BaseModel):
    """Склад + сколько городов ФО на него опирается."""

    warehouse_name: str
    cities_count: int = Field(..., ge=0)


class OkrugInfo(BaseModel):
    """Top-5 anchor / top-5 stealer по одному ФО."""

    okrug_key: str = Field(..., description="canonical key: central/volga/...")
    okrug_label: str = Field(..., description="Человекочитаемое название")
    anchors_top: list[WarehouseCount] = Field(
        default_factory=list,
        description="Топ-5 anchor-складов (своего ФО, priority 1-2)",
    )
    stealers_top: list[WarehouseCount] = Field(
        default_factory=list,
        description="Топ-5 stealer-складов (другого ФО, но WB направляет сюда)",
    )


# ─── /evaluate ───────────────────────────────────────────────────────────────


class BasketWarningDTO(BaseModel):
    """Структурное замечание о корзине (DTO над `BasketWarning`)."""

    kind: str = Field(
        ...,
        description="anchor_missing | stealer_without_anchor | low_ceiling",
    )
    severity: str = Field(..., description="info | warning | error")
    okrug: str = Field(..., description="Ключ ФО, к которому относится warning")
    okrug_label: str = Field(..., description="Человекочитаемое название ФО")
    message: str = Field(..., description="Готовая фраза для UI")
    stealer: str | None = Field(
        default=None,
        description="Имя stealer-склада (если warning относится к stealer-у)",
    )
    suggested_anchors: list[str] = Field(
        default_factory=list,
        description="Имена anchor-складов, которые предлагается добавить",
    )


class OkrugBasketStats(BaseModel):
    """Срез корзины по одному ФО."""

    okrug_key: str
    okrug_label: str
    anchors_in_basket: list[str] = Field(
        default_factory=list,
        description="Anchor-склады ФО, присутствующие в корзине",
    )
    stealers_in_basket: list[str] = Field(
        default_factory=list,
        description="Stealer-склады, присутствующие в корзине",
    )
    cities_covered_locally: int = Field(
        ...,
        ge=0,
        description="Городов ФО, у которых первый ЗАГРУЖЕННЫЙ склад — местный",
    )
    cities_total: int = Field(..., ge=0)


class CityPriorityRow(BaseModel):
    """Один priority-слот в ответе /cities (склад + часы + округ склада)."""

    warehouse_name: str
    hours: int = Field(..., ge=0)
    own_okrug: str = Field(
        default="unknown",
        description="ФО самого склада (для отличия anchor/stealer относительно okrug города)",
    )


class CitySpeedDTO(BaseModel):
    """Город + его priority-чейн."""

    city: str
    okrug_key: str
    okrug_label: str
    priorities: list[CityPriorityRow] = Field(default_factory=list)


class BasketEvaluation(BaseModel):
    """Полная оценка корзины загруженных складов."""

    loaded_warehouses: list[str] = Field(
        default_factory=list,
        description="Канонизированные имена входных складов",
    )
    realistic_ceiling_pct: float = Field(
        ...,
        ge=0,
        le=100,
        description="Реалистичный потолок ИЛ в процентах",
    )
    cities_local: int = Field(..., ge=0, description="Городов, доставка из anchor")
    cities_stealer: int = Field(..., ge=0, description="Городов, доставка из stealer")
    cities_uncovered: int = Field(..., ge=0, description="Городов без покрытия")
    cities_total: int = Field(..., ge=0)
    warnings: list[BasketWarningDTO] = Field(default_factory=list)
    per_okrug: list[OkrugBasketStats] = Field(default_factory=list)
    cities: list[CitySpeedDTO] | None = Field(
        default=None,
        description="Полный priority-чейн каждого города, только если ?include_cities=true",
    )

    model_config = ConfigDict(extra="forbid")
