"""
Tests for get_sync_status period coverage logic.

Bug context (2026-04-14): the UI sync badge showed "all days synced" whenever
ANY rows existed in wb_finance_rows for the project, even when the requested
period had zero rows. Backend now returns is_period_covered for the requested
date range so the UI can show real coverage.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import delete

from backend.models.auth import Project, User
from backend.models.wb_finance import WbFinanceRow
from backend.services.wb_finance_sync import get_sync_status


async def _make_project(db_session, label: str) -> tuple[int, int]:
    """Create a throwaway user + project for the test, return (project_id, user_id)."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        username=f"wfs_{label}_{suffix}",
        email=f"wfs_{label}_{suffix}@test.local",
        password_hash="x",
    )
    db_session.add(user)
    await db_session.flush()
    proj = Project(name=f"test_{label}", slug=f"wfs-{label}-{suffix}", owner_id=user.id)
    db_session.add(proj)
    await db_session.flush()
    return proj.id, user.id


def _row(project_id: int, rrd_id: int, rr_dt: date) -> WbFinanceRow:
    return WbFinanceRow(
        project_id=project_id,
        rrd_id=rrd_id,
        realizationreport_id=1,
        date_from=rr_dt,
        date_to=rr_dt,
        rr_dt=rr_dt,
        sa_name="ART-1",
        nm_id=1,
        brand_name="Brand",
        subject_name="Subj",
        quantity=1,
        retail_price_withdisc_rub=Decimal("100"),
        retail_amount=Decimal("100"),
        ppvz_for_pay=Decimal("80"),
        ppvz_sales_commission=Decimal("20"),
        delivery_rub=Decimal("0"),
        penalty=Decimal("0"),
        additional_payment=Decimal("0"),
        storage_fee=Decimal("0"),
        acceptance=Decimal("0"),
        deduction=Decimal("0"),
        ppvz_reward=Decimal("0"),
        rebill_logistic_cost=Decimal("0"),
        ppvz_vw=Decimal("0"),
        ppvz_vw_nds=Decimal("0"),
    )


async def _cleanup(db_session, project_ids: list[int], user_ids: list[int]) -> None:
    await db_session.execute(delete(WbFinanceRow).where(WbFinanceRow.project_id.in_(project_ids)))
    await db_session.execute(delete(Project).where(Project.id.in_(project_ids)))
    await db_session.execute(delete(User).where(User.id.in_(user_ids)))
    await db_session.commit()


@pytest.mark.asyncio
async def test_period_uncovered_when_data_only_in_past(db_session):
    """Repro of prod bug: rows exist (Jan-Mar) but requested period (Apr) is empty."""
    pid, uid = await _make_project(db_session, "uncov")
    db_session.add_all(
        [
            _row(pid, 1001, date(2026, 1, 10)),
            _row(pid, 1002, date(2026, 2, 15)),
            _row(pid, 1003, date(2026, 3, 28)),
        ]
    )
    await db_session.commit()

    try:
        status = await get_sync_status(
            db_session,
            pid,
            date_from=date(2026, 4, 6),
            date_to=date(2026, 4, 12),
        )
    finally:
        await _cleanup(db_session, [pid], [uid])

    assert status["total_rows"] == 3
    assert status["period_rows"] == 0
    assert status["is_period_covered"] is False


@pytest.mark.asyncio
async def test_period_covered_when_data_includes_period(db_session):
    """Happy path: rows cover the requested period."""
    pid, uid = await _make_project(db_session, "cov")
    db_session.add_all(
        [
            _row(pid, 2001, date(2026, 4, 6)),
            _row(pid, 2002, date(2026, 4, 9)),
            _row(pid, 2003, date(2026, 4, 12)),
        ]
    )
    await db_session.commit()

    try:
        status = await get_sync_status(
            db_session,
            pid,
            date_from=date(2026, 4, 6),
            date_to=date(2026, 4, 12),
        )
    finally:
        await _cleanup(db_session, [pid], [uid])

    assert status["period_rows"] == 3
    assert status["is_period_covered"] is True


@pytest.mark.asyncio
async def test_period_partial_coverage_is_uncovered(db_session):
    """If max(rr_dt) < date_to, the period is NOT fully covered."""
    pid, uid = await _make_project(db_session, "partial")
    db_session.add_all(
        [
            _row(pid, 3001, date(2026, 4, 6)),
            _row(pid, 3002, date(2026, 4, 8)),
        ]
    )
    await db_session.commit()

    try:
        status = await get_sync_status(
            db_session,
            pid,
            date_from=date(2026, 4, 6),
            date_to=date(2026, 4, 12),
        )
    finally:
        await _cleanup(db_session, [pid], [uid])

    assert status["period_rows"] == 2
    assert status["is_period_covered"] is False


@pytest.mark.asyncio
async def test_no_period_args_returns_legacy_shape(db_session):
    """Without date_from/date_to, period_rows and is_period_covered must be None."""
    pid, uid = await _make_project(db_session, "legacy")
    db_session.add(_row(pid, 4001, date(2026, 4, 1)))
    await db_session.commit()

    try:
        status = await get_sync_status(db_session, pid)
    finally:
        await _cleanup(db_session, [pid], [uid])

    assert status["total_rows"] == 1
    assert status["period_rows"] is None
    assert status["is_period_covered"] is None


@pytest.mark.asyncio
async def test_multi_tenant_isolation(db_session):
    """Rows in other projects must not influence the result."""
    pid_a, uid_a = await _make_project(db_session, "tenant-a")
    pid_b, uid_b = await _make_project(db_session, "tenant-b")
    db_session.add_all(
        [
            _row(pid_a, 5001, date(2026, 4, 7)),
            _row(pid_b, 5002, date(2026, 4, 7)),
            _row(pid_b, 5003, date(2026, 4, 8)),
        ]
    )
    await db_session.commit()

    try:
        status_a = await get_sync_status(
            db_session,
            pid_a,
            date_from=date(2026, 4, 6),
            date_to=date(2026, 4, 12),
        )
    finally:
        await _cleanup(db_session, [pid_a, pid_b], [uid_a, uid_b])

    assert status_a["total_rows"] == 1
    assert status_a["period_rows"] == 1
