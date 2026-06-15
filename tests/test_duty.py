"""
Tests for cost/duty — Duty Rules CRUD.
Uses DB fixtures from conftest.py.
"""

from decimal import Decimal

import pytest

from backend.models import CostOrder, CostOrderItem, Nomenclature
from backend.services.cost.duty import (
    delete_duty_exception,
    delete_duty_rule,
    get_duty_exceptions,
    get_duty_rules,
    upsert_duty_exception,
    upsert_duty_rule,
)
from backend.services.cost.items import recalculate_order_items

# ═══════════════════════════════════════════════════════════════════════════════
# get_duty_rules
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDutyRules:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no duty rules."""
        rules = await get_duty_rules(db_session, project.id)
        assert rules == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, db_session, project):
        """Limit parameter works."""
        rules = await get_duty_rules(db_session, project.id, limit=5)
        assert len(rules) <= 5


# ═══════════════════════════════════════════════════════════════════════════════
# upsert_duty_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpsertDutyRule:
    @pytest.mark.asyncio
    async def test_create_new(self, db_session, project):
        """Create a new duty rule."""
        ok, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Футболки",
                "basis": "INVOICE",
                "rate": 0.10,
                "util_collect_rub": 150,
                "note": "Test duty rule",
            },
        )
        assert ok is True
        assert err is None

        rules = await get_duty_rules(db_session, project.id)
        assert any(r.subject == "Футболки" for r in rules)

    @pytest.mark.asyncio
    async def test_update_existing(self, db_session, project):
        """Upserting same subject updates the existing rule."""
        await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Штаны",
                "rate": 0.05,
                "util_collect_rub": 100,
            },
        )
        ok, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Штаны",
                "rate": 0.08,
                "util_collect_rub": 200,
            },
        )
        assert ok is True

        rules = await get_duty_rules(db_session, project.id)
        rule = next(r for r in rules if r.subject == "Штаны")
        from decimal import Decimal

        assert rule.rate == Decimal("0.08")

    @pytest.mark.asyncio
    async def test_empty_subject_rejected(self, db_session, project):
        """Empty subject returns error."""
        result, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "",
                "rate": 0.1,
            },
        )
        assert result is None
        assert err == "subject required"

    @pytest.mark.asyncio
    async def test_missing_subject_rejected(self, db_session, project):
        """Missing subject key returns error."""
        result, err = await upsert_duty_rule(
            db_session,
            project.id,
            {
                "rate": 0.1,
            },
        )
        assert result is None
        assert err == "subject required"


# ═══════════════════════════════════════════════════════════════════════════════
# delete_duty_rule
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteDutyRule:
    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session, project):
        """Soft delete a duty rule."""
        await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "ToDelete",
                "rate": 0.01,
                "util_collect_rub": 0,
            },
        )
        rules = await get_duty_rules(db_session, project.id)
        rule_id = next(r.id for r in rules if r.subject == "ToDelete")

        result = await delete_duty_rule(db_session, project.id, rule_id)
        assert result is True

        rules_after = await get_duty_rules(db_session, project.id)
        assert not any(r.subject == "ToDelete" for r in rules_after)

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, db_session, project):
        """Deleting non-existent rule returns None."""
        result = await delete_duty_rule(db_session, project.id, 999999)
        assert result is None

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Cannot delete rule from another project."""
        await upsert_duty_rule(
            db_session,
            project.id,
            {
                "subject": "Isolated",
                "rate": 0.05,
                "util_collect_rub": 50,
            },
        )
        rules = await get_duty_rules(db_session, project.id)
        rule_id = next(r.id for r in rules if r.subject == "Isolated")

        # Try to delete from other project
        result = await delete_duty_rule(db_session, other_project.id, rule_id)
        assert result is None

        # Original project still has it
        rules_after = await get_duty_rules(db_session, project.id)
        assert any(r.subject == "Isolated" for r in rules_after)


