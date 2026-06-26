# ruff: noqa: RUF002, RUF003
"""
Tests: payment-request service — авто-заполнение, стейт-машина, submit-гейт, изоляция.
"""

from datetime import date
from decimal import Decimal

import pytest

from backend.models.counterparty import Counterparty
from backend.models.payment_request import (
    PaymentRequest,
    PaymentRequestDocType,
    PaymentRequestDocument,
    PaymentRequestStatus,
)
from backend.models.warehouse import OutboundShipment, Warehouse
from backend.schemas.payment_request import PaymentRequestCreate
from backend.services import payment_request_status as prs
from backend.services.payment_request_service import (
    PaymentRequestService,
    PaymentRequestValidationError,
)
from backend.utils.time import utcnow

_ACC = "40702810900000000001"
_BIK = "044525225"
_INN = "7700000001"


async def _mk_carrier(db, project_id, inn=_INN):
    cp = Counterparty(
        project_id=project_id,
        inn=inn,
        name="ООО Перевозчик",
        primary_type="CARRIER",
        kpp="770001001",
        bank_account=_ACC,
        bik=_BIK,
        bank_name="Сбербанк",
    )
    db.add(cp)
    await db.flush()
    return cp


async def _mk_shipment(db, project_id, cp_id, cost=Decimal("15000.00")):
    wh = Warehouse(project_id=project_id, name="ФФ Москва", warehouse_type="FULFILLMENT")
    db.add(wh)
    await db.flush()
    shp = OutboundShipment(
        project_id=project_id,
        warehouse_id=wh.id,
        number="ОТГ-1",
        status="SHIPPED",
        counterparty_id=cp_id,
        pickup_cost=cost,
        pickup_date=date(2026, 6, 20),
        shipped_date=date(2026, 6, 21),
    )
    db.add(shp)
    await db.flush()
    return shp


@pytest.mark.asyncio
async def test_create_autofill_from_counterparty(db_session, project):
    cp = await _mk_carrier(db_session, project.id)
    shp = await _mk_shipment(db_session, project.id, cp.id, cost=Decimal("18500.00"))
    await db_session.commit()

    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        project.id,
        PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_id=shp.id),
        user_id=project.owner_id,
    )
    assert pr.status == PaymentRequestStatus.PENDING_REVIEW.value  # без черновика
    assert pr.amount == Decimal("18500.00")
    assert pr.pickup_date == date(2026, 6, 20)
    assert pr.payee_inn == _INN
    assert pr.payee_account == _ACC
    assert pr.payee_bik == _BIK
    assert pr.payee_name == "ООО Перевозчик"
    assert pr.number.startswith("ОПЛ-")
    assert pr.counterparty_id == cp.id


@pytest.mark.asyncio
async def test_create_manual_requires_amount(db_session, project):
    svc = PaymentRequestService(db_session)
    with pytest.raises(PaymentRequestValidationError):
        await svc.create_request(
            project.id,
            PaymentRequestCreate(source="MANUAL", payee_inn=_INN, payee_account=_ACC, payee_bik=_BIK, payee_name="X"),
            user_id=project.owner_id,
        )


@pytest.mark.asyncio
async def test_create_requires_requisites(db_session, project):
    """Реквизиты получателя обязательны на создании (заявка сразу «На проверке»)."""
    svc = PaymentRequestService(db_session)
    with pytest.raises(PaymentRequestValidationError):
        await svc.create_request(
            project.id,
            PaymentRequestCreate(source="MANUAL", payee_name="X", amount=Decimal("9000.00")),  # нет ИНН/счёта/БИК
            user_id=project.owner_id,
        )


@pytest.mark.asyncio
async def test_create_pending_review_docs_optional(db_session, project):
    """Документы опциональны: заявка создаётся сразу PENDING_REVIEW, submit идемпотентен."""
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        project.id,
        PaymentRequestCreate(
            source="MANUAL", payee_inn=_INN, payee_account=_ACC, payee_bik=_BIK,
            payee_name="ООО Перевозчик", amount=Decimal("9000.00"),
        ),
        user_id=project.owner_id,
    )
    assert pr.status == PaymentRequestStatus.PENDING_REVIEW.value
    out = await svc.submit_request(project.id, pr.id)  # идемпотентно, без документов
    assert out.status == PaymentRequestStatus.PENDING_REVIEW.value


@pytest.mark.asyncio
async def test_submit_succeeds_with_documents(db_session, project):
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        project.id,
        PaymentRequestCreate(
            source="MANUAL", payee_inn=_INN, payee_account=_ACC, payee_bik=_BIK,
            payee_name="ООО Перевозчик", amount=Decimal("9000.00"),
        ),
        user_id=project.owner_id,
    )
    for dt in (PaymentRequestDocType.INVOICE.value, PaymentRequestDocType.ACT.value):
        db_session.add(
            PaymentRequestDocument(
                project_id=project.id, payment_request_id=pr.id, minio_path=f"x/{dt}",
                doc_type=dt, uploaded_at=utcnow(),
            )
        )
    await db_session.commit()

    out = await svc.submit_request(project.id, pr.id, comment="go")
    assert out.status == PaymentRequestStatus.PENDING_REVIEW.value


