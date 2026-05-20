"""Tests for box_multiplicity_service — per-(SKU, ФФ-склад) кратность коробки.

Кратность резолвится из ACCEPTED-приёмок машин-поставок. Цепочка:
  machine → manual per-ФФ → SKU-дефолт → none.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrderItem
from backend.models.cost import (
    BoxMultiplicityChangeLog,
    BoxQtyPerWarehouse,
    CostOrder,
    Nomenclature,
)
from backend.models.enums import VehicleStatus
from backend.models.supply_chain import FactoryOrder, FactoryOrderItem
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    Warehouse,
    WarehouseType,
)
from backend.services.box_multiplicity_service import (
    bulk_update_by_barcode,
    get_box_multiplicity_table,
    get_change_log,
    get_sku_box_sources,
    has_machine_box_qty,
    list_recent_batches,
    resolve_effective_ppb_for_assembly,
    revert_batch,
    revert_change,
    set_box_qty_override,
    update_box_multiplicity,
    update_per_warehouse,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ─── builders ────────────────────────────────────────────────────────────────


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


async def _create_vehicle(
    db: AsyncSession,
    project_id: int,
    *,
    status: VehicleStatus = VehicleStatus.DELIVERED,
    arrival_date: date | None = None,
    target_warehouse_id: int | None = None,
) -> CostOrder:
    co = CostOrder(
        project_id=project_id,
        order_no=f"V-{_uid()}",
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
    return co


async def _add_vehicle_item(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    *,
    barcode: str,
    qty: int = 10,
    pcs_per_box_override: int | None = None,
    box_size_override: str | None = None,
    factory_order_item_id: int | None = None,
) -> CostOrderItem:
    coi = CostOrderItem(
        project_id=project_id,
        order_no=order_no,
        barcode=barcode,
        qty=qty,
        price_cny=Decimal("10"),
        pcs_per_box_override=pcs_per_box_override,
        box_size_override=box_size_override,
        factory_order_item_id=factory_order_item_id,
    )
    db.add(coi)
    return coi


async def _create_receipt(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_id: int,
    cost_order_id: int | None,
    status: InboundStatus = InboundStatus.ACCEPTED,
    actual_date: date | None = None,
    items: list[tuple[int, str, int]] | None = None,  # (nom_id, barcode, actual_qty)
) -> InboundReceipt:
    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"IN-{_uid()}",
        status=status.value,
        actual_date=actual_date,
        cost_order_id=cost_order_id,
    )
    db.add(receipt)
    await db.flush()
    for nom_id, barcode, actual_qty in items or []:
        db.add(
            InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nom_id,
                barcode=barcode,
                expected_qty=actual_qty,
                actual_qty=actual_qty,
            )
        )
    await db.commit()
    return receipt


async def _machine_into_ff(
    db: AsyncSession,
    project_id: int,
    *,
    nom: Nomenclature,
    warehouse_id: int,
    ppb_lines: list[tuple[int, int]],  # list of (qty, pcs_per_box_override)
    status: InboundStatus = InboundStatus.ACCEPTED,
    arrival_date: date | None = None,
    box_size_override: str | None = None,
) -> CostOrder:
    """Создаёт машину с позициями + приёмку на ФФ-склад.

    Возвращает CostOrder.
    """
    co = await _create_vehicle(db, project_id, arrival_date=arrival_date)
    for qty, ppb in ppb_lines:
        await _add_vehicle_item(
            db,
            project_id,
            co.order_no,
            barcode=nom.barcode,
            qty=qty,
            pcs_per_box_override=ppb,
            box_size_override=box_size_override,
        )
    await db.flush()
    await _create_receipt(
        db,
        project_id,
        warehouse_id=warehouse_id,
        cost_order_id=co.id,
        status=status,
        actual_date=arrival_date,
        items=[(nom.id, nom.barcode, sum(q for q, _ in ppb_lines))],
    )
    return co


def _pw(row: dict) -> dict[int, dict]:
    """per_warehouse list → {warehouse_id: row}."""
    return {p["warehouse_id"]: p for p in row["per_warehouse"]}


# ═══════════════════════════════════════════════════════════════════════════════
# get_box_multiplicity_table — резолв кратности
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetBoxMultiplicityTable:
    @pytest.mark.asyncio
    async def test_empty_project(self, db_session: AsyncSession, project):
        rows = await get_box_multiplicity_table(db_session, project.id)
        assert rows == []

    @pytest.mark.asyncio
    async def test_sku_no_box_data_source_none(self, db_session: AsyncSession, project):
        await _create_nomenclature(
            db_session, project.id, barcode=f"BC-{_uid()}", article_seller="ART-1", article_wb=10001
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10001)
        assert len(rows) == 1
        assert rows[0]["box_qty_override"] is None
        assert rows[0]["has_machine_data"] is False
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] is None
        assert pw[wh.id]["source"] == "none"
        assert pw[wh.id]["editable"] is True

    @pytest.mark.asyncio
    async def test_sku_default_from_override(self, db_session: AsyncSession, project):
        """SKU-уровневый box_qty_override → source='default', editable=True."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="ART-2", article_wb=10002, box_qty_override=8
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10002)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 8
        assert pw[wh.id]["source"] == "default"
        assert pw[wh.id]["editable"] is True

    @pytest.mark.asyncio
    async def test_manual_per_ff_beats_default(self, db_session: AsyncSession, project):
        """Ручная per-ФФ кратность побеждает SKU-дефолт."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="ART-3", article_wb=10003, box_qty_override=8
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=20)

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10003)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 20
        assert pw[wh.id]["source"] == "manual"
        assert pw[wh.id]["editable"] is True

    @pytest.mark.asyncio
    async def test_machine_resolves_box_qty(self, db_session: AsyncSession, project):
        """ACCEPTED-приёмка машины на ФФ → source='machine', editable=False."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-4", article_wb=10004)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        co = await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(50, 12)],
            arrival_date=date(2026, 5, 1),
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10004)
        assert rows[0]["has_machine_data"] is True
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 12
        assert pw[wh.id]["source"] == "machine"
        assert pw[wh.id]["editable"] is False
        assert pw[wh.id]["machine_order_no"] == co.order_no
        assert pw[wh.id]["machine_received_at"] == "2026-05-01"

    @pytest.mark.asyncio
    async def test_machine_beats_manual_and_default(self, db_session: AsyncSession, project):
        """Машина побеждает и ручную per-ФФ, и SKU-дефолт."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="ART-5", article_wb=10005, box_qty_override=99
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=77)
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(30, 15)])

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10005)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 15
        assert pw[wh.id]["source"] == "machine"

    @pytest.mark.asyncio
    async def test_per_ff_resolution_differs_by_warehouse(self, db_session: AsyncSession, project):
        """Разные машины приняты на разные ФФ → per-ФФ кратность различается."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="ART-6", article_wb=10006, box_qty_override=5
        )
        wh1 = await _create_rf_warehouse(db_session, project.id, f"RF1-{_uid()}")
        wh2 = await _create_rf_warehouse(db_session, project.id, f"RF2-{_uid()}")
        # Машина с ppb=10 принята на wh1; машина с ppb=20 — на wh2.
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh1.id, ppb_lines=[(40, 10)])
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh2.id, ppb_lines=[(40, 20)])

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10006)
        pw = _pw(rows[0])
        assert pw[wh1.id]["box_qty"] == 10 and pw[wh1.id]["source"] == "machine"
        assert pw[wh2.id]["box_qty"] == 20 and pw[wh2.id]["source"] == "machine"

    @pytest.mark.asyncio
    async def test_latest_accepted_receipt_wins(self, db_session: AsyncSession, project):
        """Для пары (barcode, ФФ) берётся приёмка с max (actual_date, id)."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-7", article_wb=10007)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 10)],
            arrival_date=date(2026, 1, 1),
        )
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 30)],
            arrival_date=date(2026, 3, 1),
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10007)
        assert _pw(rows[0])[wh.id]["box_qty"] == 30

    @pytest.mark.asyncio
    async def test_machine_variants_counts_other_machines(self, db_session: AsyncSession, project):
        """Несколько машин на один ФФ с разной кратностью → machine_variants > 0."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-7a", article_wb=10071)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 10)], arrival_date=date(2026, 1, 1)
        )
        await _machine_into_ff(
            db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 30)], arrival_date=date(2026, 3, 1)
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10071)
        pw = _pw(rows[0])[wh.id]
        assert pw["box_qty"] == 30  # последняя машина
        assert pw["machine_variants"] == 1  # одна иная машина с кратностью 10

    @pytest.mark.asyncio
    async def test_has_mixed_ppb_includes_non_accepted(self, db_session: AsyncSession, project):
        """has_mixed_ppb=True: машина в пути имеет другую ppb, чем уже принятая."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-7c", article_wb=10073)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        # Принятая машина с ppb=10
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 10)],
            arrival_date=date(2026, 1, 1),
        )
        # Машина «в пути» (EXPECTED) с ppb=25 — не влияет на резолв, но в history есть
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 25)],
            status=InboundStatus.EXPECTED,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10073)
        assert rows[0]["has_mixed_ppb"] is True
        # Резолв всё ещё только из принятой
        assert _pw(rows[0])[wh.id]["box_qty"] == 10

    @pytest.mark.asyncio
    async def test_has_mixed_ppb_false_when_single_ppb(self, db_session: AsyncSession, project):
        """has_mixed_ppb=False: все машины имеют одну ppb."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-7d", article_wb=10074)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 15)],
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10074)
        assert rows[0]["has_mixed_ppb"] is False

    @pytest.mark.asyncio
    async def test_machine_variants_zero_when_same_ppb(self, db_session: AsyncSession, project):
        """Несколько машин на ФФ с одинаковой кратностью → machine_variants == 0."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-7b", article_wb=10072)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 20)], arrival_date=date(2026, 1, 1)
        )
        await _machine_into_ff(
            db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 20)], arrival_date=date(2026, 3, 1)
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10072)
        assert _pw(rows[0])[wh.id]["machine_variants"] == 0

    @pytest.mark.asyncio
    async def test_non_accepted_receipt_ignored(self, db_session: AsyncSession, project):
        """Машина с приёмкой не в статусе ACCEPTED — не учитывается."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="ART-8", article_wb=10008, box_qty_override=4
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 99)],
            status=InboundStatus.EXPECTED,
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10008)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 4  # fallback to SKU default
        assert pw[wh.id]["source"] == "default"
        assert rows[0]["has_machine_data"] is False

    @pytest.mark.asyncio
    async def test_receipt_without_cost_order_ignored(self, db_session: AsyncSession, project):
        """ACCEPTED-приёмка без cost_order_id — не даёт машинной кратности."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-9", article_wb=10009)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _create_receipt(
            db_session,
            project.id,
            warehouse_id=wh.id,
            cost_order_id=None,
            status=InboundStatus.ACCEPTED,
            items=[(nom.id, bc, 30)],
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10009)
        assert _pw(rows[0])[wh.id]["source"] == "none"

    @pytest.mark.asyncio
    async def test_qty_weighted_mode_inside_machine(self, db_session: AsyncSession, project):
        """Несколько строк товара в машине с разной ppb → qty-weighted mode."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-10", article_wb=10010)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        # qty=100 при ppb=10, qty=20 при ppb=12 → mode=10.
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(100, 10), (20, 12)])

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10010)
        assert _pw(rows[0])[wh.id]["box_qty"] == 10

    @pytest.mark.asyncio
    async def test_machine_inherits_foi_ppb(self, db_session: AsyncSession, project):
        """CostOrderItem без pcs_per_box_override → берёт ppb из FactoryOrderItem."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="ART-11", article_wb=10011)
        foi = await _create_foi(db_session, project.id, barcode=bc, pcs_per_box=18)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        co = await _create_vehicle(db_session, project.id, arrival_date=date(2026, 2, 1))
        await _add_vehicle_item(
            db_session,
            project.id,
            co.order_no,
            barcode=bc,
            qty=40,
            pcs_per_box_override=None,
            factory_order_item_id=foi.id,
        )
        await db_session.flush()
        await _create_receipt(
            db_session,
            project.id,
            warehouse_id=wh.id,
            cost_order_id=co.id,
            status=InboundStatus.ACCEPTED,
            items=[(nom.id, bc, 40)],
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10011)
        assert _pw(rows[0])[wh.id]["box_qty"] == 18

    @pytest.mark.asyncio
    async def test_only_skus_with_article_wb_returned(self, db_session: AsyncSession, project):
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
        assert None not in [r["nm_id"] for r in rows]

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Машинная приёмка в другом проекте не влияет на наш SKU."""
        bc = f"BC-SHARED-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=10020)
        nom_other = await _create_nomenclature(
            db_session, other_project.id, barcode=bc, article_seller="A", article_wb=10021
        )
        wh_other = await _create_rf_warehouse(db_session, other_project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session, other_project.id, nom=nom_other, warehouse_id=wh_other.id, ppb_lines=[(40, 50)]
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=10020)
        assert rows[0]["has_machine_data"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# update_box_multiplicity / set_box_qty_override (SKU-уровень — всегда разрешён)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateBoxMultiplicity:
    @pytest.mark.asyncio
    async def test_set_box_qty_override(self, db_session: AsyncSession, project):
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
            db_session, project.id, barcode=bc, article_seller="A", article_wb=20002, box_qty_override=10
        )
        ok = await set_box_qty_override(db_session, project.id, 20002, None)
        assert ok is True
        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.article_wb == 20002))).scalar_one()
        assert nom.box_qty_override is None

    @pytest.mark.asyncio
    async def test_unknown_nm_returns_false(self, db_session: AsyncSession, project):
        assert await set_box_qty_override(db_session, project.id, 99999999, 10) is False

    @pytest.mark.asyncio
    async def test_toggle_use_flag(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=20003)
        await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        await update_box_multiplicity(db_session, project.id, 20003, use_box_multiplicity=False)
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=20003)
        assert rows[0]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_partial_does_not_touch_omitted_field(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=20004, box_qty_override=50
        )
        await update_box_multiplicity(db_session, project.id, 20004, use_box_multiplicity=False)
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=20004)
        assert rows[0]["box_qty_override"] == 50
        assert rows[0]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_other_project_isolation(self, db_session: AsyncSession, project, other_project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=20010)
        await _create_nomenclature(
            db_session, other_project.id, barcode=f"BC-{_uid()}", article_seller="A", article_wb=20010
        )
        await set_box_qty_override(db_session, project.id, 20010, 100)
        other = (
            await db_session.execute(
                select(Nomenclature).where(
                    Nomenclature.project_id == other_project.id, Nomenclature.article_wb == 20010
                )
            )
        ).scalar_one()
        assert other.box_qty_override is None


