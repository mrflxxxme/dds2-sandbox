"""Recalc self-heals предмет/артикул from Nomenclature when the item snapshot is empty.

Regression: a new SKU added to a vehicle BEFORE its WB card exists stores NULL
subject/article_seller (the card isn't in Nomenclature yet). The daily nomenclature
sync lands the card later, but the vehicle line keeps its stale NULL snapshot — and
nothing back-fills it, so предмет/артикул AND пошлина (matched by subject) stay empty
forever. recalculate_order_items must heal the snapshot from Nomenclature on recalc.

Prod case: V-0027 / project default — 12 carpet SKUs entered ahead of their cards.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models import (
    CostOrder,
    CostOrderItem,
    DutyBasis,
    DutyRule,
    Nomenclature,
    VehicleStatus,
)
from backend.services.cost.items import recalculate_order_items


def _vno() -> str:
    return f"V-{uuid.uuid4().hex[:8]}"


class TestRecalcSelfHealSubjectArticle:
    @pytest.mark.asyncio
    async def test_backfills_empty_snapshot_from_nomenclature(self, db_session, project):
        """Empty subject/article snapshot is filled from Nomenclature, and пошлина computes."""
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.61")))
        db_session.add(
            Nomenclature(
                project_id=pid,
                barcode="BC-NEW",
                subject="Ковры",
                article_seller="120х170_новинка",
                area_m2=Decimal("2.04"),
            )
        )
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        # Added before the card existed → empty snapshot.
        db_session.add(
            CostOrderItem(
                project_id=pid,
                order_no=vno,
                barcode="BC-NEW",
                qty=10,
                price_cny=Decimal("45"),
                subject=None,
                article_seller=None,
            )
        )
        await db_session.commit()

        updated, err = await recalculate_order_items(db_session, pid, vno)
        assert err is None
        assert updated == 1

        item = (
            await db_session.execute(
                select(CostOrderItem).where(CostOrderItem.order_no == vno, CostOrderItem.is_deleted == False)
            )
        ).scalar_one()
        assert item.subject == "Ковры"
        assert item.article_seller == "120х170_новинка"
        # subject подобран → правило пошлины (AREA) сработало → пошлина > 0 (была 0).
        assert item.duty_rub is not None and item.duty_rub > 0

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_snapshot(self, db_session, project):
        """An existing snapshot (manual / from factory order) wins over Nomenclature."""
        pid = project.id
        db_session.add(
            Nomenclature(project_id=pid, barcode="BC-KEEP", subject="ИзНоменклатуры", article_seller="art_nom")
        )
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        db_session.add(
            CostOrderItem(
                project_id=pid,
                order_no=vno,
                barcode="BC-KEEP",
                qty=5,
                price_cny=Decimal("10"),
                subject="Ручной",
                article_seller="art_manual",
            )
        )
        await db_session.commit()

        await recalculate_order_items(db_session, pid, vno)

        item = (
            await db_session.execute(
                select(CostOrderItem).where(CostOrderItem.order_no == vno, CostOrderItem.is_deleted == False)
            )
        ).scalar_one()
        assert item.subject == "Ручной"
        assert item.article_seller == "art_manual"

    @pytest.mark.asyncio
    async def test_fills_only_missing_side(self, db_session, project):
        """If only article is empty, subject is kept and only article is filled."""
        pid = project.id
        db_session.add(
            Nomenclature(project_id=pid, barcode="BC-HALF", subject="ИзНоменклатуры", article_seller="art_nom")
        )
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        db_session.add(
            CostOrderItem(
                project_id=pid,
                order_no=vno,
                barcode="BC-HALF",
                qty=3,
                price_cny=Decimal("10"),
                subject="Ковры",
                article_seller=None,
            )
        )
        await db_session.commit()

        await recalculate_order_items(db_session, pid, vno)

        item = (
            await db_session.execute(
                select(CostOrderItem).where(CostOrderItem.order_no == vno, CostOrderItem.is_deleted == False)
            )
        ).scalar_one()
        assert item.subject == "Ковры"  # own value kept
        assert item.article_seller == "art_nom"  # filled from nomenclature
