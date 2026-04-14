"""
Tests for cost_dna_helpers — pure-function tests for the Cost-DNA category
metric computation and tax helper.

No DB required — all inputs are built manually.
"""

from decimal import Decimal
from types import SimpleNamespace

from backend.services.cost_dna_helpers import (
    _aggregate_cost_by_subject_sales,
    _compute_tax,
    compute_category_metrics,
)

D = Decimal


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_revenue_row(
    subject="Футболка",
    sale_retail=0,
    ret_retail=0,
    sale_amount=0,
    ret_amount=0,
    ppvz_sale=0,
    ppvz_ret=0,
    comm_sale=0,
    comm_ret=0,
    logistics=0,
    storage=0,
    penalty=0,
    acceptance=0,
    other_deduction=0,
    sale_qty=0,
    ret_qty=0,
):
    """Build a mock revenue_row (SimpleNamespace mimics sa.Row)."""
    return SimpleNamespace(
        subject=subject,
        sale_retail=sale_retail,
        ret_retail=ret_retail,
        sale_amount=sale_amount,
        ret_amount=ret_amount,
        ppvz_sale=ppvz_sale,
        ppvz_ret=ppvz_ret,
        comm_sale=comm_sale,
        comm_ret=comm_ret,
        logistics=logistics,
        storage=storage,
        penalty=penalty,
        acceptance=acceptance,
        other_deduction=other_deduction,
        sale_qty=sale_qty,
        ret_qty=ret_qty,
    )


