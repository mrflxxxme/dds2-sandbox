"""
WB BDR helpers — SQL query builders, metrics computation, serialization.

Extracted from wb_bdr_service.py for maintainability.
"""

from decimal import Decimal
from typing import Any

D = Decimal
ZERO = D("0")


# ─── SQL aggregation query ────────────────────────────────────────────────────

_DATE_FILTER = """(
    COALESCE(sale_dt, rr_dt) BETWEEN :date_from AND :date_to
    OR (sale_dt IS NULL AND rr_dt IS NULL AND date_from >= :date_from AND date_to <= :date_to)
  )"""

_MONTH_KEY_EXPR = (
    "COALESCE(to_char(sale_dt, 'YYYY-MM'), to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM'))"
)

# Метрики без identity-блока — переиспользуются помесячным тоталом (налог при
# смене ставок внутри диапазона): строки обоих SQL кормятся compute_metrics_from_sql.
_METRIC_COLS_BDR = """
    -- Realization components (sale/ret split)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_price_withdisc_rub ELSE 0 END), 0) AS sale_retail,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_price_withdisc_rub ELSE 0 END), 0) AS ret_retail,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN retail_amount ELSE 0 END), 0) AS sale_amount,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN retail_amount ELSE 0 END), 0) AS ret_amount,

    -- ppvz_for_pay (sale/ret split for commission calc)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_for_pay ELSE 0 END), 0) AS ppvz_ret,

    -- Compensation (Добровольная компенсация при возврате)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
        AND supplier_oper_name = 'Добровольная компенсация при возврате'
        THEN ppvz_for_pay ELSE 0 END), 0) AS comp_ppvz,

    -- WB reward components (for total_wb_reward)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_sales_commission ELSE 0 END), 0) AS ppvz_comm_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_sales_commission ELSE 0 END), 0) AS ppvz_comm_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_vw ELSE 0 END), 0) AS ppvz_vw_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_vw ELSE 0 END), 0) AS ppvz_vw_ret,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN ppvz_vw_nds ELSE 0 END), 0) AS ppvz_vw_nds_sale,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN ppvz_vw_nds ELSE 0 END), 0) AS ppvz_vw_nds_ret,

    -- Product quantities (only from oper_name Продажа/Возврат)
    COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа'
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        THEN quantity ELSE 0 END), 0) AS sale_qty,
    COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат'
        AND supplier_oper_name IN ('Продажа', 'Возврат')
        THEN quantity ELSE 0 END), 0) AS ret_qty,

    -- ALL buckets sums (WB distributes across doc_types)
    COALESCE(SUM(delivery_rub), 0) AS logistics,
    COALESCE(SUM(penalty), 0) AS penalties,
    COALESCE(SUM(storage_fee), 0) AS storage,
    COALESCE(SUM(acceptance), 0) AS acceptance_total,
    COALESCE(SUM(rebill_logistic_cost), 0) AS rebill,
    COALESCE(SUM(deduction), 0) AS deductions_total,

    -- Deduction splits (from all buckets)
    COALESCE(SUM(CASE WHEN deduction != 0
        AND (bonus_type_name LIKE '%%Продвижение%%' OR bonus_type_name LIKE '%%Медиа%%')
        THEN deduction ELSE 0 END), 0) AS ad_deduction,
    COALESCE(SUM(CASE WHEN deduction != 0
        AND bonus_type_name LIKE '%%отзыв%%'
        THEN deduction ELSE 0 END), 0) AS other_deduction,
    COALESCE(SUM(CASE WHEN deduction != 0
        AND (LOWER(bonus_type_name) LIKE '%%кредит%%' OR LOWER(bonus_type_name) LIKE '%%заём%%')
        THEN deduction ELSE 0 END), 0) AS loan_deduction
"""

_SELECT_COLS_BDR = (
    """
    sa_name,
    MAX(NULLIF(nm_id, 0)) AS nm_id,
    MAX(NULLIF(brand_name, '')) AS brand_name,
    MAX(NULLIF(subject_name, '')) AS subject_name,
    COUNT(*) AS row_count,
"""
    + _METRIC_COLS_BDR
)


_ALLOWED_GROUP_COLS = {"brand": "brand_name", "subject": "subject_name", "article": "sa_name"}


def _resolve_group_col(group_by: str) -> str:
    """Return validated column name from allowlist (prevents SQL injection)."""
    col = _ALLOWED_GROUP_COLS.get(group_by)
    if col is None:
        raise ValueError(f"Invalid group_by: {group_by!r}")
    return col


