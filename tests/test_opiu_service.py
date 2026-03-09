"""
Tests for opiu_service — pure computation functions.
No DB required — tests _empty_totals, _accumulate_row, and P&L formula chain.

Following the same pattern as test_wb_bdr_service.py.
"""
import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

D = Decimal
ZERO = D("0")


# ─── Imports ─────────────────────────────────────────────────────────────────

from backend.services.opiu_service import (
    _empty_totals,
    _accumulate_row,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _make_finance_row(**kwargs) -> MagicMock:
    """Create a mock WbFinanceRow with defaults."""
    defaults = {
        "rrd_id": 1,
        "brand_name": "TestBrand",
        "sa_name": "ART-001",
        "nm_id": 12345,
        "doc_type_name": "Продажа",
        "supplier_oper_name": "Продажа",
        "bonus_type_name": None,
        "rr_dt": date(2024, 1, 15),
        "date_from": date(2024, 1, 1),
        "date_to": date(2024, 1, 31),
        "quantity": 1,
        "retail_price_withdisc_rub": D("1000"),
        "retail_amount": D("800"),
        "ppvz_for_pay": D("600"),
        "ppvz_sales_commission": D("100"),
        "delivery_rub": D("0"),
        "penalty": D("0"),
        "additional_payment": D("0"),
        "storage_fee": D("0"),
        "acceptance": D("0"),
        "deduction": D("0"),
        "ppvz_reward": D("0"),
        "rebill_logistic_cost": D("0"),
        "ppvz_vw": D("0"),
        "ppvz_vw_nds": D("0"),
    }
    defaults.update(kwargs)
    return MagicMock(**defaults)


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
            "realization", "sales_amount", "logistics", "commission",
            "penalties", "storage", "acceptance", "deductions",
            "ad_deduction", "additional_payment", "compensation_ppvz",
        ]
        for field in required:
            assert field in t, f"Missing field '{field}' in _empty_totals()"


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _accumulate_row — Sales
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccumulateSale:
    """Accumulation logic for Продажа (Sale) rows."""

    def test_basic_sale(self):
        """Single sale: realization and sales_amount populated correctly."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Продажа",
            supplier_oper_name="Продажа",
            retail_price_withdisc_rub=D("1000"),
            retail_amount=D("800"),
            ppvz_for_pay=D("600"),
        )
        _accumulate_row(target, row, "Продажа", "Продажа")

        assert target["realization"] == D("1000")
        assert target["sales_amount"] == D("800")

    def test_sale_ppvz_tracks(self):
        """ppvz_for_pay tracked in _ppvz_for_pay_sale for commission calc."""
        target = _empty_totals()
        row = _make_finance_row(ppvz_for_pay=D("600"))
        _accumulate_row(target, row, "Продажа", "Продажа")

        assert target["_ppvz_for_pay_sale"] == D("600")
        assert target["_ppvz_for_pay_ret"] == ZERO

    def test_commission_formula(self):
        """Commission = sales_amount - (ppvz_net - compensation).

        ppvz_net = ppvz_for_pay_sale - ppvz_for_pay_ret
        With single sale (no returns): commission = 800 - (600 - 0) = 200
        """
        target = _empty_totals()
        row = _make_finance_row(
            retail_amount=D("800"),
            ppvz_for_pay=D("600"),
        )
        _accumulate_row(target, row, "Продажа", "Продажа")

        assert target["commission"] == D("200")

    def test_additional_payment_sale(self):
        """Bonus points (additional_payment) tracked for sales."""
        target = _empty_totals()
        row = _make_finance_row(
            additional_payment=D("50"),
            retail_price_withdisc_rub=D("500"),
            retail_amount=D("400"),
            ppvz_for_pay=D("300"),
        )
        _accumulate_row(target, row, "Продажа", "Продажа")

        assert target["additional_payment"] == D("50")

    def test_multiple_sales_accumulate(self):
        """Two sales → values sum correctly."""
        target = _empty_totals()
        row1 = _make_finance_row(
            retail_price_withdisc_rub=D("1000"),
            retail_amount=D("800"),
            ppvz_for_pay=D("600"),
        )
        row2 = _make_finance_row(
            retail_price_withdisc_rub=D("500"),
            retail_amount=D("400"),
            ppvz_for_pay=D("300"),
        )
        _accumulate_row(target, row1, "Продажа", "Продажа")
        _accumulate_row(target, row2, "Продажа", "Продажа")

        assert target["realization"] == D("1500")
        assert target["sales_amount"] == D("1200")
        # commission = 1200 - (900 - 0) = 300
        assert target["commission"] == D("300")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _accumulate_row — Returns
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccumulateReturn:
    """Accumulation logic for Возврат (Return) rows."""

    def test_return_subtracts(self):
        """Return subtracts from realization and sales_amount."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Возврат",
            supplier_oper_name="Возврат",
            retail_price_withdisc_rub=D("200"),
            retail_amount=D("160"),
            ppvz_for_pay=D("120"),
        )
        _accumulate_row(target, row, "Возврат", "Возврат")

        assert target["realization"] == D("-200")
        assert target["sales_amount"] == D("-160")

    def test_return_ppvz_tracked_separately(self):
        """Return ppvz stored in _ppvz_for_pay_ret."""
        target = _empty_totals()
        row = _make_finance_row(ppvz_for_pay=D("120"))
        _accumulate_row(target, row, "Возврат", "Возврат")

        assert target["_ppvz_for_pay_ret"] == D("120")
        assert target["_ppvz_for_pay_sale"] == ZERO

    def test_sale_then_return_net(self):
        """Net values after sale + return."""
        target = _empty_totals()

        sale = _make_finance_row(
            retail_price_withdisc_rub=D("1000"),
            retail_amount=D("800"),
            ppvz_for_pay=D("600"),
        )
        ret = _make_finance_row(
            retail_price_withdisc_rub=D("200"),
            retail_amount=D("160"),
            ppvz_for_pay=D("120"),
        )
        _accumulate_row(target, sale, "Продажа", "Продажа")
        _accumulate_row(target, ret, "Возврат", "Возврат")

        assert target["realization"] == D("800")   # 1000 - 200
        assert target["sales_amount"] == D("640")  # 800 - 160

        # ppvz_net = 600 - 120 = 480
        # commission = 640 - (480 - 0) = 160
        assert target["commission"] == D("160")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _accumulate_row — Other (logistics, storage, penalties, deductions)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccumulateOther:
    """Non-sale/return rows (logistics, storage, penalties, etc.)."""

    def test_logistics(self):
        """Delivery rows accumulate into logistics."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Логистика",
            supplier_oper_name="Логистика",
            delivery_rub=D("150"),
        )
        _accumulate_row(target, row, "Логистика", "Логистика")

        assert target["logistics"] == D("150")
        assert target["realization"] == ZERO  # not a sale

    def test_storage(self):
        """Storage fee rows."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Штрафы/прочее",
            supplier_oper_name="Хранение",
            storage_fee=D("200"),
        )
        _accumulate_row(target, row, "Штрафы/прочее", "Хранение")

        assert target["storage"] == D("200")

    def test_penalties(self):
        """Penalty rows."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Штрафы/прочее",
            supplier_oper_name="Штрафы",
            penalty=D("500"),
        )
        _accumulate_row(target, row, "Штрафы/прочее", "Штрафы")

        assert target["penalties"] == D("500")

    def test_acceptance(self):
        """Paid acceptance rows."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Платная приёмка",
            supplier_oper_name="Приёмка",
            acceptance=D("100"),
        )
        _accumulate_row(target, row, "Платная приёмка", "Приёмка")

        assert target["acceptance"] == D("100")

    def test_deduction_generic(self):
        """Generic deduction (no ad bonus_type_name)."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Штрафы/прочее",
            supplier_oper_name="Удержание",
            deduction=D("300"),
            bonus_type_name="Списание за невывоз",
        )
        _accumulate_row(target, row, "Штрафы/прочее", "Удержание")

        assert target["deductions"] == D("300")
        assert target["ad_deduction"] == ZERO  # not ad-related

    def test_deduction_ad_promotion(self):
        """Ad deduction (bonus_type_name contains 'Продвижение')."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Штрафы/прочее",
            supplier_oper_name="Удержание",
            deduction=D("1000"),
            bonus_type_name="Продвижение товара",
        )
        _accumulate_row(target, row, "Штрафы/прочее", "Удержание")

        assert target["deductions"] == D("1000")
        assert target["ad_deduction"] == D("1000")  # ad-related

    def test_deduction_ad_media(self):
        """Ad deduction (bonus_type_name contains 'Медиа')."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Штрафы/прочее",
            supplier_oper_name="Удержание",
            deduction=D("500"),
            bonus_type_name="Медиа-баннер",
        )
        _accumulate_row(target, row, "Штрафы/прочее", "Удержание")

        assert target["deductions"] == D("500")
        assert target["ad_deduction"] == D("500")

    def test_other_does_not_affect_realization(self):
        """Non-sale/return rows don't change realization or sales_amount."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Логистика",
            delivery_rub=D("150"),
            retail_price_withdisc_rub=D("999"),  # should be ignored
            retail_amount=D("888"),
        )
        _accumulate_row(target, row, "Логистика", "Логистика")

        assert target["realization"] == ZERO
        assert target["sales_amount"] == ZERO


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _accumulate_row — Voluntary compensation
# ═══════════════════════════════════════════════════════════════════════════════

