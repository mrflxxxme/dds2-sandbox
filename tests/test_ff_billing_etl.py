# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: ETL авто-матчер счетов ФФ (etl/sync_ff_invoices).

Матчер — sync ORM (как sync_shipment_payments): сидируем и проверяем в
SYNC-сессии без commit (откат на закрытии сессии).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.database import SyncSessionLocal
from backend.etl.service import _ensure_account
from backend.etl.sync_ff_invoices import sync_ff_invoices
from backend.models.counterparty import Counterparty
from backend.models.ff_billing import FfInvoice
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.transactions import Transaction
from backend.models.warehouse import OutboundShipment, Warehouse
from backend.utils.time import utcnow

_INN = "7811223344"


def _mk_ff_wh(sdb, pid, *, inn=_INN):
    cp = Counterparty(project_id=pid, inn=inn, name="ООО ФФ ETL")
    sdb.add(cp)
    sdb.flush()
    wh = Warehouse(
        project_id=pid, name="FFB ETL WH", warehouse_type="FULFILLMENT", counterparty_id=cp.id
    )
    sdb.add(wh)
    sdb.flush()
    return wh, cp


def _mk_invoice(sdb, pid, wh_id, *, amount, number=None, status="NEW", period_end=None):
    inv = FfInvoice(
        project_id=pid, warehouse_id=wh_id, number=number or f"E-{uuid.uuid4().hex[:6]}",
        amount=Decimal(amount), kind="MIXED", status=status,
        period_end=period_end or date.today(), invoice_date=date.today(),
    )
    sdb.add(inv)
    sdb.flush()
    return inv


def _mk_debit(sdb, pid, *, amount, inn=_INN, days_ago=0):
    acc = f"407018{uuid.uuid4().int % 10**14:014d}"
    _ensure_account(sdb, acc, "FAKTURA_WB_BANK", pid)
    t = Transaction(
        project_id=pid, date=utcnow() - timedelta(days=days_ago), bank="FAKTURA_WB_BANK",
        account=acc, currency="RUB", inn=inn, counterparty="ФФ",
        expense=Decimal(amount), income=Decimal("0"), txn_id=f"ffbetl-{uuid.uuid4().hex[:10]}",
    )
    sdb.add(t)
    sdb.flush()
    return t


@pytest.mark.asyncio
async def test_etl_links_unique_candidate(project):
    with SyncSessionLocal() as sdb:
        wh, _cp = _mk_ff_wh(sdb, project.id)
        inv = _mk_invoice(sdb, project.id, wh.id, amount="1500.00")
        t = _mk_debit(sdb, project.id, amount="1500.00")

        sync_ff_invoices(sdb, project.id)

        assert inv.matched_transaction_id == t.id
        assert inv.status == "PAID"
        assert inv.matched_at is not None


@pytest.mark.asyncio
async def test_etl_skips_ambiguous_candidates(project):
    with SyncSessionLocal() as sdb:
        wh, _cp = _mk_ff_wh(sdb, project.id)
        inv1 = _mk_invoice(sdb, project.id, wh.id, amount="2000.00")
        inv2 = _mk_invoice(sdb, project.id, wh.id, amount="2000.00")
        _mk_debit(sdb, project.id, amount="2000.00")

        sync_ff_invoices(sdb, project.id)

        assert inv1.matched_transaction_id is None
        assert inv2.matched_transaction_id is None
        assert inv1.status == "NEW" and inv2.status == "NEW"


@pytest.mark.asyncio
async def test_etl_skips_foreign_inn_and_consumed_debit(project):
    with SyncSessionLocal() as sdb:
        wh, _cp = _mk_ff_wh(sdb, project.id)
        inv = _mk_invoice(sdb, project.id, wh.id, amount="3000.00")
        # Дебет с чужим ИНН — не ФФ-юрлицо.
        _mk_debit(sdb, project.id, amount="3000.00", inn="7700000777")
        # Дебет с нужным ИНН, но уже занят забором.
        t_busy = _mk_debit(sdb, project.id, amount="3000.00")
        ship = OutboundShipment(
            project_id=project.id, warehouse_id=wh.id, number=f"E-S{uuid.uuid4().hex[:4]}",
            status="SHIPPED", attempt_no=1, matched_transaction_id=t_busy.id,
        )
        sdb.add(ship)
        sdb.flush()

        sync_ff_invoices(sdb, project.id)

        assert inv.matched_transaction_id is None
        assert inv.status == "NEW"


@pytest.mark.asyncio
async def test_etl_window_45_days(project):
    with SyncSessionLocal() as sdb:
        wh, _cp = _mk_ff_wh(sdb, project.id)
        inv = _mk_invoice(
            sdb, project.id, wh.id, amount="4000.00",
            period_end=date.today() - timedelta(days=100),
        )
        inv.invoice_date = date.today() - timedelta(days=100)
        sdb.flush()
        _mk_debit(sdb, project.id, amount="4000.00")  # сегодня — вне окна ±45

        sync_ff_invoices(sdb, project.id)

        assert inv.matched_transaction_id is None


@pytest.mark.asyncio
async def test_etl_propagates_paid_payment_request(project):
    with SyncSessionLocal() as sdb:
        wh, cp = _mk_ff_wh(sdb, project.id)
        paid_at = utcnow() - timedelta(days=1)
        pr = PaymentRequest(
            project_id=project.id, number=f"ОПЛ-{uuid.uuid4().hex[:6]}",
            status=PaymentRequestStatus.PAID.value, amount=Decimal("5000.00"),
            counterparty_id=cp.id, matched_at=paid_at,
        )
        sdb.add(pr)
        sdb.flush()
        inv = _mk_invoice(sdb, project.id, wh.id, amount="5000.00")
        inv.payment_request_id = pr.id
        sdb.flush()

        sync_ff_invoices(sdb, project.id)

        assert inv.status == "PAID"
        assert inv.matched_at == paid_at


@pytest.mark.asyncio
async def test_etl_project_isolation(project, other_project):
    """Счёт чужого проекта не матчится нашим дебетом."""
    with SyncSessionLocal() as sdb:
        wh_other, _ = _mk_ff_wh(sdb, other_project.id)
        inv_other = _mk_invoice(sdb, other_project.id, wh_other.id, amount="6000.00")
        _mk_ff_wh(sdb, project.id)
        _mk_debit(sdb, project.id, amount="6000.00")

        sync_ff_invoices(sdb, project.id)

        assert inv_other.matched_transaction_id is None