def build_bdr_aggregate_sql(
    brand: str | None,
    article: str | None,
    group_by: str = "article",
) -> str:
    """Build SQL aggregation query with optional filters.

    group_by: "article" (default) | "brand" | "subject"
    """
    group_col = _resolve_group_col(group_by)

    if group_by == "brand":
        select_cols = _SELECT_COLS_BDR.replace("sa_name,", "brand_name AS sa_name,")
    elif group_by == "subject":
        select_cols = _SELECT_COLS_BDR.replace("sa_name,", "subject_name AS sa_name,")
    else:
        select_cols = _SELECT_COLS_BDR

    where = "project_id = :project_id AND " + _DATE_FILTER
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += r" AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
    return (
        "SELECT "  # noqa: S608 — column names from _ALLOWED_GROUP_COLS allowlist, not user input
        + select_cols
        + " FROM wb_finance_rows WHERE "
        + where
        + " GROUP BY "
        + group_col
        + " ORDER BY "
        + group_col
    )


def build_bdr_monthly_totals_sql(brand: str | None, article: str | None) -> str:
    """Помесячные тоталы тех же метрик (identity-блок заменён на month_key).

    Нужны налогу при смене ставок внутри диапазона: каждая строка-месяц
    скармливается compute_metrics_from_sql (метрики идентичны основному SQL),
    итоговый налог = сумма помесячных. Фильтры — те же, что у основного SQL."""
    where = "project_id = :project_id AND " + _DATE_FILTER
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += r" AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
    return (
        "SELECT "  # noqa: S608 — фильтры параметризованы, колонки из констант
        + _MONTH_KEY_EXPR
        + " AS month_key, COUNT(*) AS row_count, "
        + _METRIC_COLS_BDR
        + " FROM wb_finance_rows WHERE "
        + where
        + " GROUP BY month_key ORDER BY month_key"
    )


def build_group_nm_ids_sql(group_by: str, brand: str | None, article: str | None) -> str:
    """Get nm_id → group_key mapping for aggregating enrichment data by brand/subject."""
    group_col = _resolve_group_col(group_by)
    if group_by == "article":
        return ""
    where = "project_id = :project_id AND " + _DATE_FILTER
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    where += " AND " + group_col + " IS NOT NULL AND " + group_col + " != ''"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += r" AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
    return (
        "SELECT DISTINCT NULLIF(nm_id, 0) AS nm_id, " + group_col + " AS group_key"  # noqa: S608 — allowlist
        " FROM wb_finance_rows WHERE " + where + " AND nm_id IS NOT NULL AND nm_id != 0"
    )


def build_group_sa_names_sql(group_by: str, brand: str | None, article: str | None) -> str:
    """Get sa_name → group_key mapping for aggregating cost data by brand/subject."""
    group_col = _resolve_group_col(group_by)
    if group_by == "article":
        return ""
    where = "project_id = :project_id AND " + _DATE_FILTER
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    where += " AND " + group_col + " IS NOT NULL AND " + group_col + " != ''"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += r" AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
    return (
        "SELECT DISTINCT sa_name, " + group_col + " AS group_key"  # noqa: S608 — allowlist
        " FROM wb_finance_rows WHERE " + where + " AND sa_name IS NOT NULL"
    )


def build_brands_sql_bdr() -> str:
    """Get distinct brand names for filter dropdown."""
    return (
        "SELECT DISTINCT brand_name FROM wb_finance_rows"  # noqa: S608 — no user input
        " WHERE project_id = :project_id AND "
        + _DATE_FILTER
        + " AND brand_name IS NOT NULL AND brand_name != '' ORDER BY brand_name"
    )


def build_total_count_sql(brand: str | None, article: str | None) -> str:
    """Count total raw rows (for total_rows in response)."""
    where = "project_id = :project_id AND " + _DATE_FILTER
    where += " AND LOWER(COALESCE(sa_name, '')) != 'неопознанный товар'"
    if brand:
        where += " AND brand_name = :brand"
    if article:
        where += r" AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
    return "SELECT COUNT(*) AS cnt FROM wb_finance_rows WHERE " + where  # noqa: S608


# ─── Metrics computation ──────────────────────────────────────────────────────


def empty_metrics() -> dict:
    """Empty metrics dict (same keys as compute_metrics_from_sql output)."""
    return {
        "realization": ZERO,
        "sales_amount": ZERO,
        "returns_amount": ZERO,
        "to_pay": ZERO,
        "ppvz_for_pay": ZERO,
        "compensation": ZERO,
        "sale_qty": 0,
        "sale_qty_gross": 0,
        "ret_qty": 0,
        "buyout_pct": ZERO,
        "avg_sale_price": ZERO,
        "avg_retail_price": ZERO,
        "avg_logistics": ZERO,
        "commission": ZERO,
        "total_wb_reward": ZERO,
        "logistics": ZERO,
        "penalties": ZERO,
        "storage": ZERO,
        "acceptance": ZERO,
        "deductions": ZERO,
        "deductions_total": ZERO,
        "ad_deduction": ZERO,
        "other_deduction": ZERO,
        "loan_deduction": ZERO,
        "wb_deductions": ZERO,
        "rebill": ZERO,
    }


