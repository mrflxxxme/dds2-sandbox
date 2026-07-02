# ruff: noqa: RUF001
"""
Tests: SupplierDebtService.get_overview — «Заказано» (загруженные машины через
factory_order→supplier→counterparty) − «Оплачено» (выписка по counterparty_id),
+ бакет «без поставщика» для несвязанных позиций.
"""

import uuid
from decimal import Decimal

import pytest

from backend.models import Account
from backend.models.cost import CostOrder, CostOrderItem
from backend.models.counterparty import Counterparty
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem, Supplier
from backend.models.transactions import Transaction
from backend.services.supplier_debt_service import SupplierDebtService
from backend.utils.time import utcnow


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"debt_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_debt_overview_ordered_paid(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    acc = f"ACC{uuid.uuid4().hex[:12]}"
    db_session.add(Account(account=acc, bank="VTB", currency="CNY", project_id=pid))

    cp = Counterparty(project_id=pid, name="TiAmo", primary_type="SUPPLIER")
    db_session.add(cp)
    await db_session.flush()

    sup = Supplier(project_id=pid, name="TiAmo", currency="CNY", counterparty_id=cp.id)
    db_session.add(sup)
    await db_session.flush()

    fo = FactoryOrder(project_id=pid, order_number=f"FO-{uuid.uuid4().hex[:6]}", supplier_id=sup.id)
    db_session.add(fo)
    await db_session.flush()
    foi = FactoryOrderItem(project_id=pid, factory_order_id=fo.id, barcode="BC1", qty=10, price_cny=Decimal("100"))
    db_session.add(foi)
    await db_session.flush()

    # Загруженная машина (DELIVERED): 10 × 100 = 1000 заказано.
    co = CostOrder(project_id=pid, order_no=f"V-{uuid.uuid4().hex[:6]}", status=VehicleStatus.DELIVERED.value)
    db_session.add(co)
    await db_session.flush()
    db_session.add(
        CostOrderItem(
            project_id=pid,
            order_no=co.order_no,
            barcode="BC1",
            qty=10,
            price_cny=Decimal("100"),
            factory_order_item_id=foi.id,
        )
    )
    # Машина FORMING — НЕ считается в «Заказано».
    co2 = CostOrder(project_id=pid, order_no=f"V-{uuid.uuid4().hex[:6]}", status=VehicleStatus.FORMING.value)
    db_session.add(co2)
    await db_session.flush()
    db_session.add(
        CostOrderItem(
            project_id=pid,
            order_no=co2.order_no,
            barcode="BC1",
            qty=99,
            price_cny=Decimal("100"),
            factory_order_item_id=foi.id,
        )
    )
    # Позиция без factory_order-связки в загруженной машине → «без поставщика».
    db_session.add(
        CostOrderItem(project_id=pid, order_no=co.order_no, barcode="BC2", qty=5, price_cny=Decimal("100"))
    )

    # Оплата поставщику: 600 CNY.
    db_session.add(
        Transaction(
            project_id=pid,
            date=utcnow(),
            bank="VTB",
            account=acc,
            currency="CNY",
            counterparty_id=cp.id,
            expense=Decimal("600"),
            income=Decimal("0"),
            txn_id=f"t-{uuid.uuid4().hex[:10]}",
            event_type2="OPER",
            is_internal=False,
            is_fx=False,
        )
    )
    await db_session.commit()

    data = await SupplierDebtService(db_session).get_overview(pid)
    tiamo = next(i for i in data["items"] if i["counterparty_id"] == cp.id)
    assert tiamo["currency"] == "CNY"
    assert tiamo["ordered"] == Decimal("1000")
    assert tiamo["paid"] == Decimal("600")
    assert tiamo["debt"] == Decimal("400")
    assert data["unassigned_ordered"] == Decimal("500")  # 5 × 100


@pytest.mark.asyncio
async def test_debt_overview_excludes_fx_and_internal_from_paid(db_session, client, auth_headers):
    pid = await _create_project(client, auth_headers)
    acc = f"ACC{uuid.uuid4().hex[:12]}"
    db_session.add(Account(account=acc, bank="VTB", currency="CNY", project_id=pid))
    cp = Counterparty(project_id=pid, name="Палатки", primary_type="SUPPLIER")
    db_session.add(cp)
    await db_session.flush()
    for kind, kwargs in (
        ("ok", {"event_type2": "OPER", "is_internal": False, "is_fx": False}),
        ("fx", {"event_type2": "FX_BUY", "is_internal": False, "is_fx": True}),
        ("int", {"event_type2": "INTERNAL_TRANSFER", "is_internal": True, "is_fx": False}),
    ):
        db_session.add(
            Transaction(
                project_id=pid,
                date=utcnow(),
                bank="VTB",
                account=acc,
                currency="CNY",
                counterparty_id=cp.id,
                expense=Decimal("100"),
                income=Decimal("0"),
                txn_id=f"t-{kind}-{uuid.uuid4().hex[:8]}",
                **kwargs,
            )
        )
    await db_session.commit()

    data = await SupplierDebtService(db_session).get_overview(pid)
    item = next(i for i in data["items"] if i["counterparty_id"] == cp.id)
    assert item["paid"] == Decimal("100")  # only the OPER row counts