def _make_cost_row(
    factory_total=0.0,
    duty_total=0.0,
    delivery_total=0.0,
    vat_total=0.0,
    cost_total=0.0,
    qty_total=0,
):
    """Build a mock cost_row dict returned by load_cost_components_by_subject."""
    return {
        "factory_total": factory_total,
        "duty_total": duty_total,
        "delivery_total": delivery_total,
        "vat_total": vat_total,
        "cost_total": cost_total,
        "qty_total": qty_total,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: compute_category_metrics
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeCategoryMetrics:
    """Happy path + edge cases for per-category metrics."""

    def test_compute_category_metrics_happy_path(self):
        """Full-data path: revenue, cost, tax, MP fees → margin == 100 - sum(components).

        Scenario:
            retail=10000, sales_amount=9000, ppvz_net=7200
            mp_commission   = revenue - ppvz_net    = 10000 - 7200 = 2800 -> 28% (incl. SPP)
            opiu_commission = sales_amount - ppvz_net = 9000 - 7200 = 1800 (tax base)
            logistics=500, storage=200, adv=400, sale_qty=10
            cost per unit: factory=300, duty=60, delivery=40, vat=20 → total 420 * 10 = 4200
            tax (regime=usn_income, default): 9000 * 0.06 = 540
        """
        rev_row = _make_revenue_row(
            subject="Футболка",
            sale_retail=10000,
            ret_retail=0,
            sale_amount=9000,
            ret_amount=0,
            ppvz_sale=7200,
            ppvz_ret=0,
            logistics=500,
            storage=200,
            other_deduction=0,
            sale_qty=10,
            ret_qty=0,
        )
        cost_row = _make_cost_row(
            factory_total=3000.0,  # 300/unit * 10 units
            duty_total=600.0,  # 60/unit * 10
            delivery_total=400.0,  # 40/unit * 10
            vat_total=200.0,  # 20/unit * 10
            cost_total=4200.0,
            qty_total=10,
        )
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income"}

        m = compute_category_metrics(rev_row, cost_row, adv_sum=400, tax_info=tax_info)

        assert m["category"] == "Футболка"
        assert m["revenue"] == 10000.0
        assert m["has_cost_data"] is True

        # MP components (as % of revenue)
        # mp_commission = revenue - ppvz_net = 10000 - 7200 = 2800 → 28% (включает СПП)
        assert m["mp_commission_pct"] == 28.0
        assert m["mp_logistics_pct"] == 5.0  # 500 / 10000 * 100
        assert m["mp_storage_pct"] == 2.0  # 200 / 10000 * 100
        assert m["mp_advertising_pct"] == 4.0  # 400 / 10000 * 100
        assert m["mp_other_pct"] == 0.0
        assert m["mp_total_pct"] == 39.0

        # Cost components: weighted avg per unit * net_qty (10) / revenue
        assert m["cost_factory_pct"] == 30.0  # 3000/10000 * 100
        assert m["cost_duty_pct"] == 6.0
        assert m["cost_delivery_pct"] == 4.0
        assert m["cost_vat_pct"] == 2.0
        assert m["cost_total_pct"] == 42.0

        # Tax: regime=usn_income → expenses не вычитаются → 9000 * 0.06 = 540 → 5.4%
        assert m["tax_pct"] == 5.4

        # Margin = 100 - 42 - 39 - 5.4 = 13.6
        assert m["margin_pct"] == 13.6

    def test_compute_category_metrics_no_cost_data(self):
        """cost_row=None → has_cost_data=False, all cost_*_pct=None, margin=None,
        but mp/tax/revenue are still computed."""
        rev_row = _make_revenue_row(
            sale_retail=5000,
            sale_amount=4500,
            ppvz_sale=3600,
            logistics=250,
            sale_qty=5,
        )
        tax_info = {"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income"}

        m = compute_category_metrics(rev_row, cost_row=None, adv_sum=100, tax_info=tax_info)

        assert m["has_cost_data"] is False
        assert m["cost_factory_pct"] is None
        assert m["cost_duty_pct"] is None
        assert m["cost_delivery_pct"] is None
        assert m["cost_vat_pct"] is None
        assert m["cost_total_pct"] is None
        assert m["margin_pct"] is None

        # MP/tax still computed
        assert m["revenue"] == 5000.0
        # mp_commission = revenue - ppvz_net = 5000 - 3600 = 1400 → 28%
        assert m["mp_commission_pct"] == 28.0
        assert m["mp_logistics_pct"] == 5.0
        assert m["mp_advertising_pct"] == 2.0
        # tax (regime=usn_income) = 4500 * 0.06 = 270 → 5.4% of revenue
        assert m["tax_pct"] == 5.4

    def test_compute_category_metrics_zero_revenue(self):
        """sale_retail == ret_retail → revenue = 0 → empty metrics."""
        rev_row = _make_revenue_row(
            sale_retail=1000,
            ret_retail=1000,
            sale_amount=900,
            ret_amount=900,
            sale_qty=1,
            ret_qty=1,
        )
        cost_row = _make_cost_row(cost_total=500, qty_total=5, factory_total=500)
        tax_info = {"usn_rate": 6, "nds_rate": 0}

        m = compute_category_metrics(rev_row, cost_row, adv_sum=100, tax_info=tax_info)

        assert m["revenue"] == 0
        assert m["has_cost_data"] is False
        assert m["cost_total_pct"] is None
        assert m["margin_pct"] is None
        assert m["mp_total_pct"] == 0
        assert m["tax_pct"] == 0

    def test_compute_category_metrics_returns_only(self):
        """sale=0, only returns → revenue negative → zero metrics (returns empty)."""
        rev_row = _make_revenue_row(
            sale_retail=0,
            ret_retail=500,  # revenue = -500 → not > 0 → empty
            sale_amount=0,
            ret_amount=450,
            ppvz_sale=0,
            ppvz_ret=360,
            sale_qty=0,
            ret_qty=1,
        )
        m = compute_category_metrics(rev_row, cost_row=None, adv_sum=0, tax_info={"usn_rate": 6, "nds_rate": 0})

        assert m["revenue"] == 0
        assert m["has_cost_data"] is False
        assert m["margin_pct"] is None
        assert m["mp_total_pct"] == 0
        assert m["tax_pct"] == 0

    def test_compute_category_metrics_with_ads(self):
        """adv_sum > 0 → mp_advertising_pct correct and included in mp_total_pct."""
        rev_row = _make_revenue_row(
            sale_retail=10000,
            sale_amount=9000,
            ppvz_sale=8000,  # mp_commission = revenue - ppvz_net = 10000 - 8000 = 2000 → 20%
            sale_qty=10,
        )
        tax_info = {"usn_rate": 0, "nds_rate": 0}
        m = compute_category_metrics(rev_row, cost_row=None, adv_sum=750, tax_info=tax_info)

        # 750 / 10000 * 100 = 7.5
        assert m["mp_advertising_pct"] == 7.5
        # mp_total = commission(20) + adv(7.5)
        assert m["mp_total_pct"] == 27.5

    def test_compute_category_metrics_net_qty_zero(self):
        """sale_qty == ret_qty → has_cost_data=False even if cost_row provided.

        Because without units sold we cannot project cost onto revenue.
        """
        rev_row = _make_revenue_row(
            sale_retail=5000,
            sale_amount=4500,
            ppvz_sale=3600,
            sale_qty=3,
            ret_qty=3,  # net = 0
        )
        cost_row = _make_cost_row(factory_total=300, cost_total=400, qty_total=5)
        tax_info = {"usn_rate": 6, "nds_rate": 0}

        m = compute_category_metrics(rev_row, cost_row, adv_sum=0, tax_info=tax_info)

        # Revenue > 0 still → MP and tax computed
        assert m["revenue"] == 5000.0
        # But no net units → no cost projection
        assert m["has_cost_data"] is False
        assert m["cost_total_pct"] is None
        assert m["margin_pct"] is None

    def test_compute_category_metrics_returns_private_totals_fields(self):
        """compute_category_metrics must emit the private _* fields used by totals."""
        rev_row = _make_revenue_row(
            sale_retail=1000,
            sale_amount=900,
            ppvz_sale=720,
            logistics=50,
            storage=20,
            penalty=10,
            acceptance=5,
            sale_qty=2,
        )
        cost_row = _make_cost_row(
            factory_total=200, duty_total=40, delivery_total=30, vat_total=10, cost_total=280, qty_total=2
        )
        m = compute_category_metrics(rev_row, cost_row, adv_sum=50, tax_info={"usn_rate": 6, "nds_rate": 0})

        assert "_sales_amount" in m
        assert "_logistics" in m
        assert "_storage" in m
        assert "_mp_commission" in m
        assert "_opiu_commission" in m
        assert "_penalty" in m
        assert "_acceptance" in m
        assert "_other_deduction" in m
        assert "_adv_sum" in m
        assert "_net_qty" in m
        assert "_proj_factory" in m
        assert "_proj_duty" in m
        assert "_proj_delivery" in m
        assert "_proj_vat" in m
        assert "_proj_cost_total" in m
        # Exact value spot check
        assert m["_adv_sum"] == 50
        assert m["_net_qty"] == 2
        # mp_commission = revenue - ppvz_net = 1000 - 720 = 280
        assert m["_mp_commission"] == 280
        # opiu_commission = sales - ppvz_net = 900 - 720 = 180
        assert m["_opiu_commission"] == 180
        assert m["_penalty"] == 10
        assert m["_acceptance"] == 5


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _compute_tax
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeTax:
    """Tax calc — mirrors opiu_service._calc_tax + _tax_expenses_for."""

    def test_compute_tax_usn_income(self):
        """regime=usn_income, usn_rate=6, nds_rate=0 → tax = sales * 0.06 (expenses ignored)."""
        tax = _compute_tax(100000.0, tax_info={"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income"})
        assert tax == 6000.0

    def test_compute_tax_with_nds(self):
        """regime=usn_income, usn_rate=6, nds_rate=20 → NDS extracted first, then USN on base.

        nds  = 100000 * 0.20 / 1.20 = 16666.666...
        base = 100000 - 16666.666... = 83333.333...
        usn  = base * 0.06 ≈ 5000
        tot  = usn + nds ≈ 21666.666...
        """
        tax = _compute_tax(100000.0, tax_info={"usn_rate": 6, "nds_rate": 20, "tax_regime": "usn_income"})
        expected_nds = 100000 * 0.20 / 1.20
        expected_usn = (100000 - expected_nds) * 0.06
        assert abs(tax - (expected_nds + expected_usn)) < 1e-6

    def test_compute_tax_zero_rate(self):
        """usn_rate=0 → no tax whatsoever."""
        tax = _compute_tax(50000.0, tax_info={"usn_rate": 0, "nds_rate": 0})
        assert tax == 0.0

    def test_compute_tax_zero_income(self):
        """sales_amount<=0 → tax is zero regardless of rate."""
        assert _compute_tax(0.0, tax_info={"usn_rate": 6, "nds_rate": 0}) == 0.0
        assert _compute_tax(-100.0, tax_info={"usn_rate": 6, "nds_rate": 0}) == 0.0

    def test_compute_tax_missing_keys(self):
        """Missing keys in tax_info should default to 0 (no crash)."""
        assert _compute_tax(1000.0, tax_info={}) == 0.0
        assert _compute_tax(1000.0, tax_info=None) == 0.0

    def test_compute_tax_usn_income_ignores_expenses(self):
        """regime=usn_income → expenses are ignored, only sales matter."""
        tax_with_exp = _compute_tax(
            100000.0,
            log=5000,
            stor=2000,
            opiu_comm=1000,
            pen=500,
            other_ded=300,
            adv=4000,
            cost_val=20000,
            tax_info={"usn_rate": 6, "nds_rate": 0, "tax_regime": "usn_income", "cost_as_expense": True},
        )
        # regime=usn_income → expenses irrelevant → 100000 * 0.06 = 6000
        assert tax_with_exp == 6000.0

    def test_compute_tax_expense_vat_regime_no_cost(self):
        """regime=usn_income_expense_vat, cost_as_expense=False → expenses subtract from base, cost не вычитается.

        sales=100000, nds=7%, usn=15%
        nds  = 100000 * 0.07 / 1.07 ≈ 6542.06
        base = 100000 - 6542.06 = 93457.94
        expenses = log(5000) + stor(2000) + opiu_comm(1000) + pen(500) + other(300) + adv(4000) = 12800
        base -= 12800 = 80657.94
        usn  = 80657.94 * 0.15 ≈ 12098.69
        tax  = 6542.06 + 12098.69 ≈ 18640.75
        """
        tax = _compute_tax(
            100000.0,
            log=5000,
            stor=2000,
            opiu_comm=1000,
            pen=500,
            other_ded=300,
            adv=4000,
            cost_val=50000,  # игнорируется т.к. cost_as_expense=False
            tax_info={
                "usn_rate": 15,
                "nds_rate": 7,
                "tax_regime": "usn_income_expense_vat",
                "cost_as_expense": False,
            },
        )
        expected_nds = 100000 * 0.07 / 1.07
        expected_base = (100000 - expected_nds) - 12800
        expected_usn = expected_base * 0.15
        assert abs(tax - (expected_nds + expected_usn)) < 1e-6

    def test_compute_tax_expense_vat_regime_with_cost(self):
        """regime=usn_income_expense_vat, cost_as_expense=True → cost ALSO subtracts from base."""
        tax = _compute_tax(
            100000.0,
            log=5000,
            stor=2000,
            opiu_comm=1000,
            pen=500,
            other_ded=300,
            adv=4000,
            cost_val=50000,
            tax_info={
                "usn_rate": 15,
                "nds_rate": 7,
                "tax_regime": "usn_income_expense_vat",
                "cost_as_expense": True,
            },
        )
        expected_nds = 100000 * 0.07 / 1.07
        expected_base = (100000 - expected_nds) - 12800 - 50000
        expected_usn = max(expected_base * 0.15, 0)
        assert abs(tax - (expected_nds + expected_usn)) < 1e-6

    def test_compute_tax_expense_vat_negative_base_clamped_to_zero(self):
        """If expenses > income then USN is clamped to 0, only NDS remains."""
        tax = _compute_tax(
            10000.0,
            log=20000,  # расходы существенно больше выручки
            stor=5000,
            opiu_comm=3000,
            pen=0,
            other_ded=0,
            adv=2000,
            cost_val=5000,
            tax_info={
                "usn_rate": 15,
                "nds_rate": 7,
                "tax_regime": "usn_income_expense_vat",
                "cost_as_expense": True,
            },
        )
        expected_nds = 10000 * 0.07 / 1.07
        # base уйдёт в минус, max(...,0) = 0 → tax = только НДС
        assert abs(tax - expected_nds) < 1e-6


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _aggregate_cost_by_subject_sales
# Pure helper used by load_cost_components_by_subject.
# Weights per-article cost by actual SALE quantity (not purchase quantity) —
# fixes the bug where Cost-DNA inflated cost_total when purchase-mix differed
# from sale-mix (see DOMAIN_REPORTS.md "Cost-DNA weighted by sales" section).
# ═══════════════════════════════════════════════════════════════════════════════


class TestAggregateCostBySubjectSales:
    """Pure-function tests for sales-weighted cost aggregation per subject."""

    def test_simple_two_articles_one_subject(self):
        """Two SA in one subject, weighted by sales qty (not purchase qty).

        SA-A: cost_per_unit=100, sold=50 → contributes 5000 ₽ × qty=50
        SA-B: cost_per_unit=200, sold=10 → contributes 2000 ₽ × qty=10

        Expected per-subject:
            factory_total = 5000 + 2000 = 7000 (since only 'factory' populated)
            cost_total    = 7000
            qty_total     = 60
            implied avg   = 7000 / 60 = 116.67 ₽/шт (weighted by sales)
        """
        cost_per_sa = {
            "SA-A": {
                "avg_factory": 100.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 100.0,
            },
            "SA-B": {
                "avg_factory": 200.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 200.0,
            },
        }
        sales_per_sa = {"SA-A": ("Ковры", 50), "SA-B": ("Ковры", 10)}

        result = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa)

        assert "Ковры" in result
        assert result["Ковры"]["factory_total"] == 7000.0
        assert result["Ковры"]["cost_total"] == 7000.0
        assert result["Ковры"]["qty_total"] == 60
        assert result["Ковры"]["duty_total"] == 0.0
        assert result["Ковры"]["delivery_total"] == 0.0
        assert result["Ковры"]["vat_total"] == 0.0

    def test_demonstrates_fix_vs_purchase_weighted(self):
        """Regression test for the bug: purchase-weighted vs sales-weighted.

        Purchase mix: SA-A (cheap) 100 шт закупки, SA-B (dear) 900 шт закупки
        Sales mix:    SA-A 90 шт продаж, SA-B 10 шт продаж

        cost_per_unit SA-A = 100 ₽, SA-B = 1000 ₽

        Old algo (weighted by purchases): 100*100+1000*900 / 1000 = 910 ₽/шт
        Old applied to 100 sold units:    91000 ₽

        New algo (weighted by sales):     100*90+1000*10 / 100 = 190 ₽/шт
        New applied to 100 sold units:    19000 ₽

        Difference: 72000 ₽ — bug where purchase-heavy items dominate.
        """
        cost_per_sa = {
            "SA-A": {
                "avg_factory": 100.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 100.0,
            },
            "SA-B": {
                "avg_factory": 1000.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 1000.0,
            },
        }
        sales_per_sa = {
            "SA-A": ("Тест", 90),
            "SA-B": ("Тест", 10),
        }
        result = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa)

        # Sales-weighted: 90 × 100 + 10 × 1000 = 9000 + 10000 = 19000
        assert result["Тест"]["cost_total"] == 19000.0
        assert result["Тест"]["qty_total"] == 100
        # Implied avg-per-unit: 190 ₽/шт (not 910 as the old algo would yield)
        implied_avg = result["Тест"]["cost_total"] / result["Тест"]["qty_total"]
        assert abs(implied_avg - 190.0) < 1e-9

    def test_multiple_subjects_isolated(self):
        """Different SA in different subjects are aggregated independently."""
        cost_per_sa = {
            "SA-A": {
                "avg_factory": 100.0,
                "avg_duty": 10.0,
                "avg_delivery": 5.0,
                "avg_vat": 2.0,
                "avg_total": 117.0,
            },
            "SA-B": {
                "avg_factory": 200.0,
                "avg_duty": 20.0,
                "avg_delivery": 10.0,
                "avg_vat": 4.0,
                "avg_total": 234.0,
            },
        }
        sales_per_sa = {
            "SA-A": ("Ковры", 10),
            "SA-B": ("Пледы", 20),
        }
        result = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa)

        assert result["Ковры"]["factory_total"] == 1000.0
        assert result["Ковры"]["cost_total"] == 1170.0
        assert result["Ковры"]["duty_total"] == 100.0
        assert result["Ковры"]["delivery_total"] == 50.0
        assert result["Ковры"]["vat_total"] == 20.0
        assert result["Ковры"]["qty_total"] == 10

        assert result["Пледы"]["factory_total"] == 4000.0
        assert result["Пледы"]["cost_total"] == 4680.0
        assert result["Пледы"]["qty_total"] == 20

    def test_sa_without_cost_data_skipped(self):
        """If SA has sales but no cost_per_sa entry, it's skipped (not NULL-cast to 0)."""
        cost_per_sa = {
            "SA-A": {
                "avg_factory": 100.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 100.0,
            },
        }
        sales_per_sa = {
            "SA-A": ("Ковры", 50),
            "SA-UNKNOWN": ("Ковры", 30),  # no cost → skipped
        }
        result = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa)

        # Only SA-A contributes to qty_total; SA-UNKNOWN is skipped
        assert result["Ковры"]["cost_total"] == 5000.0
        assert result["Ковры"]["qty_total"] == 50

    def test_zero_sales_excluded(self):
        """SA with zero or negative net sales qty doesn't contribute."""
        cost_per_sa = {
            "SA-A": {
                "avg_factory": 100.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 100.0,
            },
            "SA-B": {
                "avg_factory": 500.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 500.0,
            },
        }
        sales_per_sa = {
            "SA-A": ("Ковры", 10),
            "SA-B": ("Ковры", 0),  # zero sales → excluded
            "SA-C": ("Ковры", -5),  # negative (net returns) → excluded
        }
        result = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa)

        assert result["Ковры"]["cost_total"] == 1000.0
        assert result["Ковры"]["qty_total"] == 10

    def test_empty_inputs(self):
        """Empty dicts → empty result."""
        assert _aggregate_cost_by_subject_sales({}, {}) == {}

    def test_subject_with_no_cost_data_absent(self):
        """Subject where NO article has cost → not included in output.

        compute_category_metrics handles this as "no cost data" (margin=None).
        """
        cost_per_sa: dict[str, dict[str, float]] = {}
        sales_per_sa = {
            "SA-A": ("Ковры", 100),
            "SA-B": ("Пледы", 50),
        }
        result = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa)
        assert result == {}

    def test_case_sensitive_sa_matching_from_caller(self):
        """Pure helper trusts the caller to normalize SA keys.

        Real catalogs have mixed case (cost import = UPPER, WB API = lower),
        so `load_cost_components_by_subject` normalizes via `LOWER()` in SQL.
        This test documents that the pure helper itself does NOT normalize —
        the caller must provide already-normalized keys, otherwise cost gets
        dropped silently.
        """
        cost_per_sa = {
            "sa-a": {  # lowercase — as produced by LOWER() in SQL
                "avg_factory": 100.0,
                "avg_duty": 0.0,
                "avg_delivery": 0.0,
                "avg_vat": 0.0,
                "avg_total": 100.0,
            },
        }
        # Caller passes mismatched case → treated as no cost for SA-A
        sales_per_sa_mismatched = {"SA-A": ("Ковры", 50)}
        result_mismatched = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa_mismatched)
        assert result_mismatched == {}

        # Caller passes normalized case → cost picked up correctly
        sales_per_sa_normalized = {"sa-a": ("Ковры", 50)}
        result_normalized = _aggregate_cost_by_subject_sales(cost_per_sa, sales_per_sa_normalized)
        assert result_normalized["Ковры"]["cost_total"] == 5000.0
