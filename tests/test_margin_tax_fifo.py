"""Тесты фиксов маржи 2026-07-21: фолбэк нулей движка + налог помесячно.

Прод-кейсы: движок FIFO давал eff_cost=0 при известной средней закупочной
(elka_240х220 — партии не сматчились по ключу; case_2seat — нетто-ноль окна),
и налог многомесячного диапазона считался по ставке первого месяца.
"""

from backend.services.bdr_enrichment import (
    apply_tax,
    apply_tax_by_month,
    blended_tax_info,
    tax_settings_uniform,
)
from backend.services.bdr_loaders import month_keys
from backend.services.wb_bdr_helpers import cost_with_fallback

USN6 = {"tax_regime": "usn_income", "usn_rate": 6, "nds_rate": 0, "cost_as_expense": False}
USN6_NDS5 = {"tax_regime": "usn_income", "usn_rate": 6, "nds_rate": 5, "cost_as_expense": False}
DR15 = {"tax_regime": "usn_income_expense_vat", "usn_rate": 15, "nds_rate": 0, "cost_as_expense": False}


# ─── cost_with_fallback ──────────────────────────────────────────────────────


class TestCostWithFallback:
    def test_engine_price_wins(self):
        assert cost_with_fallback(100.0, "sku", 1, {"sku": 50.0}, {1: 30.0}) == 100.0

    def test_engine_zero_falls_back_to_avg(self):
        # Прод-кейс elka: движок 0 (партии не сматчились), средняя известна.
        assert cost_with_fallback(0.0, "elka", 463459139, {"elka": 1396.42}, {}) == 1396.42

    def test_engine_zero_no_avg_falls_back_to_override(self):
        assert cost_with_fallback(0.0, "sku", 7, {}, {7: 210.5}) == 210.5

    def test_all_sources_empty_stays_zero(self):
        # Дубль-карточка: себестоимости нет нигде — честный 0.
        assert cost_with_fallback(0.0, "дубль", 907970646, {}, {}) == 0

    def test_negative_engine_price_treated_as_missing(self):
        assert cost_with_fallback(-5.0, "sku", 1, {"sku": 40.0}, {}) == 40.0

    def test_empty_sku_uses_override(self):
        assert cost_with_fallback(0.0, "", 3, {"": 99.0}, {3: 12.0}) == 12.0


# ─── month_keys / tax_settings_uniform ───────────────────────────────────────


class TestMonthKeys:
    def test_single_month(self):
        from datetime import date

        assert month_keys(date(2026, 7, 13), date(2026, 7, 19)) == ["2026-07"]

    def test_year_boundary(self):
        from datetime import date

        assert month_keys(date(2025, 12, 15), date(2026, 2, 1)) == ["2025-12", "2026-01", "2026-02"]


class TestUniform:
    def test_uniform(self):
        assert tax_settings_uniform({"2026-06": dict(USN6), "2026-07": dict(USN6)})

    def test_mixed(self):
        assert not tax_settings_uniform({"2026-06": USN6, "2026-07": DR15})

    def test_empty(self):
        assert tax_settings_uniform({})


# ─── apply_tax_by_month ──────────────────────────────────────────────────────


def _summary(income: float) -> dict:
    return {"sales_amount": income, "to_pay": income, "ad_deduction": 0, "loan_deduction": 0}


class TestApplyTaxByMonth:
    def test_uniform_months_match_aggregate(self):
        # При одинаковых настройках (без флора по месяцам) сумма месяцев == агрегат.
        monthly = {
            "2026-06": {"income": 100_000.0},
            "2026-07": {"income": 200_000.0},
        }
        tbm = {"2026-06": dict(USN6_NDS5), "2026-07": dict(USN6_NDS5)}
        s_monthly = _summary(300_000.0)
        apply_tax_by_month(s_monthly, monthly, tbm, 0, 0)

        s_agg = _summary(300_000.0)
        apply_tax(s_agg, dict(USN6_NDS5), 0, 0)
        assert abs(s_monthly["tax_total"] - s_agg["tax_total"]) < 0.02
        assert abs(s_monthly["profit"] - s_agg["profit"]) < 0.02

    def test_mixed_rates_sum_of_months(self):
        # Июнь УСН 6%, июль Д−Р 15% с расходами: каждый месяц по своей формуле.
        monthly = {
            "2026-06": {"income": 100_000.0},
            "2026-07": {"income": 100_000.0, "logistics": 20_000.0, "adv": 10_000.0},
        }
        tbm = {"2026-06": dict(USN6), "2026-07": dict(DR15)}
        s = _summary(200_000.0)
        apply_tax_by_month(s, monthly, tbm, 0, 0)
        expected_june = 100_000 * 0.06
        expected_july = (100_000 - 20_000 - 10_000) * 0.15
        assert abs(s["tax_total"] - (expected_june + expected_july)) < 0.01
        assert s["expenses_total"] == 30_000.0

    def test_negative_base_floors_per_month(self):
        # Убыточный месяц Д−Р флорится нулём и не съедает налог прибыльного.
        monthly = {
            "2026-06": {"income": 50_000.0, "logistics": 90_000.0},
            "2026-07": {"income": 100_000.0},
        }
        tbm = {"2026-06": dict(DR15), "2026-07": dict(USN6)}
        s = _summary(150_000.0)
        apply_tax_by_month(s, monthly, tbm, 0, 0)
        assert abs(s["tax_total"] - 100_000 * 0.06) < 0.01


# ─── blended_tax_info ────────────────────────────────────────────────────────


class TestBlendedTaxInfo:
    def test_uniform_returns_same_rates(self):
        info = blended_tax_info(
            {"2026-06": 100.0, "2026-07": 300.0},
            {"2026-06": dict(USN6), "2026-07": dict(USN6)},
        )
        assert info["usn_rate"] == 6
        assert info["nds_rate"] == 0

    def test_income_weighted_usn(self):
        info = blended_tax_info(
            {"2026-06": 100.0, "2026-07": 300.0},
            {"2026-06": dict(USN6), "2026-07": {**USN6, "usn_rate": 10}},
        )
        # 6% × 0.25 + 10% × 0.75 = 9%
        assert abs(info["usn_rate"] - 9.0) < 1e-9

    def test_nds_blend_roundtrip(self):
        # Доли извлечения r/(1+r) blend'ятся и конвертируются обратно в ставку:
        # применение blended-ставки к суммарному доходу == сумме помесячных НДС.
        incomes = {"2026-06": 100_000.0, "2026-07": 200_000.0}
        tbm = {"2026-06": {**USN6, "nds_rate": 5}, "2026-07": {**USN6, "nds_rate": 7}}
        info = blended_tax_info(incomes, tbm)
        r = info["nds_rate"] / 100
        blended_nds = 300_000 * r / (1 + r)
        exact = 100_000 * 0.05 / 1.05 + 200_000 * 0.07 / 1.07
        assert abs(blended_nds - exact) < 0.01

    def test_dominant_month_regime(self):
        info = blended_tax_info(
            {"2026-06": 100.0, "2026-07": 300.0},
            {"2026-06": dict(USN6), "2026-07": dict(DR15)},
        )
        assert info["tax_regime"] == "usn_income_expense_vat"

    def test_zero_income_falls_back_to_first(self):
        info = blended_tax_info({"2026-06": 0.0}, {"2026-06": dict(DR15)})
        assert info["tax_regime"] == "usn_income_expense_vat"