def compute_metrics_from_sql(row: Any) -> dict:  # type: ignore[type-arg]
    """Compute BDR metrics from a SQL aggregation row.

    Commission formula verified against TrueStats:
        commission = sales_amount_net - (ppvz_net - compensation)

    IMPORTANT: logistics, penalties, deductions etc. are summed from ALL 3 buckets
    because WB API distributes these values across sale/return/other rows.
    """
    sale_retail = D(str(row["sale_retail"]))
    ret_retail = D(str(row["ret_retail"]))
    sale_amount = D(str(row["sale_amount"]))
    ret_amount = D(str(row["ret_amount"]))
    ppvz_sale = D(str(row["ppvz_sale"]))
    ppvz_ret = D(str(row["ppvz_ret"]))
    compensation = D(str(row["comp_ppvz"]))

    realization = sale_retail - ret_retail
    sales_amount = sale_amount - ret_amount
    returns_amount = ret_retail

    sale_qty = int(row["sale_qty"])
    ret_qty = int(row["ret_qty"])
    net_sale_qty = sale_qty - ret_qty

    # ALL buckets sums
    logistics = D(str(row["logistics"]))
    penalties = D(str(row["penalties"]))
    storage = D(str(row["storage"]))
    acceptance_val = D(str(row["acceptance_total"]))
    rebill = D(str(row["rebill"]))

    # Deductions
    deductions_total = D(str(row["deductions_total"]))
    ad_deduction = D(str(row["ad_deduction"]))
    other_deduction = D(str(row["other_deduction"]))
    loan_deduction = D(str(row["loan_deduction"]))

    # Operating deductions = total - ads - loans
    operating_deductions = deductions_total - ad_deduction - loan_deduction

    # Commission: TrueStats formula
    ppvz_net = ppvz_sale - ppvz_ret
    commission = sales_amount - (ppvz_net - compensation)

    # WB total reward (for reference)
    ppvz_comm_net = D(str(row["ppvz_comm_sale"])) - D(str(row["ppvz_comm_ret"]))
    ppvz_vw_net = D(str(row["ppvz_vw_sale"])) - D(str(row["ppvz_vw_ret"]))
    ppvz_vw_nds_net = D(str(row["ppvz_vw_nds_sale"])) - D(str(row["ppvz_vw_nds_ret"]))
    total_wb_reward = ppvz_comm_net + ppvz_vw_net + ppvz_vw_nds_net

    # To Pay (rebill already subtracted by WB from ppvz_for_pay — do NOT subtract again)
    to_pay = ppvz_net - logistics - penalties - storage - deductions_total - acceptance_val

    avg_sale_price = sales_amount / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO
    avg_retail_price = realization / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO
    avg_logistics = logistics / D(str(net_sale_qty)) if net_sale_qty > 0 else ZERO

    total_qty = sale_qty + ret_qty
    buyout_pct = D(str(sale_qty)) / D(str(total_qty)) * 100 if total_qty > 0 else ZERO

    return {
        "realization": realization,
        "sales_amount": sales_amount,
        "returns_amount": returns_amount,
        "to_pay": to_pay,
        "ppvz_for_pay": ppvz_net,
        "compensation": compensation,
        "sale_qty": net_sale_qty,
        "sale_qty_gross": sale_qty,
        "ret_qty": ret_qty,
        "buyout_pct": buyout_pct,
        "avg_sale_price": avg_sale_price,
        "avg_retail_price": avg_retail_price,
        "avg_logistics": avg_logistics,
        "commission": commission,
        "total_wb_reward": total_wb_reward,
        "logistics": logistics,
        "penalties": penalties,
        "storage": storage,
        "acceptance": acceptance_val,
        "deductions": operating_deductions,
        "deductions_total": deductions_total,
        "ad_deduction": ad_deduction,
        "other_deduction": other_deduction,
        "loan_deduction": loan_deduction,
        "wb_deductions": ad_deduction + loan_deduction,
        "rebill": rebill,
    }


def serialize(d: dict) -> dict:
    """Convert Decimals to floats for JSON."""
    return {k: float(v) if isinstance(v, Decimal) else v for k, v in d.items()}


def cost_with_fallback(
    engine_price: float,
    sku: str,
    nm_id: int,
    cost_map: dict[str, float],
    overrides: dict[int, float],
) -> float:
    """Цена движка, если положительная; иначе средняя закупочная → override.

    Движок оценки отдаёт 0, когда партии не сматчились по ключу или окно
    нетто-нулевое (возвраты = продажам). Списывать ноль при известной цене
    нельзя — прибыль тихо завышается (прод-кейс elka_240х220: 123 шт/нед по
    0 ₽ при известных 1 396 ₽)."""
    if engine_price > 0:
        return engine_price
    price = cost_map.get(sku, 0) if sku else 0
    if price == 0 and nm_id in overrides:
        price = overrides[nm_id]
    return price
