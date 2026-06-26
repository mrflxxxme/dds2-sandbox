# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: прямая связка забор ↔ выписка (etl/sync_shipment_payments) + сверка
оплат на карточке контрагента (PaymentRequestService.get_counterparty_reconciliation).

Матчер — sync ORM (как sync_payment_requests): сидируем и проверяем в SYNC-сессии
без commit (откат на закрытии). Сверка — async через сервис на db_session.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.database import SyncSessionLocal
from backend.etl.service import _ensure_account
from backend.etl.sync_shipment_payments import sync_shipment_payments
from backend.models import Account
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.counterparty import Counterparty
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.models.transactions import Transaction
from backend.models.warehouse import OutboundShipment, OutboundStatus, Warehouse
from backend.services.payment_request_service import PaymentRequestService
from backend.utils.time import utcnow

_ACC = "40702810900000000088"
_INN = "7799999911"


# ─── sync helpers ───────────────────────────────────────────────────────────


def _mk_wh(sdb, pid):
    wh = Warehouse(project_id=pid, name="Match Test WH", warehouse_type="FULFILLMENT")
    sdb.add(wh)
    sdb.flush()
    return wh


def _mk_cp(sdb, pid, *, inn=_INN, name="ИП Перевозчик"):
    cp = Counterparty(project_id=pid, inn=inn, name=name)
    sdb.add(cp)
    sdb.flush()
    return cp


def _mk_shipment(sdb, pid, wh_id, cp_id, *, number, cost, status=OutboundStatus.SHIPPED, shipped_days_ago=0):
    s = OutboundShipment(
        project_id=pid,
        warehouse_id=wh_id,
        number=number,
        status=status,
        counterparty_id=cp_id,
        pickup_cost=Decimal(cost) if cost is not None else None,
        attempt_no=1,
        shipped_date=date.today() - timedelta(days=shipped_days_ago),
    )
    sdb.add(s)
    sdb.flush()
    return s


def _mk_debit(sdb, pid, *, txn_id, amount, inn=_INN, days_ago=0):
    _ensure_account(sdb, _ACC, "FAKTURA_WB_BANK", pid)  # transactions.account FK
    t = Transaction(
        project_id=pid,
        date=utcnow() - timedelta(days=days_ago),
        bank="FAKTURA_WB_BANK",
        account=_ACC,
        currency="RUB",
        inn=inn,
        counterparty="ИП Перевозчик",
        expense=Decimal(amount),
        income=Decimal("0"),
        txn_id=txn_id,
    )
    sdb.add(t)
    sdb.flush()
    return t


# ─── matcher (sync) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_match_links_shipment(project):
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M1", cost="5000.00")
        t = _mk_debit(sdb, project.id, txn_id="ship-match-1", amount="5000.00")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id == t.id
        assert s.matched_at is not None


@pytest.mark.asyncio
async def test_amount_mismatch_not_matched(project):
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M2", cost="5000.00")
        _mk_debit(sdb, project.id, txn_id="ship-nomatch-1", amount="4999.00")  # diff > 0.01

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id is None


@pytest.mark.asyncio
async def test_carrier_without_inn_skipped(project):
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id, inn=None, name="Нал-перевозчик")
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M3", cost="5000.00")
        _mk_debit(sdb, project.id, txn_id="ship-noinn-1", amount="5000.00", inn=None)

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id is None


@pytest.mark.asyncio
async def test_one_debit_consumed_once(project):
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s1 = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M4a", cost="5000.00")
        s2 = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M4b", cost="5000.00")
        _mk_debit(sdb, project.id, txn_id="ship-single-1", amount="5000.00")  # only ONE debit

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s1)
        sdb.refresh(s2)
        linked = [s for s in (s1, s2) if s.matched_transaction_id is not None]
        assert len(linked) == 1  # exactly one shipment consumes the single debit


