"""
Tests for the COGS («себестоимость») exclusion in expense reports
(backend/services/reports/_cogs.py + dashboard expense breakdown).

Expense categories flagged CategoryRef.is_cogs are excluded from expense
aggregations; income and balances are unaffected.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models.counterparty import Counterparty
from backend.models.refs import CategoryRef
from backend.models.transactions import Transaction
from backend.services.reports._cogs import cogs_include_clause, get_cogs_keys
from backend.services.reports.dashboard import _compute_expense_by_type


async def _project(client, auth_headers) -> int:
    r = await client.post(
        "/api/v1/projects", json={"name": f"cogs_{uuid.uuid4().hex[:6]}"}, headers=auth_headers
    )
    return r.json()["id"]


def _exp(pid: int, cat1: str, cat2: str | None, amount: str, **kw) -> Transaction:
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
        cat_lvl1_2=cat1,
        cat_lvl2_2=cat2,
        is_cashflow2=1,
        status="OK",
    )
    base.update(kw)
    return Transaction(**base)


@pytest.mark.asyncio
async def test_get_cogs_keys_splits_lvl1_and_pairs(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    db_session.add_all([
        CategoryRef(project_id=pid, direction="expense", cat_lvl1="Таможня", cat_lvl2="", is_cogs=True),
        CategoryRef(project_id=pid, direction="expense", cat_lvl1="Логистика", cat_lvl2="Доставка", is_cogs=True),
        CategoryRef(project_id=pid, direction="expense", cat_lvl1="Реклама", cat_lvl2="", is_cogs=False),
        # income-direction flag must be ignored (COGS is an expense concept)
        CategoryRef(project_id=pid, direction="income", cat_lvl1="Выручка", cat_lvl2="", is_cogs=True),
    ])
    await db_session.commit()

    lvl1, pairs = await get_cogs_keys(db_session, pid)
    assert lvl1 == {"Таможня"}
    assert pairs == {("Логистика", "Доставка")}


@pytest.mark.asyncio
async def test_cogs_include_clause_lvl1_and_pair(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    db_session.add_all([
        _exp(pid, "Таможня", None, "1000"),         # lvl1 COGS → excluded
        _exp(pid, "Таможня", "Пошлина", "300"),     # lvl1 COGS (any sub) → excluded
        _exp(pid, "Логистика", "Доставка", "500"),  # exact pair COGS → excluded
        _exp(pid, "Логистика", "Прочее", "200"),    # sibling pair → KEPT
        _exp(pid, "Реклама", None, "400"),          # not flagged → KEPT
    ])
    await db_session.commit()

    clause = cogs_include_clause({"Таможня"}, {("Логистика", "Доставка")})
    rows = (await db_session.execute(
        select(Transaction.cat_lvl1_2, Transaction.cat_lvl2_2, Transaction.expense).where(
            Transaction.project_id == pid, clause
        )
    )).all()
    kept = {(r.cat_lvl1_2, r.cat_lvl2_2) for r in rows}
    assert kept == {("Логистика", "Прочее"), ("Реклама", None)}
    assert sum(float(r.expense) for r in rows) == 600.0  # 200 + 400


@pytest.mark.asyncio
async def test_cogs_does_not_exclude_income(db_session, client, auth_headers):
    """An income row (refund) carrying a COGS expense-category name is KEPT;
    only the expense row of that category is excluded."""
    pid = await _project(client, auth_headers)
    db_session.add_all([
        _exp(pid, "Таможня", None, "1000"),  # COGS expense → excluded
        Transaction(
            project_id=pid, date=date(2026, 6, 15), bank="WB_BANK",
            account="40702810800000001893", currency="RUB",
            income=Decimal("700"), expense=Decimal("0"), net=Decimal("700"),
            txn_id=f"t_{uuid.uuid4().hex[:12]}", cat_lvl1_2="Таможня", cat_lvl2_2=None,
            is_cashflow2=1, status="OK",
        ),
    ])
    await db_session.commit()

    clause = cogs_include_clause({"Таможня"}, set())
    rows = (await db_session.execute(
        select(Transaction.income, Transaction.expense).where(Transaction.project_id == pid, clause)
    )).all()
    assert len(rows) == 1                  # expense COGS row dropped
    assert float(rows[0].income) == 700.0  # income refund kept


@pytest.mark.asyncio
async def test_cogs_clause_empty_is_noop(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    db_session.add(_exp(pid, "Таможня", None, "1000"))
    await db_session.commit()
    clause = cogs_include_clause(set(), set())
    rows = (await db_session.execute(
        select(Transaction).where(Transaction.project_id == pid, clause)
    )).scalars().all()
    assert len(rows) == 1  # nothing flagged → nothing excluded


@pytest.mark.asyncio
async def test_expense_by_type_excludes_cogs(db_session, client, auth_headers):
    pid = await _project(client, auth_headers)
    cp = Counterparty(project_id=pid, inn=None, name="ФТС", primary_type="GOVERNMENT")
    db_session.add(cp)
    await db_session.flush()
    db_session.add_all([
        _exp(pid, "Таможня", None, "1000", counterparty_id=cp.id),
        _exp(pid, "Реклама", None, "400", counterparty_id=cp.id),
    ])
    await db_session.commit()

    clause = cogs_include_clause({"Таможня"}, set())
    out = await _compute_expense_by_type(db_session, pid, date(2026, 6, 1), date(2026, 6, 30), 1.0, clause)
    cats = {c["name"] for grp in out for c in grp["categories"]}
    assert "Реклама" in cats
    assert "Таможня" not in cats
    assert sum(grp["value"] for grp in out) == 400.0
