# ruff: noqa: RUF001, RUF002, RUF003
"""Tests: одна заявка на оплату за несколько заборов (B) + назначение (D)."""

from datetime import date
from decimal import Decimal

import pytest

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.counterparty import Counterparty
from backend.models.warehouse import OutboundShipment, OutboundStatus, Warehouse
from backend.schemas.payment_request import PaymentRequestCreate
from backend.services.payment_request_service import (
    PaymentRequestService,
    PaymentRequestValidationError,
)

pytestmark = pytest.mark.asyncio


async def _seed(db_session, pid, costs, dest="Электросталь", pallets=5):
    wh = Warehouse(project_id=pid, name="Multi WH", warehouse_type="FULFILLMENT")
    cp = Counterparty(
        project_id=pid, inn="7712345678", name="ИП Мульти",
        bank_account="40802810000000000111", bik="044525225",
    )
    db_session.add_all([wh, cp])
    await db_session.flush()
    ships = []
    for i, cost in enumerate(costs):
        req = AssemblyRequest(
            project_id=pid, warehouse_id=wh.id, number=f"RM-{i}", status=AssemblyStatus.SHIPPED,
            counterparty_id=cp.id, pallets_count=pallets, pallet_weight_kg=Decimal("100"),
        )
        db_session.add(req)
        await db_session.flush()
        s = OutboundShipment(
            project_id=pid, warehouse_id=wh.id, number=f"OM-{i}", status=OutboundStatus.SHIPPED,
            assembly_request_id=req.id, attempt_no=1, pickup_cost=Decimal(cost),
            counterparty_id=cp.id, shipped_date=date(2026, 6, 18), pickup_date=date(2026, 6, 18),
            destination=dest, pallets_count=pallets,
        )
        db_session.add(s)
        await db_session.flush()
        ships.append(s)
    await db_session.commit()
    return cp, ships


async def test_one_payment_for_two_shipments(db_session, project):
    pid = project.id
    cp, ships = await _seed(db_session, pid, ["3000.00", "4000.00"])
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        pid,
        PaymentRequestCreate(
            source="COUNTERPARTY",
            outbound_shipment_ids=[ships[0].id, ships[1].id],
            counterparty_id=cp.id,
        ),
        user_id=project.owner_id,
    )
    assert pr.amount == Decimal("7000.00")  # сумма двух заборов
    assert pr.status == "PENDING_REVIEW"
    nums = await svc.covered_shipment_numbers(pid, pr.id)
    assert set(nums) == {"OM-0", "OM-1"}
    # Назначение (D): палеты + дата + куда сдано
    assert "палет" in pr.purpose and "сдача" in pr.purpose and "18.06.2026" in pr.purpose
    # Оба забора теперь «занятые»
    rows = await svc.list_shippable(pid, counterparty_id=cp.id)
    for r in rows:
        assert r.already_requested is True


async def test_shipment_already_in_active_request_rejected(db_session, project):
    pid = project.id
    cp, ships = await _seed(db_session, pid, ["5000.00", "6000.00"])
    svc = PaymentRequestService(db_session)
    await svc.create_request(
        pid,
        PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_ids=[ships[0].id], counterparty_id=cp.id),
        user_id=project.owner_id,
    )
    # Тот же забор во второй заявке → ошибка
    with pytest.raises(PaymentRequestValidationError):
        await svc.create_request(
            pid,
            PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_ids=[ships[0].id], counterparty_id=cp.id),
            user_id=project.owner_id,
        )


async def test_different_carriers_rejected(db_session, project):
    pid = project.id
    cp, ships = await _seed(db_session, pid, ["3000.00"])
    # второй перевозчик + его забор
    cp2 = Counterparty(project_id=pid, inn="7798765432", name="ИП Другой",
                       bank_account="40802810000000000222", bik="044525225")
    db_session.add(cp2)
    await db_session.flush()
    req2 = AssemblyRequest(project_id=pid, warehouse_id=ships[0].warehouse_id, number="RM-x",
                           status=AssemblyStatus.SHIPPED, counterparty_id=cp2.id,
                           pallets_count=3, pallet_weight_kg=Decimal("100"))
    db_session.add(req2)
    await db_session.flush()
    s2 = OutboundShipment(project_id=pid, warehouse_id=ships[0].warehouse_id, number="OM-x",
                          status=OutboundStatus.SHIPPED, assembly_request_id=req2.id, attempt_no=1,
                          pickup_cost=Decimal("4000.00"), counterparty_id=cp2.id, shipped_date=date(2026, 6, 18))
    db_session.add(s2)
    await db_session.commit()
    svc = PaymentRequestService(db_session)
    with pytest.raises(PaymentRequestValidationError):
        await svc.create_request(
            pid,
            PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_ids=[ships[0].id, s2.id]),
            user_id=project.owner_id,
        )


async def test_cancel_request_frees_shipment(db_session, project):
    pid = project.id
    cp, ships = await _seed(db_session, pid, ["3000.00"])
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        pid,
        PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_ids=[ships[0].id], counterparty_id=cp.id),
        user_id=project.owner_id,
    )
    # забор занят активной заявкой
    rows = await svc.list_shippable(pid, counterparty_id=cp.id)
    assert all(r.already_requested for r in rows)

    # отмена → CANCELLED
    cancelled = await svc.cancel_request(pid, pr.id, comment="не нужно")
    assert cancelled.status == "CANCELLED"

    # забор снова свободен и под него можно создать новую заявку
    rows2 = await svc.list_shippable(pid, counterparty_id=cp.id)
    assert all(not r.already_requested for r in rows2)
    pr2 = await svc.create_request(
        pid,
        PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_ids=[ships[0].id], counterparty_id=cp.id),
        user_id=project.owner_id,
    )
    assert pr2.status == "PENDING_REVIEW"


async def test_cancel_terminal_rejected(db_session, project):
    pid = project.id
    cp, ships = await _seed(db_session, pid, ["3000.00"])
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        pid,
        PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_ids=[ships[0].id], counterparty_id=cp.id),
        user_id=project.owner_id,
    )
    await svc.cancel_request(pid, pr.id)
    # повторная отмена уже отменённой (терминальной) → ValueError
    with pytest.raises(ValueError):
        await svc.cancel_request(pid, pr.id)