def test_transition_guard():
    prs.check_transition(PaymentRequestStatus.DRAFT, PaymentRequestStatus.PENDING_REVIEW)  # ok
    prs.check_transition(PaymentRequestStatus.DRAFT_CREATED, PaymentRequestStatus.PAID)  # ok
    with pytest.raises(ValueError):
        prs.check_transition(PaymentRequestStatus.DRAFT, PaymentRequestStatus.PAID)
    with pytest.raises(ValueError):
        prs.check_transition(PaymentRequestStatus.PAID, PaymentRequestStatus.DRAFT)


@pytest.mark.asyncio
async def test_project_isolation(db_session, project, other_project):
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        project.id,
        PaymentRequestCreate(
            source="MANUAL", payee_inn=_INN, payee_account=_ACC, payee_bik=_BIK,
            payee_name="X", amount=Decimal("100.00"),
        ),
        user_id=project.owner_id,
    )
    assert await svc.get_request(other_project.id, pr.id) is None
    assert await svc.get_request(project.id, pr.id) is not None


@pytest.mark.asyncio
async def test_soft_deleted_excluded_from_list(db_session, project):
    svc = PaymentRequestService(db_session)
    pr = await svc.create_request(
        project.id,
        PaymentRequestCreate(
            source="MANUAL", payee_inn=_INN, payee_account=_ACC, payee_bik=_BIK,
            payee_name="X", amount=Decimal("100.00"),
        ),
        user_id=project.owner_id,
    )
    db_pr = await svc.get_request(project.id, pr.id)
    db_pr.soft_delete()
    await db_session.commit()

    rows, total, _ = await svc.list_requests(project.id)
    assert total == 0
    assert all(r.id != pr.id for r in rows)


@pytest.mark.asyncio
async def test_list_shippable_already_requested_flag(db_session, project):
    cp = await _mk_carrier(db_session, project.id)
    shp = await _mk_shipment(db_session, project.id, cp.id)
    # link the shipment to a (fake) assembly request id so it passes the filter
    from sqlalchemy import text

    ar_id = (
        await db_session.execute(
            text(
                "INSERT INTO assembly_requests (project_id, warehouse_id, number, status, pallets_count, "
                "pallet_weight_kg, package_type, created_at) VALUES (:p, :w, 'ЗАК-1', 'SHIPPED', 1, 100, 'BOX', NOW()) "
                "RETURNING id"
            ),
            {"p": project.id, "w": shp.warehouse_id},
        )
    ).scalar()
    shp.assembly_request_id = ar_id
    await db_session.commit()

    svc = PaymentRequestService(db_session)
    rows = await svc.list_shippable(project.id)
    assert any(r.outbound_shipment_id == shp.id and not r.already_requested for r in rows)

    await svc.create_request(
        project.id,
        PaymentRequestCreate(source="COUNTERPARTY", outbound_shipment_id=shp.id),
        user_id=project.owner_id,
    )
    rows2 = await svc.list_shippable(project.id)
    match = next(r for r in rows2 if r.outbound_shipment_id == shp.id)
    assert match.already_requested is True
    assert match.carrier_inn == _INN


@pytest.mark.asyncio
async def test_archive_hides_from_default_and_shows_in_archived(db_session, project):
    from sqlalchemy import text

    cp = await _mk_carrier(db_session, project.id)
    shp = await _mk_shipment(db_session, project.id, cp.id)
    ar_id = (
        await db_session.execute(
            text(
                "INSERT INTO assembly_requests (project_id, warehouse_id, number, status, pallets_count, "
                "pallet_weight_kg, package_type, created_at) VALUES (:p, :w, 'ЗАК-A', 'SHIPPED', 1, 100, 'BOX', NOW()) "
                "RETURNING id"
            ),
            {"p": project.id, "w": shp.warehouse_id},
        )
    ).scalar()
    shp.assembly_request_id = ar_id
    await db_session.commit()

    svc = PaymentRequestService(db_session)
    assert any(r.outbound_shipment_id == shp.id for r in await svc.list_shippable(project.id))

    assert await svc.archive_shipments(project.id, [shp.id], project.owner_id) == 1
    assert all(r.outbound_shipment_id != shp.id for r in await svc.list_shippable(project.id))
    assert any(r.outbound_shipment_id == shp.id for r in await svc.list_shippable(project.id, archived=True))

    # idempotent re-archive adds nothing
    assert await svc.archive_shipments(project.id, [shp.id], project.owner_id) == 0

    assert await svc.unarchive_shipments(project.id, [shp.id]) == 1
    assert any(r.outbound_shipment_id == shp.id for r in await svc.list_shippable(project.id))
