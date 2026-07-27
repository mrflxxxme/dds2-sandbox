"""
Tests for wb_bdr_helpers — SQL query builders and metrics computation.
Pure functions, no DB required.
"""

from decimal import Decimal

from backend.services.wb_bdr_helpers import (
    build_bdr_aggregate_sql,
    build_brands_sql_bdr,
    build_group_nm_ids_sql,
    build_group_sa_names_sql,
    build_total_count_sql,
    compute_metrics_from_sql,
    empty_metrics,
    serialize,
)

D = Decimal
ZERO = D("0")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_row(
    sale_retail=0,
    ret_retail=0,
    sale_amount=0,
    ret_amount=0,
    ppvz_sale=0,
    ppvz_ret=0,
    comp_ppvz=0,
    ppvz_comm_sale=0,
    ppvz_comm_ret=0,
    ppvz_vw_sale=0,
    ppvz_vw_ret=0,
    ppvz_vw_nds_sale=0,
    ppvz_vw_nds_ret=0,
    sale_qty=0,
    ret_qty=0,
    logistics=0,
    penalties=0,
    storage=0,
    acceptance_total=0,
    rebill=0,
    deductions_total=0,
    ad_deduction=0,
    other_deduction=0,
    loan_deduction=0,
    sa_name="test",
    nm_id=1,
    brand_name="Brand",
    subject_name="Subject",
    row_count=1,
) -> dict:
    return {
        "sa_name": sa_name,
        "nm_id": nm_id,
        "brand_name": brand_name,
        "subject_name": subject_name,
        "row_count": row_count,
        "sale_retail": sale_retail,
        "ret_retail": ret_retail,
        "sale_amount": sale_amount,
        "ret_amount": ret_amount,
        "ppvz_sale": ppvz_sale,
        "ppvz_ret": ppvz_ret,
        "comp_ppvz": comp_ppvz,
        "ppvz_comm_sale": ppvz_comm_sale,
        "ppvz_comm_ret": ppvz_comm_ret,
        "ppvz_vw_sale": ppvz_vw_sale,
        "ppvz_vw_ret": ppvz_vw_ret,
        "ppvz_vw_nds_sale": ppvz_vw_nds_sale,
        "ppvz_vw_nds_ret": ppvz_vw_nds_ret,
        "sale_qty": sale_qty,
        "ret_qty": ret_qty,
        "logistics": logistics,
        "penalties": penalties,
        "storage": storage,
        "acceptance_total": acceptance_total,
        "rebill": rebill,
        "deductions_total": deductions_total,
        "ad_deduction": ad_deduction,
        "other_deduction": other_deduction,
        "loan_deduction": loan_deduction,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# SQL Query Builders
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildBdrAggregateSql:
    """Tests for build_bdr_aggregate_sql."""

    def test_default_group_by_article(self):
        """Default grouping by article uses sa_name."""
        sql = build_bdr_aggregate_sql(brand=None, article=None)
        assert "GROUP BY sa_name" in sql
        assert "project_id = :project_id" in sql

    def test_group_by_brand(self):
        """Group by brand uses brand_name column."""
        sql = build_bdr_aggregate_sql(brand=None, article=None, group_by="brand")
        assert "GROUP BY brand_name" in sql

    def test_group_by_subject(self):
        """Group by subject uses subject_name column."""
        sql = build_bdr_aggregate_sql(brand=None, article=None, group_by="subject")
        assert "GROUP BY subject_name" in sql

    def test_brand_filter(self):
        """Brand filter adds WHERE clause."""
        sql = build_bdr_aggregate_sql(brand="TestBrand", article=None)
        assert "brand_name = :brand" in sql

    def test_article_filter(self):
        """Article filter adds LIKE clause with escape."""
        sql = build_bdr_aggregate_sql(brand=None, article="test-art")
        assert "LIKE :article_like" in sql
        assert "ESCAPE" in sql

    def test_both_filters(self):
        """Both brand and article filter applied."""
        sql = build_bdr_aggregate_sql(brand="B", article="A")
        assert "brand_name = :brand" in sql
        assert "LIKE :article_like" in sql

    def test_excludes_unknown_products(self):
        """SQL excludes rows with 'неопознанный товар'."""
        sql = build_bdr_aggregate_sql(brand=None, article=None)
        assert "неопознанный товар" in sql.lower()

    def test_period_mode_sale_default(self):
        """Default period_mode filters by sale/accrual date."""
        sql = build_bdr_aggregate_sql(brand=None, article=None)
        assert "COALESCE(sale_dt, rr_dt) BETWEEN" in sql

    def test_period_mode_report_uses_report_period(self):
        """report mode filters by WB report period, not sale date."""
        sql = build_bdr_aggregate_sql(brand=None, article=None, period_mode="report")
        assert "date_from >= :date_from AND date_to <= :date_to" in sql
        assert "COALESCE(sale_dt, rr_dt) BETWEEN" not in sql

    def test_period_mode_unknown_falls_back_to_sale(self):
        """Unknown period_mode falls back to the sale-date predicate."""
        sql = build_bdr_aggregate_sql(brand=None, article=None, period_mode="garbage")
        assert "COALESCE(sale_dt, rr_dt) BETWEEN" in sql


class TestBuildGroupNmIdsSql:
    """Tests for build_group_nm_ids_sql."""

    def test_brand_grouping(self):
        """Brand grouping returns SQL with brand_name."""
        sql = build_group_nm_ids_sql(group_by="brand", brand=None, article=None)
        assert "brand_name AS group_key" in sql

    def test_subject_grouping(self):
        """Subject grouping returns SQL with subject_name."""
        sql = build_group_nm_ids_sql(group_by="subject", brand=None, article=None)
        assert "subject_name AS group_key" in sql

    def test_article_grouping_returns_empty(self):
        """Article grouping returns empty string (no nm_id mapping needed)."""
        sql = build_group_nm_ids_sql(group_by="article", brand=None, article=None)
        assert sql == ""


class TestBuildGroupSaNamesSql:
    """Tests for build_group_sa_names_sql."""

    def test_brand_grouping(self):
        sql = build_group_sa_names_sql(group_by="brand", brand=None, article=None)
        assert "brand_name AS group_key" in sql

    def test_article_returns_empty(self):
        sql = build_group_sa_names_sql(group_by="article", brand=None, article=None)
        assert sql == ""


class TestBuildBrandsSqlBdr:
    """Tests for build_brands_sql_bdr."""

    def test_returns_brand_query(self):
        sql = build_brands_sql_bdr()
        assert "DISTINCT brand_name" in sql
        assert "project_id = :project_id" in sql


class TestBuildTotalCountSql:
    """Tests for build_total_count_sql."""

    def test_basic_count(self):
        sql = build_total_count_sql(brand=None, article=None)
        assert "COUNT(*)" in sql
        assert "project_id = :project_id" in sql

    def test_with_filters(self):
        sql = build_total_count_sql(brand="B", article="A")
        assert "brand_name = :brand" in sql
        assert "LIKE :article_like" in sql


# ═══════════════════════════════════════════════════════════════════════════════
# Metrics Computation
# ═══════════════════════════════════════════════════════════════════════════════


class TestComputeMetrics:
    """Tests for compute_metrics_from_sql."""

    def test_all_zeros(self):
        """All-zero input produces all-zero output."""
        row = _make_row()
        m = compute_metrics_from_sql(row)
        assert m["realization"] == ZERO
        assert m["sales_amount"] == ZERO
        assert m["logistics"] == ZERO
        assert m["sale_qty"] == 0

    def test_realization_formula(self):
        """realization = sale_retail - ret_retail."""
        row = _make_row(sale_retail=10000, ret_retail=2000)
        m = compute_metrics_from_sql(row)
        assert m["realization"] == D("8000")
        assert m["returns_amount"] == D("2000")

    def test_sales_amount_formula(self):
        """sales_amount = sale_amount - ret_amount."""
        row = _make_row(sale_amount=5000, ret_amount=1000)
        m = compute_metrics_from_sql(row)
        assert m["sales_amount"] == D("4000")

    def test_commission_formula(self):
        """commission = sales_amount - (ppvz_net - compensation)."""
        row = _make_row(
            sale_amount=10000,
            ret_amount=0,
            ppvz_sale=8000,
            ppvz_ret=0,
            comp_ppvz=500,
        )
        m = compute_metrics_from_sql(row)
        # commission = 10000 - (8000 - 500) = 10000 - 7500 = 2500
        assert m["commission"] == D("2500")

    def test_to_pay_formula(self):
        """to_pay = ppvz_net - logistics - penalties - storage - deductions - acceptance."""
        row = _make_row(
            ppvz_sale=10000,
            ppvz_ret=0,
            logistics=1000,
            penalties=200,
            storage=300,
            deductions_total=500,
            acceptance_total=100,
        )
        m = compute_metrics_from_sql(row)
        expected = D("10000") - D("1000") - D("200") - D("300") - D("500") - D("100")
        assert m["to_pay"] == expected

    def test_buyout_pct(self):
        """buyout_pct = sale_qty / (sale_qty + ret_qty) * 100."""
        row = _make_row(sale_qty=80, ret_qty=20)
        m = compute_metrics_from_sql(row)
        assert m["buyout_pct"] == D("80")

    def test_buyout_pct_no_qty(self):
        """Zero quantities yield 0% buyout."""
        row = _make_row(sale_qty=0, ret_qty=0)
        m = compute_metrics_from_sql(row)
        assert m["buyout_pct"] == ZERO

    def test_avg_sale_price(self):
        """avg_sale_price = sales_amount / net_sale_qty."""
        row = _make_row(sale_amount=10000, ret_amount=0, sale_qty=10, ret_qty=0)
        m = compute_metrics_from_sql(row)
        assert m["avg_sale_price"] == D("1000")

    def test_avg_sale_price_zero_qty(self):
        """Zero net qty yields 0 avg price."""
        row = _make_row(sale_amount=1000, sale_qty=5, ret_qty=5)
        m = compute_metrics_from_sql(row)
        assert m["avg_sale_price"] == ZERO

    def test_deductions_split(self):
        """operating_deductions = deductions_total - ad_deduction - loan_deduction."""
        row = _make_row(
            deductions_total=1000,
            ad_deduction=300,
            other_deduction=200,
            loan_deduction=100,
        )
        m = compute_metrics_from_sql(row)
        # operating = 1000 - 300 - 100 = 600
        assert m["deductions"] == D("600")
        assert m["ad_deduction"] == D("300")
        assert m["loan_deduction"] == D("100")
        assert m["wb_deductions"] == D("400")  # ad + loan


class TestEmptyMetrics:
    """Tests for empty_metrics helper."""

    def test_all_keys_present(self):
        m = empty_metrics()
        assert "realization" in m
        assert "logistics" in m
        assert "sale_qty" in m
        assert m["realization"] == ZERO
        assert m["sale_qty"] == 0


class TestSerialize:
    """Tests for serialize helper."""

    def test_converts_decimals(self):
        d = {"a": D("1.50"), "b": 10, "c": "text"}
        result = serialize(d)
        assert result["a"] == 1.5
        assert isinstance(result["a"], float)
        assert result["b"] == 10
        assert result["c"] == "text"
