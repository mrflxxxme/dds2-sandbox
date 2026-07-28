"""Unit tests for pure helpers in warehouse_stock_engine.

Covers _compute_tax_and_profit which mirrors bdr_enrichment.apply_tax_article.
This is the function that fixes the "0.9% vs 10% margin" bug — for projects
on `usn_income_expense_vat` regime the unified-stock formula was historically
applying simple `usn_income` math, which inflated the tax and crushed margin.
"""

from decimal import Decimal

from backend.services.warehouse_stock_engine import _compute_tax_and_profit

D = Decimal


def _kwargs(**overrides):
    """Realistic 14d numbers for divandek_кофе on prod (project=4)."""
    base = {
        "sales_amount": D("764026.54"),
        "to_pay": D("729846.34"),
        "ppvz_net": D("730397.62"),
        "logistics": D("511.28"),
        "storage": D("0"),
        "penalties": D("40"),
        "deductions_total": D("0"),
        "ad_deduction": D("0"),
        "loan_deduction": D("0"),
        "adv_sum": D("106109.13"),
        "cost_total": D("457166.55"),
        "usn_rate": D("0.15"),
        "nds_rate": D("0.07"),
        "regime": "usn_income_expense_vat",
        "cost_as_expense": True,
    }
    base.update(overrides)
    return base


def test_usn_income_expense_vat_with_cost_as_expense():
    """Главный кейс — режим Доходы−Расходы + cost_as_expense=True (как на проде)."""
    _tax, profit = _compute_tax_and_profit(**_kwargs())
    # Realization 1.13M, expected margin around 8-9%
    realization = D("1136067.20")
    margin = profit / realization * 100
    assert D("8") < margin < D("10"), f"Expected ~8.7% margin, got {margin:.2f}%"
    assert profit > D("90000"), "Profit should be ~99K, was crushed to ~10K by old formula"


def test_simple_usn_income_regime():
    """Простой режим — вычитает только НДС, потом 15% от остатка (как было РАНЬШЕ)."""
    _tax, profit = _compute_tax_and_profit(**_kwargs(regime="usn_income", cost_as_expense=False))
    # Old buggy behaviour — margin ~0.8%
    realization = D("1136067.20")
    margin = profit / realization * 100
    assert margin < D("2"), f"Old usn_income should give ~0.8% margin, got {margin:.2f}%"


def test_zero_tax_rates():
    """0% ставки → tax=0, profit ≈ to_pay - cost - adv."""
    tax, profit = _compute_tax_and_profit(
        **_kwargs(usn_rate=D("0"), nds_rate=D("0"), regime="usn_income"),
    )
    assert tax == D("0")
    expected = D("729846.34") - D("457166.55") - D("106109.13")
    assert profit == expected


def test_cost_as_expense_false_does_not_subtract_cost():
    """С cost_as_expense=False себестоимость НЕ уходит из налоговой базы."""
    tax_with, _ = _compute_tax_and_profit(**_kwargs(cost_as_expense=True))
    tax_without, _ = _compute_tax_and_profit(**_kwargs(cost_as_expense=False))
    # Without cost-as-expense, the tax base is bigger → tax is bigger
    assert tax_without > tax_with


def test_usn_base_clamped_to_zero():
    """Если расходы > доходов, налоговая база отрицательная — УСН должен быть 0."""
    # Tiny sales, big expenses — base would be negative
    tax, _profit = _compute_tax_and_profit(
        **_kwargs(
            sales_amount=D("10000"),
            to_pay=D("8000"),
            ppvz_net=D("9000"),
            logistics=D("3000"),
            adv_sum=D("5000"),
            cost_total=D("8000"),
        ),
    )
    # NDS should still be charged on income, but USN portion is 0
    expected_nds = D("10000") * D("0.07") / D("1.07")
    assert abs(tax - expected_nds) < D("0.01"), f"Tax should equal only NDS={expected_nds}, got {tax}"


def test_loan_and_ad_deductions_are_added_back():
    """to_pay уже вычел deductions_total; profit добавляет обратно ad/loan."""
    _, profit_a = _compute_tax_and_profit(
        **_kwargs(deductions_total=D("0"), ad_deduction=D("0"), loan_deduction=D("0")),
    )
    _, profit_b = _compute_tax_and_profit(
        # to_pay = 729846 - extra 5000 = 724846 (caller subtracted total deductions)
        **_kwargs(
            to_pay=D("724846.34"),
            deductions_total=D("5000"),
            ad_deduction=D("3000"),
            loan_deduction=D("2000"),
        ),
    )
    # Net effect: ad+loan are added back, only operating_deductions=0 remain subtracted
    # → profit_b should equal profit_a (within rounding) when operating_deductions=0
    assert abs(profit_a - profit_b) < D("1"), f"profit_a={profit_a} vs profit_b={profit_b}"


def test_returns_decimal_types():
    """Результат должен быть Decimal, не float — деньги в DDS только Decimal."""
    tax, profit = _compute_tax_and_profit(**_kwargs())
    assert isinstance(tax, Decimal)
    assert isinstance(profit, Decimal)


def test_regime_progression():
    """Чем больше расходов вычитаем, тем выше маржа (sanity ladder)."""
    _, profit_simple = _compute_tax_and_profit(**_kwargs(regime="usn_income", cost_as_expense=False))
    _, profit_dr = _compute_tax_and_profit(**_kwargs(regime="usn_income_expense_vat", cost_as_expense=False))
    _, profit_dr_cost = _compute_tax_and_profit(**_kwargs(regime="usn_income_expense_vat", cost_as_expense=True))
    # usn_income_expense_vat должен давать прибыль выше, чем usn_income
    assert profit_dr > profit_simple, f"DR ({profit_dr}) should beat simple ({profit_simple})"
    # cost_as_expense=True ещё больше уменьшает налог → ещё выше прибыль
    assert profit_dr_cost > profit_dr, f"DR+cost ({profit_dr_cost}) should beat DR ({profit_dr})"


class TestPerUnit:
    """_per_unit: период-расход без продаж не должен становиться «прибылью за
    штуку» (кламп max(qty,1) давал −8.7M фейка в KPI «Прибыль» на проде 28.07)."""

    def test_zero_qty_gives_zero(self):
        from decimal import Decimal

        from backend.services.warehouse_stock_engine import _per_unit

        assert _per_unit(Decimal("-3994.83"), 0) == 0.0

    def test_negative_qty_gives_zero(self):
        from decimal import Decimal

        from backend.services.warehouse_stock_engine import _per_unit

        assert _per_unit(Decimal("500.00"), -3) == 0.0

    def test_positive_qty_divides(self):
        from decimal import Decimal

        from backend.services.warehouse_stock_engine import _per_unit

        assert _per_unit(Decimal("100.00"), 4) == 25.0