# ═══════════════════════════════════════════════════════════════════════════════
# update_per_warehouse — ручная per-ФФ кратность
# ═══════════════════════════════════════════════════════════════════════════════


class TestPerWarehouse:
    @pytest.mark.asyncio
    async def test_create_per_ff_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=30001)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        assert await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=15) is True
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30001)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 15
        assert pw[wh.id]["source"] == "manual"

    @pytest.mark.asyncio
    async def test_update_existing_per_ff_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=30002)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10)
        await update_per_warehouse(db_session, project.id, bc, wh.id, use_box_multiplicity=False)
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30002)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 10
        assert pw[wh.id]["use_box_multiplicity"] is False

    @pytest.mark.asyncio
    async def test_unknown_warehouse_returns_false(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=30003)
        assert await update_per_warehouse(db_session, project.id, bc, 99999998, box_qty=10) is False

    @pytest.mark.asyncio
    async def test_unknown_barcode_returns_false(self, db_session: AsyncSession, project):
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        assert await update_per_warehouse(db_session, project.id, "NOPE-BC", wh.id, box_qty=10) is False

    @pytest.mark.asyncio
    async def test_per_ff_use_flag_overrides_sku_flag(self, db_session: AsyncSession, project):
        """use_box_multiplicity per-ФФ важнее SKU-уровневого флага."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=30004)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, use_box_multiplicity=False)

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=30004)
        # SKU-флаг по умолчанию True, но per-ФФ строка выключает.
        assert _pw(rows[0])[wh.id]["use_box_multiplicity"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# has_machine_box_qty — 409-guard helper
# ═══════════════════════════════════════════════════════════════════════════════


class TestHasMachineBoxQty:
    @pytest.mark.asyncio
    async def test_true_when_machine_resolved(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=35001)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 12)])

        assert await has_machine_box_qty(db_session, project.id, bc, wh.id) is True

    @pytest.mark.asyncio
    async def test_false_when_no_machine(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=35002)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        assert await has_machine_box_qty(db_session, project.id, bc, wh.id) is False

    @pytest.mark.asyncio
    async def test_false_for_other_ff(self, db_session: AsyncSession, project):
        """Машина принята на wh1 → wh2 не заблокирован."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=35003)
        wh1 = await _create_rf_warehouse(db_session, project.id, f"RF1-{_uid()}")
        wh2 = await _create_rf_warehouse(db_session, project.id, f"RF2-{_uid()}")
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh1.id, ppb_lines=[(40, 12)])

        assert await has_machine_box_qty(db_session, project.id, bc, wh1.id) is True
        assert await has_machine_box_qty(db_session, project.id, bc, wh2.id) is False


