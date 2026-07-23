# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: FF billing — счета ФФ: reconcile (пересборка строк + сверка), аллокация
суммы счёта по строкам, кандидаты-платежи, link/unlink, изоляция project_id.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import BoxQtyPerWarehouse
from backend.models.counterparty import Counterparty
from backend.models.ff_billing import FfInvoice, FfInvoiceLine, FfStorageDaily, WarehouseTariff
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.refs import Account
from backend.models.transactions import Transaction
from backend.models.warehouse import (
    InboundReceipt,
    OutboundShipment,
    Warehouse,
    WarehouseCounterparty,
)
from backend.schemas.ff_billing import (
    FfInvoicePaymentLinkPayload,
    FfInvoicePaymentUnlinkPayload,
)
from backend.services.ff_billing import (
    get_invoice_detail,
    link_invoice_payments,
    list_candidate_payments,
    reconcile_invoice,
    resolve_warehouse_by_inn,
    unlink_invoice_payments,
)
from backend.utils.time import utcnow

BC1 = "FFB_INV_BC1"
FF_INN = "7712345678"
FF_INN2 = "7787654321"

P_START = date(2026, 7, 1)
P_END = date(2026, 7, 15)


@pytest_asyncio.fixture
async def env(db_session, project, other_project):
    """ФФ-склад с юрлицами (основное + доп.), тарифы, сборки, приёмка, забор."""
    cp = Counterparty(project_id=project.id, inn=FF_INN, name="ООО ФФ Основное")
    cp2 = Counterparty(project_id=project.id, inn=FF_INN2, name="ООО ФФ Доп")
    db_session.add_all([cp, cp2])
    await db_session.flush()

    wh = Warehouse(
        project_id=project.id, name="FFB INV WH", warehouse_type="FULFILLMENT",
        counterparty_id=cp.id,
    )
    db_session.add(wh)
    await db_session.flush()
    db_session.add(
        WarehouseCounterparty(project_id=project.id, warehouse_id=wh.id, counterparty_id=cp2.id)
    )

    nom1 = (
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, updated_at) "
                "VALUES (:pid, :bc, NOW()) RETURNING id"
            ),
            {"pid": project.id, "bc": BC1},
        )
    ).scalar()
    db_session.add(
        BoxQtyPerWarehouse(project_id=project.id, barcode=BC1, warehouse_id=wh.id, box_qty=10)
    )

    for svc, rate, unit in (
        ("PALLETIZING", "200", None),
        ("BOX_PROCESSING", "30", None),
        ("LOADING", "15", "BOX"),
        ("TRUCK_UNLOADING", "5000", None),
    ):
        db_session.add(
            WarehouseTariff(
                project_id=project.id, warehouse_id=wh.id, service_type=svc,
                unit=unit, rate=Decimal(rate), valid_from=date(2026, 6, 1),
            )
        )

    async def _mk_asm(number, status, shipped_at, pallets, qty, *, deleted=False):
        req = AssemblyRequest(
            project_id=project.id, warehouse_id=wh.id, number=number, status=status,
            pallets_count=pallets, pallet_weight_kg=Decimal("100"), shipped_at=shipped_at,
        )
        if deleted:
            req.soft_delete()
        db_session.add(req)
        await db_session.flush()
        db_session.add(
            AssemblyRequestItem(
                project_id=project.id, assembly_request_id=req.id,
                nomenclature_id=nom1, barcode=BC1, quantity=qty,
            )
        )
        return req

    # A1: 2 паллеты, 25 шт → 3 короба → 200×2+30×3+15×3 = 535.
    a1 = await _mk_asm("FFB-A1", AssemblyStatus.SHIPPED, datetime(2026, 7, 5, 10), 2, 25)
    # A2: 1 паллета, 10 шт → 1 короб → 200+30+15 = 245.
    a2 = await _mk_asm("FFB-A2", AssemblyStatus.DELIVERED, datetime(2026, 7, 10, 10), 1, 10)
    # Вне периода / не тот статус / удалена — не попадают.
    await _mk_asm("FFB-A3", AssemblyStatus.SHIPPED, datetime(2026, 7, 20, 10), 1, 10)
    await _mk_asm("FFB-A4", AssemblyStatus.CANCELLED, datetime(2026, 7, 6, 10), 1, 10)
    await _mk_asm("FFB-A5", AssemblyStatus.SHIPPED, datetime(2026, 7, 7, 10), 1, 10, deleted=True)

    # Приёмки: ACCEPTED в периоде (+1 DRAFT, +1 вне периода).
    r1 = InboundReceipt(
        project_id=project.id, warehouse_id=wh.id, number="FFB-R1",
        status="ACCEPTED", actual_date=date(2026, 7, 3),
    )
    db_session.add_all(
        [
            r1,
            InboundReceipt(
                project_id=project.id, warehouse_id=wh.id, number="FFB-R2",
                status="DRAFT", actual_date=date(2026, 7, 4),
            ),
            InboundReceipt(
                project_id=project.id, warehouse_id=wh.id, number="FFB-R3",
                status="ACCEPTED", actual_date=date(2026, 7, 20),
            ),
        ]
    )

    # Заборы силами ФФ: перевозчик = юрлицо склада (доп. юрлицо тоже считается).
    s1 = OutboundShipment(
        project_id=project.id, warehouse_id=wh.id, number="FFB-S1", status="SHIPPED",
        counterparty_id=cp2.id, pickup_cost=Decimal("7000"), pickup_date=date(2026, 7, 4),
        attempt_no=1,
    )
    other_cp = Counterparty(project_id=project.id, inn="7700000001", name="Чужой перевозчик")
    db_session.add(other_cp)
    await db_session.flush()
    db_session.add_all(
        [
            s1,
            OutboundShipment(
                project_id=project.id, warehouse_id=wh.id, number="FFB-S2", status="SHIPPED",
                counterparty_id=other_cp.id, pickup_cost=Decimal("9999"),
                pickup_date=date(2026, 7, 5), attempt_no=1,
            ),
        ]
    )

    # Хранение за период: 2 дня по 3 и 2 паллеты.
    db_session.add_all(
        [
            FfStorageDaily(
                project_id=project.id, warehouse_id=wh.id, snapshot_date=date(2026, 7, 1),
                units=100, boxes=10, pallets=3, storage_rate=Decimal("25"),
                storage_cost=Decimal("75"),
            ),
            FfStorageDaily(
                project_id=project.id, warehouse_id=wh.id, snapshot_date=date(2026, 7, 2),
                units=80, boxes=8, pallets=2, storage_rate=Decimal("25"),
                storage_cost=Decimal("50"),
            ),
        ]
    )
    await db_session.commit()
    return SimpleNamespace(
        project_id=project.id, other_project_id=other_project.id,
        wh_id=wh.id, cp_id=cp.id, cp2_id=cp2.id,
        a1_id=a1.id, a2_id=a2.id, r1_id=r1.id, s1_id=s1.id,
    )


