"""
Tests for the dashboard income-type split (marketplace / financing / other).

Covers backend/services/reports/dashboard.py::_compute_income_by_type — the
classifier that lets the dashboard show real marketplace payouts separately from
financing inflows (loan tranches), with inter-account transfers (is_cashflow2=0)
excluded.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from backend.models.transactions import Transaction
from backend.services.reports.dashboard import _compute_income_by_type


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"inc_type_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


def _txn(project_id: int, **kw) -> Transaction:
    base = dict(
        project_id=project_id,
        date=date(2026, 6, 15),
        bank="WB_BANK",
        account="40702810800000001893",
        currency="RUB",
        income=Decimal("0"),
        expense=Decimal("0"),
        txn_id=f"txn_{uuid.uuid4().hex[:12]}",
        status="OK",
        is_cashflow2=1,
    )
    base.update(kw)
    base["net"] = base["income"] - base["expense"]
    return Transaction(**base)


@pytest.mark.asyncio
async def test_income_by_type_split(db_session, client, auth_headers):
    """Income rows classify into marketplace / financing / other; internal excluded."""
    pid = await _create_project(client, auth_headers)
    db_session.add_all([
        # marketplace — by counterparty name (РВБ)
        _txn(pid, counterparty="РВБ ООО", income=Decimal("100000"), purpose="оплата по договору за товар"),
        # marketplace — by counterparty→category mapping
        _txn(pid, counterparty="ИП Магазин", cat_lvl1_2="Маркетплейсы", income=Decimal("50000"), purpose="за товар"),
        # financing — by purpose (транш)
        _txn(pid, counterparty="", income=Decimal("6000000"),
             purpose="Выдача транша № 03 по договору РЛ-21/26", date=date(2026, 6, 16)),
        # other
        _txn(pid, counterparty="ООО Ромашка", income=Decimal("7000"), purpose="прочее поступление"),
        # EXCLUDED: internal transfer (is_cashflow2=0) must not count even though it looks marketplace
        _txn(pid, counterparty="РВБ ООО", income=Decimal("999999"), is_cashflow2=0, purpose="за товар"),
    ])
    await db_session.commit()

    daily, totals = await _compute_income_by_type(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 30), {}, None
    )

    by_key = {t["key"]: t for t in totals}
    assert by_key["marketplace"]["value"] == 150000.0  # 100k + 50k, NOT the 999999 internal
    assert by_key["marketplace"]["count"] == 2
    assert by_key["financing"]["value"] == 6000000.0
    assert by_key["financing"]["count"] == 1
    assert by_key["other"]["value"] == 7000.0
    assert by_key["other"]["count"] == 1

    # Daily split: 15th = marketplace 150k, 16th = financing 6M
    dmap = {d["date"]: d for d in daily}
    assert dmap["2026-06-15"]["marketplace"] == 150000.0
    assert dmap["2026-06-15"]["financing"] == 0.0
    assert dmap["2026-06-16"]["financing"] == 6000000.0
    assert dmap["2026-06-16"]["marketplace"] == 0.0


@pytest.mark.asyncio
async def test_income_by_type_empty(db_session, client, auth_headers):
    """No income rows → empty daily series and totals (no zero-count buckets)."""
    pid = await _create_project(client, auth_headers)
    daily, totals = await _compute_income_by_type(
        db_session, pid, date(2026, 6, 1), date(2026, 6, 30), {}, None
    )
    assert daily == []
    assert totals == []