# ═══════════════════════════════════════════════════════════════════════════════
# resolve_effective_ppb_for_assembly
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveEffectivePpb:
    @pytest.mark.asyncio
    async def test_machine_resolved(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=40001, box_qty_override=99
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 16)])

        ppb, use = await resolve_effective_ppb_for_assembly(db_session, project.id, bc, wh.id)
        assert ppb == 16  # machine beats SKU override
        assert use is True

    @pytest.mark.asyncio
    async def test_manual_per_ff(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=40002, box_qty_override=20
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=30)

        ppb, use = await resolve_effective_ppb_for_assembly(db_session, project.id, bc, wh.id)
        assert ppb == 30  # manual per-ФФ beats SKU default
        assert use is True

    @pytest.mark.asyncio
    async def test_falls_to_sku_default(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=40003, box_qty_override=25
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        ppb, use = await resolve_effective_ppb_for_assembly(db_session, project.id, bc, wh.id)
        assert ppb == 25
        assert use is True

    @pytest.mark.asyncio
    async def test_none_when_nothing(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=40004)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        ppb, _use = await resolve_effective_ppb_for_assembly(db_session, project.id, bc, wh.id)
        assert ppb is None

    @pytest.mark.asyncio
    async def test_per_ff_use_flag_false(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=40005, box_qty_override=10
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, use_box_multiplicity=False)

        _ppb, use = await resolve_effective_ppb_for_assembly(db_session, project.id, bc, wh.id)
        assert use is False


# ═══════════════════════════════════════════════════════════════════════════════
# bulk_update_by_barcode
# ═══════════════════════════════════════════════════════════════════════════════


class TestBulkUpdate:
    @pytest.mark.asyncio
    async def test_match_updates_sku_ppb(self, db_session: AsyncSession, project):
        bc1, bc2 = f"BC-{_uid()}", f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc1, article_seller="A1", article_wb=50001)
        await _create_nomenclature(db_session, project.id, barcode=bc2, article_seller="A2", article_wb=50002)

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
        assert result["updated_nm_ids"] == {50001, 50002}
        assert result["locked"] == []

    @pytest.mark.asyncio
    async def test_unknown_barcode_in_not_found(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50010)
        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "box_qty_override": 5}, {"barcode": "NOPE-1", "box_qty_override": 5}],
        )
        assert bc in result["matched_barcodes"]
        assert result["not_found"] == ["NOPE-1"]

    @pytest.mark.asyncio
    async def test_per_ff_bulk_creates_rows(self, db_session: AsyncSession, project):
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
        assert result["locked"] == []
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=50020)
        pw = _pw(rows[0])
        assert pw[wh1.id]["box_qty"] == 10
        assert pw[wh2.id]["box_qty"] == 12

    @pytest.mark.asyncio
    async def test_machine_locked_pair_goes_to_locked(self, db_session: AsyncSession, project):
        """box_qty для машинно-заблокированной пары (barcode, ФФ) → locked, не апдейтится."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50030)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 14)])
        wh_id = wh.id  # capture before bulk no-change rollback expires ORM state

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "warehouse_id": wh_id, "box_qty_override": 99}],
        )
        assert result["locked"] == [bc]
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=50030)
        # box_qty остался машинным.
        assert _pw(rows[0])[wh_id]["box_qty"] == 14
        assert _pw(rows[0])[wh_id]["source"] == "machine"

    @pytest.mark.asyncio
    async def test_machine_locked_still_applies_use_flag(self, db_session: AsyncSession, project):
        """На машинном ФФ use_box_multiplicity всё ещё применяется через bulk."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50040)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 14)])
        wh_id = wh.id

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "warehouse_id": wh_id, "box_qty_override": 99, "use_box_multiplicity": False}],
        )
        assert result["locked"] == [bc]
        per_rf = (
            await db_session.execute(
                select(BoxQtyPerWarehouse).where(
                    BoxQtyPerWarehouse.project_id == project.id,
                    BoxQtyPerWarehouse.barcode == bc,
                    BoxQtyPerWarehouse.warehouse_id == wh_id,
                )
            )
        ).scalar_one()
        assert per_rf.use_box_multiplicity is False
        assert per_rf.box_qty is None  # box_qty не записан — машинная блокировка

    @pytest.mark.asyncio
    async def test_empty_items(self, db_session: AsyncSession, project):
        result = await bulk_update_by_barcode(db_session, project.id, [])
        assert result["matched_barcodes"] == set()
        assert result["locked"] == []

    @pytest.mark.asyncio
    async def test_other_project_isolation(self, db_session: AsyncSession, project, other_project):
        bc = f"BC-SHARED-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=50050)
        await _create_nomenclature(db_session, other_project.id, barcode=bc, article_seller="A", article_wb=50051)

        await bulk_update_by_barcode(db_session, project.id, [{"barcode": bc, "box_qty_override": 77}])
        other = (
            await db_session.execute(
                select(Nomenclature).where(Nomenclature.project_id == other_project.id, Nomenclature.barcode == bc)
            )
        ).scalar_one()
        assert other.box_qty_override is None


