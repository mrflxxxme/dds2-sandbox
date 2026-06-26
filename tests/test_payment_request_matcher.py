# ruff: noqa: RUF002, RUF003
"""
Tests: bank-statement matcher (etl/sync_payment_requests) — sync ORM.

Данные создаём и проверяем в SYNC-сессии без commit (на закрытии откат — не
оставляем строк для cleanup). project создаётся async-фикстурой (закоммичен).
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.database import SyncSessionLocal
from backend.etl.service import _ensure_account
from backend.etl.sync_payment_requests import sync_payment_requests
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.transactions import Transaction
from backend.utils.time import utcnow

_ACC = "40702810900000000099"
_INN = "7711111111"


def _mk_pr(sdb, project_id, *, number, amount, inn=_INN, status=PaymentRequestStatus.DRAFT_CREATED.value):
    pr = PaymentRequest(
        project_id=project_id,
        number=number,
        status=status,
        source="MANUAL",
        payee_inn=inn,
        payee_account=_ACC,
        payee_bik="044525225",
        payee_name="ООО Перевозчик",
        amount=Decimal(amount),
        currency="RUB",
    )
    sdb.add(pr)
    sdb.flush()
    return pr


def _mk_debit(sdb, project_id, *, txn_id, amount, inn=_INN, days_ago=0):
    t = Transaction(
        project_id=project_id,
        date=utcnow() - timedelta(days=days_ago),
        bank="FAKTURA_WB_BANK",
        account=_ACC,
        currency="RUB",
        inn=inn,
        counterparty="ООО Перевозчик",
        expense=Decimal(amount),
        income=Decimal("0"),
        txn_id=txn_id,
    )
    sdb.add(t)
    sdb.flush()
    return t


@pytest.mark.asyncio
async def test_match_flips_to_paid(project):
    with SyncSessionLocal() as sdb:
        _ensure_account(sdb, _ACC, "FAKTURA_WB_BANK", project.id)
        sdb.flush()
        pr = _mk_pr(sdb, project.id, number="ОПЛ-00001", amount="15000.00")
        t = _mk_debit(sdb, project.id, txn_id="txn-match-1", amount="15000.00")

        sync_payment_requests(sdb, project.id)
        sdb.refresh(pr)

        assert pr.status == PaymentRequestStatus.PAID.value
        assert pr.matched_transaction_id == t.id
        assert pr.matched_at is not None
        # rollback on context exit — no persisted rows


@pytest.mark.asyncio
async def test_amount_mismatch_not_matched(project):
    with SyncSessionLocal() as sdb:
        _ensure_account(sdb, _ACC, "FAKTURA_WB_BANK", project.id)
        sdb.flush()
        pr = _mk_pr(sdb, project.id, number="ОПЛ-00002", amount="15000.00")
        _mk_debit(sdb, project.id, txn_id="txn-nomatch-1", amount="14999.00")  # diff > 0.01

        sync_payment_requests(sdb, project.id)
        sdb.refresh(pr)
        assert pr.status == PaymentRequestStatus.DRAFT_CREATED.value
        assert pr.matched_transaction_id is None


@pytest.mark.asyncio
async def test_empty_inn_skipped(project):
    with SyncSessionLocal() as sdb:
        _ensure_account(sdb, _ACC, "FAKTURA_WB_BANK", project.id)
        sdb.flush()
        pr = _mk_pr(sdb, project.id, number="ОПЛ-00003", amount="15000.00", inn=None)
        _mk_debit(sdb, project.id, txn_id="txn-noinn-1", amount="15000.00")

        sync_payment_requests(sdb, project.id)
        sdb.refresh(pr)
        assert pr.status == PaymentRequestStatus.DRAFT_CREATED.value


@pytest.mark.asyncio
async def test_one_debit_consumed_once(project):
    with SyncSessionLocal() as sdb:
        _ensure_account(sdb, _ACC, "FAKTURA_WB_BANK", project.id)
        sdb.flush()
        pr1 = _mk_pr(sdb, project.id, number="ОПЛ-00004", amount="15000.00")
        pr2 = _mk_pr(sdb, project.id, number="ОПЛ-00005", amount="15000.00")
        _mk_debit(sdb, project.id, txn_id="txn-single-1", amount="15000.00")  # only ONE debit

        sync_payment_requests(sdb, project.id)
        sdb.refresh(pr1)
        sdb.refresh(pr2)
        paid = [p for p in (pr1, pr2) if p.status == PaymentRequestStatus.PAID.value]
        assert len(paid) == 1  # exactly one request consumes the single debit


@pytest.mark.asyncio
async def test_already_paid_not_rematched(project):
    with SyncSessionLocal() as sdb:
        _ensure_account(sdb, _ACC, "FAKTURA_WB_BANK", project.id)
        sdb.flush()
        # already PAID with a prior txn → must be ignored (status not DRAFT_CREATED)
        pr = _mk_pr(sdb, project.id, number="ОПЛ-00006", amount="15000.00", status=PaymentRequestStatus.PAID.value)
        _mk_debit(sdb, project.id, txn_id="txn-late-1", amount="15000.00")

        sync_payment_requests(sdb, project.id)
        sdb.refresh(pr)
        assert pr.matched_transaction_id is None  # untouched