def _mk_invoice(pid, wh_id, *, amount, kind="SHIPMENT", cp_id=None, number="INV-1"):
    return FfInvoice(
        project_id=pid, warehouse_id=wh_id, counterparty_id=cp_id, number=number,
        invoice_date=P_END, period_start=P_START, period_end=P_END,
        kind=kind, amount=Decimal(amount),
    )


async def _acc(db, pid) -> str:
    acc_no = f"407028{uuid.uuid4().int % 10**14:014d}"
    db.add(Account(project_id=pid, account=acc_no, bank="FAKTURA_WB_BANK", currency="RUB"))
    await db.flush()
    return acc_no


async def _mk_debit(db, pid, *, amount, inn=FF_INN, days_ago=0):
    acc_no = await _acc(db, pid)
    t = Transaction(
        project_id=pid, date=utcnow() - timedelta(days=days_ago), bank="FAKTURA_WB_BANK",
        account=acc_no, currency="RUB", inn=inn, counterparty="ФФ",
        expense=Decimal(amount), income=Decimal("0"), txn_id=f"ffb-{uuid.uuid4().hex[:12]}",
    )
    db.add(t)
    await db.flush()
    return t


# ─── reconcile ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconcile_shipment_reconciled(db_session, env):
    inv = _mk_invoice(env.project_id, env.wh_id, amount="780.00")
    db_session.add(inv)
    await db_session.commit()

    detail = await reconcile_invoice(db_session, env.project_id, inv.id)
    assert detail.our_amount == Decimal("780.00")  # 535 + 245
    assert detail.status == "RECONCILED"
    lines = {ln.assembly_request_id for ln in detail.lines}
    assert lines == {env.a1_id, env.a2_id}
    by_asm = {ln.assembly_request_id: ln for ln in detail.lines}
    assert by_asm[env.a1_id].our_cost == Decimal("535.00")
    assert by_asm[env.a1_id].qty_units == 25
    assert by_asm[env.a1_id].qty_boxes == 3
    assert by_asm[env.a1_id].qty_pallets == 2
    assert by_asm[env.a1_id].ref_number == "FFB-A1"
    # Аллокация: Σ allocated == amount копейка-в-копейку.
    assert sum(ln.allocated_amount for ln in detail.lines) == Decimal("780.00")