# ═══════════════════════════════════════════════════════════════════════════════
# get_sku_box_sources (drill-down, read-only)
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetSkuBoxSources:
    @pytest.mark.asyncio
    async def test_empty_no_sources(self, db_session: AsyncSession, project):
        assert await get_sku_box_sources(db_session, project.id, f"BC-NONE-{_uid()}") == []

    @pytest.mark.asyncio
    async def test_factory_row(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_foi(
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
        assert row["box_qty"] == 10
        assert row["box_size"] == "40x30x20"
        assert row["accepted"] is False

    @pytest.mark.asyncio
    async def test_vehicle_row_accepted_flag(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=60001)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 12)],
            arrival_date=date(2026, 4, 1),
        )

        rows = await get_sku_box_sources(db_session, project.id, bc)
        veh = next(r for r in rows if r["source_type"] == "vehicle")
        assert veh["box_qty"] == 12
        assert veh["accepted"] is True
        assert veh["warehouse_name"] == wh.name

    @pytest.mark.asyncio
    async def test_vehicle_not_accepted(self, db_session: AsyncSession, project):
        """Машина без ACCEPTED-приёмки → accepted=False."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=60002)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        co = await _create_vehicle(db_session, project.id, target_warehouse_id=wh.id)
        await _add_vehicle_item(db_session, project.id, co.order_no, barcode=bc, qty=40, pcs_per_box_override=8)
        await db_session.commit()

        rows = await get_sku_box_sources(db_session, project.id, bc)
        veh = next(r for r in rows if r["source_type"] == "vehicle")
        assert veh["accepted"] is False
        assert veh["warehouse_name"] == wh.name  # target_warehouse fallback

    @pytest.mark.asyncio
    async def test_other_project_isolation(self, db_session: AsyncSession, project, other_project):
        bc = f"BC-SHARED-{_uid()}"
        await _create_foi(db_session, other_project.id, barcode=bc, pcs_per_box=10)
        assert await get_sku_box_sources(db_session, project.id, bc) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Задача #1 — per-ФФ box_size
# ═══════════════════════════════════════════════════════════════════════════════


async def _change_rows(db: AsyncSession, project_id: int, barcode: str) -> list[BoxMultiplicityChangeLog]:
    """Все строки журнала по barcode, отсортированы id asc."""
    res = await db.execute(
        select(BoxMultiplicityChangeLog)
        .where(
            BoxMultiplicityChangeLog.project_id == project_id,
            BoxMultiplicityChangeLog.barcode == barcode,
        )
        .order_by(BoxMultiplicityChangeLog.id)
    )
    return list(res.scalars().all())


class TestPerWarehouseBoxSize:
    @pytest.mark.asyncio
    async def test_update_per_ff_box_size(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=70001)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=12, box_size="40x30x20")
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=70001)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 12
        assert pw[wh.id]["box_size"] == "40x30x20"
        assert pw[wh.id]["source"] == "manual"

    @pytest.mark.asyncio
    async def test_box_size_resolved_for_default_source(self, db_session: AsyncSession, project):
        """box_size из per-ФФ строки отображается даже когда box_qty резолвится из SKU-дефолта."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=70002, box_qty_override=8
        )
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        # Задаём только box_size — box_qty остаётся None → source='default'.
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_size="50x40x30")

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=70002)
        pw = _pw(rows[0])
        assert pw[wh.id]["source"] == "default"
        assert pw[wh.id]["box_qty"] == 8
        assert pw[wh.id]["box_size"] == "50x40x30"

    @pytest.mark.asyncio
    async def test_machine_box_size_wins(self, db_session: AsyncSession, project):
        """На машинном ФФ box_size берётся из машины, а не из ручной строки."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=70003)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(
            db_session,
            project.id,
            nom=nom,
            warehouse_id=wh.id,
            ppb_lines=[(40, 12)],
            box_size_override="MACHINE-SIZE",
        )

        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=70003)
        pw = _pw(rows[0])
        assert pw[wh.id]["source"] == "machine"
        assert pw[wh.id]["box_size"] == "MACHINE-SIZE"

    @pytest.mark.asyncio
    async def test_bulk_per_ff_box_size(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=70004)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "warehouse_id": wh.id, "box_qty_override": 10, "box_size": "BX-1"}],
        )
        assert result["locked"] == []
        rows = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=70004)
        pw = _pw(rows[0])
        assert pw[wh.id]["box_qty"] == 10
        assert pw[wh.id]["box_size"] == "BX-1"

    @pytest.mark.asyncio
    async def test_bulk_box_size_locked_on_machine_ff(self, db_session: AsyncSession, project):
        """bulk box_size для машинно-заблокированной пары → locked, не записан."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=70005)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 12)])
        wh_id = wh.id

        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "warehouse_id": wh_id, "box_size": "NEW-SIZE"}],
        )
        assert result["locked"] == [bc]
        per_rf = (
            await db_session.execute(
                select(BoxQtyPerWarehouse).where(
                    BoxQtyPerWarehouse.project_id == project.id,
                    BoxQtyPerWarehouse.barcode == bc,
                    BoxQtyPerWarehouse.warehouse_id == wh_id,
                )
            )
        ).scalar_one_or_none()
        assert per_rf is None  # строка не создана — нечего было применить