# ═══════════════════════════════════════════════════════════════════════════════
# Duty Exceptions — CRUD (article-level overrides)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDutyExceptionCrud:
    @pytest.mark.asyncio
    async def test_create_and_get(self, db_session, project):
        ok, err = await upsert_duty_exception(
            db_session,
            project.id,
            {"article_seller": "FLOOR-001", "basis": "INVOICE", "rate": 6.5, "note": "exc"},
        )
        assert ok is True
        assert err is None
        rows = await get_duty_exceptions(db_session, project.id)
        exc = next(e for e in rows if e.article_seller == "FLOOR-001")
        assert exc.basis == "INVOICE"
        assert exc.rate == Decimal("6.5")

    @pytest.mark.asyncio
    async def test_update_existing(self, db_session, project):
        """Upserting the same article updates basis+rate in place."""
        await upsert_duty_exception(db_session, project.id, {"article_seller": "FLOOR-UPD", "basis": "AREA", "rate": 1})
        ok, _ = await upsert_duty_exception(
            db_session, project.id, {"article_seller": "FLOOR-UPD", "basis": "INVOICE", "rate": 7}
        )
        assert ok is True
        rows = await get_duty_exceptions(db_session, project.id)
        exc = next(e for e in rows if e.article_seller == "FLOOR-UPD")
        assert exc.basis == "INVOICE"
        assert exc.rate == Decimal("7")
        # exactly one active row for this article (no duplicate)
        assert sum(1 for e in rows if e.article_seller == "FLOOR-UPD") == 1

    @pytest.mark.asyncio
    async def test_empty_article_rejected(self, db_session, project):
        result, err = await upsert_duty_exception(db_session, project.id, {"basis": "INVOICE", "rate": 5})
        assert result is None
        assert err == "article_seller required"

    @pytest.mark.asyncio
    async def test_soft_delete_then_readd(self, db_session, project):
        """Partial unique index lets the same article be re-added after soft-delete
        (the SoftDelete + unique mine — see learnings.md)."""
        await upsert_duty_exception(
            db_session, project.id, {"article_seller": "FLOOR-RE", "basis": "INVOICE", "rate": 5}
        )
        rows = await get_duty_exceptions(db_session, project.id)
        exc_id = next(e.id for e in rows if e.article_seller == "FLOOR-RE")

        assert await delete_duty_exception(db_session, project.id, exc_id) is True
        rows_after = await get_duty_exceptions(db_session, project.id)
        assert not any(e.article_seller == "FLOOR-RE" for e in rows_after)

        # Re-add same article — must NOT raise IntegrityError
        ok, err = await upsert_duty_exception(
            db_session, project.id, {"article_seller": "FLOOR-RE", "basis": "AREA", "rate": 0.4}
        )
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        await upsert_duty_exception(
            db_session, project.id, {"article_seller": "FLOOR-ISO", "basis": "INVOICE", "rate": 5}
        )
        rows = await get_duty_exceptions(db_session, project.id)
        exc_id = next(e.id for e in rows if e.article_seller == "FLOOR-ISO")

        # cannot delete from another project
        assert await delete_duty_exception(db_session, other_project.id, exc_id) is None
        # not visible to the other project
        assert await get_duty_exceptions(db_session, other_project.id) == [] or all(
            e.article_seller != "FLOOR-ISO" for e in await get_duty_exceptions(db_session, other_project.id)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Duty Exceptions — calculation precedence
# ═══════════════════════════════════════════════════════════════════════════════


class TestDutyExceptionCalc:
    @pytest.mark.asyncio
    async def test_exception_overrides_basis_util_stays_category(self, db_session, project):
        """Article exception overrides the duty basis+rate; util стаётся из категории.

        Category «Напольные покрытия» = AREA 0.38 €/m² + утиль 5 ₽.
        Article FLOOR-EXC = INVOICE 6.5%. A plain article in the same category
        keeps the AREA rule.
        """
        order_no = f"EXC-CALC-{project.id}"
        await upsert_duty_rule(
            db_session,
            project.id,
            {"subject": "Напольные покрытия", "basis": "AREA", "rate": 0.38, "util_collect_rub": 5},
        )
        await upsert_duty_exception(
            db_session, project.id, {"article_seller": "FLOOR-EXC", "basis": "INVOICE", "rate": 6.5}
        )

        db_session.add(
            CostOrder(
                order_no=order_no,
                project_id=project.id,
                country="CHINA",
                rate_cny=Decimal("1"),
                rate_eur=Decimal("1"),
                rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"),
                delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add_all(
            [
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode="BC-EXC",
                    subject="Напольные покрытия",
                    article_seller="FLOOR-EXC",
                    qty=1,
                    price_cny=Decimal("10"),
                    area_m2=Decimal("2"),
                    weight_kg=Decimal("0"),
                    volume_m3=Decimal("0"),
                ),
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode="BC-CAT",
                    subject="Напольные покрытия",
                    article_seller="FLOOR-PLAIN",
                    qty=1,
                    price_cny=Decimal("10"),
                    area_m2=Decimal("2"),
                    weight_kg=Decimal("0"),
                    volume_m3=Decimal("0"),
                ),
            ]
        )
        await db_session.commit()

        updated, err = await recalculate_order_items(db_session, project.id, order_no, vat_rate=Decimal("22"))
        assert err is None
        assert updated == 2

        items = (await db_session.execute(_select_items(order_no))).scalars().all()
        by_bc = {i.barcode: i for i in items}
        # Exception item: INVOICE 6.5% of cost (10) = 0.65; util from category = 5
        assert by_bc["BC-EXC"].duty_rub == Decimal("0.65")
        assert by_bc["BC-EXC"].util_rub == Decimal("5.00")
        # Plain item in same category: AREA 2 * 0.38 * 1 = 0.76; util = 5
        assert by_bc["BC-CAT"].duty_rub == Decimal("0.76")
        assert by_bc["BC-CAT"].util_rub == Decimal("5.00")


def _select_items(order_no: str):
    from sqlalchemy import select

    return select(CostOrderItem).where(CostOrderItem.order_no == order_no, CostOrderItem.is_deleted == False)  # noqa: E712


class TestWeightFallbackCalc:
    @pytest.mark.asyncio
    async def test_recalc_uses_nomenclature_weight_when_item_has_none(self, db_session, project):
        """WEIGHT-basis duty falls back to Nomenclature.weight_kg when the item weight is empty."""
        order_no = f"WT-CALC-{project.id}"
        await upsert_duty_rule(
            db_session, project.id, {"subject": "Наволочки", "basis": "WEIGHT", "rate": 0.61, "util_collect_rub": 0}
        )
        db_session.add(
            Nomenclature(project_id=project.id, barcode="BC-WT", subject="Наволочки", weight_kg=Decimal("2"))
        )
        db_session.add(
            CostOrder(
                order_no=order_no,
                project_id=project.id,
                country="CHINA",
                rate_cny=Decimal("1"),
                rate_eur=Decimal("100"),
                rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"),
                delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add(
            CostOrderItem(
                project_id=project.id,
                order_no=order_no,
                barcode="BC-WT",
                subject="Наволочки",
                qty=1,
                price_cny=Decimal("10"),
                weight_kg=None,  # no weight on the item — must fall back to Nomenclature
                volume_m3=Decimal("0"),
            )
        )
        await db_session.commit()

        updated, err = await recalculate_order_items(db_session, project.id, order_no, vat_rate=Decimal("22"))
        assert err is None
        assert updated == 1

        items = (await db_session.execute(_select_items(order_no))).scalars().all()
        # WEIGHT duty = weight(2) * rate(0.61) * rate_eur(100) = 122.00
        assert items[0].duty_rub == Decimal("122.00")

    @pytest.mark.asyncio
    async def test_recalc_propagates_weight_across_sources_same_barcode(self, db_session, project):
        """Same barcode from 2 factory-order sources, weight declared on only one of
        them and NO Nomenclature reference: the declared weight must apply to the
        weightless sibling too. Otherwise WEIGHT-basis duty for that barcode is
        silently halved (the weightless row computes duty=0). См. инвариант merge
        (factory_orders._merge: один баркод ⇒ один вес)."""
        order_no = f"WT-SRC-{project.id}"
        await upsert_duty_rule(
            db_session, project.id, {"subject": "Наволочки", "basis": "WEIGHT", "rate": 0.61, "util_collect_rub": 0}
        )
        # No Nomenclature weight reference on purpose — weight lives only on source A.
        db_session.add(
            CostOrder(
                order_no=order_no,
                project_id=project.id,
                country="CHINA",
                rate_cny=Decimal("1"),
                rate_eur=Decimal("100"),
                rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"),
                delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add_all(
            [
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode="BC-SRC",
                    subject="Наволочки",
                    qty=1,
                    price_cny=Decimal("10"),
                    weight_kg=Decimal("2"),  # source A: weight declared
                    volume_m3=Decimal("0"),
                ),
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode="BC-SRC",
                    subject="Наволочки",
                    qty=1,
                    price_cny=Decimal("10"),
                    weight_kg=None,  # source B: weight blank → must inherit source A's 2 kg
                    volume_m3=Decimal("0"),
                ),
            ]
        )
        await db_session.commit()

        updated, err = await recalculate_order_items(db_session, project.id, order_no, vat_rate=Decimal("22"))
        assert err is None
        assert updated == 2

        items = (await db_session.execute(_select_items(order_no))).scalars().all()
        # Both source rows: WEIGHT duty = 2 * 0.61 * 100 = 122.00 (B no longer 0).
        assert {i.duty_rub for i in items} == {Decimal("122.00")}

    @pytest.mark.asyncio
    async def test_recalc_propagates_area_across_sources_same_barcode(self, db_session, project):
        """Same as weight, but AREA-basis: area declared on one source applies to the
        sibling with blank area — AREA duty must not be halved."""
        order_no = f"AR-SRC-{project.id}"
        await upsert_duty_rule(
            db_session,
            project.id,
            {"subject": "Напольные покрытия", "basis": "AREA", "rate": 0.38, "util_collect_rub": 0},
        )
        db_session.add(
            CostOrder(
                order_no=order_no,
                project_id=project.id,
                country="CHINA",
                rate_cny=Decimal("1"),
                rate_eur=Decimal("100"),
                rate_usd=Decimal("1"),
                delivery_cost_cny=Decimal("0"),
                delivery_cost_usd=Decimal("0"),
            )
        )
        db_session.add_all(
            [
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode="BC-AR",
                    subject="Напольные покрытия",
                    qty=1,
                    price_cny=Decimal("10"),
                    area_m2=Decimal("2"),  # source A: area declared
                    volume_m3=Decimal("0"),
                ),
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode="BC-AR",
                    subject="Напольные покрытия",
                    qty=1,
                    price_cny=Decimal("10"),
                    area_m2=None,  # source B: area blank → must inherit source A's 2 m²
                    volume_m3=Decimal("0"),
                ),
            ]
        )
        await db_session.commit()

        updated, err = await recalculate_order_items(db_session, project.id, order_no, vat_rate=Decimal("22"))
        assert err is None
        assert updated == 2

        items = (await db_session.execute(_select_items(order_no))).scalars().all()
        # Both source rows: AREA duty = 2 * 0.38 * 100 = 76.00 (B no longer 0).
        assert {i.duty_rub for i in items} == {Decimal("76.00")}
