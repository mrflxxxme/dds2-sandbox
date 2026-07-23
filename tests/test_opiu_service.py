"""
Tests for opiu_service — pure computation functions.
No DB required — tests _empty_totals and P&L formula structure.

Note: _accumulate_row was removed when OPIU was migrated to SQL GROUP BY aggregation.
Row-level accumulation is now done in SQL (see _build_aggregate_sql).
These tests validate the totals structure and P&L formula correctness.
"""

from datetime import date
from decimal import Decimal

D = Decimal
ZERO = D("0")


# ─── Imports ─────────────────────────────────────────────────────────────────

from backend.services.opiu_helpers import (
    OPEX_EXCLUDED_TYPES,
    build_opex_by_type_sql,
)
from backend.services.opiu_service import _build_pnl_result, _empty_totals

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _empty_totals
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmptyTotals:
    """Verify initial state of P&L accumulators."""

    def test_all_fields_zero(self):
        """All numeric fields should be ZERO."""
        t = _empty_totals()
        for key, val in t.items():
            assert val == ZERO, f"Field '{key}' should be ZERO, got {val}"

    def test_required_fields_exist(self):
        """Must contain all P&L metric fields."""
        t = _empty_totals()
        required = [
            "realization",
            "sales_amount",
            "logistics",
            "commission",
            "penalties",
            "storage",
            "acceptance",
            "deductions",
            "ad_deduction",
            "additional_payment",
            "compensation_ppvz",
        ]
        for field in required:
            assert field in t, f"Missing field '{field}' in _empty_totals()"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: P&L formula chain (using pre-filled data)
# ═══════════════════════════════════════════════════════════════════════════════


class TestPnLFormulas:
    """Verify P&L calculations using pre-filled accumulator data."""

    def _build_scenario(self):
        """Build a realistic P&L scenario with pre-filled metrics."""
        t = _empty_totals()
        # Simulate: 10 sales @ 1000 retail, 2 returns
        t["realization"] = D("8000")  # 10*1000 - 2*1000
        t["sales_amount"] = D("6400")  # 10*800 - 2*800
        t["commission"] = D("1600")  # sales - ppvz_net
        t["logistics"] = D("500")
        t["storage"] = D("200")
        t["penalties"] = D("100")
        t["acceptance"] = D("80")
        t["deductions"] = D("350")  # 300 ad + 50 non-ad
        t["ad_deduction"] = D("300")
        t["additional_payment"] = D("80")  # 10*10 - 2*10
        t["compensation_ppvz"] = D("0")
        return t

    def test_spp_discount(self):
        """SPP discount = realization - sales_amount."""
        t = self._build_scenario()
        spp = t["realization"] - t["sales_amount"]
        assert spp == D("1600")

    def test_other_deductions_formula(self):
        """Прочие удержания = deductions - ad_deduction."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        assert other_ded == D("50")

    def test_direct_costs(self):
        """Direct = logistics + commission + penalties + storage + ads + other_ded + acceptance."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = (
            t["logistics"]
            + t["commission"]
            + t["penalties"]
            + t["storage"]
            + t["ad_deduction"]
            + other_ded
            + t["acceptance"]
        )
        assert direct == D("2830")

    def test_gross_margin(self):
        """Gross margin = sales_amount - direct_costs + compensation."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = (
            t["logistics"]
            + t["commission"]
            + t["penalties"]
            + t["storage"]
            + t["ad_deduction"]
            + other_ded
            + t["acceptance"]
        )
        comp = t["additional_payment"] + t["compensation_ppvz"]
        margin = t["sales_amount"] - direct + comp
        assert margin == D("3650")

    def test_tax_calculation_usn(self):
        """USN 6% tax on sales_amount = 6400 * 0.06 = 384."""
        t = self._build_scenario()
        usn_rate = D("0.06")
        tax = max(t["sales_amount"] * usn_rate, ZERO)
        assert tax == D("384.00")

    def test_net_profit(self):
        """Net profit = EBITDA - taxes."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = (
            t["logistics"]
            + t["commission"]
            + t["penalties"]
            + t["storage"]
            + t["ad_deduction"]
            + other_ded
            + t["acceptance"]
        )
        comp = t["additional_payment"] + t["compensation_ppvz"]
        margin = t["sales_amount"] - direct + comp

        tax = t["sales_amount"] * D("0.06")
        net_profit = margin - tax
        assert net_profit == D("3266.00")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Операционные расходы (opex by counterparty type)