@pytest.mark.asyncio
async def test_shipment_with_active_request_skipped(project):
    """Забор с активной заявкой на оплату → авто-матчер его не трогает (ручной поток владеет)."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M5", cost="5000.00")
        pr = PaymentRequest(
            project_id=project.id,
            number="ОПЛ-M5",
            status=PaymentRequestStatus.PENDING_REVIEW.value,
            source="MANUAL",
            outbound_shipment_id=s.id,
            payee_inn=_INN,
            amount=Decimal("5000.00"),
            currency="RUB",
        )
        sdb.add(pr)
        sdb.flush()
        _mk_debit(sdb, project.id, txn_id="ship-active-req-1", amount="5000.00")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id is None


@pytest.mark.asyncio
async def test_debit_already_consumed_by_request_not_reused(project):
    """Транзакция, уже занятая заявкой на оплату, не уходит на забор."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-M6", cost="5000.00")
        t = _mk_debit(sdb, project.id, txn_id="ship-taken-1", amount="5000.00")
        pr = PaymentRequest(
            project_id=project.id,
            number="ОПЛ-M6",
            status=PaymentRequestStatus.PAID.value,
            source="MANUAL",
            payee_inn=_INN,
            amount=Decimal("5000.00"),
            currency="RUB",
            matched_transaction_id=t.id,  # txn already consumed by a request
        )
        sdb.add(pr)
        sdb.flush()

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id is None


@pytest.mark.asyncio
async def test_cancelled_shipment_not_matched(project):
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(
            sdb, project.id, wh.id, cp.id, number="O-M7", cost="5000.00", status=OutboundStatus.CANCELLED
        )
        _mk_debit(sdb, project.id, txn_id="ship-cancelled-1", amount="5000.00")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id is None


@pytest.mark.asyncio
async def test_delivered_shipment_matched(project):
    """Сданный (DELIVERED) забор тоже кандидат на авто-связку."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(
            sdb, project.id, wh.id, cp.id, number="O-D1", cost="5000.00", status=OutboundStatus.DELIVERED
        )
        t = _mk_debit(sdb, project.id, txn_id="ship-delivered-1", amount="5000.00")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id == t.id


@pytest.mark.asyncio
async def test_debit_outside_window_not_matched(project):
    """Дебет за 30 дней до отгрузки — вне окна (shipped_date − 7д) → не матчится."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-W1", cost="5000.00", shipped_days_ago=0)
        _mk_debit(sdb, project.id, txn_id="ship-old-1", amount="5000.00", days_ago=30)

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id is None


@pytest.mark.asyncio
async def test_pickup_date_anchor_when_no_shipped_date(project):
    """Если shipped_date пуст — окно считается от pickup_date."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = OutboundShipment(
            project_id=project.id, warehouse_id=wh.id, number="O-PD1",
            status=OutboundStatus.SHIPPED, counterparty_id=cp.id, pickup_cost=Decimal("5000.00"),
            attempt_no=1, shipped_date=None, pickup_date=date.today(),
        )
        sdb.add(s)
        sdb.flush()
        t = _mk_debit(sdb, project.id, txn_id="ship-pd-1", amount="5000.00", days_ago=0)

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id == t.id


@pytest.mark.asyncio
async def test_zero_or_null_cost_not_matched(project):
    """Заборы с pickup_cost = 0 или NULL — не кандидаты."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s_zero = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-Z1", cost="0.00")
        s_null = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-Z2", cost=None)
        _mk_debit(sdb, project.id, txn_id="ship-zero-1", amount="0.01")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s_zero)
        sdb.refresh(s_null)
        assert s_zero.matched_transaction_id is None
        assert s_null.matched_transaction_id is None


@pytest.mark.asyncio
async def test_idempotent_rerun_is_noop(project):
    """Повторный прогон не пере-связывает и не перезаписывает matched_at."""
    with SyncSessionLocal() as sdb:
        wh = _mk_wh(sdb, project.id)
        cp = _mk_cp(sdb, project.id)
        s = _mk_shipment(sdb, project.id, wh.id, cp.id, number="O-ID1", cost="5000.00")
        t = _mk_debit(sdb, project.id, txn_id="ship-idem-1", amount="5000.00")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s)
        assert s.matched_transaction_id == t.id
        first_matched_at = s.matched_at

        sync_shipment_payments(sdb, project.id)  # second pass
        sdb.refresh(s)
        assert s.matched_transaction_id == t.id
        assert s.matched_at == first_matched_at  # untouched


@pytest.mark.asyncio
async def test_project_isolation(project, other_project):
    """Матчер трогает только заборы своего проекта; дебет чужого проекта не используется."""
    with SyncSessionLocal() as sdb:
        wh_a = _mk_wh(sdb, project.id)
        cp_a = _mk_cp(sdb, project.id)
        s_a = _mk_shipment(sdb, project.id, wh_a.id, cp_a.id, number="O-PA1", cost="5000.00")
        t_a = _mk_debit(sdb, project.id, txn_id="ship-iso-a", amount="5000.00")

        # Чужой проект: тот же ИНН/сумма, но забор без дебета в своём проекте.
        wh_b = _mk_wh(sdb, other_project.id)
        cp_b = _mk_cp(sdb, other_project.id)
        s_b = _mk_shipment(sdb, other_project.id, wh_b.id, cp_b.id, number="O-PB1", cost="5000.00")

        sync_shipment_payments(sdb, project.id)
        sdb.refresh(s_a)
        sdb.refresh(s_b)
        assert s_a.matched_transaction_id == t_a.id
        assert s_b.matched_transaction_id is None  # другой проект — не тронут


