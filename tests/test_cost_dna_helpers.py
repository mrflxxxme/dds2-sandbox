"""
Tests for cost_dna_helpers — pure-function tests for the Cost-DNA category
metric computation and tax helper.

No DB required — all inputs are built manually.
"""

from decimal import Decimal
from types import SimpleNamespace

from backend.services.cost_dna_helpers import _compute_tax, compute_category_metrics

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
            retail=10000, sales_amount=9000, ppvz_net=7200 → commission=1800
            logistics=500, storage=200, adv=400, sale_qty=10
            cost per unit: factory=300, duty=60, delivery=40, vat=20 → total 420 * 10 = 4200
            tax: usn=6%, nds=0% → 9000 * 0.06 = 540
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
        tax_info = {"usn_rate": 6, "nds_rate": 0}

        m = compute_category_metrics(rev_row, cost_row, adv_sum=400, tax_info=tax_info)

        assert m["category"] == "Футболка"
        assert m["revenue"] == 10000.0
        assert m["has_cost_data"] is True

        # MP components (as % of revenue)
        # commission = sales_amount - ppvz_net = 9000 - 7200 = 1800 → 18%
        assert m["mp_commission_pct"] == 18.0
        assert m["mp_logistics_pct"] == 5.0  # 500 / 10000 * 100
        assert m["mp_storage_pct"] == 2.0  # 200 / 10000 * 100
        assert m["mp_advertising_pct"] == 4.0  # 400 / 10000 * 100
        assert m["mp_other_pct"] == 0.0
        assert m["mp_total_pct"] == 29.0

        # Cost components: weighted avg per unit * net_qty (10) / revenue
        assert m["cost_factory_pct"] == 30.0  # 3000/10000 * 100
        assert m["cost_duty_pct"] == 6.0
        assert m["cost_delivery_pct"] == 4.0
        assert m["cost_vat_pct"] == 2.0
        assert m["cost_total_pct"] == 42.0

        # Tax: 9000 * 0.06 = 540 → 5.4%
        assert m["tax_pct"] == 5.4

        # Margin = 100 - 42 - 29 - 5.4 = 23.6
        assert m["margin_pct"] == 23.6

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
        tax_info = {"usn_rate": 6, "nds_rate": 0}

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
        assert m["mp_commission_pct"] == round(900 / 5000 * 100, 2)  # 18.0
        assert m["mp_logistics_pct"] == 5.0
        assert m["mp_advertising_pct"] == 2.0
        # tax = 4500 * 0.06 = 270 → 5.4%
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
            ppvz_sale=8000,  # commission = 9000-8000 = 1000 → 10%
            sale_qty=10,
        )
        tax_info = {"usn_rate": 0, "nds_rate": 0}
        m = compute_category_metrics(rev_row, cost_row=None, adv_sum=750, tax_info=tax_info)

        # 750 / 10000 * 100 = 7.5
        assert m["mp_advertising_pct"] == 7.5
        # mp_total includes adv
        assert m["mp_total_pct"] == round(10.0 + 7.5, 2)  # commission + adv

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
            sale_qty=2,
        )
        cost_row = _make_cost_row(
            factory_total=200, duty_total=40, delivery_total=30, vat_total=10, cost_total=280, qty_total=2
        )
        m = compute_category_metrics(rev_row, cost_row, adv_sum=50, tax_info={"usn_rate": 6, "nds_rate": 0})

        assert "_sales_amount" in m
        assert "_logistics" in m
        assert "_storage" in m
        assert "_commission" in m
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


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _compute_tax
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeTax:
    """Simplified USN tax calculation used by Cost-DNA."""

    def test_compute_tax_usn_income(self):
        """usn_rate=6, nds_rate=0 → tax_total == sales * 0.06 (no NDS)."""
        tax = _compute_tax(100000.0, {"usn_rate": 6, "nds_rate": 0})
        assert tax == 6000.0

    def test_compute_tax_with_nds(self):
        """usn_rate=6, nds_rate=20 → NDS extracted first, then USN on base.

        nds  = 100000 * 0.20 / 1.20 = 16666.666...
        base = 100000 - 16666.666... = 83333.333...
        usn  = base * 0.06 ≈ 5000
        tot  = usn + nds ≈ 21666.666...
        """
        tax = _compute_tax(100000.0, {"usn_rate": 6, "nds_rate": 20})
        expected_nds = 100000 * 0.20 / 1.20
        expected_usn = (100000 - expected_nds) * 0.06
        assert abs(tax - (expected_nds + expected_usn)) < 1e-6

    def test_compute_tax_zero_rate(self):
        """usn_rate=0 → no tax whatsoever."""
        tax = _compute_tax(50000.0, {"usn_rate": 0, "nds_rate": 0})
        assert tax == 0.0

    def test_compute_tax_zero_income(self):
        """income<=0 → tax is zero regardless of rate."""
        assert _compute_tax(0.0, {"usn_rate": 6, "nds_rate": 0}) == 0.0
        assert _compute_tax(-100.0, {"usn_rate": 6, "nds_rate": 0}) == 0.0

    def test_compute_tax_missing_keys(self):
        """Missing keys in tax_info should default to 0 (no crash)."""
        assert _compute_tax(1000.0, {}) == 0.0
