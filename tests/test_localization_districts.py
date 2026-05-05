"""Тесты per-district breakdown для отчёта локализации (wb_orders).

Покрытие:
- _load_district_breakdown: разбивает заказы на local/non_local по ФО.
- get_district_breakdown: возвращает пустые districts если wb_orders нет.
- get_summary / get_by_sku: добавляют district_totals + districts (per nm_id).
- multi-tenancy: проект 1 не видит заказы проекта 2.
- is_cancel=true исключаются из подсчёта.
- Зарубеж (Беларусь) → district=abroad, source=Россия → non_local.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytz
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.services.localization_index_service import (
    _load_district_breakdown,
    get_by_sku,
    get_district_breakdown,
    get_summary,
)
from backend.services.warehouse_district import (
    DISTRICT_CENTRAL,
    DISTRICT_VOLGA,
)

_MSK = pytz.timezone("Europe/Moscow")


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


async def _insert_wb_order(
    db: AsyncSession,
    project_id: int,
    *,
    nm_id: int,
    srid: str,
    warehouse_name: str,
    okrug: str | None,
    country: str = "Россия",
    warehouse_type: str = "Склад WB",
    is_cancel: bool = False,
    days_ago: int = 5,
) -> None:
    """Минимальный INSERT в wb_orders для тестов."""
    order_dt = datetime.now(_MSK) - timedelta(days=days_ago)
    await db.execute(
        text(
            "INSERT INTO wb_orders "
            "(project_id, srid, order_date, nm_id, "
            "warehouse_name, warehouse_type, country_name, "
            "oblast_okrug_name, region_name, "
            "is_cancel, is_supply, is_realization, synced_at) "
            "VALUES "
            "(:pid, :srid, :odt, :nm, "
            ":wn, :wt, :cn, "
            ":okr, 'Тестовый регион', "
            ":isc, false, true, NOW())"
        ),
        {
            "pid": project_id,
            "srid": srid,
            "odt": order_dt,
            "nm": nm_id,
            "wn": warehouse_name,
            "wt": warehouse_type,
            "cn": country,
            "okr": okrug,
            "isc": is_cancel,
        },
    )
    await db.commit()


async def _insert_funnel_row(
    db: AsyncSession,
    project_id: int,
    *,
    nm_id: int,
    funnel_date: date,
    orders_count: int = 100,
    localization_percent: float = 50.0,
    vendor_code: str = "VC",
) -> None:
    """Заполнить wb_funnel_daily — нужно чтобы get_summary/get_by_sku видели SKU."""
    await db.execute(
        text(
            "INSERT INTO wb_funnel_daily "
            "(project_id, nm_id, date, orders_count, orders_sum_rub, "
            "localization_percent, vendor_code, "
            "open_card, add_to_cart, stocks_wb, stocks_mp, "
            "adv_views, adv_clicks, adv_sum) "
            "VALUES (:pid, :nm, :d, :oc, 0, "
            ":loc, :vc, "
            "0, 0, 0, 0, 0, 0, 0)"
        ),
        {
            "pid": project_id,
            "nm": nm_id,
            "d": funnel_date,
            "oc": orders_count,
            "loc": localization_percent,
            "vc": vendor_code,
        },
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# _load_district_breakdown — низкоуровневая логика
# ═══════════════════════════════════════════════════════════════════════════════


class TestLoadDistrictBreakdown:
    """Корректность матчинга delivery vs source ФО."""

    @pytest.mark.asyncio
    async def test_local_order_central_to_central(self, db_session: AsyncSession, project):
        """Заказ из Коледино (Central) в Московскую обл. (Central) → local."""
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=1,
            srid="A1",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        nm_data = result.get(1, {})
        central = nm_data.get(DISTRICT_CENTRAL, {"local": 0, "non_local": 0})
        assert central["local"] == 1
        assert central["non_local"] == 0

    @pytest.mark.asyncio
    async def test_non_local_central_source_to_volga_delivery(self, db_session: AsyncSession, project):
        """Заказ из Коледино (Central) в Приволжский ФО → non_local в volga bucket."""
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=2,
            srid="B1",
            warehouse_name="Коледино",
            okrug="Приволжский федеральный округ",
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        nm_data = result.get(2, {})
        volga = nm_data.get(DISTRICT_VOLGA, {"local": 0, "non_local": 0})
        assert volga["non_local"] == 1
        assert volga["local"] == 0

    @pytest.mark.asyncio
    async def test_local_kazan_to_volga(self, db_session: AsyncSession, project):
        """Казань (Volga) → Приволжский ФО → local в volga."""
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=3,
            srid="C1",
            warehouse_name="Казань",
            okrug="Приволжский федеральный округ",
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        volga = result.get(3, {}).get(DISTRICT_VOLGA, {})
        assert volga["local"] == 1

    @pytest.mark.asyncio
    async def test_abroad_country_belarus(self, db_session: AsyncSession, project):
        """Доставка в Беларусь (country!=Россия) — НЕ попадает в выборку
        потому что фильтр country_name='Россия' отсекает.
        """
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=4,
            srid="D1",
            warehouse_name="Коледино",
            okrug=None,
            country="Беларусь",
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        # Поскольку фильтр country='Россия', этот заказ не попадает
        assert 4 not in result

    @pytest.mark.asyncio
    async def test_cancelled_orders_excluded(self, db_session: AsyncSession, project):
        """is_cancel=true заказы должны быть исключены."""
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=5,
            srid="E1",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
            is_cancel=True,
        )
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=5,
            srid="E2",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
            is_cancel=False,
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        central = result.get(5, {}).get(DISTRICT_CENTRAL, {})
        # Только один (не отменённый)
        assert central["local"] == 1

    @pytest.mark.asyncio
    async def test_fbs_excluded_only_fbo_counted(self, db_session: AsyncSession, project):
        """warehouse_type != 'Склад WB' (например FBS) исключается."""
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=6,
            srid="F1",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
            warehouse_type="Маркетплейс",  # FBS — исключается
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        assert 6 not in result

    @pytest.mark.asyncio
    async def test_multi_tenancy_isolation(self, db_session: AsyncSession, project, other_project):
        """Проект 1 не видит заказы проекта 2."""
        await invalidate_cache("reports:localization_districts")
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=7,
            srid="P1-1",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
        )
        await _insert_wb_order(
            db_session,
            other_project.id,
            nm_id=7,
            srid="P2-1",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
        )
        result = await _load_district_breakdown(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        nm_data = result.get(7, {})
        central = nm_data.get(DISTRICT_CENTRAL, {})
        # Проект 1 видит только 1 заказ, не 2
        assert central["local"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# get_district_breakdown — пустой период
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDistrictBreakdown:
    @pytest.mark.asyncio
    async def test_empty_returns_no_data(self, db_session: AsyncSession, project):
        """Если wb_orders пуст за период → has_data=False, totals=[]."""
        await invalidate_cache("reports:localization_districts")
        result = await get_district_breakdown(
            db_session,
            project.id,
            "2026-01-01",
            "2026-01-31",
        )
        assert result["has_data"] is False
        assert result["totals"] == []
        assert result["by_nm"] == {}


# ═══════════════════════════════════════════════════════════════════════════════
# get_summary / get_by_sku — расширение с districts
# ═══════════════════════════════════════════════════════════════════════════════


class TestSummaryDistrictTotals:
    @pytest.mark.asyncio
    async def test_summary_districts_empty_when_no_wb_orders(self, db_session: AsyncSession, project):
        """Funnel есть, wb_orders нет → district_totals=[] (UI: «нет данных»)."""
        await invalidate_cache("reports:localization")
        await invalidate_cache("reports:localization_districts")
        await _insert_funnel_row(
            db_session,
            project.id,
            nm_id=10,
            funnel_date=date.today() - timedelta(days=5),
            orders_count=100,
            localization_percent=50.0,
        )
        result = await get_summary(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        assert result["district_totals"] == []
        assert result["total_orders"] == 100  # из funnel

    @pytest.mark.asyncio
    async def test_summary_with_district_totals(self, db_session: AsyncSession, project):
        """Funnel + wb_orders → district_totals содержит агрегаты."""
        await invalidate_cache("reports:localization")
        await invalidate_cache("reports:localization_districts")
        await invalidate_cache("reports:localization_skus")
        # Funnel-строка
        await _insert_funnel_row(
            db_session,
            project.id,
            nm_id=20,
            funnel_date=date.today() - timedelta(days=5),
            orders_count=2,
            localization_percent=50.0,
        )
        # wb_orders: 1 local (central→central), 1 non_local (central→volga)
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=20,
            srid="L1",
            warehouse_name="Коледино",
            okrug="Центральный федеральный округ",
        )
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=20,
            srid="L2",
            warehouse_name="Коледино",
            okrug="Приволжский федеральный округ",
        )
        result = await get_summary(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        district_totals = result["district_totals"]
        # Должно быть 7 ключей (DISTRICT_ORDER)
        assert len(district_totals) == 7
        # Найдём central и volga
        central = next(d for d in district_totals if d["district"] == DISTRICT_CENTRAL)
        volga = next(d for d in district_totals if d["district"] == DISTRICT_VOLGA)
        assert central["local"] == 1
        assert central["non_local"] == 0
        assert volga["local"] == 0
        assert volga["non_local"] == 1


class TestGetBySkuDistricts:
    @pytest.mark.asyncio
    async def test_by_sku_has_districts_for_each_row(self, db_session: AsyncSession, project):
        """Каждая строка per-SKU получает поле districts (7 ключей или [])."""
        await invalidate_cache("reports:localization_skus")
        await invalidate_cache("reports:localization_districts")
        await _insert_funnel_row(
            db_session,
            project.id,
            nm_id=30,
            funnel_date=date.today() - timedelta(days=5),
            orders_count=10,
            localization_percent=70.0,
            vendor_code="A30",
        )
        await _insert_wb_order(
            db_session,
            project.id,
            nm_id=30,
            srid="X1",
            warehouse_name="Казань",
            okrug="Приволжский федеральный округ",
        )
        rows = await get_by_sku(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        assert len(rows) == 1
        row = rows[0]
        assert int(row["nm_id"]) == 30
        assert "districts" in row
        # 7 ключей DISTRICT_ORDER
        assert len(row["districts"]) == 7
        # Volga local=1
        volga = next(d for d in row["districts"] if d["district"] == DISTRICT_VOLGA)
        assert volga["local"] == 1
        assert Decimal(str(volga["local_pct"])) == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_by_sku_empty_districts_when_no_wb_orders(self, db_session: AsyncSession, project):
        """Если wb_orders пуст → каждая строка получает districts=[]."""
        await invalidate_cache("reports:localization_skus")
        await invalidate_cache("reports:localization_districts")
        await _insert_funnel_row(
            db_session,
            project.id,
            nm_id=40,
            funnel_date=date.today() - timedelta(days=5),
            orders_count=5,
            localization_percent=80.0,
            vendor_code="A40",
        )
        rows = await get_by_sku(
            db_session,
            project.id,
            (date.today() - timedelta(days=30)).isoformat(),
            date.today().isoformat(),
        )
        assert len(rows) == 1
        assert rows[0]["districts"] == []
