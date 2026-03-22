"""
Tests for capital service — liquidity classification, ROI, recommendations, grouping.

Covers:
1. _classify_turnover — liquid (< 30), transition (30-60), illiquid (> 60)
2. _calc_recommendation — ROI calc, star, reduce_10, liquidate, cut_ads, check_quality, hold
3. _enrich_products — filters min_orders, zero cost; adds capital metrics
4. _group_products — by brand, category, article; parent_filter
5. _build_summary — correct liquid/transition/illiquid totals
6. get_capital_analysis — integration via mocks, empty data, trend building
"""

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from backend.services.funnel.capital import (
    _build_summary,
    _calc_recommendation,
    _classify_turnover,
    _enrich_products,
    _group_products,
    get_capital_analysis,
)

# ─── Helpers ─────────────────────────────────────────────────────────────────


def make_product(
    nm_id=1,
    vendor_code="ART-001",
    brand="TestBrand",
    subject="TestSubject",
    orders_count=100,
    orders_sum_rub=500_000,
    revenue=400_000,
    margin=15.0,
    profit=60_000,
    drr=5.0,
    adv_sum=25_000,
    turnover_days=20,
    stocks_wb=200,
    avg_price=5_000,
    cost_price=2_000,
    cost_total=160_000,
    **kwargs,
) -> dict:
    return {
        "nm_id": nm_id,
        "vendor_code": vendor_code,
        "brand": brand,
        "subject": subject,
        "orders_count": orders_count,
        "orders_sum_rub": orders_sum_rub,
        "revenue": revenue,
        "margin": margin,
        "profit": profit,
        "drr": drr,
        "adv_sum": adv_sum,
        "turnover_days": turnover_days,
        "stocks_wb": stocks_wb,
        "avg_price": avg_price,
        "cost_price": cost_price,
        "cost_total": cost_total,
        **kwargs,
    }


def _enrich_one(product, **kwargs):
    """Helper: enrich a single product with defaults."""
    defaults = {
        "rf_stocks_map": {},
        "include_rf": False,
        "bdr_rates_map": None,
        "period_days": 7,
        "elasticity": 1.8,
    }
    defaults.update(kwargs)
    return _enrich_products(
        [product],
        defaults["rf_stocks_map"],
        defaults["include_rf"],
        defaults["bdr_rates_map"],
        defaults["period_days"],
        defaults["elasticity"],
    )


# ─── _classify_turnover ─────────────────────────────────────────────────────


class TestClassifyTurnover:
    def test_classify_liquid(self):
        """Turnover < 30 → 'liquid'."""
        assert _classify_turnover(10) == "liquid"
        assert _classify_turnover(29) == "liquid"
        assert _classify_turnover(0) == "liquid"

    def test_classify_transition(self):
        """Turnover 30-60 → 'transition'."""
        assert _classify_turnover(30) == "transition"
        assert _classify_turnover(45) == "transition"
        assert _classify_turnover(60) == "transition"

    def test_classify_illiquid(self):
        """Turnover > 60 → 'illiquid'."""
        assert _classify_turnover(61) == "illiquid"
        assert _classify_turnover(120) == "illiquid"


# ─── _calc_recommendation ───────────────────────────────────────────────────


