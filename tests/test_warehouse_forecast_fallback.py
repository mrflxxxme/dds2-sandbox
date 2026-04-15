"""Unit tests for the narrowed forecast fallback.

Context (2026-04-15): earlier the unified-stock report substituted a
category-average revenue/profit for ANY item without sales in the 30-day
window. That inflated group totals by the share of stale/incoming SKUs (we
observed ~1.92x БДР on a real project) and made «Единые остатки» diverge from
«БДР (WB)».

The fix is a narrower fallback that only runs when the user explicitly asks
for the forecast view, and only for items that are en route (factory order /
vehicle FORMING/SHIPPED/CUSTOMS/DISPATCHED) and NOT yet physically on a
shelf. Shelf stock without sales stays at zero so «Факт» mode lines up with
БДР copeck-for-copeck.
"""

from backend.services.warehouse_stock_engine import _apply_forecast_fallback


def _empty_trend() -> dict:
    return {"avg_daily_qty": 0.0, "revenue": 0.0, "profit": 0.0}


def _make_entry(
    *,
    subject: str | None = "Ковры",
    total_own: int = 0,
    total_wb: int = 0,
    factory_qty: int = 0,
    vehicle_forming_qty: int = 0,
    vehicle_transit_qty: int = 0,
    trend_30_revenue: float = 0.0,
) -> dict:
    return {
        "subject": subject,
        "total_own": total_own,
        "total_wb": total_wb,
        "factory_qty": factory_qty,
        "vehicle_forming_qty": vehicle_forming_qty,
        "vehicle_transit_qty": vehicle_transit_qty,
        "trend_7": _empty_trend(),
        "trend_14": _empty_trend(),
        "trend_30": {**_empty_trend(), "revenue": trend_30_revenue},
        "is_revenue_estimated": False,
    }


def _category_bdr() -> dict[str, dict]:
    return {
        "Ковры": {
            "trend_7": {"avg_daily_qty": 0.0, "revenue": 70_000.0, "profit": 10_500.0},
            "trend_14": {"avg_daily_qty": 0.0, "revenue": 140_000.0, "profit": 21_000.0},
            "trend_30": {"avg_daily_qty": 0.0, "revenue": 300_000.0, "profit": 45_000.0},
        },
    }


class TestApplyForecastFallback:
    def test_incoming_item_without_shelf_gets_estimate(self):
        """Factory-only SKU with no sales → estimate applied, flag set."""
        unified = {1: _make_entry(factory_qty=50)}
        _apply_forecast_fallback(unified, _category_bdr())
        entry = unified[1]
        assert entry["is_revenue_estimated"] is True
        assert entry["trend_30"]["revenue"] == 300_000.0
        assert entry["trend_14"]["revenue"] == 140_000.0
        assert entry["trend_7"]["revenue"] == 70_000.0

    def test_vehicle_transit_item_gets_estimate(self):
        """Vehicle-in-transit SKU with no sales → estimate applied."""
        unified = {1: _make_entry(vehicle_transit_qty=10)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is True
        assert unified[1]["trend_30"]["revenue"] == 300_000.0

    def test_vehicle_forming_item_gets_estimate(self):
        """Vehicle being formed also counts as «en route» for the estimate."""
        unified = {1: _make_entry(vehicle_forming_qty=5)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is True

    def test_shelf_stock_without_sales_stays_zero(self):
        """SKU sitting on the warehouse with no recent sales → NO estimate.

        This is the whole point of the fix — stale shelf stock must not inflate
        category totals, otherwise «Факт» mode diverges from БДР.
        """
        unified = {1: _make_entry(total_own=100, factory_qty=0)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is False
        assert unified[1]["trend_30"]["revenue"] == 0.0

    def test_wb_stock_without_sales_stays_zero(self):
        """Same rule for WB-side stock without sales."""
        unified = {1: _make_entry(total_wb=100)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is False
        assert unified[1]["trend_30"]["revenue"] == 0.0

    def test_shelf_plus_incoming_no_estimate_because_already_on_shelf(self):
        """Mixed case: some stock already on shelf → no estimate even if incoming > 0.

        Rationale: the shelf part would contribute real sales (if it sold), so
        estimating on top would double-count when grouped with sold siblings.
        """
        unified = {1: _make_entry(total_own=50, factory_qty=30)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is False
        assert unified[1]["trend_30"]["revenue"] == 0.0

    def test_item_with_real_sales_is_left_alone(self):
        """Real sales in the period → don't touch trend values."""
        unified = {1: _make_entry(factory_qty=20, trend_30_revenue=123_456.78)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is False
        assert unified[1]["trend_30"]["revenue"] == 123_456.78

    def test_unknown_subject_skipped(self):
        """Subject not in category_bdr → can't estimate, leave entry at zero."""
        unified = {1: _make_entry(subject="Редкая категория", factory_qty=50)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is False
        assert unified[1]["trend_30"]["revenue"] == 0.0

    def test_missing_subject_skipped(self):
        """subject=None (no Nomenclature.subject) → skip estimate."""
        unified = {1: _make_entry(subject=None, factory_qty=50)}
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["is_revenue_estimated"] is False

    def test_mixed_batch_partial_estimate(self):
        """Parity guarantee: only incoming-only items get the bump.

        Scenario mirrors what tripped the production report:
          * one SKU selling normally (real revenue)
          * one SKU sitting on shelf without sales (must stay zero)
          * one SKU only in factory order (must get the estimate)

        After the helper runs, the sum across the three entries should be
        EXACTLY real_revenue + category_avg_30 — no inflation from the stale
        shelf item.
        """
        unified = {
            1: _make_entry(total_own=5, trend_30_revenue=1_000_000.0),  # real
            2: _make_entry(total_own=50),  # stale shelf — MUST stay 0
            3: _make_entry(factory_qty=30),  # incoming — MUST get 300k
        }
        _apply_forecast_fallback(unified, _category_bdr())
        assert unified[1]["trend_30"]["revenue"] == 1_000_000.0
        assert unified[2]["trend_30"]["revenue"] == 0.0
        assert unified[3]["trend_30"]["revenue"] == 300_000.0
        total = sum(u["trend_30"]["revenue"] for u in unified.values())
        assert total == 1_300_000.0
