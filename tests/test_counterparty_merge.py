"""
Tests for merging two counterparties (CounterpartyService.merge): re-point every
counterparty_id FK source→target, fill target's empty fields, rewrite merged
transactions' cp_key, archive source, re-apply the winning expense category.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.counterparty import Counterparty
from backend.models.refs import CounterpartyCategory
from backend.models.transactions import Transaction
from backend.services.counterparty_service import (
    CounterpartyConflictError,
    CounterpartyNotFoundError,
    CounterpartyService,
)


def _inn() -> str:
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _project(client, auth_headers) -> int:
    r = await client.post(
        "/api/v1/projects", json={"name": f"merge_{uuid.uuid4().hex[:6]}"}, headers=auth_headers
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
async def test_merge_reassigns_fields_txns_and_category(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    src_inn = _inn()
    target = Counterparty(project_id=pid, inn=None, name="ООО Перевозчик Главный", primary_type="OTHER")
    source = Counterparty(
        project_id=pid, inn=src_inn, name="ПЕРЕВОЗЧИК ООО", primary_type="CARRIER",
        bank_account="40700000000000000001",
    )
    db_session.add_all([target, source])
    await db_session.flush()
    tgt_key = target.name.lower()
    db_session.add_all([
        _txn(pid, src_inn, source.id, "1000"),   # source by counterparty_id
        _txn(pid, src_inn, None, "500"),         # source by cp_key only
        _txn(pid, tgt_key, target.id, "200"),    # target's own
    ])
    db_session.add(CounterpartyCategory(
        project_id=pid, cp_key=src_inn, cp_name="ПЕРЕВОЗЧИК ООО",
        counterparty_id=source.id, cat_lvl1="Логистика", cat_lvl2="Доставка",
    ))
    await db_session.commit()

    svc = CounterpartyService(db_session)
    res = await svc.merge(target_id=target.id, source_id=source.id, project_id=pid)

    assert res["moved"]["transactions"] == 2
    assert res["inn_assigned"] is True
    assert res["category_action"] == "moved"

    s = (await db_session.execute(select(Counterparty).where(Counterparty.id == source.id))).scalar_one()
    assert s.is_deleted is True

    t = (await db_session.execute(select(Counterparty).where(Counterparty.id == target.id))).scalar_one()
    assert t.inn == src_inn                       # inn moved (target was empty)
    assert t.primary_type == "CARRIER"            # filled (target was OTHER)
    assert t.bank_account == "40700000000000000001"

    txns = (await db_session.execute(select(Transaction).where(Transaction.project_id == pid))).scalars().all()
    assert len(txns) == 3
    assert all(x.counterparty_id == target.id for x in txns)
    assert all(x.cp_key == src_inn for x in txns)         # rewritten to target's key (= moved inn)
    assert all(x.cat_lvl1_2 == "Логистика" for x in txns)  # winning category propagated

    # Exactly one active category row, now pointing at target.
    cats = (await db_session.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.project_id == pid,
            CounterpartyCategory.is_deleted == False,  # noqa: E712
        )
    )).scalars().all()
    assert len(cats) == 1
    assert cats[0].counterparty_id == target.id and cats[0].cat_lvl1 == "Логистика"


@pytest.mark.asyncio
async def test_merge_collapses_to_single_category_row(db_session, client, auth_headers):
    """Target has its OWN category AND inherits source's inn → exactly one active
    category row survives (target's stale name-keyed row must not linger)."""
    pid = await _project(client, auth_headers)
    src_inn = _inn()
    target = Counterparty(project_id=pid, inn=None, name="Главный КА", primary_type="OTHER")
    source = Counterparty(project_id=pid, inn=src_inn, name="Дубль", primary_type="CARRIER")
    db_session.add_all([target, source])
    await db_session.flush()
    db_session.add_all([
        CounterpartyCategory(  # target's OWN category, keyed by its name
            project_id=pid, cp_key=target.name.lower(), cp_name=target.name,
            counterparty_id=target.id, cat_lvl1="Реклама", cat_lvl2=None,
        ),
        CounterpartyCategory(  # source's category, keyed by its inn
            project_id=pid, cp_key=src_inn, cp_name=source.name,
            counterparty_id=source.id, cat_lvl1="Логистика", cat_lvl2=None,
        ),
    ])
    await db_session.commit()

    svc = CounterpartyService(db_session)
    res = await svc.merge(target_id=target.id, source_id=source.id, project_id=pid)
    assert res["category_action"] == "kept_target"

    cats = (await db_session.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.project_id == pid,
            CounterpartyCategory.is_deleted == False,  # noqa: E712
        )
    )).scalars().all()
    assert len(cats) == 1                       # no lingering duplicate
    assert cats[0].counterparty_id == target.id
    assert cats[0].cat_lvl1 == "Реклама"        # target's category won


@pytest.mark.asyncio
async def test_merge_never_overwrites_set_target_fields(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    target = Counterparty(
        project_id=pid, inn=_inn(), name="Главный", primary_type="CARRIER", bank_account="AAA",
    )
    source = Counterparty(
        project_id=pid, inn=_inn(), name="Дубль", primary_type="SUPPLIER", bank_account="BBB",
    )
    db_session.add_all([target, source])
    await db_session.flush()
    target_inn = target.inn
    await db_session.commit()

    svc = CounterpartyService(db_session)
    res = await svc.merge(target_id=target.id, source_id=source.id, project_id=pid)

    t = (await db_session.execute(select(Counterparty).where(Counterparty.id == target.id))).scalar_one()
    assert t.bank_account == "AAA"        # not overwritten by source's BBB
    assert t.inn == target_inn            # target kept its own inn
    assert t.primary_type == "CARRIER"    # not overwritten (target wasn't OTHER)
    assert res["inn_assigned"] is False


@pytest.mark.asyncio
async def test_merge_self_raises(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    cp = Counterparty(project_id=pid, inn=_inn(), name="X", primary_type="OTHER")
    db_session.add(cp)
    await db_session.flush()
    await db_session.commit()
    svc = CounterpartyService(db_session)
    with pytest.raises(CounterpartyConflictError):
        await svc.merge(target_id=cp.id, source_id=cp.id, project_id=pid)


@pytest.mark.asyncio
async def test_merge_missing_source_raises(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    cp = Counterparty(project_id=pid, inn=_inn(), name="X", primary_type="OTHER")
    db_session.add(cp)
    await db_session.flush()
    await db_session.commit()
    svc = CounterpartyService(db_session)
    with pytest.raises(CounterpartyNotFoundError):
        await svc.merge(target_id=cp.id, source_id=999_999_999, project_id=pid)


@pytest.mark.asyncio
async def test_merge_project_isolation(db_session, client, auth_headers):
    """Source living in another project is not visible → NotFound (no cross-tenant merge)."""
    pa = await _project(client, auth_headers)
    pb = await _project(client, auth_headers)
    target = Counterparty(project_id=pb, inn=_inn(), name="B-target", primary_type="OTHER")
    source = Counterparty(project_id=pa, inn=_inn(), name="A-source", primary_type="OTHER")
    db_session.add_all([target, source])
    await db_session.flush()
    await db_session.commit()
    svc = CounterpartyService(db_session)
    with pytest.raises(CounterpartyNotFoundError):
        await svc.merge(target_id=target.id, source_id=source.id, project_id=pb)
