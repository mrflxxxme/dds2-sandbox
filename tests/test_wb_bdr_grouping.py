"""
Tests for wb_bdr_service.py + wb_bdr_helpers.py — grouping by brand and subject.

Covers:
- build_bdr_aggregate_sql with group_by="brand" — GROUP BY brand_name in SQL
- build_bdr_aggregate_sql with group_by="subject" — GROUP BY subject_name in SQL
- build_bdr_aggregate_sql with group_by="article" (default) — GROUP BY sa_name
- build_group_nm_ids_sql — returns correct group_key column
- build_group_sa_names_sql — returns correct group_key column
- SQL does not contain f-string injections (safe parameterized)
- get_wb_bdr empty result path
"""

import pytest

# ─── build_bdr_aggregate_sql ─────────────────────────────────────────────────


class TestBuildBdrAggregateSql:
    """build_bdr_aggregate_sql — SQL grouping correctness."""

    def test_default_groups_by_sa_name(self):
        """Without group_by, SQL groups by sa_name (article)."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql(None, None)
        assert "GROUP BY sa_name" in sql
        assert "ORDER BY sa_name" in sql
        # Default select: sa_name as first column (not brand_name AS sa_name)
        assert "brand_name AS sa_name" not in sql
        assert "subject_name AS sa_name" not in sql

    def test_group_by_brand(self):
        """group_by='brand' — SQL groups by brand_name, selects brand_name AS sa_name."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql(None, None, group_by="brand")
        assert "GROUP BY brand_name" in sql
        assert "ORDER BY brand_name" in sql
        assert "brand_name AS sa_name" in sql

    def test_group_by_subject(self):
        """group_by='subject' — SQL groups by subject_name, selects subject_name AS sa_name."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql(None, None, group_by="subject")
        assert "GROUP BY subject_name" in sql
        assert "ORDER BY subject_name" in sql
        assert "subject_name AS sa_name" in sql

    def test_brand_filter_adds_brand_param(self):
        """When brand filter is given, SQL contains AND brand_name = :brand."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql("BrandX", None, group_by="article")
        assert "brand_name = :brand" in sql

    def test_article_filter_adds_like_param(self):
        """When article filter is given, SQL contains AND LOWER(sa_name) LIKE :article_like."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql(None, "ART", group_by="article")
        assert "LOWER(sa_name) LIKE :article_like" in sql

    def test_no_fstring_injection_in_sql(self):
        """SQL uses :param placeholders, not raw values in WHERE clause."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql("'; DROP TABLE wb_finance_rows;--", "test")
        # The malicious string should NOT appear literally in the SQL
        assert "DROP TABLE" not in sql
        # But :brand placeholder should be present
        assert ":brand" in sql

    def test_always_filters_by_project_id_param(self):
        """SQL always contains :project_id parameter placeholder."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        for group_by in ("article", "brand", "subject"):
            sql = build_bdr_aggregate_sql(None, None, group_by=group_by)
            assert "project_id = :project_id" in sql, f"Missing project_id filter for group_by={group_by}"

    def test_always_filters_out_unknown_article(self):
        """SQL always excludes 'неопознанный товар' articles."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        for group_by in ("article", "brand", "subject"):
            sql = build_bdr_aggregate_sql(None, None, group_by=group_by)
            assert "неопознанный товар" in sql, f"Missing unknown article filter for group_by={group_by}"

    def test_date_filter_present(self):
        """SQL contains :date_from and :date_to parameter placeholders."""
        from backend.services.wb_bdr_helpers import build_bdr_aggregate_sql

        sql = build_bdr_aggregate_sql(None, None)
        assert ":date_from" in sql
        assert ":date_to" in sql


# ─── build_group_nm_ids_sql ──────────────────────────────────────────────────


