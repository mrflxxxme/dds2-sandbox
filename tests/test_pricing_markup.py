"""Тесты домена «Ценообразование» (наценка по артикулам).

Покрывает parse_wb_prices (парсер ответа WB) и get_markup_analytics
(наценка/доля себестоимости, группировка по категории, фильтры, изоляция).
"""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.wb_api import parse_wb_prices
from backend.models import WbPrice
from backend.services.funnel.cost_overrides import set_cost_override
from backend.services.pricing.markup import _build_row, get_markup_analytics
from backend.utils.time import utcnow

# ═══════════════════════════════════════════════════════════════════════════════
# parse_wb_prices — чистый парсер (без БД)
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseWbPrices:
    def test_basic(self):
        goods = [
            {
                "nmID": 111,
                "vendorCode": "ART-1",
                "discount": 20,
                "currencyIsoCode4217": "RUB",
                "sizes": [{"price": 1000, "discountedPrice": 800, "techSizeName": ""}],
            }
        ]
        out = parse_wb_prices(goods)
        assert len(out) == 1
        r = out[0]
        assert r["nm_id"] == 111
        assert r["vendor_code"] == "ART-1"
        assert r["base_price"] == 1000
        assert r["price"] == 800  # discountedPrice — витрина
        assert r["discount"] == 20
        assert r["currency"] == "RUB"

    def test_multi_size_takes_first_priced(self):
        goods = [
            {
                "nmID": 222,
                "vendorCode": "ART-2",
                "sizes": [
                    {"price": 0, "discountedPrice": 0, "techSizeName": "S"},
                    {"price": 500, "discountedPrice": 450, "techSizeName": "M"},
                ],
            }
        ]
        out = parse_wb_prices(goods)
        assert out[0]["price"] == 450
        assert out[0]["base_price"] == 500

    def test_skips_no_price(self):
        goods = [{"nmID": 333, "vendorCode": "X", "sizes": [{"price": 0, "discountedPrice": 0}]}]
        assert parse_wb_prices(goods) == []

    def test_dedup_nm_id(self):
        goods = [
            {"nmID": 444, "sizes": [{"discountedPrice": 100}]},
            {"nmID": 444, "sizes": [{"discountedPrice": 999}]},
        ]
        out = parse_wb_prices(goods)
        assert len(out) == 1
        assert out[0]["price"] == 100  # первый выигрывает

    def test_currency_fallback(self):
        out = parse_wb_prices([{"nmID": 555, "sizes": [{"discountedPrice": 100}]}])
        assert out[0]["currency"] == "RUB"


class TestBuildRowExpenses:
    """Регрессия: wb_expenses = commission (выручка − к перечислению), НЕ revenue×to_pay_rate.

    to_pay_rate из воронки в ПРОЦЕНТАХ (×100) — старая формула давала дикий минус.
    """

    def test_wb_expenses_equals_commission(self):
        funnel = {
            "nm_id": 1,
            "vendor_code": "X",
            "brand": "B",
            "subject": "S",
            "revenue": 10000.0,
            "commission": 4020.0,  # = выручка − к перечислению
            "to_pay_rate": 59.8,  # ПРОЦЕНТ, не доля
            "profit": 3000.0,
            "cost_total": 2000.0,
            "spp_rate": 20.0,
            "margin": 30.0,
            "adv_sum": 0.0,
            "tax": 0.0,
            "orders_count": 10,
        }
        row = _build_row(1, None, funnel, 300.0, {}, None)
        assert row.wb_expenses == 4020.0  # не -588000 (revenue − revenue×59.8)
        assert 0 <= row.wb_expenses <= row.revenue


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers для сервис-тестов
# ═══════════════════════════════════════════════════════════════════════════════


async def _add_price(db: AsyncSession, pid: int, nm_id: int, price: float, vendor_code: str = "ART"):
    db.add(
        WbPrice(
            project_id=pid,
            nm_id=nm_id,
            vendor_code=vendor_code,
            base_price=Decimal(str(price)),
            price=Decimal(str(price)),
            discount=Decimal("0"),
            currency="RUB",
            synced_at=utcnow(),
        )
    )
    await db.commit()


