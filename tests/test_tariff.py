"""
Tests for WB tariff feature:
- tariff_parser.py (xlsx parsing)
- _compute_metrics (profit formula with tariff-based commission)
"""

import io
from decimal import Decimal

import pandas as pd
import pytest

# ─── tariff_parser tests ─────────────────────────────────────────────────────


def _make_xlsx(rows: list[dict], columns: list[str] | None = None) -> bytes:
    """Helper: create xlsx bytes from list of dicts."""
    df = pd.DataFrame(rows, columns=columns)
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()


class TestTariffParser:
    def test_parse_valid(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx(
            [
                {"Предмет": "Платья", "Склад WB %": 15.5},
                {"Предмет": "Футболки", "Склад WB %": 12.0},
            ]
        )
        result = parse_tariff_xlsx(data)
        assert len(result) == 2
        assert result[0]["subject_name"] == "Платья"
        assert result[0]["commission_rate"] == Decimal("15.5")
        assert result[1]["subject_name"] == "Футболки"
        assert result[1]["commission_rate"] == Decimal("12.0")

    def test_parse_deduplicates(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx(
            [
                {"Предмет": "Платья", "Склад WB %": 10},
                {"Предмет": "Платья", "Склад WB %": 15},
            ]
        )
        result = parse_tariff_xlsx(data)
        assert len(result) == 1
        assert result[0]["commission_rate"] == Decimal("15.0")

    def test_parse_missing_subject_column(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([{"Категория": "Одежда", "Ставка": 10}])
        with pytest.raises(ValueError, match="Предмет"):
            parse_tariff_xlsx(data)

    def test_parse_missing_rate_column(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([{"Предмет": "Платья", "Другое": 10}])
        with pytest.raises(ValueError, match="тарифом"):
            parse_tariff_xlsx(data)

    def test_parse_empty_file(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([])
        with pytest.raises(ValueError, match="пустой"):
            parse_tariff_xlsx(data)

    def test_parse_rate_out_of_range(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([{"Предмет": "Платья", "Склад WB %": 150}])
        with pytest.raises(ValueError, match="0-100"):
            parse_tariff_xlsx(data)

    def test_parse_rate_negative(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([{"Предмет": "Платья", "Склад WB %": -5}])
        with pytest.raises(ValueError, match="0-100"):
            parse_tariff_xlsx(data)

    def test_parse_flexible_column_names(self):
        """Accept alternative column names."""
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([{"Предмет товара": "Платья", "Комиссия": 12}])
        result = parse_tariff_xlsx(data)
        assert len(result) == 1
        assert result[0]["subject_name"] == "Платья"

    def test_parse_strips_whitespace(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx([{"Предмет": "  Платья  ", "Склад WB %": 12}])
        result = parse_tariff_xlsx(data)
        assert result[0]["subject_name"] == "Платья"

    def test_parse_skips_non_numeric_rates(self):
        from backend.etl.tariff_parser import parse_tariff_xlsx

        data = _make_xlsx(
            [
                {"Предмет": "Платья", "Склад WB %": 12},
                {"Предмет": "Юбки", "Склад WB %": "abc"},
            ]
        )
        result = parse_tariff_xlsx(data)
        assert len(result) == 1
        assert result[0]["subject_name"] == "Платья"


# ─── _compute_metrics tests (tariff-based formula) ──────────────────────────


class TestComputeMetrics:
    def test_basic_profit(self):
        from backend.services.funnel.queries import _compute_metrics

        m = _compute_metrics(
            orders_sum=100_000,
            buyout=80,
            adv=5_000,
            tax_rate=6,
            views=1000,
            clicks=50,
            orders_count=10,
            cost_total=20_000,
            commission_rate_pct=15,
        )
        # revenue = 100000 * 80 / 100 = 80000
        assert m["revenue"] == 80_000.0
        # commission = 80000 * 15 / 100 = 12000
        assert m["commission"] == 12_000.0
        # tax = 80000 * 6 / 100 = 4800
        assert m["tax"] == 4_800.0
        # profit = 80000 - 5000 - 12000 - 20000 - 4800 = 38200
        assert m["profit"] == 38_200.0
        assert m["commission_rate"] == 15.0

    def test_zero_tariff(self):
        from backend.services.funnel.queries import _compute_metrics

        m = _compute_metrics(
            orders_sum=50_000,
            buyout=100,
            adv=1_000,
            tax_rate=6,
            views=100,
            clicks=10,
            orders_count=5,
            cost_total=10_000,
            commission_rate_pct=0,
        )
        # commission = 0
        assert m["commission"] == 0.0
        # profit = 50000 - 1000 - 0 - 10000 - 3000 = 36000
        assert m["profit"] == 36_000.0

    def test_no_revenue(self):
        from backend.services.funnel.queries import _compute_metrics

        m = _compute_metrics(
            orders_sum=0,
            buyout=0,
            adv=0,
            tax_rate=6,
            views=0,
            clicks=0,
            orders_count=0,
            commission_rate_pct=15,
        )
        assert m["revenue"] == 0.0
        assert m["commission"] == 0.0
        assert m["profit"] == 0.0
        assert m["margin"] == 0.0

    def test_drr_calculation(self):
        from backend.services.funnel.queries import _compute_metrics

        m = _compute_metrics(
            orders_sum=100_000,
            buyout=100,
            adv=10_000,
            tax_rate=0,
            views=0,
            clicks=0,
            orders_count=10,
            commission_rate_pct=0,
        )
        assert m["drr"] == 10.0  # 10000/100000 * 100

    def test_margin_calculation(self):
        from backend.services.funnel.queries import _compute_metrics

        m = _compute_metrics(
            orders_sum=100_000,
            buyout=100,
            adv=0,
            tax_rate=0,
            views=0,
            clicks=0,
            orders_count=10,
            cost_total=0,
            commission_rate_pct=20,
        )
        # revenue = 100000, commission = 20000, profit = 80000
        # margin = 80000/100000 * 100 = 80
        assert m["margin"] == 80.0
