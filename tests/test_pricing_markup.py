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
from backend.models import WbPrice, WbWarehouseStock
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

    def test_ad_bleed_new_product_levers(self):
        """Новинка с огромным ДРР → рекомендация про рычаги (цена/карточка), не «просто резать»."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("974"), base_price=Decimal("974"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        funnel = {
            "nm_id": 1, "vendor_code": "mosaic", "revenue": 974.0, "commission": 200.0,
            "to_pay_rate": 70.0, "tax": 50.0, "cost_total": 232.0, "adv_sum": 6997.0,
            "profit": -6630.0, "orders_count": 1, "margin": -680.0, "spp_rate": 0.0, "cr": 2.0,
        }
        row = _build_row(1, price, funnel, 232.0, {}, None, wb_stock=50)  # meta None → новинка
        assert row.anomaly == "Реклама в минус"
        assert row.is_new is True
        assert row.cr == 2.0
        assert "карточ" in row.recommendation.lower() or "цену" in row.recommendation.lower()

    def test_financial_metrics(self):
        """GMROI / запас прочности / sell-through / точка безубыточности."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("1000"), base_price=Decimal("1200"),
            discount=Decimal("16"), currency="RUB", synced_at=utcnow(),
        )
        funnel = {
            "nm_id": 1, "vendor_code": "X", "brand": "B", "subject": "S",
            "revenue": 10000.0, "commission": 3000.0, "to_pay_rate": 70.0, "tax": 600.0,
            "cost_total": 4000.0, "adv_sum": 500.0, "profit": 1900.0, "orders_count": 10,
            "margin": 19.0, "spp_rate": 10.0,
        }
        row = _build_row(1, price, funnel, 400.0, {}, None, wb_stock=20, period_days=30)
        assert row.stock_value_cost == 8000.0  # 20 × 400
        assert row.gmroi == 0.75  # (10000 − 4000) / 8000
        assert row.sell_through_pct == 33.3  # 10 / (10 + 20)
        # breakeven БЕЗ рекламы = 1000 × 4000 / (10000−3000−600) = 625.0
        assert row.breakeven_price == pytest.approx(625.0, abs=0.1)
        assert row.safety_margin_pct == pytest.approx(37.5, abs=0.1)

    def test_pipeline_stock_breakdown(self):
        """Остаток по локациям: ВБ+наш склад+сборка+в пути; заморожено по ВСЕМ."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("1000"), base_price=Decimal("1000"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        funnel = {
            "nm_id": 1, "vendor_code": "X", "revenue": 2000.0, "commission": 300.0,
            "to_pay_rate": 70.0, "tax": 100.0, "cost_total": 500.0, "adv_sum": 0.0,
            "profit": 1100.0, "orders_count": 5, "margin": 55.0, "spp_rate": 0.0,
        }
        row = _build_row(
            1, price, funnel, 100.0, {}, None,
            wb_stock=20, period_days=30, extra={"own": 50, "assembly": 30, "transit": 10},
        )
        assert row.own_stock == 50
        assert row.assembly_stock == 30
        assert row.transit_stock == 10
        assert row.total_stock == 110  # 20 + 50 + 30 + 10
        assert row.stock_value_cost == 11000.0  # 110 × 100 — по всем локациям

    def test_anomaly_ad_bleed(self):
        """Убыток из-за рекламы (без неё был бы плюс) → «Реклама в минус», не «поднять цену»."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("2136"), base_price=Decimal("2136"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        funnel = {
            "nm_id": 1, "vendor_code": "MRAMOR", "revenue": 2137.0, "commission": 722.0,
            "to_pay_rate": 66.0, "tax": 470.0, "cost_total": 853.0, "adv_sum": 3594.0,
            "profit": -3502.0, "orders_count": 1, "margin": -163.0, "spp_rate": 0.0,
        }
        row = _build_row(1, price, funnel, 846.0, {}, None, wb_stock=200, period_days=30)
        assert row.anomaly == "Реклама в минус"  # не «Убыток после расходов ВБ»
        assert "рекламу" in row.recommendation.lower()  # не «поднять цену»


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers для сервис-тестов
# ═══════════════════════════════════════════════════════════════════════════════


async def _add_price(db: AsyncSession, pid: int, nm_id: int, price: float, vendor_code: str = "ART", discount: float = 0.0):
    db.add(
        WbPrice(
            project_id=pid,
            nm_id=nm_id,
            vendor_code=vendor_code,
            base_price=Decimal(str(price)),
            price=Decimal(str(price)),
            discount=Decimal(str(discount)),
            currency="RUB",
            synced_at=utcnow(),
        )
    )
    await db.commit()


async def _add_wb_stock(db: AsyncSession, pid: int, nm_id: int, qty: int):
    db.add(
        WbWarehouseStock(
            project_id=pid, nm_id=nm_id, warehouse_name="Тест", quantity=qty, quantity_full=qty
        )
    )
    await db.commit()


async def _add_nomenclature(db: AsyncSession, pid: int, nm_id: int, first_sale_date: str):
    """Номенклатура с заданной датой первой продажи (для is_new-логики)."""
    await db.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_wb, first_sale_date, volume_l, updated_at) "
            "VALUES (:pid, :bc, :nm, :fsd, 0, NOW())"
        ),
        {"pid": pid, "bc": f"BC{nm_id}", "nm": nm_id, "fsd": date.fromisoformat(first_sale_date)},
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