class TestAccumulateCompensation:
    """Voluntary compensation treated as sale with special oper_name."""

    def test_voluntary_compensation(self):
        """'Добровольная компенсация при возврате' → compensation_ppvz + _comp_ppvz."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Продажа",
            supplier_oper_name="Добровольная компенсация при возврате",
            ppvz_for_pay=D("250"),
            retail_price_withdisc_rub=D("0"),
            retail_amount=D("0"),
        )
        _accumulate_row(target, row, "Продажа", "Добровольная компенсация при возврате")

        assert target["compensation_ppvz"] == D("250")
        assert target["_comp_ppvz"] == D("250")


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: P&L formula chain (end-to-end with accumulated data)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPnLFormulas:
    """Verify end-to-end P&L calculations using accumulated data."""

    def _build_scenario(self):
        """Build a realistic P&L scenario with sales, returns, and expenses."""
        target = _empty_totals()

        # 10 sales @ 1000 retail / 800 actual / 600 ppvz_for_pay each
        for _ in range(10):
            sale = _make_finance_row(
                retail_price_withdisc_rub=D("1000"),
                retail_amount=D("800"),
                ppvz_for_pay=D("600"),
                additional_payment=D("10"),
            )
            _accumulate_row(target, sale, "Продажа", "Продажа")

        # 2 returns
        for _ in range(2):
            ret = _make_finance_row(
                retail_price_withdisc_rub=D("1000"),
                retail_amount=D("800"),
                ppvz_for_pay=D("600"),
                additional_payment=D("10"),
            )
            _accumulate_row(target, ret, "Возврат", "Возврат")

        # Logistics
        log_row = _make_finance_row(delivery_rub=D("500"))
        _accumulate_row(target, log_row, "Логистика", "Логистика")

        # Storage
        stor_row = _make_finance_row(storage_fee=D("200"))
        _accumulate_row(target, stor_row, "Штрафы/прочее", "Хранение")

        # Penalty
        pen_row = _make_finance_row(penalty=D("100"))
        _accumulate_row(target, pen_row, "Штрафы/прочее", "Штрафы")

        # Ad deduction
        ad_row = _make_finance_row(deduction=D("300"), bonus_type_name="Продвижение товара")
        _accumulate_row(target, ad_row, "Штрафы/прочее", "Удержание")

        # Non-ad deduction
        ded_row = _make_finance_row(deduction=D("50"), bonus_type_name="Списание за невывоз")
        _accumulate_row(target, ded_row, "Штрафы/прочее", "Удержание")

        # Acceptance
        acc_row = _make_finance_row(acceptance=D("80"))
        _accumulate_row(target, acc_row, "Платная приёмка", "Приёмка")

        return target

    def test_realization(self):
        """Net realization = 10*1000 - 2*1000 = 8000."""
        t = self._build_scenario()
        assert t["realization"] == D("8000")

    def test_sales_amount(self):
        """Net sales = 10*800 - 2*800 = 6400."""
        t = self._build_scenario()
        assert t["sales_amount"] == D("6400")

    def test_spp_discount(self):
        """SPP discount = realization - sales_amount = 8000 - 6400 = 1600."""
        t = self._build_scenario()
        spp = t["realization"] - t["sales_amount"]
        assert spp == D("1600")

    def test_commission(self):
        """Commission = sales_amount - (ppvz_net - compensation).

        ppvz_net = 10*600 - 2*600 = 4800
        compensation = 0
        commission = 6400 - 4800 = 1600
        """
        t = self._build_scenario()
        assert t["commission"] == D("1600")

    def test_logistics(self):
        t = self._build_scenario()
        assert t["logistics"] == D("500")

    def test_storage(self):
        t = self._build_scenario()
        assert t["storage"] == D("200")

    def test_penalties(self):
        t = self._build_scenario()
        assert t["penalties"] == D("100")

    def test_ad_deduction_separated(self):
        """Ad deductions tracked separately from generic."""
        t = self._build_scenario()
        assert t["ad_deduction"] == D("300")
        assert t["deductions"] == D("350")  # 300 ad + 50 non-ad

    def test_other_deductions_formula(self):
        """Прочие удержания = deductions - ad_deduction = 350 - 300 = 50."""
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        assert other_ded == D("50")

    def test_acceptance(self):
        t = self._build_scenario()
        assert t["acceptance"] == D("80")

    def test_additional_payment(self):
        """additional_payment = 10*10 - 2*10 = 80."""
        t = self._build_scenario()
        assert t["additional_payment"] == D("80")

    def test_direct_costs(self):
        """Direct = cost + logistics + commission + penalties + storage + ads + other_ded + acceptance.

        cost = 0 (not in accumulator, calculated later from cost_map)
        direct = 0 + 500 + 1600 + 100 + 200 + 300 + 50 + 80 = 2830
        """
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        cost = 0  # cost is calculated separately
        direct = cost + float(t["logistics"]) + float(t["commission"]) + \
                 float(t["penalties"]) + float(t["storage"]) + \
                 float(t["ad_deduction"]) + float(other_ded) + float(t["acceptance"])
        assert direct == 2830.0

    def test_gross_margin(self):
        """Gross margin = sales_amount - direct_costs + compensation.

        sales = 6400, direct(no cost) = 2830, compensation = 80 (additional_payment)
        gross_margin = 6400 - 2830 + 80 = 3650
        """
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = float(t["logistics"]) + float(t["commission"]) + \
                 float(t["penalties"]) + float(t["storage"]) + \
                 float(t["ad_deduction"]) + float(other_ded) + float(t["acceptance"])
        comp = float(t["additional_payment"]) + float(t["compensation_ppvz"])
        margin = float(t["sales_amount"]) - direct + comp
        assert margin == 3650.0

    def test_tax_calculation_usn(self):
        """USN 6% tax on sales_amount = 6400 * 0.06 = 384."""
        t = self._build_scenario()
        usn_rate = 6 / 100
        tax = max(float(t["sales_amount"]) * usn_rate, 0)
        assert tax == 384.0

    def test_tax_calculation_with_nds(self):
        """Tax with NDS 5%: NDS = sales / 1.05 * 0.05, then USN on (sales - NDS)."""
        t = self._build_scenario()
        sales = float(t["sales_amount"])
        nds_rate = 5 / 100
        usn_rate = 6 / 100

        nds = sales * nds_rate / (1 + nds_rate)
        base = sales - nds
        usn = max(base * usn_rate, 0)
        total_tax = nds + usn

        expected_nds = 6400 * 0.05 / 1.05
        expected_usn = (6400 - expected_nds) * 0.06
        assert abs(total_tax - (expected_nds + expected_usn)) < 0.01

    def test_net_profit(self):
        """Net profit = EBITDA - taxes.

        EBITDA = gross_margin - 0 (ops) = 3650
        Tax (USN 6%, no NDS) = 384
        Net = 3650 - 384 = 3266
        """
        t = self._build_scenario()
        other_ded = t["deductions"] - t["ad_deduction"]
        direct = float(t["logistics"]) + float(t["commission"]) + \
                 float(t["penalties"]) + float(t["storage"]) + \
                 float(t["ad_deduction"]) + float(other_ded) + float(t["acceptance"])
        comp = float(t["additional_payment"]) + float(t["compensation_ppvz"])
        margin = float(t["sales_amount"]) - direct + comp

        tax = float(t["sales_amount"]) * 0.06
        net_profit = margin - tax
        assert net_profit == 3266.0


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_zero_row(self):
        """Row with all zeros → no change."""
        target = _empty_totals()
        row = _make_finance_row(
            retail_price_withdisc_rub=D("0"),
            retail_amount=D("0"),
            ppvz_for_pay=D("0"),
            additional_payment=D("0"),
        )
        _accumulate_row(target, row, "Продажа", "Продажа")
        assert target["realization"] == ZERO
        assert target["sales_amount"] == ZERO

    def test_unknown_doc_type_ignored(self):
        """Unknown doc_type (not Продажа/Возврат) → no realization change."""
        target = _empty_totals()
        row = _make_finance_row(
            doc_type_name="Неизвестный",
            retail_price_withdisc_rub=D("999"),
            delivery_rub=D("100"),
        )
        _accumulate_row(target, row, "Неизвестный", "Логистика")

        assert target["realization"] == ZERO
        assert target["logistics"] == D("100")  # still accumulates logistics

    def test_none_values_handled(self):
        """None values in finance row → treated as 0."""
        target = _empty_totals()
        row = _make_finance_row(
            retail_price_withdisc_rub=None,
            retail_amount=None,
            ppvz_for_pay=None,
            additional_payment=None,
        )
        _accumulate_row(target, row, "Продажа", "Продажа")
        assert target["realization"] == ZERO
        assert target["sales_amount"] == ZERO

    def test_large_numbers(self):
        """Large realistic values should work without overflow."""
        target = _empty_totals()
        row = _make_finance_row(
            retail_price_withdisc_rub=D("99999999.99"),
            retail_amount=D("88888888.88"),
            ppvz_for_pay=D("77777777.77"),
        )
        _accumulate_row(target, row, "Продажа", "Продажа")
        assert target["realization"] == D("99999999.99")