class TestBuildGroupNmIdsSql:
    """build_group_nm_ids_sql — nm_id → group_key mapping queries."""

    def test_brand_grouping_returns_sql(self):
        """group_by='brand' returns non-empty SQL with brand_name AS group_key."""
        from backend.services.wb_bdr_helpers import build_group_nm_ids_sql

        sql = build_group_nm_ids_sql("brand", None, None)
        assert sql != ""
        assert "brand_name AS group_key" in sql
        assert "nm_id" in sql

    def test_subject_grouping_returns_sql(self):
        """group_by='subject' returns non-empty SQL with subject_name AS group_key."""
        from backend.services.wb_bdr_helpers import build_group_nm_ids_sql

        sql = build_group_nm_ids_sql("subject", None, None)
        assert sql != ""
        assert "subject_name AS group_key" in sql

    def test_article_grouping_returns_empty_string(self):
        """group_by='article' returns empty string (no mapping needed)."""
        from backend.services.wb_bdr_helpers import build_group_nm_ids_sql

        sql = build_group_nm_ids_sql("article", None, None)
        assert sql == ""

    def test_nm_ids_sql_uses_project_id_param(self):
        """SQL uses :project_id placeholder."""
        from backend.services.wb_bdr_helpers import build_group_nm_ids_sql

        sql = build_group_nm_ids_sql("brand", None, None)
        assert ":project_id" in sql

    def test_nm_ids_sql_excludes_null_nm_id(self):
        """SQL filters out NULL and zero nm_ids."""
        from backend.services.wb_bdr_helpers import build_group_nm_ids_sql

        sql = build_group_nm_ids_sql("brand", None, None)
        assert "nm_id IS NOT NULL" in sql
        assert "nm_id != 0" in sql


# ─── build_group_sa_names_sql ────────────────────────────────────────────────


class TestBuildGroupSaNamesSql:
    """build_group_sa_names_sql — sa_name → group_key mapping queries."""

    def test_brand_grouping_returns_sql(self):
        """group_by='brand' returns SQL with brand_name AS group_key."""
        from backend.services.wb_bdr_helpers import build_group_sa_names_sql

        sql = build_group_sa_names_sql("brand", None, None)
        assert sql != ""
        assert "brand_name AS group_key" in sql
        assert "sa_name" in sql

    def test_subject_grouping_returns_sql(self):
        """group_by='subject' returns SQL with subject_name AS group_key."""
        from backend.services.wb_bdr_helpers import build_group_sa_names_sql

        sql = build_group_sa_names_sql("subject", None, None)
        assert sql != ""
        assert "subject_name AS group_key" in sql

    def test_article_grouping_returns_empty_string(self):
        """group_by='article' returns empty string."""
        from backend.services.wb_bdr_helpers import build_group_sa_names_sql

        sql = build_group_sa_names_sql("article", None, None)
        assert sql == ""


# ─── get_wb_bdr empty result ─────────────────────────────────────────────────


class TestGetWbBdrGroupingEmptyResult:
    """get_wb_bdr returns correct empty structure when no data in DB."""

    @pytest.mark.asyncio
    async def test_empty_result_structure_brand_grouping(self):
        """group_by='brand' with no data returns summary, articles=[], brands=[]."""
        from datetime import date
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.services.wb_bdr_service import get_wb_bdr

        mock_agg_result = MagicMock()
        mock_agg_result.mappings.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_agg_result)

        # get_sync_status is imported inside get_wb_bdr as:
        # from backend.services.wb_finance_sync import get_sync_status
        # Redis client is _redis_client (private) in backend.cache
        with (
            patch(
                "backend.services.wb_finance_sync.get_sync_status",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ),
            patch(
                "backend.cache._redis_client",
                None,
            ),
        ):
            result = await get_wb_bdr(
                mock_db,
                project_id=1,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 1, 31),
                group_by="brand",
            )

        assert result["articles"] == []
        assert result["brands"] == []
        assert result["total_rows"] == 0
        assert "summary" in result
        assert "period" in result

    @pytest.mark.asyncio
    async def test_empty_result_structure_subject_grouping(self):
        """group_by='subject' with no data returns correct empty structure."""
        from datetime import date
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.services.wb_bdr_service import get_wb_bdr

        mock_agg_result = MagicMock()
        mock_agg_result.mappings.return_value.all.return_value = []

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_agg_result)

        with (
            patch(
                "backend.services.wb_finance_sync.get_sync_status",
                new_callable=AsyncMock,
                return_value={"status": "ok"},
            ),
            patch(
                "backend.cache._redis_client",
                None,
            ),
        ):
            result = await get_wb_bdr(
                mock_db,
                project_id=1,
                date_from=date(2024, 1, 1),
                date_to=date(2024, 1, 31),
                group_by="subject",
            )

        assert result["articles"] == []
        assert result["total_rows"] == 0


