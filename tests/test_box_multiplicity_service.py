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
from backend.models.warehouse import Warehouse, WarehouseType
from backend.services.box_multiplicity_service import (
    bulk_update_by_barcode,
    get_box_multiplicity_table,
    get_sku_box_sources,
    resolve_effective_ppb_for_assembly,
    set_box_qty_override,
    update_box_multiplicity,
    update_order_box_qty,
    update_per_warehouse,
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
    order_date: date | None = None,
    box_size: str | None = None,
    mix_box_size: str | None = None,
) -> FactoryOrderItem:
    fo = FactoryOrder(
        project_id=project_id,
        order_number=f"FO-{_uid()}",
        factory_name="TestFactory",
        status="FORMING",
        order_date=order_date,
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
        box_size=box_size,
        mix_box_size=mix_box_size,
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
    target_warehouse_id: int | None = None,
) -> CostOrderItem:
    order_no = f"V-{_uid()}"
    co = CostOrder(
        project_id=project_id,
        order_no=order_no,
        status=status,
        actual_arrival_date=arrival_date,
        target_warehouse_id=target_warehouse_id,
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


# ═══════════════════════════════════════════════════════════════════════════════
# update_box_multiplicity (partial: ppb + use flag)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateBoxMultiplicity:
    @pytest.mark.asyncio
    async def test_default_use_flag_is_true(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=30001,
        )
        assert nom.use_box_multiplicity is True

    @pytest.mark.asyncio
    async def test_toggle_use_flag_off(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=30002,
        )

        ok = await update_box_multiplicity(
            db_session,
            project.id,
            30002,
            use_box_multiplicity=False,
        )
        assert ok is True

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30002)
        assert rows[0]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_partial_update_does_not_touch_omitted_field(
        self,
        db_session: AsyncSession,
        project,
    ):
        """Toggle use=False — box_qty_override должен остаться 50."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=30003,
            box_qty_override=50,
        )

        await update_box_multiplicity(
            db_session,
            project.id,
            30003,
            use_box_multiplicity=False,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30003)
        assert rows[0]["box_qty_override"] == 50  # not cleared
        assert rows[0]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_set_both_fields_at_once(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=30004,
        )

        await update_box_multiplicity(
            db_session,
            project.id,
            30004,
            box_qty_override=12,
            use_box_multiplicity=True,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30004)
        assert rows[0]["box_qty_override"] == 12
        assert rows[0]["use_box_multiplicity"] is True

    @pytest.mark.asyncio
    async def test_no_op_when_nothing_passed(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=30005,
            box_qty_override=10,
        )

        ok = await update_box_multiplicity(db_session, project.id, 30005)
        assert ok is True

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30005)
        assert rows[0]["box_qty_override"] == 10
        assert rows[0]["use_box_multiplicity"] is True

    @pytest.mark.asyncio
    async def test_unknown_nm_returns_false(self, db_session: AsyncSession, project):
        ok = await update_box_multiplicity(
            db_session,
            project.id,
            99999998,
            use_box_multiplicity=False,
        )
        assert ok is False


# ═══════════════════════════════════════════════════════════════════════════════
# bulk_update_by_barcode (paste из буфера)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBulkUpdateByBarcode:
    @pytest.mark.asyncio
    async def test_match_by_barcode_updates_ppb(self, db_session: AsyncSession, project):
        bc1 = f"BC-{_uid()}"
        bc2 = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc1, article_seller="A1", article_wb=40001)
        await _create_nomenclature(db_session, project.id, barcode=bc2, article_seller="A2", article_wb=40002)

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [
                {"barcode": bc1, "box_qty_override": 10},
                {"barcode": bc2, "box_qty_override": 20, "use_box_multiplicity": False},
            ],
        )
        assert result["matched_barcodes"] == {bc1, bc2}
        assert result["not_found"] == []
        assert result["updated_nm_ids"] == {40001, 40002}

        rows = await get_box_multiplicity_table(db_session, project.id)
        by_nm = {r["nm_id"]: r for r in rows}
        assert by_nm[40001]["box_qty_override"] == 10
        assert by_nm[40001]["use_box_multiplicity"] is True  # not changed
        assert by_nm[40002]["box_qty_override"] == 20
        assert by_nm[40002]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_unknown_barcode_in_not_found(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=40010)

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [
                {"barcode": bc, "box_qty_override": 5},
                {"barcode": "NOT-EXIST-1", "box_qty_override": 5},
                {"barcode": "NOT-EXIST-2"},
            ],
        )
        assert bc in result["matched_barcodes"]
        assert sorted(result["not_found"]) == ["NOT-EXIST-1", "NOT-EXIST-2"]

    @pytest.mark.asyncio
    async def test_partial_only_use_flag(self, db_session: AsyncSession, project):
        """Если в строке только use — ppb не трогается."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=40020,
            box_qty_override=99,
        )

        await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "use_box_multiplicity": False}],
        )
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=40020)
        assert rows[0]["box_qty_override"] == 99  # not cleared
        assert rows[0]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_no_change_no_commit(self, db_session: AsyncSession, project):
        """Если значения не отличаются — updated_nm_ids пустой."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=40030,
            box_qty_override=15,
        )

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "box_qty_override": 15, "use_box_multiplicity": True}],
        )
        # Barcode matched but nothing changed
        assert bc in result["matched_barcodes"]
        assert result["updated_nm_ids"] == set()

    @pytest.mark.asyncio
    async def test_other_project_isolation(
        self,
        db_session: AsyncSession,
        project,
        other_project,
    ):
        """SKU с тем же barcode в other_project не должен затрагиваться."""
        bc = f"BC-SHARED-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=40040)
        await _create_nomenclature(db_session, other_project.id, barcode=bc, article_seller="A", article_wb=40041)

        await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "box_qty_override": 77}],
        )

        other = (
            await db_session.execute(
                select(Nomenclature).where(
                    Nomenclature.project_id == other_project.id,
                    Nomenclature.barcode == bc,
                )
            )
        ).scalar_one()
        assert other.box_qty_override is None

    @pytest.mark.asyncio
    async def test_empty_items_list(self, db_session: AsyncSession, project):
        result = await bulk_update_by_barcode(db_session, project.id, [])
        assert result["matched_barcodes"] == set()
        assert result["not_found"] == []
        assert result["updated_nm_ids"] == set()


# ═══════════════════════════════════════════════════════════════════════════════
# Per-RF override (BoxQtyPerWarehouse)
# ═══════════════════════════════════════════════════════════════════════════════


async def _create_rf_warehouse(db: AsyncSession, project_id: int, name: str) -> Warehouse:
    wh = Warehouse(
        project_id=project_id,
        name=name,
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db.add(wh)
    await db.commit()
    return wh


class TestPerWarehouseOverride:
    @pytest.mark.asyncio
    async def test_create_per_rf_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50001)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        ok = await update_per_warehouse(
            db_session,
            project.id,
            bc,
            wh.id,
            box_qty=15,
        )
        assert ok is True

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=50001)
        per_wh = {p["warehouse_id"]: p for p in rows[0]["per_warehouse"]}
        assert per_wh[wh.id]["box_qty"] == 15
        assert per_wh[wh.id]["use_box_multiplicity"] is True

    @pytest.mark.asyncio
    async def test_update_existing_per_rf_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50002)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10)
        await update_per_warehouse(db_session, project.id, bc, wh.id, use_box_multiplicity=False)

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=50002)
        per_wh = {p["warehouse_id"]: p for p in rows[0]["per_warehouse"]}
        assert per_wh[wh.id]["box_qty"] == 10  # not cleared
        assert per_wh[wh.id]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_unknown_warehouse_returns_false(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50003)

        ok = await update_per_warehouse(db_session, project.id, bc, 99999998, box_qty=10)
        assert ok is False

    @pytest.mark.asyncio
    async def test_unknown_barcode_returns_false(self, db_session: AsyncSession, project):
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        ok = await update_per_warehouse(db_session, project.id, "NOT-EXISTS-BC", wh.id, box_qty=10)
        assert ok is False

    @pytest.mark.asyncio
    async def test_resolve_per_rf_priority(self, db_session: AsyncSession, project):
        """Per-RF override должен побеждать SKU-level override."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=50010,
            box_qty_override=20,  # SKU-level
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=30)

        ppb, use = await resolve_effective_ppb_for_assembly(
            db_session,
            project.id,
            bc,
            wh.id,
        )
        assert ppb == 30  # per-RF wins
        assert use is True

    @pytest.mark.asyncio
    async def test_resolve_falls_through_to_sku_level(self, db_session: AsyncSession, project):
        """Если per-RF не задан — берём SKU-level."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=50011,
            box_qty_override=25,
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        ppb, use = await resolve_effective_ppb_for_assembly(
            db_session,
            project.id,
            bc,
            wh.id,
        )
        assert ppb == 25
        assert use is True

    @pytest.mark.asyncio
    async def test_resolve_per_rf_use_flag_false(self, db_session: AsyncSession, project):
        """Per-RF use=false — отключает округление, даже если ppb задан."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session,
            project.id,
            barcode=bc,
            article_seller="A",
            article_wb=50012,
            box_qty_override=10,
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, use_box_multiplicity=False)

        _, use = await resolve_effective_ppb_for_assembly(
            db_session,
            project.id,
            bc,
            wh.id,
        )
        assert use is False  # per-RF flag wins

    @pytest.mark.asyncio
    async def test_bulk_per_rf_creates_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50020)
        wh1 = await _create_rf_warehouse(db_session, project.id, f"RF1-{_uid()}")
        wh2 = await _create_rf_warehouse(db_session, project.id, f"RF2-{_uid()}")

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [
                {"barcode": bc, "warehouse_id": wh1.id, "box_qty_override": 10},
                {"barcode": bc, "warehouse_id": wh2.id, "box_qty_override": 12},
            ],
        )
        assert bc in result["matched_barcodes"]
        assert result["not_found"] == []

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=50020)
        per_wh = {p["warehouse_id"]: p["box_qty"] for p in rows[0]["per_warehouse"]}
        assert per_wh[wh1.id] == 10
        assert per_wh[wh2.id] == 12


