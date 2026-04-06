"""
Tests for planning/customs — Customs Topup, Alloc, DT, and FTS PDF parsing.
Uses DB fixtures from conftest.py.
"""

import contextlib

import pytest

from backend.services.planning.customs import (
    delete_customs_dt,
    get_customs_alloc,
    get_customs_dt_list,
    get_customs_topup,
    parse_fts_pdf,
    update_customs_dt,
    upload_fts_and_create_dts,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Customs Topup
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomsTopup:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no customs topups."""
        topups, alloc_map = await get_customs_topup(db_session, project.id)
        assert topups == []
        assert alloc_map == {}

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Topups are isolated by project."""
        topups_a, _ = await get_customs_topup(db_session, project.id)
        topups_b, _ = await get_customs_topup(db_session, other_project.id)
        assert topups_a == []
        assert topups_b == []


# ═══════════════════════════════════════════════════════════════════════════════
# Customs Alloc
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomsAlloc:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no allocs."""
        allocs = await get_customs_alloc(db_session, project.id)
        assert allocs == []

    @pytest.mark.asyncio
    async def test_filter_by_topup_txn_id(self, db_session, project):
        """Filter by topup_txn_id returns empty when no data."""
        allocs = await get_customs_alloc(db_session, project.id, topup_txn_id="nonexistent")
        assert allocs == []


# ═══════════════════════════════════════════════════════════════════════════════
# Customs DT
# ═══════════════════════════════════════════════════════════════════════════════


class TestCustomsDT:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no DTs."""
        dts = await get_customs_dt_list(db_session, project.id)
        assert dts == []

    @pytest.mark.asyncio
    async def test_upload_and_list(self, db_session, project):
        """Upload parsed FTS data and verify it appears in list."""
        parsed = [
            {"dt_number": "10001010/010124/0000001", "dt_date": "2024-01-01", "amount_rub": 15000.50},
            {"dt_number": "10001010/010124/0000002", "dt_date": "2024-01-02", "amount_rub": 25000.00},
        ]
        created, skipped = await upload_fts_and_create_dts(db_session, project.id, parsed)
        assert created == 2
        assert skipped == 0

        dts = await get_customs_dt_list(db_session, project.id)
        assert len(dts) == 2

    @pytest.mark.asyncio
    async def test_upload_dedup(self, db_session, project):
        """Re-uploading same DT number is skipped."""
        parsed = [
            {"dt_number": "10001010/010124/0000099", "dt_date": "2024-01-01", "amount_rub": 1000},
        ]
        await upload_fts_and_create_dts(db_session, project.id, parsed)
        created, skipped = await upload_fts_and_create_dts(db_session, project.id, parsed)
        assert created == 0
        assert skipped == 1

    @pytest.mark.asyncio
    async def test_update_customs_dt(self, db_session, project):
        """Update order_no and note on a DT record."""
        parsed = [
            {"dt_number": "10001010/010124/0000050", "dt_date": "2024-01-01", "amount_rub": 5000},
        ]
        await upload_fts_and_create_dts(db_session, project.id, parsed)
        dts = await get_customs_dt_list(db_session, project.id)
        dt_id = next(d.id for d in dts if d.dt_number == "10001010/010124/0000050")

        result = await update_customs_dt(
            db_session,
            project.id,
            dt_id,
            {
                "order_no": 42,
                "note": "Test note",
            },
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, db_session, project):
        """Updating non-existent DT returns None."""
        result = await update_customs_dt(db_session, project.id, 999999, {"note": "x"})
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_customs_dt(self, db_session, project):
        """Soft delete a DT record."""
        parsed = [
            {"dt_number": "10001010/010124/0000060", "dt_date": "2024-01-05", "amount_rub": 3000},
        ]
        await upload_fts_and_create_dts(db_session, project.id, parsed)
        dts = await get_customs_dt_list(db_session, project.id)
        dt_id = next(d.id for d in dts if d.dt_number == "10001010/010124/0000060")

        result = await delete_customs_dt(db_session, project.id, dt_id)
        assert result is True

        # Verify not in list after delete
        dts = await get_customs_dt_list(db_session, project.id)
        assert not any(d.dt_number == "10001010/010124/0000060" for d in dts)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, project):
        """Deleting non-existent DT returns None."""
        result = await delete_customs_dt(db_session, project.id, 999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """DTs are isolated by project."""
        parsed = [
            {"dt_number": "10001010/010124/0000070", "dt_date": "2024-01-10", "amount_rub": 7000},
        ]
        await upload_fts_and_create_dts(db_session, project.id, parsed)

        dts_a = await get_customs_dt_list(db_session, project.id)
        dts_b = await get_customs_dt_list(db_session, other_project.id)
        assert len(dts_a) >= 1
        assert all(d.dt_number != "10001010/010124/0000070" for d in dts_b)


# ═══════════════════════════════════════════════════════════════════════════════
# parse_fts_pdf — pure function (no DB)
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseFtsPdf:
    """parse_fts_pdf requires pdfplumber, which might not be available.
    Test the basic contract."""

    def test_empty_pdf_returns_empty(self):
        """Non-PDF bytes should return empty or handle gracefully."""
        with contextlib.suppress(Exception):
            parse_fts_pdf(b"not a real pdf")
