"""
Cost-DNA helpers — SQL builders, loaders, and per-category metric computation.

Pure functions and DB loaders for the Cost-DNA report. Pattern follows
``wb_bdr_helpers.py`` and ``bdr_loaders.py``.

Phase 2 teammate fills out the function bodies. Phase 1 (Lead) provides
the public contract so router/service stubs compile.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrder, CostOrderItem, Nomenclature, WbFunnelDaily

D = Decimal
ZERO = D("0")


# ─── Revenue + WB-fees per subject ───────────────────────────────────────────
#
# Source: wb_finance_rows GROUP BY subject_name (excluding NULL/'')
# Date filter: COALESCE(sale_dt, rr_dt) BETWEEN :date_from AND :date_to
# Formula: realization = SUM(retail_price_withdisc_rub WHERE doc_type='Продажа')
#                      - SUM(retail_price_withdisc_rub WHERE doc_type='Возврат')
#
# 408k untagged rows (subject_name IS NULL OR '') are SKIPPED — they correspond
# to shop-level adv/loan deductions which BDR/OPIU compensate by adding back
# ad_deduction+loan_deduction. For Cost-DNA we ignore them; per-category ad
# spend is sourced from wb_funnel_daily.adv_sum directly.

_REVENUE_BY_SUBJECT_SQL = """
SELECT
    subject_name AS subject,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) AS sale_retail,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0) AS ret_retail,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_amount ELSE 0 END), 0) AS sale_amount,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_amount ELSE 0 END), 0) AS ret_amount,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_sales_commission ELSE 0 END), 0) AS comm_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_sales_commission ELSE 0 END), 0) AS comm_ret,
    COALESCE(SUM(delivery_rub), 0) AS logistics,
    COALESCE(SUM(storage_fee), 0) AS storage,
    COALESCE(SUM(penalty), 0) AS penalty,
    COALESCE(SUM(acceptance), 0) AS acceptance,
    -- "Прочие удержания": всё кроме рекламы (учитывается отдельно из wb_funnel_daily.adv_sum)
    -- и кредитов (финансовая операция, не операционный расход).
    COALESCE(SUM(CASE WHEN deduction != 0
        AND NOT (bonus_type_name LIKE '%%Продвижение%%' OR bonus_type_name LIKE '%%Медиа%%')
        AND NOT (LOWER(bonus_type_name) LIKE '%%кредит%%' OR LOWER(bonus_type_name) LIKE '%%заём%%')
        THEN deduction ELSE 0 END), 0) AS other_deduction,
    -- Net units sold for cost-per-unit projection
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        THEN quantity ELSE 0 END), 0) AS sale_qty,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат'
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        THEN quantity ELSE 0 END), 0) AS ret_qty
FROM wb_finance_rows
WHERE project_id = :project_id
  AND (
    COALESCE(sale_dt, rr_dt) BETWEEN :date_from AND :date_to
    OR (sale_dt IS NULL AND rr_dt IS NULL AND date_from >= :date_from AND date_to <= :date_to)
  )
  AND subject_name IS NOT NULL
  AND subject_name != ''