class TestVehicleQtyWeightedMode:
    @pytest.mark.asyncio
    async def test_mode_picks_majority_ppb(self, db_session: AsyncSession, project):
        """Если в одной машине qty=100 при ppb=10 + qty=20 при ppb=12 → mode=10."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=60001)
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=8)

        # 2 строки в одной машине, разная ppb через override
        order_no = f"V-{_uid()}"
        co = CostOrder(
            project_id=project.id,
            order_no=order_no,
            status=VehicleStatus.DELIVERED,
            actual_arrival_date=date(2026, 5, 1),
            delivery_cost_cny=Decimal("0"),
            delivery_cost_usd=Decimal("0"),
            delivery_cost_rub=Decimal("0"),
            rate_cny=Decimal("1"),
            rate_eur=Decimal("1"),
            rate_usd=Decimal("1"),
        )
        db_session.add(co)
        await db_session.flush()
        for qty, ppb in [(100, 10), (20, 12)]:
            db_session.add(
                CostOrderItem(
                    project_id=project.id,
                    order_no=order_no,
                    barcode=bc,
                    qty=qty,
                    price_cny=Decimal("10"),
                    pcs_per_box_override=ppb,
                    factory_order_item_id=foi.id,
                )
            )
        await db_session.commit()

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=60001)
        assert rows[0]["box_qty_from_vehicle"] == 10  # mode (qty-weighted)
        assert rows[0]["box_qty_from_vehicle_alts"] == [12]


# ═══════════════════════════════════════════════════════════════════════════════
# get_sku_box_sources (drill-down: история снабжения SKU)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetSkuBoxSources:
    @pytest.mark.asyncio
    async def test_empty_no_sources(self, db_session: AsyncSession, project):
        rows = await get_sku_box_sources(db_session, project.id, f"BC-NONE-{_uid()}")
        assert rows == []

    @pytest.mark.asyncio
    async def test_factory_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        foi = await _create_foi(
            db_session,
            project.id,
            barcode=bc,
            pcs_per_box=10,
            box_size="40x30x20",
            order_date=date(2026, 3, 1),
        )

        rows = await get_sku_box_sources(db_session, project.id, bc)
        assert len(rows) == 1
        row = rows[0]
        assert row["source_type"] == "factory"
        assert row["factory_order_item_id"] == foi.id
        assert row["box_qty"] == 10
        assert row["box_size"] == "40x30x20"
        assert row["date"] == "2026-03-01"
        assert row["editable"] is True
        assert row["is_overridden"] is False

    @pytest.mark.asyncio
    async def test_vehicle_row_with_warehouse(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _create_vehicle_with_item(
            db_session,
            project.id,
            barcode=bc,
            arrival_date=date(2026, 4, 1),
            pcs_per_box_override=12,
            target_warehouse_id=wh.id,
        )

        rows = await get_sku_box_sources(db_session, project.id, bc)
        assert len(rows) == 1
        row = rows[0]
        assert row["source_type"] == "vehicle"
        assert row["factory_order_item_id"] is None
        assert row["warehouse_name"] == wh.name
        assert row["box_qty"] == 12
        assert row["editable"] is False

    @pytest.mark.asyncio
    async def test_override_marks_factory_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)
        await update_order_box_qty(db_session, project.id, foi.id, 24)

        rows = await get_sku_box_sources(db_session, project.id, bc)
        factory = next(r for r in rows if r["source_type"] == "factory")
        assert factory["box_qty"] == 24
        assert factory["is_overridden"] is True

    @pytest.mark.asyncio
    async def test_other_project_isolation(
        self,
        db_session: AsyncSession,
        project,
        other_project,
    ):
        """История снабжения SKU из чужого проекта не должна попадать в выборку."""
        bc = f"BC-SHARED-{_uid()}"
        await _create_foi(db_session, other_project.id, barcode=bc, pcs_per_box=10)

        rows = await get_sku_box_sources(db_session, project.id, bc)
        assert rows == []


# ═══════════════════════════════════════════════════════════════════════════════
# update_order_box_qty (per-order override кратности)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateOrderBoxQty:
    @pytest.mark.asyncio
    async def test_set_override_returns_barcode(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)

        result = await update_order_box_qty(db_session, project.id, foi.id, 24)
        assert result == bc

        rows = await get_sku_box_sources(db_session, project.id, bc)
        assert rows[0]["box_qty"] == 24
        assert rows[0]["is_overridden"] is True

    @pytest.mark.asyncio
    async def test_clear_override_falls_back_to_foi(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=10)
        await update_order_box_qty(db_session, project.id, foi.id, 24)

        result = await update_order_box_qty(db_session, project.id, foi.id, None)
        assert result == bc

        rows = await get_sku_box_sources(db_session, project.id, bc)
        assert rows[0]["box_qty"] == 10  # fallback to FactoryOrderItem.pcs_per_box
        assert rows[0]["is_overridden"] is False

    @pytest.mark.asyncio
    async def test_unknown_foi_returns_none(self, db_session: AsyncSession, project):
        result = await update_order_box_qty(db_session, project.id, 99999999, 10)
        assert result is None

    @pytest.mark.asyncio
    async def test_other_project_isolation(
        self,
        db_session: AsyncSession,
        project,
        other_project,
    ):
        """FOI чужого проекта недоступен для override через наш project_id."""
        bc = f"BC-{_uid()}"
        foi = await _create_foi(db_session, other_project.id, barcode=bc, pcs_per_box=10)

        result = await update_order_box_qty(db_session, project.id, foi.id, 50)
        assert result is None

        rows = await get_sku_box_sources(db_session, other_project.id, bc)
        assert rows[0]["is_overridden"] is False