class TestAiAdvisorSelection:
    """Отбор ключевых артикулов для AI (без вызова LLM)."""

    def test_select_prioritizes_by_impact(self):
        from backend.services.pricing.ai_advisor import _select_items

        def row(nm, **kw):
            base = dict(
                nm_id=nm, vendor_code=f"A{nm}", category="C", anomaly=None,
                adv_sum=0.0, stock_value_cost=0.0, profit=0.0, revenue=0.0,
                optimal_price=None, current_price=100.0, cost_price=50.0,
            )
            base.update(kw)
            return base

        rows = [
            row(1, anomaly="Реклама в минус", adv_sum=5000.0, profit=-100.0),
            row(2, anomaly="Залежавшийся остаток", stock_value_cost=999999.0),
            row(3, revenue=500000.0),
        ] + [row(100 + i, revenue=float(i)) for i in range(80)]
        sel = _select_items(rows)
        arts = {it["арт"] for it in sel}
        assert {"A1", "A2", "A3"} <= arts  # высокоимпактные попали
        assert len(sel) <= 50  # кап соблюдён
        assert all("аномалия" in it for it in sel)  # компактный формат


class TestStockAndAnomalies:
    @pytest.mark.asyncio
    async def test_only_in_stock_filter(self, db_session: AsyncSession, project):
        await _add_price(db_session, project.id, 4001, 1000.0)  # без остатка
        await _add_price(db_session, project.id, 4002, 1000.0)
        await _add_wb_stock(db_session, project.id, 4002, 50)

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="sku")
        nm = {r["nm_id"] for r in res["data_rows"]}
        assert nm == {4002}
        assert res["data_rows"][0]["wb_stock"] == 50

    @pytest.mark.asyncio
    async def test_stock_value_cost(self, db_session: AsyncSession, project):
        await _add_price(db_session, project.id, 4003, 1000.0)
        await set_cost_override(db_session, project.id, nm_id=4003, cost_price=200.0)
        await _add_wb_stock(db_session, project.id, 4003, 10)

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="sku")
        row = next(r for r in res["data_rows"] if r["nm_id"] == 4003)
        assert row["stock_value_cost"] == 2000.0  # 10 × 200
        assert res["summary"]["wb_stock_units"] == 10

    @pytest.mark.asyncio
    async def test_anomaly_suspicious_cost(self, db_session: AsyncSession, project):
        # скидка ВЫСТАВЛЕНА (50%), но себест всё равно мизерная → кривая себест
        await _add_price(db_session, project.id, 4004, 10000.0, discount=50.0)
        await set_cost_override(db_session, project.id, nm_id=4004, cost_price=100.0)  # 1% от цены
        await _add_wb_stock(db_session, project.id, 4004, 5)

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="anomaly")
        labels = {g["category"] for g in res["data_groups"]}
        assert "Подозрительная себестоимость" in labels

    @pytest.mark.asyncio
    async def test_anomaly_price_not_set(self, db_session: AsyncSession, project):
        # скидка 0% + абсурдная наценка → «Цена завышена / нет скидки», не «подозр. себест»
        await _add_price(db_session, project.id, 4007, 20000.0, discount=0.0)
        await set_cost_override(db_session, project.id, nm_id=4007, cost_price=200.0)  # 1% от цены
        await _add_wb_stock(db_session, project.id, 4007, 10)

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="sku")
        r = next(x for x in res["data_rows"] if x["nm_id"] == 4007)
        assert r["anomaly"] == "Цена завышена / нет скидки"
        assert "скидк" in r["recommendation"].lower()

    @pytest.mark.asyncio
    async def test_anomaly_dead_stock(self, db_session: AsyncSession, project):
        # НЕ новинка (старая дата первой продажи), цена/себест ок, остаток есть, продаж нет → залежавшийся
        await _add_price(db_session, project.id, 4005, 1000.0)
        await set_cost_override(db_session, project.id, nm_id=4005, cost_price=500.0)
        await _add_wb_stock(db_session, project.id, 4005, 30)
        await _add_nomenclature(db_session, project.id, 4005, "2024-01-01")

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="anomaly")
        dead = next((g for g in res["data_groups"] if g["category"] == "Залежавшийся остаток"), None)
        assert dead is not None
        assert 4005 in {r["nm_id"] for r in dead["children"]}

    @pytest.mark.asyncio
    async def test_new_product_not_dead_stock(self, db_session: AsyncSession, project):
        # новинка (без first_sale_date), остаток есть, продаж нет → НЕ «залежавшийся»
        await _add_price(db_session, project.id, 4006, 1000.0)
        await set_cost_override(db_session, project.id, nm_id=4006, cost_price=500.0)
        await _add_wb_stock(db_session, project.id, 4006, 30)

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="sku")
        r = next(x for x in res["data_rows"] if x["nm_id"] == 4006)
        assert r["is_new"] is True
        assert r["anomaly"] != "Залежавшийся остаток"
        assert "раскачивать" in r["recommendation"].lower()
