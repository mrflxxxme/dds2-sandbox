# ruff: noqa: RUF001, RUF002, RUF003
"""Tests for box_multiplicity_service — per-SKU effective ppb resolution."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrderItem
from backend.models.cost import CostOrder, Nomenclature
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
from backend.services.box_multiplicity_service import (
    get_box_multiplicity_table,
    set_box_qty_override,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _create_nomenclature(
    db: AsyncSession,
    project_id: int,
    *,
    barcode: str,
    article_seller: str,
    article_wb: int,
    box_qty_override: int | None = None,
) -> Nomenclature:
    nom = Nomenclature(
        project_id=project_id,
        barcode=barcode,
        article_seller=article_seller,
        article_wb=article_wb,
        brand="TestBrand",
        subject="TestSubject",
        box_qty_override=box_qty_override,
    )
    db.add(nom)
    await db.commit()
    return nom


async def _create_foi(
    db: AsyncSession,
    project_id: int,
    *,
    barcode: str,
    pcs_per_box: int | None = None,
    mix_pcs_per_box: int | None = None,
    mix_group_id: str | None = None,
) -> FactoryOrderItem:
    fo = FactoryOrder(
        project_id=project_id,
        order_number=f"FO-{_uid()}",
        factory_name="TestFactory",
        status="FORMING",
    )
    db.add(fo)
    await db.flush()
    foi = FactoryOrderItem(
        project_id=project_id,
        factory_order_id=fo.id,
        barcode=barcode,
        qty=100,
        price_cny=Decimal("10"),
        pcs_per_box=pcs_per_box,
        mix_pcs_per_box=mix_pcs_per_box,
        mix_group_id=mix_group_id,
    )
    db.add(foi)
    await db.commit()
    return foi


async def _create_vehicle_with_item(
    db: AsyncSession,
    project_id: int,
    *,
    barcode: str,
    status: VehicleStatus = VehicleStatus.DELIVERED,
    arrival_date: date | None = None,
    pcs_per_box_override: int | None = None,
    factory_order_item_id: int | None = None,
) -> CostOrderItem:
    order_no = f"V-{_uid()}"
    co = CostOrder(
        project_id=project_id,
        order_no=order_no,
        status=status,
        actual_arrival_date=arrival_date,
        delivery_cost_cny=Decimal("0"),
        delivery_cost_usd=Decimal("0"),
        delivery_cost_rub=Decimal("0"),
        rate_cny=Decimal("1"),
        rate_eur=Decimal("1"),
        rate_usd=Decimal("1"),
    )
    db.add(co)
    await db.flush()
    coi = CostOrderItem(
        project_id=project_id,
        order_no=order_no,
        barcode=barcode,
        qty=10,
        price_cny=Decimal("10"),
        pcs_per_box_override=pcs_per_box_override,
        factory_order_item_id=factory_order_item_id,
    )
    db.add(coi)
    await db.commit()
    return coi


# ═══════════════════════════════════════════════════════════════════════════════
# get_box_multiplicity_table
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetBoxMultiplicityTable:
    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        rows = await get_box_multiplicity_table(db_session, project.id)
        assert rows == []

    @pytest.mark.asyncio
    async def test_sku_no_box_data(self, db_session: AsyncSession, project):
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=f"BC-{_uid()}",
            article_seller="ART-1",
            article_wb=10001,
        )
        rows = await get_box_multiplicity_table(db_session, project.id)
        assert len(rows) == 1
        assert rows[0]["nm_id"] == 10001
        assert rows[0]["box_qty_override"] is None
        assert rows[0]["box_qty_from_vehicle"] is None
        assert rows[0]["box_qty_from_factory"] is None
        assert rows[0]["effective_box_qty"] is None

    @pytest.mark.asyncio
    async def test_factory_only_fallback(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="ART-2",
            article_wb=10002,
        )
        await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10002)
        assert rows[0]["box_qty_from_factory"] == 10
        assert rows[0]["box_qty_from_vehicle"] is None
        assert rows[0]["effective_box_qty"] == 10

    @pytest.mark.asyncio
    async def test_vehicle_inherits_foi_when_no_override(self, db_session: AsyncSession, project):
        """DELIVERED vehicle с factory_order_item_id, но без pcs_per_box_override
        → from_vehicle берёт ppb из FOI."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-3", article_wb=10003)
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=15)
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            arrival_date=date(2026, 1, 10),
            pcs_per_box_override=None,
            factory_order_item_id=foi.id,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10003)
        assert rows[0]["box_qty_from_vehicle"] == 15
        assert rows[0]["box_qty_from_factory"] == 15
        assert rows[0]["effective_box_qty"] == 15

    @pytest.mark.asyncio
    async def test_vehicle_override_beats_foi(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-4", article_wb=10004)
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            arrival_date=date(2026, 1, 10),
            pcs_per_box_override=20,
            factory_order_item_id=foi.id,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10004)
        assert rows[0]["box_qty_from_vehicle"] == 20
        assert rows[0]["effective_box_qty"] == 20

    @pytest.mark.asyncio
    async def test_manual_override_top_priority(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="ART-5",
            article_wb=10005,
            box_qty_override=99,
        )
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            arrival_date=date(2026, 1, 10),
            pcs_per_box_override=20,
            factory_order_item_id=foi.id,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10005)
        assert rows[0]["box_qty_override"] == 99
        assert rows[0]["box_qty_from_vehicle"] == 20
        assert rows[0]["box_qty_from_factory"] == 10
        assert rows[0]["effective_box_qty"] == 99

    @pytest.mark.asyncio
    async def test_latest_delivered_vehicle_wins(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-6", article_wb=10006)
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)
        # Old vehicle ppb=20, newer ppb=30. Newer must win.
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            arrival_date=date(2026, 1, 1),
            pcs_per_box_override=20,
            factory_order_item_id=foi.id,
        )
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            arrival_date=date(2026, 2, 1),
            pcs_per_box_override=30,
            factory_order_item_id=foi.id,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10006)
        assert rows[0]["box_qty_from_vehicle"] == 30

    @pytest.mark.asyncio
    async def test_non_delivered_vehicle_ignored(self, db_session: AsyncSession, project):
        """Машина в IN_TRANSIT не должна давать ppb — только DELIVERED."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-7", article_wb=10007)
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            status=VehicleStatus.SHIPPED,
            arrival_date=None,
            pcs_per_box_override=99,
            factory_order_item_id=foi.id,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10007)
        assert rows[0]["box_qty_from_vehicle"] is None
        assert rows[0]["box_qty_from_factory"] == 10
        assert rows[0]["effective_box_qty"] == 10

    @pytest.mark.asyncio
    async def test_mix_ppb_used_when_mix_group_set(self, db_session: AsyncSession, project):
        """FactoryOrderItem с mix_group_id и mix_pcs_per_box — берём mix-вариант."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-8", article_wb=10008)
        await _create_foi(
            db_session,
            project.id,
            barcode=bc,
            pcs_per_box=10,
            mix_pcs_per_box=7,
            mix_group_id="grp-1",
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10008)
        assert rows[0]["box_qty_from_factory"] == 7

    @pytest.mark.asyncio
    async def test_only_skus_with_article_wb_returned(self, db_session: AsyncSession, project):
        """Nomenclature без article_wb (нет nm_id) не попадает в таблицу."""
        nom = Nomenclature(
            project_id=project.id,
            barcode=f"BC-NOWB-{_uid()}",
            article_seller="ART-NOWB",
            article_wb=None,
            brand="X",
        )
        db_session.add(nom)
        await db_session.commit()

        rows = await get_box_multiplicity_table(db_session, project.id)
        nm_ids = [r["nm_id"] for r in rows]
        assert None not in nm_ids