@pytest.mark.asyncio
async def test_reconcile_disputed_and_allocation_remainder(db_session, env):
    """Сумма не делится ровно: последняя строка добирает остаток; вне допуска → DISPUTED."""
    inv = _mk_invoice(env.project_id, env.wh_id, amount="1000.01")
    db_session.add(inv)
    await db_session.commit()

    detail = await reconcile_invoice(db_session, env.project_id, inv.id)
    assert detail.status == "DISPUTED"  # |1000.01 − 780| = 220.01 > max(0.01, 10.0001)
    assert sum(ln.allocated_amount for ln in detail.lines) == Decimal("1000.01")


@pytest.mark.asyncio
async def test_reconcile_idempotent_rebuild(db_session, env):
    inv = _mk_invoice(env.project_id, env.wh_id, amount="780.00")
    db_session.add(inv)
    await db_session.commit()
    await reconcile_invoice(db_session, env.project_id, inv.id)
    detail = await reconcile_invoice(db_session, env.project_id, inv.id)
    assert len(detail.lines) == 2
    n_lines = (
        await db_session.execute(
            select(FfInvoiceLine).where(FfInvoiceLine.invoice_id == inv.id)
        )
    ).scalars().all()
    assert len(n_lines) == 2


@pytest.mark.asyncio
async def test_reconcile_mixed_all_kinds(db_session, env):
    """MIXED: сборки (780) + хранение (125, 5 паллето-дней) + приёмка (5000) + забор (7000)."""
    inv = _mk_invoice(env.project_id, env.wh_id, amount="12905.00", kind="MIXED")
    db_session.add(inv)
    await db_session.commit()

    detail = await reconcile_invoice(db_session, env.project_id, inv.id)
    kinds = sorted(ln.kind for ln in detail.lines)
    assert kinds == ["ASSEMBLY", "ASSEMBLY", "LOGISTICS", "RECEIVING", "STORAGE"]
    st = next(ln for ln in detail.lines if ln.kind == "STORAGE")
    assert st.qty_pallets == 5
    assert st.our_cost == Decimal("125.00")
    rc = next(ln for ln in detail.lines if ln.kind == "RECEIVING")
    assert rc.our_cost == Decimal("5000.00")
    assert rc.inbound_receipt_id == env.r1_id
    assert rc.ref_number == "FFB-R1"
    lg = next(ln for ln in detail.lines if ln.kind == "LOGISTICS")
    assert lg.our_cost == Decimal("7000.00")
    assert lg.outbound_shipment_id == env.s1_id
    assert detail.our_amount == Decimal("12905.00")
    assert detail.status == "RECONCILED"


@pytest.mark.asyncio
async def test_reconcile_requires_warehouse_and_period(db_session, env):
    inv = FfInvoice(project_id=env.project_id, warehouse_id=None, amount=Decimal("100"), kind="SHIPMENT")
    db_session.add(inv)
    await db_session.commit()
    with pytest.raises(HTTPException) as ei:
        await reconcile_invoice(db_session, env.project_id, inv.id)
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_reconcile_project_isolation(db_session, env):
    inv = _mk_invoice(env.project_id, env.wh_id, amount="780.00")
    db_session.add(inv)
    await db_session.commit()
    with pytest.raises(HTTPException) as ei:
        await reconcile_invoice(db_session, env.other_project_id, inv.id)
    assert ei.value.status_code == 404


# ─── резолв склада по ИНН ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_warehouse_by_inn(db_session, env):
    wh_id, warns = await resolve_warehouse_by_inn(db_session, env.project_id, FF_INN)
    assert wh_id == env.wh_id and warns == []
    wh_id2, _ = await resolve_warehouse_by_inn(db_session, env.project_id, FF_INN2)
    assert wh_id2 == env.wh_id  # доп. юрлицо тоже резолвится
    wh_none, warns_none = await resolve_warehouse_by_inn(db_session, env.project_id, "0000000000")
    assert wh_none is None and warns_none


# ─── candidate payments / link / unlink ─────────────────────────────────────