class TestCalcRecommendation:
    def test_roi_calculation(self):
        """ROI = margin * 30 / turnover."""
        rec = _calc_recommendation(margin=15.0, turnover=20, drr=5.0, buyout_pct=80, elasticity=1.8)
        expected_roi = 15.0 * 30 / 20  # 22.5
        assert rec["current_roi"] == round(expected_roi, 2)

    def test_recommendation_star(self):
        """High ROI (>20) + low turnover (<30) → 'star'."""
        # margin=30, turnover=10 → roi = 30*30/10 = 90
        rec = _calc_recommendation(margin=30.0, turnover=10, drr=5.0, buyout_pct=80, elasticity=1.8)
        assert rec["type"] == "star"

    def test_recommendation_liquidate(self):
        """Turnover > 60 → 'liquidate' (checked before star)."""
        rec = _calc_recommendation(margin=30.0, turnover=70, drr=5.0, buyout_pct=80, elasticity=1.8)
        assert rec["type"] == "liquidate"

    def test_recommendation_cut_ads(self):
        """Low margin (<5) + high DRR (>15) → 'cut_ads'."""
        rec = _calc_recommendation(margin=3.0, turnover=20, drr=25.0, buyout_pct=80, elasticity=1.8)
        assert rec["type"] == "cut_ads"

    def test_recommendation_check_quality(self):
        """Low buyout (<70) → 'check_quality' (highest priority)."""
        rec = _calc_recommendation(margin=30.0, turnover=10, drr=5.0, buyout_pct=50, elasticity=1.8)
        assert rec["type"] == "check_quality"

    def test_recommendation_reduce_10(self):
        """ROI improves at -10% price → 'reduce_10'."""
        # We need: roi_10 > roi, margin not triggering star/liquidate/cut_ads
        # margin=12, turnover=35 → roi = 12*30/35 ≈ 10.29
        # new_margin_10 = 2, new_turnover_10 = 35 / (1 + 0.10*3.0) = 35/1.3 ≈ 26.92
        # roi_10 = 2 * 30/26.92 ≈ 2.23 < 10.29 — doesn't work
        # Need high elasticity so turnover drops a lot more
        # margin=20, turnover=40, elasticity=5.0
        # roi = 20*30/40 = 15
        # new_margin_10 = 10, new_turnover_10 = 40/(1+0.5) = 26.67
        # roi_10 = 10*30/26.67 = 11.25 < 15 — still less
        # Actually for reduce_10: the price goes DOWN → demand goes UP → turnover goes DOWN
        # roi_10 = new_margin_10 * 30 / new_turnover_10
        # For roi_10 > roi: (margin-10)*30/(turnover/(1+0.1*e)) > margin*30/turnover
        # (margin-10)*(1+0.1*e) > margin
        # margin + 0.1*e*margin - 10 - e > margin
        # 0.1*e*margin - 10 - e > 0
        # e*(0.1*margin - 1) > 10
        # With margin=20: e*(2-1) > 10 → e > 10
        rec = _calc_recommendation(margin=20.0, turnover=40, drr=5.0, buyout_pct=80, elasticity=12.0)
        assert rec["type"] == "reduce_10"

    def test_recommendation_hold(self):
        """No conditions met → 'hold'."""
        # margin=10, turnover=35 → roi = 10*30/35 ≈ 8.57 (not star)
        # turnover 35 (not > 60), margin 10 (not < 5), buyout 80 (not < 70)
        rec = _calc_recommendation(margin=10.0, turnover=35, drr=5.0, buyout_pct=80, elasticity=1.8)
        assert rec["type"] == "hold"

    def test_elasticity_affects_roi(self):
        """Different elasticity changes ROI projections and may change recommendation."""
        rec_low = _calc_recommendation(margin=20.0, turnover=40, drr=5.0, buyout_pct=80, elasticity=1.0)
        rec_high = _calc_recommendation(margin=20.0, turnover=40, drr=5.0, buyout_pct=80, elasticity=5.0)
        # ROI at -10% should differ with different elasticity
        assert rec_low["roi_at_minus_10"] != rec_high["roi_at_minus_10"]


# ─── _enrich_products ───────────────────────────────────────────────────────


class TestEnrichProducts:
    def test_filters_low_orders(self):
        """Products with orders < MIN_ORDERS (5) are filtered out."""
        p = make_product(orders_count=3)
        result = _enrich_one(p)
        assert len(result) == 0

    def test_filters_zero_cost(self):
        """Products with cost_price <= 0 are filtered out."""
        p = make_product(cost_price=0)
        result = _enrich_one(p)
        assert len(result) == 0

    def test_capital_calculation(self):
        """capital = total_stock * cost_price."""
        p = make_product(stocks_wb=200, cost_price=2_000)
        result = _enrich_one(p)
        assert len(result) == 1
        assert result[0]["capital"] == 200 * 2_000

    def test_rf_stocks_added(self):
        """RF stocks added to total_stock when include_rf=True."""
        p = make_product(nm_id=42, stocks_wb=100, cost_price=1_000)
        result = _enrich_one(p, rf_stocks_map={42: 50}, include_rf=True)
        assert result[0]["total_stock"] == 150
        assert result[0]["capital"] == 150 * 1_000


