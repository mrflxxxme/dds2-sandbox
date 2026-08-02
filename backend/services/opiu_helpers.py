"""
OPIU helpers — SQL query builders, empty totals, row builder.

Extracted from opiu_service.py for maintainability.
"""

from decimal import Decimal

D = Decimal
ZERO = D("0")


# ─── SQL aggregation query ────────────────────────────────────────────────────

_DATE_FILTER = """(
    COALESCE(sale_dt, rr_dt) BETWEEN :date_from AND :date_to
    OR (sale_dt IS NULL AND rr_dt IS NULL AND date_from >= :date_from AND date_to <= :date_to)
  )"""

_SELECT_COLS = """
    COALESCE(to_char(sale_dt, 'YYYY-MM'), to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM')) AS month_key,
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
        where += " AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
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
        where += " AND LOWER(sa_name) LIKE :article_like ESCAPE '\\'"
    return f"""SELECT
        COALESCE(to_char(sale_dt, 'YYYY-MM'), to_char(rr_dt, 'YYYY-MM'), to_char(date_from, 'YYYY-MM')) AS month_key,
        sa_name, nm_id,
        COALESCE(SUM(CASE WHEN doc_type_name = 'Продажа' THEN quantity ELSE 0 END), 0)
        - COALESCE(SUM(CASE WHEN doc_type_name = 'Возврат' THEN quantity ELSE 0 END), 0) AS total_qty
        FROM wb_finance_rows WHERE {where}
        GROUP BY month_key, sa_name, nm_id"""


# ─── Operating expenses by counterparty type ────────────────────────────────

# Russian labels for counterparty primary_type (mirror of frontend TYPE_CONFIG
# in CounterpartyTypeBadge.tsx). Unknown types fall back to the raw code.
OPEX_TYPE_LABELS: dict[str, str] = {
    "SUPPLIER": "Поставщик",
    "FULFILLMENT": "Фулфилмент",
    "CARRIER": "Перевозчик",
    "TRADING_HOUSE": "Торговый дом",
    "CUSTOMS_BROKER": "Таможенный брокер",
    "DESIGNER": "Дизайнер",
    "LEGAL": "Юридические услуги",
    "LANDLORD": "Аренда",
    "IT_SERVICE": "IT-сервис",
    "MARKETPLACE": "Маркетплейс",
    "BANK": "Банк",
    "GOVERNMENT": "Государство / ФНС",
    "AFFILIATED": "Аффилированное лицо",
    "OTHER": "Прочее",
}

# Types excluded from «Операционные расходы»:
#   OTHER         — «Прочее» (по требованию)
#   SUPPLIER /    — закупочная цепочка: их траты — это покупка товара, уже
#   TRADING_HOUSE   учтённая в строке «Себестоимость» выше; включать = задвоить P&L.
# Всё остальное (аренда, IT, дизайн, перевозчик, таможенный брокер, банк, ФНС, …)
# — реальный opex.
OPEX_EXCLUDED_TYPES: frozenset[str] = frozenset({"OTHER", "SUPPLIER", "TRADING_HOUSE"})


def build_opex_by_type_sql() -> str:
    """
    Monthly expense from bank transactions grouped by counterparty primary_type.

    Same source/filters as the counterparty reference summary (expense side,
    non-internal, RUB — i.e. everything not CNY), но с помесячной разбивкой и
    исключением закупочных типов и «Прочее». Excluded types are inlined as a
    literal tuple (fixed enum values, not user input).
    """
    excluded = ", ".join(f"'{t}'" for t in sorted(OPEX_EXCLUDED_TYPES))
    return f"""
        SELECT
            to_char(t.date, 'YYYY-MM') AS month_key,
            c.primary_type AS primary_type,
            COALESCE(SUM(t.expense), 0) AS expense_sum
        FROM transactions t
        JOIN counterparty c ON c.id = t.counterparty_id
        WHERE t.project_id = :project_id
          AND t.is_deleted = false
          AND t.is_internal = false
          AND t.date >= CAST(:date_from AS date)
          AND t.date < CAST(:date_to AS date) + INTERVAL '1 day'
          AND UPPER(t.currency) <> 'CNY'
          AND c.is_deleted = false
          AND c.project_id = :project_id
          AND c.primary_type NOT IN ({excluded})
          AND NOT EXISTS (
              -- ФОТ идёт в ОПиУ отдельной строкой НАЧИСЛЕНИЕМ (payroll_service.
              -- get_monthly_accruals); фактические выплаты сотрудникам из выписки
              -- исключаем из opex, иначе зарплата задваивается. Зеркально стороне
              -- начисления, ИНАЧЕ деньги исчезают из ОПиУ вовсе (кейс Ирины-логиста):
              --   * НЕАКТИВНЫЙ сотрудник: начисление 0 → выплаты ОСТАЮТСЯ в opex;
              --   * процентщик (состоит в живой команде): исключаем ВСЮ историю;
              --   * оклад-периоды: исключаем ТОЛЬКО с min(valid_from) — «доплатёжная»
              --     история (выплаты до первого периода) остаётся в opex;
              --   * привязан, но без команд и без периодов: не исключаем ничего.
              -- Известное ограничение (принято, не чинить без запроса юзера):
              -- деактивация сотрудника (is_active=false) ретроактивна — прошлые
              -- начисления уходят из ФОТ и выплаты возвращаются в opex за ВСЮ
              -- историю: журнала активности по датам нет, юзер пока не просил.
              SELECT 1 FROM payroll_employee pe
              WHERE pe.project_id = :project_id
                AND pe.counterparty_id = t.counterparty_id
                AND pe.is_deleted = false
                AND pe.is_active = true
                AND (
                    EXISTS (
                        SELECT 1 FROM payroll_team_member ptm
                        JOIN payroll_team pt ON pt.id = ptm.team_id
                        WHERE ptm.employee_id = pe.id
                          AND pt.is_deleted = false
                    )
                    -- NULL (нет периодов) → сравнение не истинно → не исключаем
                    OR t.date >= (
                        SELECT MIN(psp.valid_from) FROM payroll_salary_period psp
                        WHERE psp.employee_id = pe.id
                    )
                )
          )
        GROUP BY month_key, c.primary_type
        HAVING COALESCE(SUM(t.expense), 0) > 0
    """  # noqa: S608


def build_brand_nm_ids_sql() -> str:
    return (
        f"SELECT DISTINCT nm_id FROM wb_finance_rows"  # noqa: S608
        f" WHERE project_id = :project_id AND {_DATE_FILTER}"
        f" AND brand_name = :brand AND nm_id IS NOT NULL AND nm_id != 0"
    )


def build_article_nm_ids_sql() -> str:
    return (
        f"SELECT DISTINCT nm_id FROM wb_finance_rows"  # noqa: S608
        f" WHERE project_id = :project_id AND {_DATE_FILTER}"
        f" AND LOWER(sa_name) LIKE :article_like ESCAPE '\\' AND nm_id IS NOT NULL AND nm_id != 0"
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
    parent_key=None,
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
        "parent_key": parent_key,
        "total": round(float(values_total), 2),
        "total_pct": total_pct,
        "monthly": monthly,
        "monthly_pct": monthly_pct,
    }