# ═══════════════════════════════════════════════════════════════════════════════
# set_box_qty_override
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetBoxQtyOverride:
    @pytest.mark.asyncio
    async def test_set_value(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=20001)

        ok = await set_box_qty_override(db_session, project.id, 20001, 42)
        assert ok is True

        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.article_wb == 20001))).scalar_one()
        assert nom.box_qty_override == 42

    @pytest.mark.asyncio
    async def test_clear_with_none(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=20002,
            box_qty_override=10,
        )

        ok = await set_box_qty_override(db_session, project.id, 20002, None)
        assert ok is True

        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.article_wb == 20002))).scalar_one()
        assert nom.box_qty_override is None

    @pytest.mark.asyncio
    async def test_unknown_nm_returns_false(self, db_session: AsyncSession, project):
        ok = await set_box_qty_override(db_session, project.id, 99999999, 10)
        assert ok is False

    @pytest.mark.asyncio
    async def test_other_project_isolation(
        self,
        db_session: AsyncSession,
        project,
        other_project,
    ):
        """Override в другом проекте не должен затронуть наш SKU."""
        bc1 = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc1, article_seller="A", article_wb=20003)
        bc2 = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            other_project.id,
            barcode=bc2,
            article_seller="A",
            article_wb=20003,
        )

        await set_box_qty_override(db_session, project.id, 20003, 100)

        other = (
            await db_session.execute(
                select(Nomenclature).where(
                    Nomenclature.project_id == other_project.id,
                    Nomenclature.article_wb == 20003,
                )
            )
        ).scalar_one()
        assert other.box_qty_override is None
