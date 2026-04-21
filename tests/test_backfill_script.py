"""
Tests for backfill_counterparties.py script.
Seeds minimal data and verifies idempotency.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"backfill_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


# ─── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_creates_counterparties(db_session, client, auth_headers):
    """Backfill run creates counterparties from transaction INNs."""
    from sqlalchemy import select

    from backend.models.counterparty import Counterparty
    from backend.models.transactions import Transaction

    project_id = await _create_project(client, auth_headers)
    inn1 = _inn()
    inn2 = _inn()

    # Seed transactions with INNs
    for inn in [inn1, inn2]:
        for i in range(3):
            tx = Transaction(
                project_id=project_id,
                date=date(2026, 1, i + 1),
                bank="WB",
                account="40702810800000001893",
                currency="RUB",
                income=Decimal("10000"),
                expense=Decimal("0"),
                net=Decimal("10000"),
                purpose=f"Тест {inn}",
                inn=inn,
                txn_id=f"txn_{uuid.uuid4().hex[:12]}",
                status="OK",
                is_cashflow2=1,
            )
            db_session.add(tx)
    await db_session.commit()

    # Run backfill
    from scripts.backfill_counterparties import run_backfill

    stats = await run_backfill(db_session, project_id=project_id, dry_run=False)

    assert stats["created"] >= 2
    # Check rows created
    result = await db_session.execute(
        select(Counterparty).where(
            Counterparty.project_id == project_id,
            Counterparty.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
    )
    cps = result.scalars().all()
    cp_inns = {cp.inn for cp in cps}
    assert inn1 in cp_inns
    assert inn2 in cp_inns


@pytest.mark.asyncio
async def test_backfill_idempotent(db_session, client, auth_headers):
    """Second backfill run creates 0 counterparties (idempotent)."""
    from backend.models.transactions import Transaction

    project_id = await _create_project(client, auth_headers)
    inn = _inn()

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 1, 1),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("10000"),
        expense=Decimal("0"),
        net=Decimal("10000"),
        purpose="Тест идемпотентность",
        inn=inn,
        txn_id=f"txn_{uuid.uuid4().hex[:12]}",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()

    from scripts.backfill_counterparties import run_backfill

    stats1 = await run_backfill(db_session, project_id=project_id, dry_run=False)
    assert stats1["created"] >= 1

    stats2 = await run_backfill(db_session, project_id=project_id, dry_run=False)
    assert stats2["created"] == 0


@pytest.mark.asyncio
async def test_backfill_known_inn_sets_type(db_session, client, auth_headers):
    """Backfill sets primary_type for known INNs (ФНС → GOVERNMENT)."""
    from sqlalchemy import select

    from backend.models.counterparty import Counterparty
    from backend.models.transactions import Transaction

    project_id = await _create_project(client, auth_headers)
    fns_inn = "7727406020"  # ФНС

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 1, 1),
        bank="VTB",
        account="40702810400810052145",
        currency="RUB",
        income=Decimal("0"),
        expense=Decimal("50000"),
        net=Decimal("-50000"),
        purpose="Уплата налога НДС",
        inn=fns_inn,
        txn_id=f"txn_{uuid.uuid4().hex[:12]}",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()

    from scripts.backfill_counterparties import run_backfill

    await run_backfill(db_session, project_id=project_id, dry_run=False)

    result = await db_session.execute(
        select(Counterparty).where(
            Counterparty.project_id == project_id,
            Counterparty.inn == fns_inn,
            Counterparty.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
    )
    fns_cp = result.scalar_one_or_none()
    assert fns_cp is not None
    assert fns_cp.primary_type == "GOVERNMENT"


@pytest.mark.asyncio
async def test_backfill_dry_run_no_writes(db_session, client, auth_headers):
    """dry_run=True makes no changes to DB."""
    from sqlalchemy import func, select

    from backend.models.counterparty import Counterparty
    from backend.models.transactions import Transaction

    project_id = await _create_project(client, auth_headers)
    inn = _inn()

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 1, 1),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("10000"),
        expense=Decimal("0"),
        net=Decimal("10000"),
        purpose="Тест dry-run",
        inn=inn,
        txn_id=f"txn_{uuid.uuid4().hex[:12]}",
        status="OK",
        is_cashflow2=1,
    )
    db_session.add(tx)
    await db_session.commit()

    # Count before
    before = (
        await db_session.execute(select(func.count(Counterparty.id)).where(Counterparty.project_id == project_id))
    ).scalar()

    from scripts.backfill_counterparties import run_backfill

    stats = await run_backfill(db_session, project_id=project_id, dry_run=True)

    # Count after — should be same
    after = (
        await db_session.execute(select(func.count(Counterparty.id)).where(Counterparty.project_id == project_id))
    ).scalar()

    assert before == after
    assert stats["dry_run"] is True


@pytest.mark.asyncio
async def test_backfill_links_transactions(db_session, client, auth_headers):
    """Backfill sets counterparty_id on transactions with matching INN."""

    from backend.models.transactions import Transaction

    project_id = await _create_project(client, auth_headers)
    inn = _inn()

    tx = Transaction(
        project_id=project_id,
        date=date(2026, 1, 1),
        bank="WB",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("5000"),
        expense=Decimal("0"),
        net=Decimal("5000"),
        purpose="Тест привязка КА",
        inn=inn,
        txn_id=f"txn_{uuid.uuid4().hex[:12]}",
        status="OK",
        is_cashflow2=1,
        counterparty_id=None,
    )
    db_session.add(tx)
    await db_session.commit()

    from scripts.backfill_counterparties import run_backfill

    stats = await run_backfill(db_session, project_id=project_id, dry_run=False)

    assert stats["linked_transactions"] >= 1

    await db_session.refresh(tx)
    assert tx.counterparty_id is not None
