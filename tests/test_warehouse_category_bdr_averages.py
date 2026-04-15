"""Unit tests for `_compute_category_bdr_averages`.

Exercises the qty-weighted per-unit fields (`unit_revenue_30`,
`unit_profit_30`) introduced 2026-04-15 for the «Новинки» KPI card.

The invariant: per-unit values use qty-weighted averaging across all items
with sales in the category, so an item selling 1k units at 500₽ dominates a
sibling that sold 2 units at 5k₽.
"""

from backend.services.warehouse_stock_engine import _compute_category_bdr_averages


def _trend(sale_qty: float, revenue: float, profit: float) -> dict:
    return {
        "avg_daily_qty": 0.0,
        "sale_qty": sale_qty,
        "revenue": revenue,
        "profit": profit,
    }


def _bdr_entry(sale_qty_30: float, revenue_30: float, profit_30: float) -> dict:
    return {
        "avg_daily_revenue": 0.0,
        "avg_daily_profit": 0.0,
        "avg_price": 0.0,
        "avg_profit": 0.0,
        "trend_7": _trend(0, 0, 0),
        "trend_14": _trend(0, 0, 0),
        "trend_30": _trend(sale_qty_30, revenue_30, profit_30),
    }


class TestCategoryBdrAverages:
    def test_unit_revenue_is_qty_weighted(self):
        """High-volume cheap SKU dominates low-volume expensive SKU in the avg.

        Sibling A: 100 units × 500₽ = 50 000₽
        Sibling B:  10 units × 5000₽ = 50 000₽
        Total:     110 units, 100 000₽
        Expected unit_revenue_30 ≈ 909.09 (not 2750 — that would be a simple
        mean of per-item prices).
        """
        bdr_map = {
            1: _bdr_entry(100, 50_000, 7_500),
            2: _bdr_entry(10, 50_000, 7_500),
        }
        nom_map = {
            1: {"subject": "Ковры"},
            2: {"subject": "Ковры"},
        }
        result = _compute_category_bdr_averages(bdr_map, nom_map)
        assert "Ковры" in result
        cat = result["Ковры"]
        # (50000 + 50000) / (100 + 10) = 909.09
        assert cat["unit_revenue_30"] == 909.09
        # Profits (7500+7500) / qty 110 = 136.36
        assert cat["unit_profit_30"] == 136.36

    def test_items_without_sales_excluded_from_denominator(self):
        """Zero-revenue siblings don't drag the per-unit price down.

        Only items with revenue > 0 feed the aggregation — otherwise a fleet
        of stale SKUs would collapse the avg to zero and the «Новинки»
        estimate would under-count.
        """
        bdr_map = {
            1: _bdr_entry(10, 10_000, 1_500),  # real sale
            2: _bdr_entry(0, 0, 0),  # stale
            3: _bdr_entry(0, 0, 0),  # stale
        }
        nom_map = {
            1: {"subject": "Ковры"},
            2: {"subject": "Ковры"},
            3: {"subject": "Ковры"},
        }
        result = _compute_category_bdr_averages(bdr_map, nom_map)
        cat = result["Ковры"]
        assert cat["unit_revenue_30"] == 1_000.0  # 10_000 / 10
        assert cat["unit_profit_30"] == 150.0  # 1_500 / 10

    def test_missing_subject_skipped(self):
        """Items with no subject can't be assigned to any category."""
        bdr_map = {1: _bdr_entry(10, 10_000, 1_500)}
        nom_map = {1: {"subject": None}}
        result = _compute_category_bdr_averages(bdr_map, nom_map)
        assert result == {}

    def test_zero_qty_category_gets_zero_unit_metrics(self):
        """Edge case: a category passes the rev_30 > 0 filter but sums to
        zero sale_qty (shouldn't happen in practice — qty and revenue come
        from the same rows — but the divide must not blow up).

        The category is still present (because rev_30 > 0), but unit_* fields
        fall back to 0 instead of raising ZeroDivisionError.
        """
        bdr_map = {1: _bdr_entry(0, 10_000, 1_500)}
        nom_map = {1: {"subject": "Ковры"}}
        result = _compute_category_bdr_averages(bdr_map, nom_map)
        assert "Ковры" in result
        assert result["Ковры"]["unit_revenue_30"] == 0.0
        assert result["Ковры"]["unit_profit_30"] == 0.0

    def test_legacy_trend_fields_unchanged(self):
        """Adding unit_revenue_30/unit_profit_30 must not break trend_7/14/30
        shape — downstream code still reads those as per-item averages.
        """
        bdr_map = {
            1: {
                "avg_daily_revenue": 0,
                "avg_daily_profit": 0,
                "avg_price": 0,
                "avg_profit": 0,
                "trend_7": _trend(1, 100, 15),
                "trend_14": _trend(2, 200, 30),
                "trend_30": _trend(4, 400, 60),
            },
            2: {
                "avg_daily_revenue": 0,
                "avg_daily_profit": 0,
                "avg_price": 0,
                "avg_profit": 0,
                "trend_7": _trend(1, 300, 45),
                "trend_14": _trend(2, 600, 90),
                "trend_30": _trend(4, 1200, 180),
            },
        }
        nom_map = {
            1: {"subject": "Пледы"},
            2: {"subject": "Пледы"},
        }
        result = _compute_category_bdr_averages(bdr_map, nom_map)
        cat = result["Пледы"]
        # Per-ITEM average (legacy): (400 + 1200) / 2 = 800
        assert cat["trend_30"]["revenue"] == 800.0
        assert cat["trend_30"]["profit"] == 120.0
        # Per-UNIT average (new): (400 + 1200) / (4 + 4) = 200
        assert cat["unit_revenue_30"] == 200.0
        assert cat["unit_profit_30"] == 30.0

    def test_multiple_subjects_isolated(self):
        """Subjects don't leak into each other's averages."""
        bdr_map = {
            1: _bdr_entry(10, 10_000, 2_000),  # Ковры
            2: _bdr_entry(20, 60_000, 12_000),  # Чехлы
        }
        nom_map = {
            1: {"subject": "Ковры"},
            2: {"subject": "Чехлы"},
        }
        result = _compute_category_bdr_averages(bdr_map, nom_map)
        assert result["Ковры"]["unit_revenue_30"] == 1_000.0
        assert result["Чехлы"]["unit_revenue_30"] == 3_000.0
