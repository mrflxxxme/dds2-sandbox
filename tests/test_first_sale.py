"""
Tests for backend/services/cost/first_sale.py — first_sale_date backfill from
local WbFunnelDaily aggregates.

DB interactions are mocked at the service layer (AsyncMock for AsyncSession.execute/
commit) so tests don't depend on schema or fixtures.

Covered cases:
- empty candidates: no nm_ids in DB → no funnel query, no commit, zero counts
- happy path: 2 candidates, funnel returns MIN(date) per nm → 2 updates
- empty funnel: candidates exist but no funnel rows match → 0 updates, commit runs
- only_missing flag: True adds `first_sale_date IS NULL`; False omits it
- multi-tenancy: project_id appears in SELECT and every UPDATE
- funnel filter: query restricts to orders_count > 0 and candidate nm_ids
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

# ─── Mock helpers ────────────────────────────────────────────────────────────


def _mock_select_scalars_result(values: list):
    """Mock for `result.scalars().all() -> list` (used for nomenclature SELECT)."""
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    result.scalars.return_value = scalars
    return result


def _mock_funnel_result(rows: list[tuple[int, date]]):
    """Mock for `result.all() -> list[(nm_id, first_sale)]` (funnel GROUP BY)."""
    result = MagicMock()
    result.all.return_value = rows
    return result


def _mock_update_result(rowcount: int = 1):
    result = MagicMock()
    result.rowcount = rowcount
    return result


# ─── backfill_first_sale_dates ───────────────────────────────────────────────


class TestEmptyCandidates:
    """No nm_ids needing update → no funnel query, no commit, zero counts."""

    @pytest.mark.asyncio
    async def test_no_candidates_skips_funnel_and_commit(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_mock_select_scalars_result([]))
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        result = await backfill_first_sale_dates(mock_db, project_id=42)

        assert result == {"candidates": 0, "matched": 0, "updated": 0}
        # Only one statement executed (the candidate SELECT). No funnel SELECT, no UPDATEs.
        assert mock_db.execute.await_count == 1
        mock_db.commit.assert_not_awaited()


class TestHappyPath:
    """2 candidates, funnel returns MIN(date) per nm → 2 updates."""

    @pytest.mark.asyncio
    async def test_min_date_aggregation(self):
        candidate_select = _mock_select_scalars_result([100, 200])
        funnel_select = _mock_funnel_result(
            [
                (100, date(2024, 1, 10)),
                (200, date(2024, 3, 22)),
            ]
        )
        update_results = [_mock_update_result(1), _mock_update_result(1)]

        execute_calls: list = []

        async def execute_side_effect(stmt):
            execute_calls.append(stmt)
            if len(execute_calls) == 1:
                return candidate_select
            if len(execute_calls) == 2:
                return funnel_select
            return update_results.pop(0)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        result = await backfill_first_sale_dates(mock_db, project_id=42)

        assert result == {"candidates": 2, "matched": 2, "updated": 2}
        # 1 candidate SELECT + 1 funnel SELECT + 2 UPDATEs.
        assert mock_db.execute.await_count == 4
        mock_db.commit.assert_awaited_once()


class TestEmptyFunnel:
    """Candidates exist but no funnel rows match (no orders ever) → 0 updates."""

    @pytest.mark.asyncio
    async def test_no_funnel_rows_for_candidates(self):
        candidate_select = _mock_select_scalars_result([100, 200])
        funnel_select = _mock_funnel_result([])  # nothing matched

        execute_calls: list = []

        async def execute_side_effect(stmt):
            execute_calls.append(stmt)
            if len(execute_calls) == 1:
                return candidate_select
            return funnel_select

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        result = await backfill_first_sale_dates(mock_db, project_id=42)

        assert result == {"candidates": 2, "matched": 0, "updated": 0}
        # Only 2 SELECTs, no UPDATEs.
        assert mock_db.execute.await_count == 2
        mock_db.commit.assert_awaited_once()


class TestOnlyMissingFlag:
    """only_missing=True → SELECT has `first_sale_date IS NULL`. False → omitted."""

    @pytest.mark.asyncio
    async def test_only_missing_true_includes_null_filter(self):
        captured: list = []

        async def execute_side_effect(stmt):
            captured.append(stmt)
            return _mock_select_scalars_result([])

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        await backfill_first_sale_dates(mock_db, project_id=42, only_missing=True)

        sql_text = str(captured[0]).lower()
        assert "first_sale_date is null" in sql_text

    @pytest.mark.asyncio
    async def test_only_missing_false_omits_null_filter(self):
        captured: list = []

        async def execute_side_effect(stmt):
            captured.append(stmt)
            return _mock_select_scalars_result([])

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        await backfill_first_sale_dates(mock_db, project_id=42, only_missing=False)

        sql_text = str(captured[0]).lower()
        assert "first_sale_date is null" not in sql_text


class TestMultiTenancy:
    """project_id must be in SELECT (both candidate + funnel) and every UPDATE."""

    @pytest.mark.asyncio
    async def test_project_id_in_all_statements(self):
        candidate_select = _mock_select_scalars_result([100])
        funnel_select = _mock_funnel_result([(100, date(2024, 1, 10))])
        captured: list = []

        async def execute_side_effect(stmt):
            captured.append(stmt)
            if len(captured) == 1:
                return candidate_select
            if len(captured) == 2:
                return funnel_select
            return _mock_update_result(1)

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        await backfill_first_sale_dates(mock_db, project_id=77)

        for stmt in captured:
            assert "project_id" in str(stmt).lower()


class TestFunnelOrdersFilter:
    """Funnel SELECT must filter `orders_count > 0` so empty/zero days don't count."""

    @pytest.mark.asyncio
    async def test_orders_count_filter_in_funnel_query(self):
        candidate_select = _mock_select_scalars_result([100])
        funnel_select = _mock_funnel_result([])
        captured: list = []

        async def execute_side_effect(stmt):
            captured.append(stmt)
            if len(captured) == 1:
                return candidate_select
            return funnel_select

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.commit = AsyncMock()

        from backend.services.cost.first_sale import backfill_first_sale_dates

        await backfill_first_sale_dates(mock_db, project_id=42)

        # Second statement is the funnel SELECT — must restrict to orders_count > 0.
        funnel_sql = str(captured[1]).lower()
        assert "orders_count" in funnel_sql
        assert "min" in funnel_sql  # MIN(date) aggregation
        assert "group by" in funnel_sql