GROUP BY subject_name
"""


def build_revenue_by_subject_sql() -> str:
    """Return parameterized SQL for revenue + WB-fees aggregation per subject."""
    return _REVENUE_BY_SUBJECT_SQL


# ─── Cost components per subject ─────────────────────────────────────────────
#
# Source: cost_order_items (joined to nomenclature for subject lookup).
# Fields are PER-UNIT (cost_rub, delivery_rub, duty_rub, vat_rub, util_rub, total_rub).
# Weighted aggregation: SUM(field * qty) / SUM(qty) gives weighted_avg per unit.
# We return totals (not avg) so the service can divide by revenue for %-of-revenue.


async def load_cost_components_by_subject(db: AsyncSession, project_id: int) -> dict[str, dict[str, float]]:
    """Load cost components totaled by subject from cost_order_items.

    Returns:
        { subject_name: {
            "factory_total": float,  # SUM(cost_rub * qty)
            "duty_total": float,     # SUM(duty_rub * qty)
            "delivery_total": float, # SUM(delivery_rub * qty)
            "vat_total": float,      # SUM((vat_rub + util_rub) * qty)
            "cost_total": float,     # SUM(total_rub * qty)
            "qty_total": int,
        } }

    Subject is taken from Nomenclature (lookup by barcode + project_id).
    Items without a matching nomenclature row are skipped (no subject to group by).
    """
    result = await db.execute(
        select(
            Nomenclature.subject.label("subject"),
            func.sum(CostOrderItem.cost_rub * CostOrderItem.qty).label("factory_total"),
            func.sum(CostOrderItem.duty_rub * CostOrderItem.qty).label("duty_total"),
            func.sum(CostOrderItem.delivery_rub * CostOrderItem.qty).label("delivery_total"),
            func.sum(
                (func.coalesce(CostOrderItem.vat_rub, 0) + func.coalesce(CostOrderItem.util_rub, 0)) * CostOrderItem.qty
            ).label("vat_total"),
            func.sum(CostOrderItem.total_rub * CostOrderItem.qty).label("cost_total"),
            func.sum(CostOrderItem.qty).label("qty_total"),
        )
        .join(CostOrder, CostOrderItem.order_no == CostOrder.order_no)
        .join(
            Nomenclature,
            (Nomenclature.barcode == CostOrderItem.barcode) & (Nomenclature.project_id == CostOrderItem.project_id),
        )
        .where(
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712 — SQLAlchemy column comparison
            CostOrderItem.is_deleted == False,  # noqa: E712
            CostOrderItem.qty > 0,
            Nomenclature.subject.isnot(None),
            Nomenclature.subject != "",
        )
        .group_by(Nomenclature.subject)
    )
    out: dict[str, dict[str, float]] = {}
    for r in result:
        out[r.subject] = {
            "factory_total": float(r.factory_total or 0),
            "duty_total": float(r.duty_total or 0),
            "delivery_total": float(r.delivery_total or 0),
            "vat_total": float(r.vat_total or 0),
            "cost_total": float(r.cost_total or 0),
            "qty_total": int(r.qty_total or 0),
        }
    return out


# ─── Ads per subject (from wb_funnel_daily) ──────────────────────────────────


async def load_ads_by_subject(
    db: AsyncSession,
    project_id: int,
    date_from: date,
    date_to: date,
) -> dict[str, float]:
    """Load total ad spend per subject from wb_funnel_daily.adv_sum.

    Returns:
        { subject_name: total_adv_rub } — only subjects with non-empty value.
    """
    result = await db.execute(
        select(
            WbFunnelDaily.subject,
            func.sum(WbFunnelDaily.adv_sum).label("total_adv"),
        )
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.date >= date_from,
            WbFunnelDaily.date <= date_to,
            WbFunnelDaily.subject.isnot(None),
            WbFunnelDaily.subject != "",
        )
        .group_by(WbFunnelDaily.subject)
    )
    return {r.subject: float(r.total_adv or 0) for r in result}


# ─── Per-category metric computation ─────────────────────────────────────────


def compute_category_metrics(
    revenue_row: Any,  # sa.Row from build_revenue_by_subject_sql
    cost_row: dict[str, float] | None,
    adv_sum: float,
    tax_info: dict,
) -> dict:
    """Compute per-category metrics from raw aggregations.

    Returns a dict matching CostDnaCategory schema (without trend/share fields,
    which the service computes after aggregating across all categories).

    Key formulas:
        revenue           = sale_retail - ret_retail              (база 100% — цена полки до СПП)
        sales_amount      = sale_amount - ret_amount              (после СПП, то что заплатил покупатель)
        ppvz_net          = ppvz_sale - ppvz_ret
        mp_commission     = revenue - ppvz_net                    (для отображения; включает СПП)
        opiu_commission   = sales_amount - ppvz_net               (для расчёта налога; без СПП)
        net_qty           = sale_qty - ret_qty
        weighted_avg_unit_cost = SUM(component * qty) / SUM(qty)
        projected_cost_rub     = weighted_avg_unit_cost * net_qty
        cost_*_pct        = projected_cost_*_rub / revenue * 100  (None если нет cost data)
        mp_other          = penalty + acceptance + other_deduction
        tax_total         = _compute_tax(sales_amount, expenses, cost_val, tax_info)
                            где expenses = log + storage + |opiu_commission| + penalty
                                            + |other_deduction| + adv  (как в opiu_service)
        tax_pct           = tax_total / revenue * 100
        margin_pct        = 100 - cost_total_pct - mp_total_pct - tax_pct  (None если нет cost)
    """
    sale_retail = D(str(revenue_row.sale_retail))
    ret_retail = D(str(revenue_row.ret_retail))
    sale_amount = D(str(revenue_row.sale_amount))
    ret_amount = D(str(revenue_row.ret_amount))
    ppvz_sale = D(str(revenue_row.ppvz_sale))
    ppvz_ret = D(str(revenue_row.ppvz_ret))
    logistics = D(str(revenue_row.logistics))
    storage = D(str(revenue_row.storage))
    penalty = D(str(getattr(revenue_row, "penalty", 0) or 0))
    acceptance = D(str(getattr(revenue_row, "acceptance", 0) or 0))
    other_deduction = D(str(revenue_row.other_deduction))
    sale_qty = int(revenue_row.sale_qty or 0)
    ret_qty = int(revenue_row.ret_qty or 0)
    net_qty = sale_qty - ret_qty

    revenue = sale_retail - ret_retail
    sales_amount = sale_amount - ret_amount
    ppvz_net = ppvz_sale - ppvz_ret
    mp_commission = revenue - ppvz_net  # для отображения — включает СПП
    opiu_commission = sales_amount - ppvz_net  # для налога — без СПП (как в opiu_service)
    mp_other_combined = penalty + acceptance + other_deduction

    if revenue <= 0:
        return _empty_category_metrics(revenue_row, sales_amount)

    rev_f = float(revenue)
    sales_f = float(sales_amount)

    # MP fees as % of revenue (use abs() — WB stores fees as negatives sometimes)
    mp_commission_pct = abs(float(mp_commission)) / rev_f * 100
    mp_logistics_pct = abs(float(logistics)) / rev_f * 100
    mp_storage_pct = abs(float(storage)) / rev_f * 100
    mp_advertising_pct = adv_sum / rev_f * 100 if adv_sum else 0
    mp_other_pct = abs(float(mp_other_combined)) / rev_f * 100
    mp_total_pct = mp_commission_pct + mp_logistics_pct + mp_storage_pct + mp_advertising_pct + mp_other_pct

    # Cost components — weighted average per-unit * net units sold
    has_cost = cost_row is not None and cost_row.get("qty_total", 0) > 0 and net_qty > 0
    cost_factory_pct = None
    cost_duty_pct = None
    cost_delivery_pct = None
    cost_vat_pct = None
    cost_total_pct = None
    proj_factory = 0.0
    proj_duty = 0.0
    proj_delivery = 0.0
    proj_vat = 0.0
    proj_total = 0.0
    margin_pct = None
    if has_cost:
        assert cost_row is not None  # narrowed by has_cost
        qty_in_basket = cost_row["qty_total"]
        avg_factory = cost_row["factory_total"] / qty_in_basket
        avg_duty = cost_row["duty_total"] / qty_in_basket
        avg_delivery = cost_row["delivery_total"] / qty_in_basket
        avg_vat = cost_row["vat_total"] / qty_in_basket
        proj_factory = avg_factory * net_qty
        proj_duty = avg_duty * net_qty
        proj_delivery = avg_delivery * net_qty
        proj_vat = avg_vat * net_qty
        proj_total = proj_factory + proj_duty + proj_delivery + proj_vat

        cost_factory_pct = proj_factory / rev_f * 100
        cost_duty_pct = proj_duty / rev_f * 100
        cost_delivery_pct = proj_delivery / rev_f * 100
        cost_vat_pct = proj_vat / rev_f * 100
        cost_total_pct = proj_total / rev_f * 100

    # Tax — full ОПИУ logic (regime / cost_as_expense / expenses).
    # expenses зеркалят opiu_service._tax_expenses_for: log + stor + opiu_comm + pen + other + adv (+ cost).
    tax_total = _compute_tax(
        sales_amount=sales_f,
        log=float(logistics),
        stor=float(storage),
        opiu_comm=float(opiu_commission),
        pen=float(penalty),
        other_ded=float(other_deduction),
        adv=adv_sum,
        cost_val=proj_total,
        tax_info=tax_info,
    )
    tax_pct = tax_total / rev_f * 100 if rev_f else 0

    if has_cost:
        margin_pct = 100 - (cost_total_pct or 0) - mp_total_pct - tax_pct

    return {
        "category": revenue_row.subject,
        "revenue": rev_f,
        "revenue_share_pct": 0,  # filled by service after totals
        "cost_factory_pct": round(cost_factory_pct, 2) if cost_factory_pct is not None else None,
        "cost_duty_pct": round(cost_duty_pct, 2) if cost_duty_pct is not None else None,
        "cost_delivery_pct": round(cost_delivery_pct, 2) if cost_delivery_pct is not None else None,
        "cost_vat_pct": round(cost_vat_pct, 2) if cost_vat_pct is not None else None,
        "cost_total_pct": round(cost_total_pct, 2) if cost_total_pct is not None else None,
        "has_cost_data": has_cost,
        "mp_commission_pct": round(mp_commission_pct, 2),
        "mp_logistics_pct": round(mp_logistics_pct, 2),
        "mp_storage_pct": round(mp_storage_pct, 2),
        "mp_advertising_pct": round(mp_advertising_pct, 2),
        "mp_other_pct": round(mp_other_pct, 2),
        "mp_total_pct": round(mp_total_pct, 2),
        "tax_pct": round(tax_pct, 2),
        "margin_pct": round(margin_pct, 2) if margin_pct is not None else None,
        # Private fields for totals aggregation
        "_sales_amount": sales_f,
        "_logistics": float(logistics),
        "_storage": float(storage),
        "_mp_commission": float(mp_commission),
        "_opiu_commission": float(opiu_commission),
        "_penalty": float(penalty),
        "_acceptance": float(acceptance),
        "_other_deduction": float(other_deduction),
        "_adv_sum": adv_sum,
        "_net_qty": net_qty,
        "_proj_factory": proj_factory,
        "_proj_duty": proj_duty,
        "_proj_delivery": proj_delivery,
        "_proj_vat": proj_vat,
        "_proj_cost_total": proj_total,
    }


def _empty_category_metrics(revenue_row: Any, sales_amount: Decimal) -> dict:
    return {
        "category": revenue_row.subject,
        "revenue": 0,
        "revenue_share_pct": 0,
        "cost_factory_pct": None,
        "cost_duty_pct": None,
        "cost_delivery_pct": None,
        "cost_vat_pct": None,
        "cost_total_pct": None,
        "has_cost_data": False,
        "mp_commission_pct": 0,
        "mp_logistics_pct": 0,
        "mp_storage_pct": 0,
        "mp_advertising_pct": 0,
        "mp_other_pct": 0,
        "mp_total_pct": 0,
        "tax_pct": 0,
        "margin_pct": None,
        "_sales_amount": float(sales_amount),
        "_logistics": 0,
        "_storage": 0,
        "_mp_commission": 0,
        "_opiu_commission": 0,
        "_penalty": 0,
        "_acceptance": 0,
        "_other_deduction": 0,
        "_adv_sum": 0,
        "_net_qty": 0,
        "_proj_factory": 0,
        "_proj_duty": 0,
        "_proj_delivery": 0,
        "_proj_vat": 0,
        "_proj_cost_total": 0,
    }


def _compute_tax(
    sales_amount: float,
    log: float = 0,
    stor: float = 0,
    opiu_comm: float = 0,
    pen: float = 0,
    other_ded: float = 0,
    adv: float = 0,
    cost_val: float = 0,
    tax_info: dict | None = None,
) -> float:
    """Tax calc - mirrors opiu_service._calc_tax + _tax_expenses_for.

    Honours the project tax_regime from tax_info:
        - usn_income              -> USN on (sales - NDS), expenses ignored
        - usn_income_expense_vat  -> USN on (sales - NDS - expenses), plus NDS

    expenses = |log| + |stor| + |opiu_comm| + |pen| + |other_ded| + |adv|
               + (|cost_val| if cost_as_expense)

    opiu_comm is the OPIU-style commission (sales_amount - ppvz_net), without
    the SPP discount. SPP is not a deductible expense for the tax authority,
    so it stays out of the tax base. For display in "Marketplace / Commission"
    a separate variable mp_commission = revenue - ppvz_net is used (which DOES
    include SPP).
    """
    info = tax_info or {}
    usn_rate = float(info.get("usn_rate", 0) or 0) / 100
    nds_rate = float(info.get("nds_rate", 0) or 0) / 100
    regime = info.get("tax_regime", "usn_income")
    cost_as_expense = bool(info.get("cost_as_expense", False))

    if sales_amount <= 0:
        return 0.0

    nds_sum = sales_amount * nds_rate / (1 + nds_rate) if nds_rate > 0 else 0.0
    base = sales_amount - nds_sum

    if regime == "usn_income_expense_vat":
        expenses = abs(log) + abs(stor) + abs(opiu_comm) + abs(pen) + abs(other_ded) + abs(adv)
        if cost_as_expense:
            expenses += abs(cost_val)
        base -= expenses

    usn_sum = max(base * usn_rate, 0) if usn_rate > 0 else 0.0
    return nds_sum + usn_sum