# ─── reconciliation (async via service) ──────────────────────────────────────


@pytest_asyncio.fixture
async def recon_setup(db_session, project):
    """Один перевозчик: 1 оплаченный забор (matched txn) + 1 неоплаченный + 1 бесхозный платёж."""
    pid = project.id
    acc_no = f"4070281090{pid:010d}"[:20]  # accounts.account уникален глобально
    wh = Warehouse(project_id=pid, name="Recon WH", warehouse_type="FULFILLMENT")
    cp = Counterparty(project_id=pid, inn="7788889900", name="ИП Сверка")
    acc = Account(
        project_id=pid, account=acc_no, bank="WB_BANK", currency="RUB",
        account_name="ВБ Банк (Faktura)", is_our_account=True,
    )
    db_session.add_all([wh, cp, acc])
    await db_session.flush()

    # Оплаченный забор: matched к дебету paid_txn.
    paid_txn = Transaction(
        project_id=pid, date=utcnow(), bank="FAKTURA_WB_BANK", account=acc_no, currency="RUB",
        inn="7788889900", counterparty="ИП Сверка", expense=Decimal("7000.00"), income=Decimal("0"),
        txn_id=f"recon-paid-{pid}",
    )
    # Бесхозный платёж: дебет на ИНН перевозчика без забора.
    orphan_txn = Transaction(
        project_id=pid, date=utcnow(), bank="FAKTURA_WB_BANK", account=acc_no, currency="RUB",
        inn="7788889900", counterparty="ИП Сверка", expense=Decimal("3333.00"), income=Decimal("0"),
        txn_id=f"recon-orphan-{pid}",
    )
    db_session.add_all([paid_txn, orphan_txn])
    await db_session.flush()

    reqs = {}
    ships = {}
    for tag, cost, status, matched in (
        ("paid", "7000.00", AssemblyStatus.DELIVERED, paid_txn.id),
        ("unpaid", "4200.00", AssemblyStatus.SHIPPED, None),
    ):
        req = AssemblyRequest(
            project_id=pid, warehouse_id=wh.id, number=f"R-{tag}", status=status,
            counterparty_id=cp.id, pallets_count=1, pallet_weight_kg=Decimal("100"),
        )
        db_session.add(req)
        await db_session.flush()
        ship = OutboundShipment(
            project_id=pid, warehouse_id=wh.id, number=f"O-{tag}",
            status=OutboundStatus.DELIVERED if status == AssemblyStatus.DELIVERED else OutboundStatus.SHIPPED,
            assembly_request_id=req.id, attempt_no=1, pickup_cost=Decimal(cost),
            counterparty_id=cp.id, shipped_date=date.today(),
            matched_transaction_id=matched,
        )
        db_session.add(ship)
        await db_session.flush()
        reqs[tag] = req
        ships[tag] = ship
    await db_session.commit()
    return {"cp": cp, "ships": ships, "paid_txn": paid_txn, "orphan_txn": orphan_txn}


@pytest.mark.asyncio
async def test_reconciliation_states(db_session, project, recon_setup):
    svc = PaymentRequestService(db_session)
    cp = recon_setup["cp"]
    rec = await svc.get_counterparty_reconciliation(project.id, cp.id)

    assert len(rec.shipments) == 2
    assert rec.matched_count == 1
    assert rec.unpaid_count == 1
    # ВСЕ платежи перевозчика видны (оба дебета); привязка считается по платежу.
    pay = {p.transaction_id: p for p in rec.payments}
    assert recon_setup["paid_txn"].id in pay
    assert recon_setup["orphan_txn"].id in pay
    assert pay[recon_setup["paid_txn"].id].linked_count == 1  # привязан 1 забор
    assert pay[recon_setup["paid_txn"].id].diff == Decimal("0")  # 7000 − 7000
    assert pay[recon_setup["orphan_txn"].id].linked_count == 0  # без забора
    assert rec.payment_count == 2
    assert rec.unlinked_payment_count == 1


