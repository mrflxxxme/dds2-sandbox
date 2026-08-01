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

    def test_breakeven_with_avg_category_ad(self):
        """Мин. цена с рекламой = пол + средний ДРР категории (выше чистого пола)."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("1000"), base_price=Decimal("1000"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        funnel = {
            "nm_id": 1, "vendor_code": "X", "brand": "B", "subject": "S",
            "revenue": 10000.0, "commission": 3000.0, "to_pay_rate": 70.0, "tax": 600.0,
            "cost_total": 4000.0, "adv_sum": 0.0, "profit": 1900.0, "orders_count": 10,
            "margin": 19.0, "spp_rate": 0.0,
        }
        # средний ДРР категории «S» = 10%
        row = _build_row(
            1, price, funnel, 400.0, {}, None, wb_stock=20, period_days=30,
            adv_rate_by_cat={"S": 0.10}, global_adv_rate=0.10,
        )
        assert row.breakeven_price == pytest.approx(625.0, abs=0.1)  # чистый пол (без рекламы)
        # с рекламой: 1000 × 4000 / (6400 − 10000×0.10) = 740.74
        assert row.breakeven_with_adv == pytest.approx(740.74, abs=0.3)
        assert row.breakeven_with_adv > row.breakeven_price  # реклама поднимает пол

    def test_ad_metrics_ctr_cpc(self):
        """CTR/CPC/показы/клики считаются из adv_views/adv_clicks/adv_sum воронки."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("1000"), base_price=Decimal("1000"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        funnel = {
            "nm_id": 1, "vendor_code": "X", "revenue": 10000.0, "commission": 3000.0,
            "to_pay_rate": 70.0, "tax": 0.0, "cost_total": 4000.0, "adv_sum": 2000.0,
            "adv_views": 50000, "adv_clicks": 1000, "profit": 1000.0, "orders_count": 10,
            "margin": 10.0, "spp_rate": 0.0,
        }
        row = _build_row(1, price, funnel, 400.0, {}, None, wb_stock=20)
        assert row.adv_views == 50000
        assert row.adv_clicks == 1000
        assert row.ctr == 2.0   # 1000 / 50000 × 100
        assert row.cpc == 2.0   # 2000 / 1000

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


