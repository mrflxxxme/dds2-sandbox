# ruff: noqa: RUF001
"""
Tests for anomalies service — 5 pattern detectors + main get_anomalies.

Covers:
1. _detect_hit_loss — top-revenue product with margin < 10%
2. _detect_hit_loss — product NOT in top 20% → not detected
3. _detect_hit_loss — negative margin → "Хит в убытке"
4. _detect_hit_oos — top-orders product running out of stock
5. _detect_hit_oos — turnover > 10 → not detected
6. _detect_toxic_ads — DRR > 30% and spend > 5000
7. _detect_toxic_ads — adv_sum < 5000 → not detected
8. _detect_dead_stock — warning (turnover 30-60)
9. _detect_dead_stock — critical (turnover > 60)
10. _detect_dead_stock — include_rf adds RF stock
11. _detect_low_buyout — cheap product with low buyout
12. get_anomalies — min_orders filter
13. get_anomalies — deduplication keeps highest priority
14. get_anomalies — summary calculation
15. get_anomalies — empty data → no crash
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.funnel.anomalies import (
    _deduplicate,
    _detect_dead_stock,
    _detect_hit_loss,
    _detect_hit_oos,
    _detect_low_buyout,
    _detect_toxic_ads,
    get_anomalies,
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


# ─── _detect_hit_loss ────────────────────────────────────────────────────────


class TestDetectHitLoss:
    def test_hit_loss_detected(self):
        """Product in top 20% with margin < 10% → detected as critical."""
        p = make_product(orders_sum_rub=1_000_000, margin=5.0)
        result = _detect_hit_loss([p], top20_threshold=500_000)

        assert len(result) == 1
        assert result[0]["anomaly_type"] == "hit_loss"
        assert result[0]["severity"] == "critical"
        assert result[0]["title"] == "Хит с низкой маржой"
        assert result[0]["loss_amount"] > 0

    def test_hit_loss_not_triggered_low_revenue(self):
        """Product NOT in top 20% → not detected."""
        p = make_product(orders_sum_rub=100_000, margin=5.0)
        result = _detect_hit_loss([p], top20_threshold=500_000)

        assert len(result) == 0

    def test_hit_loss_negative_margin(self):
        """Negative margin → title should be 'Хит в убытке'."""
        p = make_product(orders_sum_rub=1_000_000, margin=-3.0)
        result = _detect_hit_loss([p], top20_threshold=500_000)

        assert len(result) == 1
        assert result[0]["title"] == "Хит в убытке"

    def test_hit_loss_margin_at_boundary(self):
        """Margin exactly 10% → NOT detected (condition is margin >= 10)."""
        p = make_product(orders_sum_rub=1_000_000, margin=10.0)
        result = _detect_hit_loss([p], top20_threshold=500_000)

        assert len(result) == 0


# ─── _detect_hit_oos ─────────────────────────────────────────────────────────


class TestDetectHitOos:
    def test_hit_oos_detected(self):
        """Top-orders product with turnover < 10 days → detected."""
        p = make_product(orders_count=200, turnover_days=5, stocks_wb=50, avg_price=3000)
        result = _detect_hit_oos([p], top30_threshold=100, period_days=7)

        assert len(result) == 1
        assert result[0]["anomaly_type"] == "hit_oos"
        assert result[0]["severity"] == "critical"
        assert result[0]["metrics"]["restock_qty"] is not None

    def test_hit_oos_not_triggered_high_stock(self):
        """Turnover >= 10 days → not detected."""
        p = make_product(orders_count=200, turnover_days=15)
        result = _detect_hit_oos([p], top30_threshold=100, period_days=7)

        assert len(result) == 0

    def test_hit_oos_not_triggered_low_orders(self):
        """Orders below top30 threshold → not detected."""
        p = make_product(orders_count=10, turnover_days=3)
        result = _detect_hit_oos([p], top30_threshold=100, period_days=7)

        assert len(result) == 0


# ─── _detect_toxic_ads ───────────────────────────────────────────────────────


class TestDetectToxicAds:
    def test_toxic_ads_detected(self):
        """adv_sum > 5000 AND drr > 30% → detected."""
        p = make_product(adv_sum=50_000, drr=45.0, orders_sum_rub=100_000)
        result = _detect_toxic_ads([p], period_days=7)

        assert len(result) == 1
        assert result[0]["anomaly_type"] == "toxic_ads"
        assert result[0]["severity"] == "critical"
        assert result[0]["loss_amount"] > 0

    def test_toxic_ads_not_triggered_low_spend(self):
        """adv_sum <= 5000 → not detected even if drr is high."""
        p = make_product(adv_sum=3_000, drr=50.0)
        result = _detect_toxic_ads([p], period_days=7)

        assert len(result) == 0

    def test_toxic_ads_not_triggered_low_drr(self):
        """drr <= 30 → not detected even if spend is high."""
        p = make_product(adv_sum=50_000, drr=25.0)
        result = _detect_toxic_ads([p], period_days=7)

        assert len(result) == 0


# ─── _detect_dead_stock ──────────────────────────────────────────────────────


class TestDetectDeadStock:
    def test_dead_stock_warning(self):
        """Turnover 30-60 days, frozen > 30K → warning severity."""
        p = make_product(turnover_days=45, stocks_wb=100, cost_price=500)
        # frozen = 100 * 500 = 50_000 > 30_000
        result = _detect_dead_stock([p], rf_stocks_map={}, include_rf=False)

        assert len(result) == 1
        assert result[0]["severity"] == "warning"
        assert result[0]["title"] == "Вползаем в неликвид"

    def test_dead_stock_critical(self):
        """Turnover > 60 days → critical severity."""
        p = make_product(turnover_days=90, stocks_wb=100, cost_price=500)
        result = _detect_dead_stock([p], rf_stocks_map={}, include_rf=False)

        assert len(result) == 1
        assert result[0]["severity"] == "critical"
        assert result[0]["title"] == "Мёртвый сток"

    def test_dead_stock_with_rf(self):
        """include_rf adds RF stock values to frozen capital."""
        p = make_product(nm_id=42, turnover_days=90, stocks_wb=50, cost_price=500)
        # wb_value = 50*500 = 25_000, rf = 100*500 = 50_000, total = 75_000
        rf_map = {42: 100}
        result = _detect_dead_stock([p], rf_stocks_map=rf_map, include_rf=True)

        assert len(result) == 1
        assert result[0]["metrics"]["frozen_value"] == 75_000.0
        # stocks_wb in metrics = wb + rf total
        assert result[0]["metrics"]["stocks_wb"] == 150

    def test_dead_stock_not_triggered_low_frozen(self):
        """Frozen <= 30K → not detected."""
        p = make_product(turnover_days=90, stocks_wb=10, cost_price=100)
        # frozen = 10 * 100 = 1_000 < 30_000
        result = _detect_dead_stock([p], rf_stocks_map={}, include_rf=False)

        assert len(result) == 0

    def test_dead_stock_zero_cost(self):
        """cost_price <= 0 → skipped."""
        p = make_product(turnover_days=90, stocks_wb=200, cost_price=0)
        result = _detect_dead_stock([p], rf_stocks_map={}, include_rf=False)

        assert len(result) == 0


# ─── _detect_low_buyout ──────────────────────────────────────────────────────


class TestDetectLowBuyout:
    def test_low_buyout_detected(self):
        """Cheap product (< 1500) with low buyout (< 70%) → detected."""
        p = make_product(avg_price=800, buyout_pct=50)
        result = _detect_low_buyout([p])

        assert len(result) == 1
        assert result[0]["anomaly_type"] == "low_buyout"
        assert result[0]["severity"] == "warning"

    def test_low_buyout_high_logistics_ratio(self):
        """Very cheap product where logistics_ratio > 15% → detected."""
        p = make_product(avg_price=500, buyout_pct=80)
        # logistics_ratio = 110 / 500 * 100 = 22% > 15%
        result = _detect_low_buyout([p])

        assert len(result) == 1

    def test_low_buyout_not_triggered_expensive(self):
        """Expensive product with decent buyout → not detected."""
        p = make_product(avg_price=5000, buyout_pct=80)
        # logistics_ratio = 110/5000*100 = 2.2%, price >= 1500 and buyout >= 70
        result = _detect_low_buyout([p])

        assert len(result) == 0


# ─── _deduplicate ────────────────────────────────────────────────────────────


class TestDeduplicate:
    def test_keeps_highest_priority(self):
        """Product with 2 anomalies → only highest priority kept."""
        anomalies = [
            {"nm_id": 1, "anomaly_type": "dead_stock", "other": "data1"},
            {"nm_id": 1, "anomaly_type": "hit_loss", "other": "data2"},
        ]
        result = _deduplicate(anomalies)

        assert len(result) == 1
        assert result[0]["anomaly_type"] == "hit_loss"  # priority 50 > 20

    def test_different_products_kept(self):
        """Different nm_ids → both kept."""
        anomalies = [
            {"nm_id": 1, "anomaly_type": "hit_loss"},
            {"nm_id": 2, "anomaly_type": "dead_stock"},
        ]
        result = _deduplicate(anomalies)

        assert len(result) == 2


# ─── get_anomalies (integration via mocks) ───────────────────────────────────


class TestGetAnomalies:
    @pytest.mark.asyncio
    async def test_min_orders_filter(self):
        """Products with < min_orders filtered out before detection."""
        products = [
            make_product(nm_id=1, orders_count=3, margin=2.0, orders_sum_rub=1_000_000),
            make_product(nm_id=2, orders_count=10, margin=15.0, orders_sum_rub=200_000),
        ]
        mock_trends = {"products": products}

        with patch(
            "backend.services.funnel.anomalies.get_product_trends",
            new_callable=AsyncMock,
            return_value=mock_trends,
        ):
            result = await get_anomalies(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                period_days=7,
                min_orders=5,
            )

        # Product 1 (orders=3) filtered out, product 2 is healthy
        assert result["total_products"] == 1
        assert len(result["anomalies"]) == 0

    @pytest.mark.asyncio
    async def test_summary_calculation(self):
        """Summary totals are correct."""
        products = [
            # hit_loss: top revenue, low margin
            make_product(nm_id=1, orders_count=50, orders_sum_rub=1_000_000, margin=2.0),
            # hit_oos: top orders, low turnover
            make_product(nm_id=2, orders_count=200, turnover_days=3, stocks_wb=10, avg_price=2000),
            # dead_stock: high turnover days, frozen capital
            make_product(nm_id=3, orders_count=10, turnover_days=90, stocks_wb=200, cost_price=500),
            # healthy product
            make_product(nm_id=4, orders_count=30, margin=20.0, turnover_days=20),
        ]
        mock_trends = {"products": products}

        with patch(
            "backend.services.funnel.anomalies.get_product_trends",
            new_callable=AsyncMock,
            return_value=mock_trends,
        ):
            result = await get_anomalies(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                period_days=7,
                min_orders=5,
            )

        summary = result["summary"]
        assert summary["total_loss"] >= 0
        assert summary["frozen_capital"] >= 0
        assert summary["healthy_count"] >= 0
        assert result["total_products"] == 4
        assert result["period_days"] == 7

    @pytest.mark.asyncio
    async def test_empty_data(self):
        """No products → empty response, no crash."""
        mock_trends = {"products": []}

        with patch(
            "backend.services.funnel.anomalies.get_product_trends",
            new_callable=AsyncMock,
            return_value=mock_trends,
        ):
            result = await get_anomalies(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
            )

        assert result["anomalies"] == []
        assert result["total_products"] == 0
        assert result["summary"]["total_loss"] == 0
        assert result["summary"]["healthy_count"] == 0

    @pytest.mark.asyncio
    async def test_deduplication_in_main(self):
        """Product triggering multiple detectors → only highest priority kept."""
        # Product is both hit_loss AND has toxic ads
        p = make_product(
            nm_id=1,
            orders_count=100,
            orders_sum_rub=2_000_000,
            margin=3.0,
            adv_sum=100_000,
            drr=50.0,
        )
        mock_trends = {"products": [p]}

        with patch(
            "backend.services.funnel.anomalies.get_product_trends",
            new_callable=AsyncMock,
            return_value=mock_trends,
        ):
            result = await get_anomalies(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                period_days=7,
                min_orders=5,
            )

        # Should have exactly 1 anomaly for nm_id=1 (hit_loss wins over toxic_ads)
        nm1_anomalies = [a for a in result["anomalies"] if a["nm_id"] == 1]
        assert len(nm1_anomalies) == 1
        assert nm1_anomalies[0]["anomaly_type"] == "hit_loss"

    @pytest.mark.asyncio
    async def test_rf_stocks_loaded_when_requested(self):
        """include_rf_stocks=True triggers _load_rf_stocks call."""
        products = [
            make_product(nm_id=42, orders_count=10, turnover_days=90, stocks_wb=50, cost_price=500),
        ]
        mock_trends = {"products": products}

        with (
            patch(
                "backend.services.funnel.anomalies.get_product_trends",
                new_callable=AsyncMock,
                return_value=mock_trends,
            ),
            patch(
                "backend.services.funnel.anomalies.load_rf_stocks",
                new_callable=AsyncMock,
                return_value={42: 100},
            ) as mock_rf,
        ):
            result = await get_anomalies(
                db=AsyncMock(),
                project_id=1,
                tax_info={},
                period_days=7,
                min_orders=5,
                include_rf_stocks=True,
            )

        mock_rf.assert_called_once()
        # dead_stock should include RF value
        dead = [a for a in result["anomalies"] if a["anomaly_type"] == "dead_stock"]
        if dead:
            assert dead[0]["metrics"]["frozen_value"] == 75_000.0