@pytest.mark.asyncio
async def test_archive_orphan_payment_hides_it(db_session, project, recon_setup):
    svc = PaymentRequestService(db_session)
    cp = recon_setup["cp"]
    orphan_id = recon_setup["orphan_txn"].id

    n = await svc.archive_txns(project.id, [orphan_id], user_id=None)
    assert n == 1

    active = await svc.get_counterparty_reconciliation(project.id, cp.id, archived=False)
    assert all(o.transaction_id != orphan_id for o in active.payments)

    archived = await svc.get_counterparty_reconciliation(project.id, cp.id, archived=True)
    assert any(o.transaction_id == orphan_id and o.archived for o in archived.payments)

    # idempotent + unarchive
    assert await svc.archive_txns(project.id, [orphan_id], user_id=None) == 0
    assert await svc.unarchive_txns(project.id, [orphan_id]) == 1


@pytest.mark.asyncio
async def test_archive_txns_rejects_foreign_id(db_session, project):
    """Архив платежа: чужой/несуществующий transaction_id игнорируется (multi-tenant)."""
    svc = PaymentRequestService(db_session)
    assert await svc.archive_txns(project.id, [987654321], user_id=None) == 0


@pytest.mark.asyncio
async def test_link_multiple_shipments_to_one_payment(db_session, project):
    """N заборов → одна агрегированная оплата + сверка суммы; отвязка."""
    pid = project.id
    acc_no = f"4070281091{pid:010d}"[:20]
    wh = Warehouse(project_id=pid, name="Link WH", warehouse_type="FULFILLMENT")
    cp = Counterparty(project_id=pid, inn="7755443322", name="ИП Агрегат")
    acc = Account(project_id=pid, account=acc_no, bank="WB_BANK", currency="RUB",
                  account_name="ВБ Банк", is_our_account=True)
    db_session.add_all([wh, cp, acc])
    await db_session.flush()

    # Агрегированный платёж 7000 = 3000 + 4000 (два забора).
    txn = Transaction(
        project_id=pid, date=utcnow(), bank="FAKTURA_WB_BANK", account=acc_no, currency="RUB",
        inn="7755443322", counterparty="ИП Агрегат", expense=Decimal("7000.00"), income=Decimal("0"),
        txn_id=f"link-agg-{pid}",
    )
    db_session.add(txn)
    await db_session.flush()

    ships = {}
    for tag, cost in (("a", "3000.00"), ("b", "4000.00")):
        req = AssemblyRequest(project_id=pid, warehouse_id=wh.id, number=f"RL-{tag}",
                              status=AssemblyStatus.SHIPPED, counterparty_id=cp.id,
                              pallets_count=1, pallet_weight_kg=Decimal("100"))
        db_session.add(req)
        await db_session.flush()
        s = OutboundShipment(project_id=pid, warehouse_id=wh.id, number=f"OL-{tag}",
                             status=OutboundStatus.SHIPPED, assembly_request_id=req.id, attempt_no=1,
                             pickup_cost=Decimal(cost), counterparty_id=cp.id, shipped_date=date.today())
        db_session.add(s)
        await db_session.flush()
        ships[tag] = s
    await db_session.commit()

    svc = PaymentRequestService(db_session)
    res = await svc.link_shipments_to_payment(pid, txn.id, [ships["a"].id, ships["b"].id], user_id=None)
    assert res["linked_count"] == 2
    assert res["linked_sum"] == Decimal("7000.00")
    assert res["diff"] == Decimal("0.00")

    rec = await svc.get_counterparty_reconciliation(pid, cp.id)
    assert rec.matched_count == 2  # оба забора теперь оплачены
    pay = next(p for p in rec.payments if p.transaction_id == txn.id)
    assert pay.linked_count == 2 and pay.diff == Decimal("0")

    # отвязать один → остаётся 1
    assert await svc.unlink_shipments(pid, [ships["a"].id]) == 1
    rec2 = await svc.get_counterparty_reconciliation(pid, cp.id)
    assert rec2.matched_count == 1


@pytest.mark.asyncio
async def test_link_rejects_foreign_payment(db_session, project):
    """Привязка к чужому/несуществующему платежу → ошибка валидации."""
    from backend.services.payment_request_service import PaymentRequestValidationError

    svc = PaymentRequestService(db_session)
    with pytest.raises(PaymentRequestValidationError):
        await svc.link_shipments_to_payment(project.id, 987654321, [], user_id=None)