# ─── _group_products ─────────────────────────────────────────────────────────


class TestGroupProducts:
    def _make_enriched(self, **kwargs):
        """Create an enriched product dict (post-_enrich_products)."""
        p = make_product(**kwargs)
        result = _enrich_one(p)
        assert len(result) == 1
        return result[0]

    def test_group_by_brand(self):
        """Products grouped by brand correctly."""
        p1 = self._make_enriched(nm_id=1, brand="BrandA", subject="S1")
        p2 = self._make_enriched(nm_id=2, brand="BrandA", subject="S2")
        p3 = self._make_enriched(nm_id=3, brand="BrandB", subject="S1")

        groups = _group_products([p1, p2, p3], "brand", parent_filter=None)
        keys = {g["group_key"] for g in groups}
        assert keys == {"BrandA", "BrandB"}
        brand_a = next(g for g in groups if g["group_key"] == "BrandA")
        assert brand_a["children_count"] == 2

    def test_group_by_category(self):
        """Products grouped by subject correctly."""
        p1 = self._make_enriched(nm_id=1, subject="Shoes")
        p2 = self._make_enriched(nm_id=2, subject="Shoes")
        p3 = self._make_enriched(nm_id=3, subject="Bags")

        groups = _group_products([p1, p2, p3], "category", parent_filter=None)
        keys = {g["group_key"] for g in groups}
        assert keys == {"Shoes", "Bags"}

    def test_group_by_article(self):
        """Each product is its own group (no aggregation)."""
        p1 = self._make_enriched(nm_id=1, vendor_code="ART-1")
        p2 = self._make_enriched(nm_id=2, vendor_code="ART-2")

        groups = _group_products([p1, p2], "article", parent_filter=None)
        assert len(groups) == 2
        assert all(g["children_count"] == 0 for g in groups)

    def test_parent_filter_brand(self):
        """parent_filter filters to specific brand."""
        p1 = self._make_enriched(nm_id=1, brand="BrandA")
        p2 = self._make_enriched(nm_id=2, brand="BrandB")

        # group_by=category with brand filter
        groups = _group_products([p1, p2], "brand", parent_filter="BrandA")
        assert len(groups) == 1
        assert groups[0]["group_key"] == "BrandA"

    def test_parent_filter_category(self):
        """parent_filter filters to specific category (subject)."""
        p1 = self._make_enriched(nm_id=1, subject="Shoes")
        p2 = self._make_enriched(nm_id=2, subject="Bags")

        groups = _group_products([p1, p2], "category", parent_filter="Shoes")
        assert len(groups) == 1
        assert groups[0]["group_key"] == "Shoes"


# ─── _build_summary ─────────────────────────────────────────────────────────


class TestBuildSummary:
    def test_summary_calculation(self):
        """Correct liquid/transition/illiquid totals."""
        products = [
            # liquid: turnover=20, capital = 200*2000 = 400_000
            make_product(nm_id=1, turnover_days=20, stocks_wb=200, cost_price=2_000),
            # transition: turnover=45, capital = 100*1000 = 100_000
            make_product(nm_id=2, turnover_days=45, stocks_wb=100, cost_price=1_000),
            # illiquid: turnover=90, capital = 50*3000 = 150_000
            make_product(nm_id=3, turnover_days=90, stocks_wb=50, cost_price=3_000),
        ]
        enriched = _enrich_products(products, {}, False, None, 7, 1.8)

        summary = _build_summary(enriched, trend=[])
        assert summary["total_capital"] == 400_000 + 100_000 + 150_000
        assert summary["liquid_capital"] == 400_000
        assert summary["transition_capital"] == 100_000
        assert summary["illiquid_capital"] == 150_000
        assert abs(summary["liquid_pct"] - 400_000 / 650_000 * 100) < 0.2

    def test_summary_empty(self):
        """No products → zeros."""
        summary = _build_summary([], trend=[])
        assert summary["total_capital"] == 0
        assert summary["roi_monthly"] == 0

    def test_summary_roi_trend(self):
        """ROI trend computed from first/last trend points."""
        trend = [
            {"date": "2025-01-01", "liquid": 0, "transition": 0, "illiquid": 0, "total": 0, "roi_monthly": 10.0},
            {"date": "2025-01-07", "liquid": 0, "transition": 0, "illiquid": 0, "total": 0, "roi_monthly": 12.0},
        ]
        summary = _build_summary([], trend=trend)
        # (12 - 10) / 10 * 100 = 20.0%
        assert summary["roi_trend"] == 20.0