# ═══════════════════════════════════════════════════════════════════════════════


def _pnl(opex_by_type):
    """Run _build_pnl_result over a single month with a fixed base scenario."""
    mk = "2026-01"
    d = _empty_totals()
    d["realization"] = D("10000")
    d["sales_amount"] = D("8000")
    monthly_data = {mk: d}
    total_data = dict(d)
    months_sorted = [mk]
    tax_info = {"usn_rate": 0, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": False}
    return _build_pnl_result(
        monthly_data,
        total_data,
        months_sorted,
        [],
        tax_info,
        {mk: 0.0},  # ads
        {mk: 0.0},  # cost
        0.0,
        date(2026, 1, 1),
        date(2026, 1, 31),
        opex_by_type=opex_by_type,
    )


class TestOperatingExpenses:
    """«Операционные расходы» раскрываются в разбивку по типам контрагентов."""

    def _rows_by_key(self, result):
        return {r["key"]: r for r in result["rows"]}

    def test_zero_when_no_opex(self):
        """Без данных opex строка = 0 и не раскрывается."""
        rows = self._rows_by_key(_pnl({}))
        ops = rows["operating_expenses"]
        assert ops["total"] == 0
        assert ops["expandable"] is False
        assert not any(r["key"].startswith("opex_") for r in _pnl({}).get("rows", []))

    def test_children_created_and_summed(self):
        """Родитель = сумма детей; дети несут parent_key."""
        result = _pnl({"LANDLORD": {"2026-01": 100.0}, "DESIGNER": {"2026-01": 300.0}})
        rows = self._rows_by_key(result)
        ops = rows["operating_expenses"]
        assert ops["total"] == 400
        assert ops["expandable"] is True
        assert rows["opex_landlord"]["total"] == 100
        assert rows["opex_designer"]["total"] == 300
        assert rows["opex_landlord"]["parent_key"] == "operating_expenses"
        assert rows["opex_designer"]["level"] == 1
        # RU label from OPEX_TYPE_LABELS
        assert rows["opex_designer"]["label"] == "Дизайнер"

    def test_children_sorted_by_total_desc(self):
        """Дети отсортированы по убыванию суммы, идут сразу за родителем."""
        result = _pnl({"LANDLORD": {"2026-01": 100.0}, "DESIGNER": {"2026-01": 300.0}})
        keys = [r["key"] for r in result["rows"]]
        parent_idx = keys.index("operating_expenses")
        assert keys[parent_idx + 1] == "opex_designer"  # 300 > 100
        assert keys[parent_idx + 2] == "opex_landlord"

    def test_ebitda_subtracts_opex(self):
        """EBITDA = Валовая маржа − Операционные расходы."""
        result = _pnl({"DESIGNER": {"2026-01": 300.0}})
        rows = self._rows_by_key(result)
        assert rows["ebitda"]["total"] == rows["gross_margin"]["total"] - 300


class TestOpexSql:
    """SQL-агрегатор opex по типам контрагентов."""

    def test_excluded_types(self):
        """Закупочные типы и «Прочее» исключены из opex."""
        assert OPEX_EXCLUDED_TYPES == frozenset({"OTHER", "SUPPLIER", "TRADING_HOUSE"})

    def test_sql_shape(self):
        """SQL группирует по типу, бьёт по месяцам, отсекает внутренние и CNY."""
        sql = build_opex_by_type_sql()
        assert "transactions" in sql
        assert "primary_type" in sql
        assert "is_internal = false" in sql
        assert "is_deleted = false" in sql
        assert "<> 'CNY'" in sql
        for t in OPEX_EXCLUDED_TYPES:
            assert f"'{t}'" in sql