async def _add_funnel(db: AsyncSession, pid: int, nm_id: int, orders_sum: float, orders_count: int, vendor_code: str):
    await db.execute(
        text(
            "INSERT INTO wb_funnel_daily "
            "(project_id, nm_id, date, vendor_code, orders_sum_rub, orders_count, "
            "adv_sum, open_card, add_to_cart, stocks_wb, stocks_mp, adv_views, adv_clicks) "
            "VALUES (:pid, :nm, :d, :vc, :os, :oc, 0, 0, 0, 0, 0, 0, 0)"
        ),
        {"pid": pid, "nm": nm_id, "d": date(2025, 1, 15), "vc": vendor_code, "os": orders_sum, "oc": orders_count},
    )
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════════
# get_markup_analytics
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarkupAnalytics:
    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        res = await get_markup_analytics(db_session, project.id)
        assert res["summary"]["total_articles"] == 0
        assert res["data_groups"] == []

    @pytest.mark.asyncio
    async def test_markup_coefficient(self, db_session: AsyncSession, project):
        """Цена 1000 + себест 250 → коэф 4.0, наценка 300%, доля себест 25%."""
        await _add_price(db_session, project.id, 1001, 1000.0)
        await set_cost_override(db_session, project.id, nm_id=1001, cost_price=250.0)

        res = await get_markup_analytics(db_session, project.id, group_by="sku")
        rows = res["data_rows"]
        assert len(rows) == 1
        r = rows[0]
        assert r["has_price"] is True
        assert r["has_cost"] is True
        assert r["markup_coef"] == 4.0
        assert r["markup_pct"] == 300.0
        assert r["cost_share_pct"] == 25.0

    @pytest.mark.asyncio
    async def test_price_without_cost(self, db_session: AsyncSession, project):
        """Цена есть, себестоимости нет → метрики наценки = None."""
        await _add_price(db_session, project.id, 1002, 500.0)

        res = await get_markup_analytics(db_session, project.id, group_by="sku")
        r = res["data_rows"][0]
        assert r["has_price"] is True
        assert r["has_cost"] is False
        assert r["markup_coef"] is None
        assert r["markup_pct"] is None

    @pytest.mark.asyncio
    async def test_category_grouping(self, db_session: AsyncSession, project):
        """CategoryOverride задаёт категорию → строка попадает в свою группу."""
        await _add_price(db_session, project.id, 1003, 1000.0)
        await set_cost_override(db_session, project.id, nm_id=1003, cost_price=400.0)
        await db_session.execute(
            text(
                "INSERT INTO category_overrides (project_id, nm_id, category_value, updated_at) "
                "VALUES (:pid, :nm, :cat, NOW())"
            ),
            {"pid": project.id, "nm": 1003, "cat": "Ковры"},
        )
        await db_session.commit()

        res = await get_markup_analytics(db_session, project.id, group_by="category")
        groups = {g["category"]: g for g in res["data_groups"]}
        assert "Ковры" in groups
        assert groups["Ковры"]["articles"] == 1
        assert groups["Ковры"]["markup_pct"] == 150.0  # (1000-400)/400

    @pytest.mark.asyncio
    async def test_realized_economics_from_funnel(self, db_session: AsyncSession, project):
        """Артикул с продажами → realized-экономика (выручка/прибыль) присутствует."""
        await _add_price(db_session, project.id, 1004, 1000.0, vendor_code="SOLD")
        await set_cost_override(db_session, project.id, nm_id=1004, cost_price=300.0)
        await _add_funnel(db_session, project.id, 1004, orders_sum=10000.0, orders_count=10, vendor_code="SOLD")

        res = await get_markup_analytics(db_session, project.id, group_by="sku")
        r = next(x for x in res["data_rows"] if x["nm_id"] == 1004)
        assert r["orders_count"] == 10
        assert r["revenue"] > 0

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        await _add_price(db_session, project.id, 2001, 1000.0)
        await _add_price(db_session, other_project.id, 2002, 5000.0)

        res = await get_markup_analytics(db_session, project.id, group_by="sku")
        nm_ids = {r["nm_id"] for r in res["data_rows"]}
        assert 2001 in nm_ids
        assert 2002 not in nm_ids

    @pytest.mark.asyncio
    async def test_filter_search(self, db_session: AsyncSession, project):
        await _add_price(db_session, project.id, 3001, 1000.0, vendor_code="ALPHA")
        await _add_price(db_session, project.id, 3002, 1000.0, vendor_code="BETA")

        res = await get_markup_analytics(db_session, project.id, group_by="sku", search="alpha")
        nm_ids = {r["nm_id"] for r in res["data_rows"]}
        assert nm_ids == {3001}

    @pytest.mark.asyncio
    async def test_filter_min_orders(self, db_session: AsyncSession, project):
        await _add_price(db_session, project.id, 3003, 1000.0, vendor_code="NOSALE")
        await _add_price(db_session, project.id, 3004, 1000.0, vendor_code="SALE")
        await _add_funnel(db_session, project.id, 3004, orders_sum=5000.0, orders_count=5, vendor_code="SALE")

        res = await get_markup_analytics(db_session, project.id, group_by="sku", min_orders=1)
        nm_ids = {r["nm_id"] for r in res["data_rows"]}
        assert nm_ids == {3004}