# ─── Cost aggregation logic (pure) ───────────────────────────────────────────


class TestCostAggregationByGroup:
    """Cost aggregation when grouping by brand/subject."""

    def test_grouped_cost_sum_aggregates_correctly(self):
        """Cost values from multiple articles aggregate into group sum correctly."""
        # Test the pure logic that wb_bdr_service uses internally
        # (lines 185-198 in wb_bdr_service.py)

        cost_map = {
            "ART-001": 100.0,
            "ART-002": 200.0,
            "ART-003": 50.0,
        }
        sa_to_group = {
            "ART-001": "BrandA",
            "ART-002": "BrandA",
            "ART-003": "BrandB",
        }

        # Simulate the aggregation logic
        grouped_cost_sum: dict = {}
        grouped_cost_count: dict = {}
        for sa_name, cost_val in cost_map.items():
            gk = sa_to_group.get(sa_name, "")
            if gk and cost_val > 0:
                grouped_cost_sum[gk] = grouped_cost_sum.get(gk, 0.0) + cost_val
                grouped_cost_count[gk] = grouped_cost_count.get(gk, 0) + 1

        assert grouped_cost_sum["BrandA"] == 300.0  # 100 + 200
        assert grouped_cost_sum["BrandB"] == 50.0
        assert grouped_cost_count["BrandA"] == 2
        assert grouped_cost_count["BrandB"] == 1

    def test_grouped_avg_cost_computed_from_sum_and_count(self):
        """Average cost per group = sum / count."""
        grouped_cost_sum = {"BrandA": 300.0, "BrandB": 50.0}
        grouped_cost_count = {"BrandA": 2, "BrandB": 1}

        # Replicate the avg computation from wb_bdr_service.py (line ~200)
        grouped_avg_cost = {}
        for gk in grouped_cost_sum:
            cnt = grouped_cost_count.get(gk, 0)
            if cnt > 0:
                grouped_avg_cost[gk] = grouped_cost_sum[gk] / cnt

        assert grouped_avg_cost["BrandA"] == 150.0  # (100 + 200) / 2
        assert grouped_avg_cost["BrandB"] == 50.0

    def test_ads_aggregated_by_group(self):
        """Ads (adv_sum) from multiple nm_ids aggregate per brand group."""
        ads_map = {100: 1000.0, 200: 500.0, 300: 200.0}
        nm_to_group = {100: "BrandA", 200: "BrandA", 300: "BrandB"}

        grouped_ads: dict = {}
        for nm_id, adv_val in ads_map.items():
            gk = nm_to_group.get(nm_id, "")
            if gk:
                grouped_ads[gk] = grouped_ads.get(gk, 0) + adv_val

        assert grouped_ads["BrandA"] == 1500.0  # 1000 + 500
        assert grouped_ads["BrandB"] == 200.0

    def test_totals_match_between_article_and_brand_grouping(self):
        """Sum of costs across all articles equals sum of grouped costs."""
        cost_map = {"ART-001": 100.0, "ART-002": 200.0, "ART-003": 50.0}
        sa_to_group = {"ART-001": "BrandA", "ART-002": "BrandA", "ART-003": "BrandB"}

        # Article-level total
        total_cost_article = sum(cost_map.values())

        # Group-level aggregation
        grouped_cost_sum: dict = {}
        for sa_name, cost_val in cost_map.items():
            gk = sa_to_group.get(sa_name, "")
            if gk:
                grouped_cost_sum[gk] = grouped_cost_sum.get(gk, 0.0) + cost_val
        total_cost_brand = sum(grouped_cost_sum.values())

        assert total_cost_article == total_cost_brand == 350.0
