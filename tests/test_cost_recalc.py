# ruff: noqa: RUF002, RUF003
"""
Tests for cost auto-recalculation (себестоимость) — services/cost/recalc.py and
the duty/area/weight mutation hooks that trigger it.
"""

from decimal import Decimal

import pytest
from sqlalchemy import select

from backend.models import CostOrder, CostOrderItem, DutyBasis, DutyRule, Nomenclature, VehicleStatus
from backend.services.cost.duty import (
    bulk_update_nomenclature_area,
    bulk_update_nomenclature_weight,
    delete_duty_exception,
    delete_duty_rule,
    upsert_duty_exception,
    upsert_duty_rule,
)
from backend.services.cost.recalc import (
    find_orders_by_articles,
    find_orders_by_barcodes,
    find_orders_by_subjects,
)


async def _seed_order_item(db, project_id, *, order_no, barcode, subject, article, area_m2=None, weight_kg=None):
    """One CHINA order (rate_eur=100, rate_cny=1, no delivery) with a single item + nomenclature."""
    db.add(
        CostOrder(
            order_no=order_no,
            project_id=project_id,
            country="CHINA",
            status=VehicleStatus.FORMING,
            rate_cny=Decimal("1"),
            rate_eur=Decimal("100"),
            rate_usd=Decimal("1"),
            delivery_cost_cny=Decimal("0"),
            delivery_cost_usd=Decimal("0"),
        )
    )
    db.add(Nomenclature(project_id=project_id, barcode=barcode, subject=subject, article_seller=article))
    item = CostOrderItem(
        project_id=project_id,
        order_no=order_no,
        barcode=barcode,
        subject=subject,
        article_seller=article,
        qty=1,
        price_cny=Decimal("10"),
        area_m2=area_m2,
        weight_kg=weight_kg,
        volume_m3=Decimal("0"),
        duty_rub=Decimal("0"),
        total_rub=Decimal("0"),
    )
    db.add(item)
    await db.commit()
    return item


async def _duty(db, order_no) -> Decimal:
    # one active item per order in these tests; no soft-deletes here
    row = (await db.execute(select(CostOrderItem).where(CostOrderItem.order_no == order_no))).scalar_one()
    await db.refresh(row)
    return row.duty_rub


# ═══════════════════════════════════════════════════════════════════════════════
# find_orders_by_* helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindAffectedOrders:
    @pytest.mark.asyncio
    async def test_by_barcode_subject_article(self, db_session, project):
        pid = project.id
        await _seed_order_item(
            db_session, pid, order_no=f"RC-A-{pid}", barcode="BC-1", subject="Ковры", article="ART-1"
        )

        assert await find_orders_by_barcodes(db_session, pid, ["BC-1"]) == [f"RC-A-{pid}"]
        assert await find_orders_by_subjects(db_session, pid, ["Ковры"]) == [f"RC-A-{pid}"]
        assert await find_orders_by_articles(db_session, pid, ["ART-1"]) == [f"RC-A-{pid}"]
        # unrelated keys → nothing
        assert await find_orders_by_barcodes(db_session, pid, ["NOPE"]) == []
        assert await find_orders_by_barcodes(db_session, pid, []) == []

    @pytest.mark.asyncio
    async def test_excludes_deleted_order(self, db_session, project):
        pid = project.id
        item = await _seed_order_item(
            db_session, pid, order_no=f"RC-DEL-{pid}", barcode="BC-DEL", subject="Ковры", article="ART-D"
        )
        order = (await db_session.execute(select(CostOrder).where(CostOrder.order_no == f"RC-DEL-{pid}"))).scalar_one()
        order.soft_delete()
        await db_session.commit()
        assert item is not None
        assert await find_orders_by_barcodes(db_session, pid, ["BC-DEL"]) == []

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        await _seed_order_item(
            db_session, project.id, order_no=f"RC-P-{project.id}", barcode="BC-X", subject="Ковры", article="A"
        )
        # other project asks for the same barcode → must not see it
        assert await find_orders_by_barcodes(db_session, other_project.id, ["BC-X"]) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-recalc triggers
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutoRecalcOnWeight:
    @pytest.mark.asyncio
    async def test_setting_weight_recalcs_weight_basis_duty(self, db_session, project):
        pid = project.id
        order_no = f"RC-WT-{pid}"
        await _seed_order_item(
            db_session, pid, order_no=order_no, barcode="BC-WT", subject="Наволочки", article="ART-W"
        )
        db_session.add(DutyRule(project_id=pid, subject="Наволочки", basis=DutyBasis.WEIGHT, rate=Decimal("0.61")))
        await db_session.commit()
        assert await _duty(db_session, order_no) == Decimal("0.00")  # no weight yet → 0

        # Enter weight → auto-recalc: 2 kg * 0.61 €/kg * 100 ₽/€ = 122.00
        await bulk_update_nomenclature_weight(db_session, pid, [{"barcode": "BC-WT", "weight_kg": 2}])
        assert await _duty(db_session, order_no) == Decimal("122.00")


