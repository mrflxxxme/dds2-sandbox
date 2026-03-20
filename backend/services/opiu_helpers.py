"""
OPIU helpers — SQL query builders, empty totals, row builder.

Extracted from opiu_service.py for maintainability.
"""

from decimal import Decimal

D = Decimal
ZERO = D("0")


# ─── SQL aggregation query ────────────────────────────────────────────────────

_DATE_FILTER = """(
    (rr_dt BETWEEN :date_from AND :date_to)
    OR (rr_dt IS NULL AND date_from >= :date_from AND date_to <= :date_to)
  )"""

_SELECT_COLS = """
    COALESCE(to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM')) AS month_key,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub WHEN doc_type_name = 'Возврат' THEN -retail_price_withdisc_rub ELSE 0 END), 0) AS realization,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_amount WHEN doc_type_name = 'Возврат' THEN -retail_amount ELSE 0 END), 0) AS sales_amount,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_for_pay_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_for_pay_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' AND supplier_oper_name = 'Добровольная компенсация при возврате' THEN ppvz_for_pay ELSE 0 END), 0) AS comp_ppvz,
    COALESCE(SUM(delivery_rub), 0) AS logistics,
    COALESCE(SUM(penalty), 0) AS penalties,
    COALESCE(SUM(storage_fee), 0) AS storage,
    COALESCE(SUM(acceptance), 0) AS acceptance_total,
    COALESCE(SUM(deduction), 0) AS deductions,
    COALESCE(SUM(CASE WHEN deduction != 0 AND (bonus_type_name LIKE '%%Продвижение%%' OR bonus_type_name LIKE '%%Медиа%%') THEN deduction ELSE 0 END), 0) AS ad_deduction,
    COALESCE(SUM(CASE WHEN deduction != 0 AND (LOWER(bonus_type_name) LIKE '%%кредит%%' OR LOWER(bonus_type_name) LIKE '%%заём%%') THEN deduction ELSE 0 END), 0) AS loan_deduction,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN additional_payment WHEN doc_type_name = 'Возврат' THEN -additional_payment ELSE 0 END), 0) AS additional_payment
"""


def build_aggregate_sql(brand: str | None, article: str | None) -> str:
    where = f"project_id = :project_id AND {_DATE_FILTER}"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += " AND LOWER(sa_name) LIKE :article_like"
    return f"SELECT {_SELECT_COLS} FROM wb_finance_rows WHERE {where} GROUP BY month_key ORDER BY month_key"  # noqa: S608


def build_brands_sql() -> str:
    return f"""SELECT DISTINCT brand_name FROM wb_finance_rows
        WHERE project_id = :project_id AND {_DATE_FILTER}
        AND brand_name IS NOT NULL AND brand_name != ''
        ORDER BY brand_name"""  # noqa: S608


def build_cost_qty_sql(brand: str | None, article: str | None) -> str:
    """Net qty per article/month: sale_qty - ret_qty (same as BDR)."""
    where = f"""project_id = :project_id
        AND doc_type_name IN ('Продажа', 'Возврат')
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        AND {_DATE_FILTER}"""
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += " AND LOWER(sa_name) LIKE :article_like"
    return f"""SELECT
        COALESCE(to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM')) AS month_key,
        sa_name, nm_id,
        COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN quantity ELSE 0 END), 0)
        - COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN quantity ELSE 0 END), 0) AS total_qty
        FROM wb_finance_rows WHERE {where}
        GROUP BY month_key, sa_name, nm_id"""


def build_brand_nm_ids_sql() -> str:
    return (
        f"SELECT DISTINCT nm_id FROM wb_finance_rows"  # noqa: S608
        f" WHERE project_id = :project_id AND {_DATE_FILTER}"
        f" AND brand_name = :brand AND nm_id IS NOT NULL AND nm_id != 0"
    )


# ─── Aggregation helpers ────────────────────────────────────────────────────


def empty_totals() -> dict:
    return {
        "realization": ZERO,
        "sales_amount": ZERO,
        "logistics": ZERO,
        "commission": ZERO,
        "penalties": ZERO,
        "storage": ZERO,
        "acceptance": ZERO,
        "deductions": ZERO,
        "ad_deduction": ZERO,
        "loan_deduction": ZERO,
        "additional_payment": ZERO,
        "compensation_ppvz": ZERO,
        "_ppvz_for_pay_sale": ZERO,
        "_ppvz_for_pay_ret": ZERO,
        "_comp_ppvz": ZERO,
    }


def pct(val, base):
    if not base or base == 0:
        return 0
    return round(val / base * 100, 2)


def build_row(
    key,
    label,
    level,
    bold,
    values_monthly,
    values_total,
    months_sorted,
    base_monthly=None,
    base_total=None,
    expandable=False,
):
    total_pct = pct(values_total, base_total) if base_total else None
    monthly = {}
    monthly_pct = {}
    for mk in months_sorted:
        monthly[mk] = round(float(values_monthly.get(mk, 0)), 2)
        if base_monthly and mk in base_monthly:
            monthly_pct[mk] = pct(values_monthly.get(mk, 0), base_monthly.get(mk, 0))
        else:
            monthly_pct[mk] = None
    return {
        "key": key,
        "label": label,
        "level": level,
        "bold": bold,
        "expandable": expandable,
        "total": round(float(values_total), 2),
        "total_pct": total_pct,
        "monthly": monthly,
        "monthly_pct": monthly_pct,
    }
