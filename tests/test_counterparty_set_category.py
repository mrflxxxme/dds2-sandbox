"""
Tests for setting an expense category on a counterparty and propagating it to
all of its transactions (backend/services/counterparty_service.set_expense_category).
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.counterparty import Counterparty
from backend.models.refs import CounterpartyCategory
from backend.models.transactions import Transaction
from backend.services.counterparty_service import CounterpartyConflictError, CounterpartyService


def _inn() -> str:
    """Unique INN — cp_key is GLOBALLY unique, so tests must not reuse a literal."""
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _project(client, auth_headers) -> int:
    r = await client.post(
        "/api/v1/projects", json={"name": f"catset_{uuid.uuid4().hex[:6]}"}, headers=auth_headers
    )
    return r.json()["id"]


def _txn(pid: int, cp_key: str, cp_id: int | None, amount: str, **kw) -> Transaction:
    base = dict(
        project_id=pid,
        date=date(2026, 6, 15),
        bank="WB_BANK",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("0"),
        expense=Decimal(amount),
        net=Decimal("-" + amount),
        txn_id=f"t_{uuid.uuid4().hex[:12]}",
        cp_key=cp_key,
        counterparty_id=cp_id,
        is_cashflow2=1,
        status="UNASSIGNED",
    )
    base.update(kw)
    return Transaction(**base)


@pytest.mark.asyncio
async def test_set_category_propagates_by_id_and_cpkey(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    inn = _inn()
    cp = Counterparty(project_id=pid, inn=inn, name="ООО Логист", primary_type="CARRIER")
    db_session.add(cp)
    await db_session.flush()
    db_session.add_all([
        _txn(pid, inn, cp.id, "1000"),       # linked by counterparty_id
        _txn(pid, inn, None, "500"),         # linked only by cp_key
    ])
    await db_session.commit()

    svc = CounterpartyService(db_session)
    res = await svc.set_expense_category(
        counterparty_id=cp.id, project_id=pid, cat_lvl1="Логистика", cat_lvl2="Доставка"
    )
    assert res["applied"] == 2
    assert res["cat_lvl1"] == "Логистика"

    rows = (await db_session.execute(select(Transaction).where(Transaction.project_id == pid))).scalars().all()
    assert len(rows) == 2
    assert all(t.cat_lvl1_2 == "Логистика" and t.cat_lvl2_2 == "Доставка" and t.status == "OK" for t in rows)

    cpc = (
        await db_session.execute(
            select(CounterpartyCategory).where(
                CounterpartyCategory.cp_key == inn, CounterpartyCategory.project_id == pid
            )
        )
    ).scalar_one()
    assert cpc.cat_lvl1 == "Логистика" and cpc.counterparty_id == cp.id


@pytest.mark.asyncio
async def test_set_category_clear(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    inn = _inn()
    cp = Counterparty(project_id=pid, inn=inn, name="X", primary_type="OTHER")
    db_session.add(cp)
    await db_session.flush()
    db_session.add(_txn(pid, inn, cp.id, "100"))
    await db_session.commit()

    svc = CounterpartyService(db_session)
    await svc.set_expense_category(counterparty_id=cp.id, project_id=pid, cat_lvl1="Реклама", cat_lvl2=None)
    res = await svc.set_expense_category(counterparty_id=cp.id, project_id=pid, cat_lvl1=None, cat_lvl2=None)
    assert res["cat_lvl1"] is None

    t = (await db_session.execute(select(Transaction).where(Transaction.project_id == pid))).scalar_one()
    assert t.cat_lvl1_2 is None and t.status == "UNASSIGNED"


@pytest.mark.asyncio
async def test_set_category_cross_tenant_conflict(db_session, client, auth_headers):
    """Same INN in two projects: project B gets 409 (not a hijack); A is untouched."""
    inn = _inn()
    pa = await _project(client, auth_headers)
    cpa = Counterparty(project_id=pa, inn=inn, name="A", primary_type="OTHER")
    db_session.add(cpa)
    await db_session.flush()
    svc = CounterpartyService(db_session)
    await svc.set_expense_category(counterparty_id=cpa.id, project_id=pa, cat_lvl1="A-кат", cat_lvl2=None)

    pb = await _project(client, auth_headers)
    cpb = Counterparty(project_id=pb, inn=inn, name="B", primary_type="OTHER")
    db_session.add(cpb)
    await db_session.flush()
    with pytest.raises(CounterpartyConflictError):
        await svc.set_expense_category(counterparty_id=cpb.id, project_id=pb, cat_lvl1="B-кат", cat_lvl2=None)

    # Project A's mapping is unchanged (no cross-tenant hijack).
    a_row = (
        await db_session.execute(
            select(CounterpartyCategory).where(
                CounterpartyCategory.cp_key == inn,
                CounterpartyCategory.project_id == pa,
                CounterpartyCategory.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one()
    assert a_row.cat_lvl1 == "A-кат"


@pytest.mark.asyncio
async def test_set_category_preserves_no_cashflow(db_session, client, auth_headers):
    """A non-cashflow row (internal transfer) keeps status NO_CASHFLOW after categorize."""
    pid = await _project(client, auth_headers)
    inn = _inn()
    cp = Counterparty(project_id=pid, inn=inn, name="Y", primary_type="OTHER")
    db_session.add(cp)
    await db_session.flush()
    db_session.add(_txn(pid, inn, cp.id, "100", is_cashflow2=0, status="NO_CASHFLOW"))
    await db_session.commit()

    svc = CounterpartyService(db_session)
    await svc.set_expense_category(counterparty_id=cp.id, project_id=pid, cat_lvl1="Логистика", cat_lvl2=None)
    t = (await db_session.execute(select(Transaction).where(Transaction.project_id == pid))).scalar_one()
    assert t.cat_lvl1_2 == "Логистика" and t.status == "NO_CASHFLOW"


@pytest.mark.asyncio
async def test_bulk_set_category_and_type(db_session, client, auth_headers):
    """Bulk: set category + type for many counterparties; both applied."""
    pid = await _project(client, auth_headers)
    inn1, inn2 = _inn(), _inn()
    cp1 = Counterparty(project_id=pid, inn=inn1, name="A", primary_type="OTHER")
    cp2 = Counterparty(project_id=pid, inn=inn2, name="B", primary_type="OTHER")
    db_session.add_all([cp1, cp2])
    await db_session.flush()
    db_session.add_all([_txn(pid, inn1, cp1.id, "100"), _txn(pid, inn2, cp2.id, "200")])
    await db_session.commit()

    svc = CounterpartyService(db_session)
    res = await svc.bulk_set_expense_category(
        project_id=pid,
        counterparty_ids=[cp1.id, cp2.id],
        cat_lvl1="Логистика",
        cat_lvl2=None,
        primary_type="CARRIER",
    )
    assert res["counterparties"] == 2
    assert res["transactions"] == 2

    rows = (await db_session.execute(select(Transaction).where(Transaction.project_id == pid))).scalars().all()
    assert all(t.cat_lvl1_2 == "Логистика" for t in rows)
    cps = (await db_session.execute(select(Counterparty).where(Counterparty.project_id == pid))).scalars().all()
    assert all(c.primary_type == "CARRIER" for c in cps)