@pytest.mark.asyncio
async def test_candidate_payments(db_session, env):
    inv = _mk_invoice(env.project_id, env.wh_id, amount="780.00", cp_id=env.cp_id)
    db_session.add(inv)
    t_ok = await _mk_debit(db_session, env.project_id, amount="780.00")
    t_cp2 = await _mk_debit(db_session, env.project_id, amount="500.00", inn=FF_INN2)
    t_foreign = await _mk_debit(db_session, env.project_id, amount="780.00", inn="7700000009")
    t_consumed = await _mk_debit(db_session, env.project_id, amount="780.00")
    # t_consumed занят заявкой на оплату.
    db_session.add(
        PaymentRequest(
            project_id=env.project_id, number=f"ОПЛ-{uuid.uuid4().hex[:6]}",
            status=PaymentRequestStatus.PAID.value, amount=Decimal("780.00"),
            matched_transaction_id=t_consumed.id,
        )
    )
    await db_session.commit()

    cands = await list_candidate_payments(db_session, env.project_id, inv.id)
    ids = {c.transaction_id for c in cands}
    assert t_ok.id in ids
    assert t_cp2.id in ids  # ИНН доп. юрлица склада
    assert t_foreign.id not in ids
    assert t_consumed.id not in ids


@pytest.mark.asyncio
async def test_link_sum_validation_and_paid(db_session, env):
    inv1 = _mk_invoice(env.project_id, env.wh_id, amount="500.00", number="INV-L1")
    inv2 = _mk_invoice(env.project_id, env.wh_id, amount="280.00", number="INV-L2")
    db_session.add_all([inv1, inv2])
    t = await _mk_debit(db_session, env.project_id, amount="780.00")
    await db_session.commit()

    # Σ не сходится → 422.
    with pytest.raises(HTTPException) as ei:
        await link_invoice_payments(
            db_session, env.project_id,
            FfInvoicePaymentLinkPayload(transaction_id=t.id, invoice_ids=[inv1.id]),
        )
    assert ei.value.status_code == 422

    await link_invoice_payments(
        db_session, env.project_id,
        FfInvoicePaymentLinkPayload(transaction_id=t.id, invoice_ids=[inv1.id, inv2.id]),
    )
    await db_session.refresh(inv1)
    await db_session.refresh(inv2)
    assert inv1.status == "PAID" and inv1.matched_transaction_id == t.id
    assert inv2.status == "PAID" and inv2.matched_at is not None

    # already_linked_invoice_ids у кандидата.
    inv3 = _mk_invoice(env.project_id, env.wh_id, amount="780.00", number="INV-L3", cp_id=env.cp_id)
    db_session.add(inv3)
    await db_session.commit()
    cands = await list_candidate_payments(db_session, env.project_id, inv3.id)
    cand = next(c for c in cands if c.transaction_id == t.id)
    assert set(cand.already_linked_invoice_ids) == {inv1.id, inv2.id}


@pytest.mark.asyncio
async def test_unlink_restores_status(db_session, env):
    """unlink: our_amount есть → RECONCILED/DISPUTED по допуску, нет → NEW."""
    inv_rec = _mk_invoice(env.project_id, env.wh_id, amount="780.00", number="INV-U1")
    inv_new = _mk_invoice(env.project_id, env.wh_id, amount="500.00", number="INV-U2")
    db_session.add_all([inv_rec, inv_new])
    t = await _mk_debit(db_session, env.project_id, amount="1280.00")
    await db_session.commit()
    await reconcile_invoice(db_session, env.project_id, inv_rec.id)  # our=780 → RECONCILED
    await link_invoice_payments(
        db_session, env.project_id,
        FfInvoicePaymentLinkPayload(transaction_id=t.id, invoice_ids=[inv_rec.id, inv_new.id]),
    )
    await unlink_invoice_payments(
        db_session, env.project_id,
        FfInvoicePaymentUnlinkPayload(invoice_ids=[inv_rec.id, inv_new.id]),
    )
    await db_session.refresh(inv_rec)
    await db_session.refresh(inv_new)
    assert inv_rec.matched_transaction_id is None
    assert inv_rec.status == "RECONCILED"
    assert inv_new.status == "NEW"  # сверки не было → our_amount NULL


@pytest.mark.asyncio
async def test_get_invoice_detail_isolation(db_session, env):
    inv = _mk_invoice(env.project_id, env.wh_id, amount="780.00")
    db_session.add(inv)
    await db_session.commit()
    with pytest.raises(HTTPException) as ei:
        await get_invoice_detail(db_session, env.other_project_id, inv.id)
    assert ei.value.status_code == 404