class TestAutoRecalcOnArea:
    @pytest.mark.asyncio
    async def test_setting_area_recalcs_area_basis_duty(self, db_session, project):
        pid = project.id
        order_no = f"RC-AR-{pid}"
        await _seed_order_item(db_session, pid, order_no=order_no, barcode="BC-AR", subject="Ковры", article="ART-A")
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.38")))
        await db_session.commit()
        assert await _duty(db_session, order_no) == Decimal("0.00")

        # Enter area → auto-recalc: 2 m² * 0.38 €/m² * 100 = 76.00
        await bulk_update_nomenclature_area(db_session, pid, [{"barcode": "BC-AR", "area_m2": 2}])
        assert await _duty(db_session, order_no) == Decimal("76.00")


class TestAutoRecalcOnDutyRule:
    @pytest.mark.asyncio
    async def test_upsert_and_delete_rule_recalc(self, db_session, project):
        pid = project.id
        order_no = f"RC-RULE-{pid}"
        # area lives on nomenclature; recalc falls back to it
        await _seed_order_item(db_session, pid, order_no=order_no, barcode="BC-R", subject="Ковры", article="ART-R")
        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.barcode == "BC-R"))).scalar_one()
        nom.area_m2 = Decimal("2")
        await db_session.commit()

        # Create rule → auto-recalc: 2 * 0.38 * 100 = 76
        await upsert_duty_rule(
            db_session, pid, {"subject": "Ковры", "basis": "AREA", "rate": 0.38, "util_collect_rub": 0}
        )
        assert await _duty(db_session, order_no) == Decimal("76.00")

        # Update rate → auto-recalc: 2 * 0.5 * 100 = 100
        await upsert_duty_rule(
            db_session, pid, {"subject": "Ковры", "basis": "AREA", "rate": 0.5, "util_collect_rub": 0}
        )
        assert await _duty(db_session, order_no) == Decimal("100.00")

        # Delete rule → no rule → duty 0
        rule_id = (
            await db_session.execute(select(DutyRule.id).where(DutyRule.subject == "Ковры", DutyRule.project_id == pid))
        ).scalar_one()
        await delete_duty_rule(db_session, pid, rule_id)
        assert await _duty(db_session, order_no) == Decimal("0.00")


class TestAutoRecalcOnException:
    @pytest.mark.asyncio
    async def test_upsert_and_delete_exception_recalc(self, db_session, project):
        pid = project.id
        order_no = f"RC-EXC-{pid}"
        await _seed_order_item(db_session, pid, order_no=order_no, barcode="BC-E", subject="Ковры", article="ART-E")
        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.barcode == "BC-E"))).scalar_one()
        nom.area_m2 = Decimal("2")
        await db_session.commit()
        # category rule AREA → duty 76
        await upsert_duty_rule(
            db_session, pid, {"subject": "Ковры", "basis": "AREA", "rate": 0.38, "util_collect_rub": 0}
        )
        assert await _duty(db_session, order_no) == Decimal("76.00")

        # Exception INVOICE 6.5% for the article → auto-recalc: (cost 10 + delivery 0/2) * 6.5/100 = 0.65
        await upsert_duty_exception(db_session, pid, {"article_seller": "ART-E", "basis": "INVOICE", "rate": 6.5})
        assert await _duty(db_session, order_no) == Decimal("0.65")

        # Delete exception → reverts to category AREA rule → 76
        from backend.models import DutyException

        ex_id = (
            await db_session.execute(
                select(DutyException.id).where(DutyException.article_seller == "ART-E", DutyException.project_id == pid)
            )
        ).scalar_one()
        await delete_duty_exception(db_session, pid, ex_id)
        assert await _duty(db_session, order_no) == Decimal("76.00")