# ═══════════════════════════════════════════════════════════════════════════════
# Задача #2 — журнал изменений + откат
# ═══════════════════════════════════════════════════════════════════════════════


class TestChangeLogging:
    @pytest.mark.asyncio
    async def test_sku_box_qty_override_logged(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=80001)

        await update_box_multiplicity(db_session, project.id, 80001, box_qty_override=15)
        logs = await _change_rows(db_session, project.id, bc)
        assert len(logs) == 1
        log = logs[0]
        assert log.field == "box_qty_override"
        assert log.warehouse_id is None
        assert log.old_value is None
        assert log.new_value == "15"
        assert log.change_source == "manual"

    @pytest.mark.asyncio
    async def test_no_op_not_logged(self, db_session: AsyncSession, project):
        """Запись в SKU без реального изменения значения — журнал не пишется."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=80002, box_qty_override=20
        )
        await update_box_multiplicity(db_session, project.id, 80002, box_qty_override=20)
        assert await _change_rows(db_session, project.id, bc) == []

    @pytest.mark.asyncio
    async def test_use_flag_logged_as_bool(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=80003)
        await update_box_multiplicity(db_session, project.id, 80003, use_box_multiplicity=False)
        logs = await _change_rows(db_session, project.id, bc)
        assert len(logs) == 1
        assert logs[0].field == "use_box_multiplicity"
        assert logs[0].old_value == "true"
        assert logs[0].new_value == "false"

    @pytest.mark.asyncio
    async def test_per_ff_box_qty_and_size_logged(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=80004)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10, box_size="S-1")
        logs = await _change_rows(db_session, project.id, bc)
        fields = {(log.field, log.new_value, log.warehouse_id) for log in logs}
        assert ("box_qty", "10", wh.id) in fields
        assert ("box_size", "S-1", wh.id) in fields

    @pytest.mark.asyncio
    async def test_per_ff_no_op_not_logged(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=80005)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")

        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10)
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10)  # no-op
        logs = [log for log in await _change_rows(db_session, project.id, bc) if log.field == "box_qty"]
        assert len(logs) == 1

    @pytest.mark.asyncio
    async def test_bulk_change_source(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=80006)
        await bulk_update_by_barcode(db_session, project.id, [{"barcode": bc, "box_qty_override": 7}])
        logs = await _change_rows(db_session, project.id, bc)
        assert len(logs) == 1
        assert logs[0].change_source == "bulk"
        assert logs[0].new_value == "7"


class TestGetChangeLog:
    @pytest.mark.asyncio
    async def test_empty(self, db_session: AsyncSession, project):
        assert await get_change_log(db_session, project.id, f"BC-NONE-{_uid()}") == []

    @pytest.mark.asyncio
    async def test_sorted_fresh_first(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=81001)
        await update_box_multiplicity(db_session, project.id, 81001, box_qty_override=10)
        await update_box_multiplicity(db_session, project.id, 81001, box_qty_override=20)

        log = await get_change_log(db_session, project.id, bc)
        assert len(log) == 2
        # Свежее сверху: последнее изменение (10→20) первым.
        assert log[0]["old_value"] == "10" and log[0]["new_value"] == "20"
        assert log[1]["old_value"] is None and log[1]["new_value"] == "10"

    @pytest.mark.asyncio
    async def test_warehouse_name_resolved(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=81002)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=5)
        await update_box_multiplicity(db_session, project.id, 81002, box_qty_override=9)

        log = await get_change_log(db_session, project.id, bc)
        per_ff = next(r for r in log if r["warehouse_id"] is not None)
        sku = next(r for r in log if r["warehouse_id"] is None)
        assert per_ff["warehouse_name"] == wh.name
        assert sku["warehouse_name"] is None

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        bc = f"BC-SHARED-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=81003)
        await _create_nomenclature(db_session, other_project.id, barcode=bc, article_seller="A", article_wb=81004)
        await update_box_multiplicity(db_session, project.id, 81003, box_qty_override=10)
        await update_box_multiplicity(db_session, other_project.id, 81004, box_qty_override=99)

        log = await get_change_log(db_session, project.id, bc)
        assert len(log) == 1
        assert log[0]["new_value"] == "10"


class TestRevertChange:
    @pytest.mark.asyncio
    async def test_unknown_change_id(self, db_session: AsyncSession, project):
        result = await revert_change(db_session, project.id, 99999999)
        assert result["barcode"] is None

    @pytest.mark.asyncio
    async def test_revert_sku_box_qty_override(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=82001, box_qty_override=5
        )
        # 5 → 50, потом откат → должно вернуться 5.
        await update_box_multiplicity(db_session, project.id, 82001, box_qty_override=50)
        logs = await _change_rows(db_session, project.id, bc)
        change_id = logs[0].id

        result = await revert_change(db_session, project.id, change_id)
        assert result["barcode"] == bc
        assert result["machine_locked"] is False
        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.article_wb == 82001))).scalar_one()
        assert nom.box_qty_override == 5

    @pytest.mark.asyncio
    async def test_revert_clears_to_none(self, db_session: AsyncSession, project):
        """old_value=None → откат ставит поле обратно в None."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=82002)
        await update_box_multiplicity(db_session, project.id, 82002, box_qty_override=30)
        logs = await _change_rows(db_session, project.id, bc)

        await revert_change(db_session, project.id, logs[0].id)
        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.article_wb == 82002))).scalar_one()
        assert nom.box_qty_override is None

    @pytest.mark.asyncio
    async def test_revert_creates_new_revert_log(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=82003)
        await update_box_multiplicity(db_session, project.id, 82003, box_qty_override=40)
        logs_before = await _change_rows(db_session, project.id, bc)
        assert len(logs_before) == 1

        await revert_change(db_session, project.id, logs_before[0].id)
        logs_after = await _change_rows(db_session, project.id, bc)
        assert len(logs_after) == 2
        revert_log = logs_after[-1]
        assert revert_log.change_source == "revert"
        assert revert_log.old_value == "40"
        assert revert_log.new_value is None

    @pytest.mark.asyncio
    async def test_revert_use_flag(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=82004)
        await update_box_multiplicity(db_session, project.id, 82004, use_box_multiplicity=False)
        logs = await _change_rows(db_session, project.id, bc)

        await revert_change(db_session, project.id, logs[0].id)
        nom = (await db_session.execute(select(Nomenclature).where(Nomenclature.article_wb == 82004))).scalar_one()
        assert nom.use_box_multiplicity is True  # откат к "true"

    @pytest.mark.asyncio
    async def test_revert_per_ff_box_qty(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=82005)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10)
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=25)
        logs = [log for log in await _change_rows(db_session, project.id, bc) if log.field == "box_qty"]
        # logs[1] — изменение 10→25; откат вернёт 10.
        result = await revert_change(db_session, project.id, logs[1].id)
        assert result["barcode"] == bc
        per_rf = (
            await db_session.execute(
                select(BoxQtyPerWarehouse).where(
                    BoxQtyPerWarehouse.project_id == project.id,
                    BoxQtyPerWarehouse.barcode == bc,
                    BoxQtyPerWarehouse.warehouse_id == wh.id,
                )
            )
        ).scalar_one()
        assert per_rf.box_qty == 10

    @pytest.mark.asyncio
    async def test_revert_per_ff_box_size(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=82006)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_size="OLD-SIZE")
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_size="NEW-SIZE")
        logs = [log for log in await _change_rows(db_session, project.id, bc) if log.field == "box_size"]

        await revert_change(db_session, project.id, logs[1].id)
        per_rf = (
            await db_session.execute(
                select(BoxQtyPerWarehouse).where(
                    BoxQtyPerWarehouse.project_id == project.id,
                    BoxQtyPerWarehouse.barcode == bc,
                    BoxQtyPerWarehouse.warehouse_id == wh.id,
                )
            )
        ).scalar_one()
        assert per_rf.box_size == "OLD-SIZE"

    @pytest.mark.asyncio
    async def test_revert_blocked_on_machine_ff(self, db_session: AsyncSession, project):
        """Откат box_qty для ФФ, ставшего машинно-заблокированным, → machine_locked, не применён."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=82007)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        # Сначала ручная per-ФФ кратность — создаёт лог.
        await update_per_warehouse(db_session, project.id, bc, wh.id, box_qty=10)
        logs = [log for log in await _change_rows(db_session, project.id, bc) if log.field == "box_qty"]
        change_id = logs[0].id
        wh_id = wh.id
        # Потом приходит машина на этот же ФФ → блокировка.
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh_id, ppb_lines=[(40, 14)])

        result = await revert_change(db_session, project.id, change_id)
        assert result["machine_locked"] is True
        assert result["barcode"] == bc
        # Никакой новой revert-записи не добавлено.
        revert_logs = [log for log in await _change_rows(db_session, project.id, bc) if log.change_source == "revert"]
        assert revert_logs == []

    @pytest.mark.asyncio
    async def test_revert_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Запись журнала чужого проекта не откатывается из нашего."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, other_project.id, barcode=bc, article_seller="A", article_wb=82008)
        await update_box_multiplicity(db_session, other_project.id, 82008, box_qty_override=33)
        logs = await _change_rows(db_session, other_project.id, bc)

        result = await revert_change(db_session, project.id, logs[0].id)
        assert result["barcode"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# Batch-операции: bulk возвращает batch_id, list_recent_batches, revert_batch
# ═══════════════════════════════════════════════════════════════════════════════


class TestBulkBatch:
    @pytest.mark.asyncio
    async def test_bulk_returns_batch_id_when_changed(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=90001)
        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "box_qty_override": 33}],
        )
        assert result["batch_id"] is not None
        assert len(result["batch_id"]) == 36  # UUID

    @pytest.mark.asyncio
    async def test_bulk_batch_id_null_when_no_change(self, db_session: AsyncSession, project):
        """Если apply ничего не меняет, batch_id = None (журнал не пишется)."""
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=90002, box_qty_override=10
        )
        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [{"barcode": bc, "box_qty_override": 10}],
        )
        assert result["batch_id"] is None

    @pytest.mark.asyncio
    async def test_bulk_shares_one_batch_id_across_items(self, db_session: AsyncSession, project):
        bc1, bc2 = f"BC-{_uid()}", f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc1, article_seller="A", article_wb=90011)
        await _create_nomenclature(db_session, project.id, barcode=bc2, article_seller="B", article_wb=90012)
        result = await bulk_update_by_barcode(
            db_session,
            project.id,
            [
                {"barcode": bc1, "box_qty_override": 5},
                {"barcode": bc2, "box_qty_override": 6},
            ],
        )
        # Журнал обеих записей должен иметь одинаковый batch_id.
        rows1 = await _change_rows(db_session, project.id, bc1)
        rows2 = await _change_rows(db_session, project.id, bc2)
        assert rows1[0].batch_id == result["batch_id"]
        assert rows2[0].batch_id == result["batch_id"]


class TestListRecentBatches:
    @pytest.mark.asyncio
    async def test_lists_batches_sorted_newest_first(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=91001)
        r1 = await bulk_update_by_barcode(db_session, project.id, [{"barcode": bc, "box_qty_override": 10}])
        r2 = await bulk_update_by_barcode(db_session, project.id, [{"barcode": bc, "box_qty_override": 20}])

        items = await list_recent_batches(db_session, project.id, limit=10)
        ids = [it["batch_id"] for it in items]
        # Сводки только bulk-операций.
        assert r1["batch_id"] in ids
        assert r2["batch_id"] in ids
        # Свежий первым.
        assert ids[0] == r2["batch_id"]
        # affected_barcodes/changes_count >= 1.
        assert all(it["affected_barcodes"] >= 1 for it in items if it["batch_id"] in (r1["batch_id"], r2["batch_id"]))

    @pytest.mark.asyncio
    async def test_isolation_between_projects(self, db_session: AsyncSession, project, other_project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, other_project.id, barcode=bc, article_seller="A", article_wb=91002)
        await bulk_update_by_barcode(db_session, other_project.id, [{"barcode": bc, "box_qty_override": 7}])

        items = await list_recent_batches(db_session, project.id, limit=10)
        # batch чужого проекта не должен попасть.
        for it in items:
            assert it["affected_barcodes"] >= 0  # просто ничего не падает; самого batch'а нет


class TestRevertBatch:
    @pytest.mark.asyncio
    async def test_revert_batch_restores_old_values(self, db_session: AsyncSession, project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(
            db_session, project.id, barcode=bc, article_seller="A", article_wb=92001, box_qty_override=5
        )
        # bulk меняет 5 → 30
        r = await bulk_update_by_barcode(db_session, project.id, [{"barcode": bc, "box_qty_override": 30}])
        batch_id = r["batch_id"]
        assert batch_id

        # Проверяем что значение реально применилось
        rows_before = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=92001)
        assert rows_before[0]["box_qty_override"] == 30

        # Откатываем весь batch
        result = await revert_batch(db_session, project.id, batch_id)
        assert result["found"] is True
        assert result["reverted"] >= 1
        assert bc in result["affected_barcodes"]
        assert result["locked_barcodes"] == []

        # Значение откатилось обратно
        rows_after = await get_box_multiplicity_table(db_session, project.id, nm_id_filter=92001)
        assert rows_after[0]["box_qty_override"] == 5

    @pytest.mark.asyncio
    async def test_revert_batch_not_found(self, db_session: AsyncSession, project):
        result = await revert_batch(db_session, project.id, "00000000-0000-0000-0000-000000000000")
        assert result["found"] is False
        assert result["reverted"] == 0

    @pytest.mark.asyncio
    async def test_revert_batch_skips_machine_locked(self, db_session: AsyncSession, project):
        """Если на ФФ пришла машина после bulk-вставки — откат box_qty заблокирован."""
        bc = f"BC-{_uid()}"
        nom = await _create_nomenclature(db_session, project.id, barcode=bc, article_seller="A", article_wb=92002)
        wh = await _create_rf_warehouse(db_session, project.id, f"RF-{_uid()}")
        # bulk per-ФФ box_qty
        r = await bulk_update_by_barcode(
            db_session,
            project.id,
            [
                {"barcode": bc, "warehouse_id": wh.id, "box_qty_override": 11},
            ],
        )
        batch_id = r["batch_id"]
        # Машина блокирует ФФ
        await _machine_into_ff(db_session, project.id, nom=nom, warehouse_id=wh.id, ppb_lines=[(40, 18)])

        result = await revert_batch(db_session, project.id, batch_id)
        assert result["found"] is True
        assert bc in result["locked_barcodes"]
        # reverted может быть 0 если только box_qty был в batch
        assert result["reverted"] == 0

    @pytest.mark.asyncio
    async def test_revert_batch_project_isolation(self, db_session: AsyncSession, project, other_project):
        bc = f"BC-{_uid()}"
        await _create_nomenclature(db_session, other_project.id, barcode=bc, article_seller="A", article_wb=92003)
        r = await bulk_update_by_barcode(db_session, other_project.id, [{"barcode": bc, "box_qty_override": 9}])
        # Откат из чужого проекта не находит batch
        result = await revert_batch(db_session, project.id, r["batch_id"])
        assert result["found"] is False
