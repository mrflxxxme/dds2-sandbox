"""
Tests for the dashboard operational «action-center» counters
(backend.services.reports.dashboard_ops.get_dashboard_operations).

Covers the shape contract, the empty-project baseline, and project isolation of
the pending-payment counter.
"""

from decimal import Decimal

import pytest

from backend.schemas.payment_request import PaymentRequestCreate
from backend.services.payment_request_service import PaymentRequestService
from backend.services.reports.dashboard_ops import get_dashboard_operations as _cached_ops

# В тестах зовём небкэшированную функцию (@cached, TTL 60с): базлайн-вызовы иначе
# зафиксируют значение в Redis, а create_request dashboard_ops-префикс не сбрасывает.
get_dashboard_operations = _cached_ops.__wrapped__

_ACC = "40702810900000000001"
_BIK = "044525225"
_INN = "7700000001"

_EXPECTED_KEYS = {
    "payments_pending",
    "fbo_orphans",
    "fbo_partial",
    "fbo_excess",
    "returns_pending",
    "returns_soon_expire",
    "sync_errors_24h",
    "ff_unlinked",
    "vehicles_in_transit",
    "vehicles_forming",
    "supply_items_in_transit",
    "supply_amount_cny",
}


@pytest.mark.asyncio
async def test_empty_project_returns_full_shape_all_zero(db_session, project):
    """Fresh project → every counter present and zero (no data, no failures).

    payments_pending — исключение: «общие» заявки (project_id IS NULL) видны каждому
    проекту by design, а в полном прогоне сьюта их могли оставить соседние тесты →
    сверяем не с нулём, а с независимым raw-подсчётом NULL-project PENDING_REVIEW.
    """
    from sqlalchemy import text as _text

    out = await get_dashboard_operations(db_session, project.id)
    assert set(out.keys()) == _EXPECTED_KEYS
    assert all(out[k] == 0 for k in _EXPECTED_KEYS - {"payments_pending"})
    general = (
        await db_session.execute(
            _text(
                "SELECT count(*) FROM payment_request "
                "WHERE project_id IS NULL AND status = 'PENDING_REVIEW' AND is_deleted = false"
            )
        )
    ).scalar()
    assert out["payments_pending"] == general


@pytest.mark.asyncio
async def test_pending_payment_counted_and_project_isolated(db_session, project, other_project):
    """A PENDING_REVIEW request raises payments_pending for its project only."""
    base_own = (await get_dashboard_operations(db_session, project.id))["payments_pending"]
    base_other = (await get_dashboard_operations(db_session, other_project.id))["payments_pending"]

    svc = PaymentRequestService(db_session)
    await svc.create_request(
        project.id,
        PaymentRequestCreate(
            source="MANUAL",
            payee_inn=_INN,
            payee_account=_ACC,
            payee_bik=_BIK,
            payee_name="ООО Перевозчик",
            amount=Decimal("9000.00"),
        ),
        user_id=project.owner_id,
    )
    await db_session.commit()

    own = await get_dashboard_operations(db_session, project.id)
    assert own["payments_pending"] == base_own + 1

    # Named-project isolation: project X's request must not leak into project Y
    # (общие заявки project_id IS NULL шарятся by design — поэтому дельты, не абсолюты).
    other = await get_dashboard_operations(db_session, other_project.id)
    assert other["payments_pending"] == base_other