class TestSklejkaGrouping:
    """Группировка по склейке (imt_id): честный ДРР склейки + роли якорь/донор."""

    @staticmethod
    def _row(nm, revenue, adv_sum, orders, imt):
        funnel = {
            "nm_id": nm, "vendor_code": f"A{nm}", "revenue": revenue, "commission": revenue * 0.2,
            "to_pay_rate": 70.0, "tax": 0.0, "cost_total": revenue * 0.3, "adv_sum": adv_sum,
            "profit": revenue * 0.2, "orders_count": orders, "margin": 20.0, "spp_rate": 0.0,
        }
        price = WbPrice(
            project_id=1, nm_id=nm, price=Decimal("1000"), base_price=Decimal("1000"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        return _build_row(nm, price, funnel, 300.0, {}, {"imt_id": imt}, wb_stock=10)

    def test_anchor_donor_and_sklejka_drr(self):
        from backend.services.pricing.markup import _group_by_sklejka

        a = self._row(1, 10000.0, 5000.0, 10, 555)  # якорь: 83% рекламы склейки
        b = self._row(2, 8000.0, 0.0, 8, 555)        # донор: 40% выручки, 0 своей рекламы
        c = self._row(3, 2000.0, 1000.0, 2, 555)     # нейтраль
        groups = _group_by_sklejka([a, b, c])

        assert len(groups) == 1
        g = groups[0]
        assert g.imt_id == 555
        assert g.articles == 3
        assert g.advertised_variants == 2  # a, c
        assert g.converting_variants == 3
        assert g.category == "Склейка 555"  # имя склейки (нет алиаса → «Склейка {imt}»)
        assert g.drr == pytest.approx(30.0, abs=0.1)  # 6000 / 20000 × 100

        assert a.sklejka_role == "якорь"
        assert b.sklejka_role == "донор"
        assert c.sklejka_role == ""
        assert a.adv_share_pct == pytest.approx(83.3, abs=0.2)
        assert b.rev_share_pct == pytest.approx(40.0, abs=0.1)

    def test_no_roles_without_ads(self):
        """Склейка без рекламы → ролей нет (нечего делить)."""
        from backend.services.pricing.markup import _group_by_sklejka

        a = self._row(1, 5000.0, 0.0, 5, 777)
        b = self._row(2, 5000.0, 0.0, 5, 777)
        groups = _group_by_sklejka([a, b])
        assert groups[0].drr == 0
        assert a.sklejka_role == "" and b.sklejka_role == ""

    def test_no_imt_falls_into_without_sklejka(self):
        """Товары без imt_id — одна плоская группа «Без склейки», без ролей."""
        from backend.services.pricing.markup import _group_by_sklejka

        a = self._row(1, 5000.0, 1000.0, 5, None)
        b = self._row(2, 5000.0, 0.0, 5, None)
        groups = _group_by_sklejka([a, b])
        assert len(groups) == 1
        assert groups[0].imt_id is None
        assert groups[0].category == "Без склейки"
        assert a.sklejka_role == "" and b.sklejka_role == ""


class TestCardSpp:
    """Публичный card-API: парсер + приоритет реальной цены с СПП в _build_row."""

    PRODUCTS = [
        {"id": 893149026, "sizes": [{"price": {"basic": 1842000, "product": 984300}}]},
        {"id": 222, "sizes": [{"price": {"product": 0}}, {"price": {"product": 50000, "basic": 60000}}]},
        {"id": 333, "sizes": []},  # нет цены → пропуск
    ]

    @pytest.mark.parametrize("wrapped", [True, False], ids=["data.products", "products"])
    def test_parse_card_products(self, wrapped):
        """v4 дрейфует: сейчас товары приходят на верхнем уровне, раньше — под `data`.

        Читаем обе формы: на одной устаревшей фикстуре синк уже молча забирал
        ноль строк, а «Цена с СПП» тихо уезжала на BDR-фолбэк.
        """
        from backend.integrations.wb_card_api import parse_card_products

        data = {"data": {"products": self.PRODUCTS}} if wrapped else {"products": self.PRODUCTS}
        out = parse_card_products(data)
        assert out[893149026]["product"] == 9843.0  # копейки → рубли
        assert out[893149026]["basic"] == 18420.0
        assert out[222]["product"] == 500.0  # первый ненулевой размер
        assert 333 not in out

    def test_card_price_overrides_bdr_spp(self):
        """card_buyer_price приоритетнее BDR: цена с СПП = card, spp_rate из неё."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("1000"), base_price=Decimal("1000"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        # card даёт реальную цену 700; current_spp (BDR) другой — должен проиграть
        row = _build_row(1, price, None, 300.0, {}, None, current_spp=20.0, card_buyer_price=700.0)
        assert row.buyer_price == 700.0
        assert row.spp_rate == 30.0  # 1 − 700/1000

    def test_bdr_spp_fallback_when_no_card(self):
        """Без card-цены — фолбэк на СПП последнего BDR."""
        price = WbPrice(
            project_id=1, nm_id=1, price=Decimal("1000"), base_price=Decimal("1000"),
            discount=Decimal("0"), currency="RUB", synced_at=utcnow(),
        )
        row = _build_row(1, price, None, 300.0, {}, None, current_spp=25.0, card_buyer_price=None)
        assert row.spp_rate == 25.0
        assert row.buyer_price == 750.0  # 1000 × (1 − 0.25)


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


class TestAiAdvisorPayload:
    """Сборка payload AI по склейкам + охват кампаниями (без вызова LLM)."""

    @staticmethod
    def _variant(nm, **kw):
        base = dict(
            nm_id=nm, vendor_code=f"A{nm}", size="M", category="Ковры",
            current_price=1000.0, cost_price=300.0, markup_coef=3.3, markup_pct=233.0,
            drr=10.0, cr=2.0, rev_share_pct=None, adv_share_pct=None, sklejka_role="",
            adv_sum=0.0, revenue=0.0, profit=0.0, total_stock=0, is_new=False,
            anomaly=None, stock_value_cost=0.0,
        )
        base.update(kw)
        return base

    def test_sklejka_payload_roles_and_campaign_coverage(self):
        from backend.services.pricing.ai_advisor import _build_singles_payload, _build_sklejka_payload

        sklejka = dict(
            category="Склейка 555", imt_id=555, articles=2, advertised_variants=1, converting_variants=2,
            revenue=18000.0, adv_sum=5000.0, drr=27.8, profit=4000.0, markup_pct=200.0, stock_value_cost=10000.0,
            children=[
                self._variant(1, revenue=10000.0, adv_sum=5000.0, sklejka_role="якорь", adv_share_pct=100.0, rev_share_pct=55.6),
                self._variant(2, revenue=8000.0, adv_sum=0.0, sklejka_role="донор", adv_share_pct=0.0, rev_share_pct=44.4),
            ],
        )
        singles_group = dict(
            category="Без склейки", imt_id=None, articles=1, advertised_variants=0, converting_variants=0,
            revenue=0.0, adv_sum=0.0, drr=0.0, profit=-500.0, markup_pct=None, stock_value_cost=99999.0,
            children=[self._variant(9, revenue=0.0, profit=-500.0, stock_value_cost=99999.0, anomaly="Залежавшийся остаток")],
        )
        groups = [sklejka, singles_group]
        nm_to_camp = {1: {111}}  # вариант 1 — в активной кампании 111
        dyn = {1: {"тренд_заказов%": -33, "ДРР_3д%": 25.0}}  # моментум по варианту 1

        sk = _build_sklejka_payload(groups, nm_to_camp, dyn)
        assert len(sk) == 1  # одиночная группа без склейки не попала
        s = sk[0]
        assert s["imt_id"] == 555
        assert s["охвачено_активными_кампаниями"] == 1
        assert s["активных_кампаний"] == 1
        assert s["ДРР_склейки%"] == 27.8
        roles = {v["арт"]: v["роль"] for v in s["варианты"]}
        assert roles["A1"] == "якорь" and roles["A2"] == "донор"
        # динамика подмешана к варианту
        v1 = next(v for v in s["варианты"] if v["арт"] == "A1")
        assert v1["динамика_3д"]["тренд_заказов%"] == -33

        singles = _build_singles_payload(groups, dyn)
        arts = {it["арт"] for it in singles}
        assert "A9" in arts  # одиночная аномалия попала
        assert all("аномалия" in it for it in singles)  # компактный формат


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
    async def test_group_by_size_tree(self, db_session: AsyncSession, project):
        """Группировка «По размеру» → дерево Категория → Размер → SKU."""
        await _add_price(db_session, project.id, 6001, 1000.0, vendor_code="KOVER_200x300_серый")
        await set_cost_override(db_session, project.id, nm_id=6001, cost_price=300.0)
        await _add_wb_stock(db_session, project.id, 6001, 5)

        res = await get_markup_analytics(db_session, project.id, only_in_stock=True, group_by="size")
        assert res["group_by"] == "size"
        assert len(res["data_groups"]) >= 1
        cat = res["data_groups"][0]
        assert len(cat["subgroups"]) >= 1  # уровень размера присутствует
        leaf = cat["subgroups"][0]["children"][0]
        assert leaf["nm_id"] == 6001
        assert leaf["size"]  # размер заполнен (parse_size или «Без размера»)
        assert leaf["markup_coef"] is not None  # коэф. наценки

    @pytest.mark.asyncio
    async def test_breakeven_fallback_for_non_seller(self, db_session: AsyncSession, project):
        """Непродающийся товар получает оценку мин. цены по средней доле категории."""
        # продающийся задаёт среднюю «долю к получению» категории
        await _add_price(db_session, project.id, 5001, 1000.0, discount=50.0)
        await set_cost_override(db_session, project.id, nm_id=5001, cost_price=300.0)
        await _add_funnel(db_session, project.id, 5001, orders_sum=10000.0, orders_count=10, vendor_code="SELLER")
        # непродающийся в той же категории
        await _add_price(db_session, project.id, 5002, 800.0, discount=50.0)
        await set_cost_override(db_session, project.id, nm_id=5002, cost_price=200.0)
        for nm in (5001, 5002):
            await db_session.execute(
                text(
                    "INSERT INTO category_overrides (project_id, nm_id, category_value, updated_at) "
                    "VALUES (:p, :n, 'TestCat', NOW())"
                ),
                {"p": project.id, "n": nm},
            )
        await db_session.commit()

        res = await get_markup_analytics(db_session, project.id, group_by="sku")
        nonsel = next(r for r in res["data_rows"] if r["nm_id"] == 5002)
        assert nonsel["orders_count"] == 0
        assert nonsel["breakeven_price"] is not None  # оценка по категории, не None
        assert nonsel["breakeven_price"] > 0

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