# ─── get_capital_analysis (integration via mocks) ───────────────────────────


class TestGetCapitalAnalysis:
    @pytest.mark.asyncio
    async def test_empty_data(self):
        """No products → empty response, no crash."""
        mock_trends = {"products": []}

        with (
            patch(
                "backend.services.funnel.capital.get_product_trends",
                new_callable=AsyncMock,
                return_value=mock_trends,
            ),
            patch(
                "backend.services.funnel.capital.load_wb_stock_history",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await get_capital_analysis(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
            )

        assert result["total_products"] == 0
        assert result["groups"] == []
        assert result["trend"] == []
        assert result["summary"]["total_capital"] == 0

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """Products flow through enrichment → grouping → summary."""
        products = [
            make_product(nm_id=1, brand="A", turnover_days=15, stocks_wb=100, cost_price=1_000),
            make_product(nm_id=2, brand="A", turnover_days=50, stocks_wb=50, cost_price=2_000),
            make_product(nm_id=3, brand="B", turnover_days=80, stocks_wb=30, cost_price=3_000),
        ]
        mock_trends = {"products": products}

        with (
            patch(
                "backend.services.funnel.capital.get_product_trends",
                new_callable=AsyncMock,
                return_value=mock_trends,
            ),
            patch(
                "backend.services.funnel.capital.load_wb_stock_history",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await get_capital_analysis(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                period_days=7,
                group_by="brand",
            )

        assert result["total_products"] == 3
        assert len(result["groups"]) == 2  # brand A and B
        assert result["summary"]["total_capital"] > 0
        assert result["period_days"] == 7

    @pytest.mark.asyncio
    async def test_trend_building(self):
        """Mock stock history → correct trend output."""
        products = [
            make_product(nm_id=1, turnover_days=20, stocks_wb=100, cost_price=1_000, orders_count=70),
        ]
        mock_trends = {"products": products}

        today = date.today()
        d1 = today - timedelta(days=2)
        d2 = today - timedelta(days=1)
        mock_history = {
            d1: {1: 100},
            d2: {1: 80},
        }

        with (
            patch(
                "backend.services.funnel.capital.get_product_trends",
                new_callable=AsyncMock,
                return_value=mock_trends,
            ),
            patch(
                "backend.services.funnel.capital.load_wb_stock_history",
                new_callable=AsyncMock,
                return_value=mock_history,
            ),
        ):
            result = await get_capital_analysis(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                period_days=7,
            )

        assert len(result["trend"]) == 2
        # First day: 100 units * 1000 cost = 100_000
        assert result["trend"][0]["total"] == 100_000.0
        # Second day: 80 units * 1000 cost = 80_000
        assert result["trend"][1]["total"] == 80_000.0

    @pytest.mark.asyncio
    async def test_elasticity_parameter(self):
        """Elasticity param flows through to recommendations."""
        products = [
            make_product(nm_id=1, turnover_days=20, stocks_wb=100, cost_price=1_000),
        ]
        mock_trends = {"products": products}

        with (
            patch(
                "backend.services.funnel.capital.get_product_trends",
                new_callable=AsyncMock,
                return_value=mock_trends,
            ),
            patch(
                "backend.services.funnel.capital.load_wb_stock_history",
                new_callable=AsyncMock,
                return_value={},
            ),
        ):
            result = await get_capital_analysis(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                elasticity=3.0,
            )

        assert result["elasticity"] == 3.0
        assert result["total_products"] == 1
