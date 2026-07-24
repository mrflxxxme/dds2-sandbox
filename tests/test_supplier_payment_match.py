# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: атрибуция строк выписки поставщику/займу по контракту+счёту
(etl/sync_supplier_payments).

Матчер — sync ORM (как sync_shipment_payments): сидируем и проверяем в SYNC-сессии
без commit (откат на закрытии).
"""

from datetime import date
from decimal import Decimal

import pytest

from backend.database import SyncSessionLocal
from backend.etl.service import _ensure_account
from backend.etl.sync_supplier_payments import sync_supplier_payments
from backend.models.counterparty import Counterparty, CounterpartyIdentifier, IdentifierKind
from backend.models.loan import Loan, LoanDirection, LoanStatus
from backend.models.transactions import Transaction
from backend.utils.time import utcnow

_ACC = "40700000000000000099"  # фейковый наш счёт (transactions.account FK; не из seed)
_TRANSIT = "30301156700810000001"  # транзитный счёт «БАНК ВТБ» в назначении


def _mk_cp(sdb, pid, *, name, inn=None, primary_type="SUPPLIER"):
    cp = Counterparty(project_id=pid, inn=inn, name=name, primary_type=primary_type)
    sdb.add(cp)
    sdb.flush()
    return cp


def _mk_identifier(sdb, pid, cp_id, kind, value):
    ci = CounterpartyIdentifier(project_id=pid, counterparty_id=cp_id, kind=kind, value=value)
    sdb.add(ci)
    sdb.flush()
    return ci


def _mk_txn(
    sdb,
    pid,
    *,
    txn_id,
    contract=None,
    account=_TRANSIT,
    cp_id=None,
    event_type2="OPER",
    is_internal=False,
    is_fx=False,
    expense="100.00",
):
    _ensure_account(sdb, _ACC, "VTB", pid)
    t = Transaction(
        project_id=pid,
        date=utcnow(),
        bank="VTB",
        account=_ACC,
        currency="CNY",
        counterparty="БАНК ВТБ (ПАО)",
        counterparty_account=account,
        contract_number=contract,
        counterparty_id=cp_id,
        event_type2=event_type2,
        is_internal=is_internal,
        is_fx=is_fx,
        expense=Decimal(expense),
        income=Decimal("0"),
        txn_id=txn_id,
    )
    sdb.add(t)
    sdb.flush()
    return t


@pytest.mark.asyncio
async def test_match_by_contract_sets_counterparty(project):
    with SyncSessionLocal() as sdb:
        supplier = _mk_cp(sdb, project.id, name="TiAmo")
        _mk_identifier(sdb, project.id, supplier.id, IdentifierKind.CONTRACT.value, "20250707")
        t = _mk_txn(sdb, project.id, txn_id="c1", contract="20250707", cp_id=None)

        sync_supplier_payments(sdb, project.id)
        sdb.refresh(t)
        assert t.counterparty_id == supplier.id


@pytest.mark.asyncio
async def test_overrides_wrong_bank_attribution(project):
    """CNY/SWIFT строка ошибочно привязана к «БАНК ВТБ» по ИНН → матчер перебивает."""
    with SyncSessionLocal() as sdb:
        bank = _mk_cp(sdb, project.id, name="БАНК ВТБ (ПАО)", inn="7702070139", primary_type="BANK")
        supplier = _mk_cp(sdb, project.id, name="Палатки")
        _mk_identifier(sdb, project.id, supplier.id, IdentifierKind.CONTRACT.value, "20260317")
        t = _mk_txn(sdb, project.id, txn_id="c2", contract="20260317", cp_id=bank.id)

        sync_supplier_payments(sdb, project.id)
        sdb.refresh(t)
        assert t.counterparty_id == supplier.id


@pytest.mark.asyncio
async def test_skips_fx_and_internal_and_deposit(project):
    with SyncSessionLocal() as sdb:
        supplier = _mk_cp(sdb, project.id, name="Мозаика")
        _mk_identifier(sdb, project.id, supplier.id, IdentifierKind.CONTRACT.value, "20260417")
        fx = _mk_txn(sdb, project.id, txn_id="fx", contract="20260417", is_fx=True)
        internal = _mk_txn(sdb, project.id, txn_id="int", contract="20260417", is_internal=True)
        deposit = _mk_txn(sdb, project.id, txn_id="dep", contract="20260417", event_type2="DEPOSIT_PLACE")

        sync_supplier_payments(sdb, project.id)
        for t in (fx, internal, deposit):
            sdb.refresh(t)
            assert t.counterparty_id is None


@pytest.mark.asyncio
async def test_match_by_account(project):
    with SyncSessionLocal() as sdb:
        supplier = _mk_cp(sdb, project.id, name="Xiaomi", primary_type="SUPPLIER")
        payee_acc = "40702810901300010687"
        _mk_identifier(sdb, project.id, supplier.id, IdentifierKind.ACCOUNT.value, payee_acc)
        t = _mk_txn(sdb, project.id, txn_id="acc1", contract=None, account=payee_acc)

        sync_supplier_payments(sdb, project.id)
        sdb.refresh(t)
        assert t.counterparty_id == supplier.id


@pytest.mark.asyncio
async def test_loan_by_contract_sets_loan_and_counterparty(project):
    with SyncSessionLocal() as sdb:
        lender = _mk_cp(sdb, project.id, name="Иностранный займодавец", primary_type="BANK")
        loan = Loan(
            project_id=project.id,
            counterparty_id=lender.id,
            direction=LoanDirection.INCOMING.value,
            principal=Decimal("1000000.00"),
            currency="CNY",
            contract_number="20269999",
            contract_date=date(2026, 1, 1),
            start_date=date(2026, 1, 1),
            status=LoanStatus.ACTIVE.value,
        )
        sdb.add(loan)
        sdb.flush()
        t = _mk_txn(sdb, project.id, txn_id="loan1", contract="20269999")

        sync_supplier_payments(sdb, project.id)
        sdb.refresh(t)
        assert t.loan_id == loan.id
        assert t.counterparty_id == lender.id


@pytest.mark.asyncio
async def test_idempotent(project):
    with SyncSessionLocal() as sdb:
        supplier = _mk_cp(sdb, project.id, name="Чашки")
        _mk_identifier(sdb, project.id, supplier.id, IdentifierKind.CONTRACT.value, "20260519")
        t = _mk_txn(sdb, project.id, txn_id="idem", contract="20260519")

        sync_supplier_payments(sdb, project.id)
        sync_supplier_payments(sdb, project.id)  # second run must be a no-op
        sdb.refresh(t)
        assert t.counterparty_id == supplier.id
