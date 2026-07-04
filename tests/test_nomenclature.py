"""
Tests for cost/nomenclature — get and upload nomenclature.
Uses DB fixtures from conftest.py.
"""

import io
import uuid
from decimal import Decimal

import pandas as pd
import pytest

from backend.models import (
    CostOrder,
    CostOrderItem,
    DutyBasis,
    DutyException,
    DutyRule,
    FulfillmentProvider,
    FulfillmentStock,
    Nomenclature,
    VehicleStatus,
    Warehouse,
    WarehouseStock,
    WarehouseType,
)
from backend.services.cost.duty import bulk_update_nomenclature_weight
from backend.services.cost.nomenclature import (
    get_missing_area_barcodes,
    get_missing_weight_barcodes,
    get_nomenclature,
    get_nomenclature_subjects,
    upload_nomenclature,
)

# ═══════════════════════════════════════════════════════════════════════════════
# get_nomenclature
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetNomenclature:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        """New project has no nomenclature."""
        result = await get_nomenclature(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_respects_limit(self, db_session, project):
        """Limit parameter works."""
        result = await get_nomenclature(db_session, project.id, limit=5)
        assert len(result) <= 5

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Nomenclature is isolated by project."""
        result_a = await get_nomenclature(db_session, project.id)
        result_b = await get_nomenclature(db_session, other_project.id)
        assert result_a == []
        assert result_b == []


# ═══════════════════════════════════════════════════════════════════════════════
# get_nomenclature_subjects
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetNomenclatureSubjects:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        result = await get_nomenclature_subjects(db_session, project.id)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_distinct_sorted(self, db_session, project):
        """Duplicates collapsed, result sorted; nulls and empty strings excluded."""
        for bc, subj in [
            ("9000000000001", "Шторы интерьерные"),
            ("9000000000002", "Шторы интерьерные"),
            ("9000000000003", "Ковры"),
            ("9000000000004", "Вазы"),
            ("9000000000005", None),
            ("9000000000006", ""),
        ]:
            db_session.add(Nomenclature(project_id=project.id, barcode=bc, subject=subj))
        await db_session.commit()

        result = await get_nomenclature_subjects(db_session, project.id)
        assert result == ["Вазы", "Ковры", "Шторы интерьерные"]

    @pytest.mark.asyncio
    async def test_not_limited_by_row_count(self, db_session, project):
        """Subject at alphabet tail still returned even if >1000 rows precede it."""
        for i in range(1001):
            db_session.add(Nomenclature(project_id=project.id, barcode=f"8{i:012d}", subject="Ковры"))
        db_session.add(Nomenclature(project_id=project.id, barcode="8999999999999", subject="Шторы интерьерные"))
        await db_session.commit()

        result = await get_nomenclature_subjects(db_session, project.id)
        assert "Шторы интерьерные" in result

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        db_session.add(Nomenclature(project_id=project.id, barcode="7000000000001", subject="Только_A"))
        db_session.add(Nomenclature(project_id=other_project.id, barcode="7000000000002", subject="Только_B"))
        await db_session.commit()

        result_a = await get_nomenclature_subjects(db_session, project.id)
        result_b = await get_nomenclature_subjects(db_session, other_project.id)
        assert "Только_A" in result_a and "Только_B" not in result_a
        assert "Только_B" in result_b and "Только_A" not in result_b


# ═══════════════════════════════════════════════════════════════════════════════
# upload_nomenclature
# ═══════════════════════════════════════════════════════════════════════════════


class TestUploadNomenclature:
    def _make_excel(self, rows: list[dict]) -> bytes:
        """Create an Excel file from a list of dicts."""
        df = pd.DataFrame(rows)
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return buf.getvalue()

    @pytest.mark.asyncio
    async def test_insert_new(self, db_session, project):
        """Upload new nomenclature items."""
        data = self._make_excel(
            [
                {
                    "Баркод": "2000000000001",
                    "Бренд": "TestBrand",
                    "Предмет": "Футболки",
                    "Артикул продавца": "ART-001",
                    "Артикул WB": 12345,
                    "Объем, л": 0.5,
                },
                {
                    "Баркод": "2000000000002",
                    "Бренд": "TestBrand",
                    "Предмет": "Штаны",
                    "Артикул продавца": "ART-002",
                    "Артикул WB": 12346,
                    "Объем, л": 1.0,
                },
            ]
        )
        inserted, updated = await upload_nomenclature(db_session, project.id, data)
        assert inserted == 2
        assert updated == 0

        # Verify data is in DB
        items = await get_nomenclature(db_session, project.id)
        barcodes = {n.barcode for n in items}
        assert "2000000000001" in barcodes
        assert "2000000000002" in barcodes

    @pytest.mark.asyncio
    async def test_update_existing(self, db_session, project):
        """Re-uploading updates existing barcodes."""
        data1 = self._make_excel(
            [
                {
                    "Баркод": "3000000000001",
                    "Бренд": "Old",
                    "Предмет": "X",
                    "Артикул продавца": "A1",
                    "Артикул WB": 1,
                    "Объем, л": 0.1,
                },
            ]
        )
        await upload_nomenclature(db_session, project.id, data1)

        data2 = self._make_excel(
            [
                {
                    "Баркод": "3000000000001",
                    "Бренд": "New",
                    "Предмет": "Y",
                    "Артикул продавца": "A2",
                    "Артикул WB": 2,
                    "Объем, л": 0.2,
                },
            ]
        )
        inserted, updated = await upload_nomenclature(db_session, project.id, data2)
        assert inserted == 0
        assert updated == 1

    @pytest.mark.asyncio
    async def test_skips_empty_barcode(self, db_session, project):
        """Rows with empty or nan barcode are skipped."""
        data = self._make_excel(
            [
                {"Баркод": "", "Бренд": "B", "Предмет": "S", "Артикул продавца": "", "Артикул WB": None, "Объем, л": 0},
            ]
        )
        inserted, updated = await upload_nomenclature(db_session, project.id, data)
        assert inserted == 0
        assert updated == 0

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        """Upload to project A does not affect project B."""
        data = self._make_excel(
            [
                {
                    "Баркод": "4000000000001",
                    "Бренд": "Isolated",
                    "Предмет": "Test",
                    "Артикул продавца": "X",
                    "Артикул WB": 99,
                    "Объем, л": 0.5,
                },
            ]
        )
        await upload_nomenclature(db_session, project.id, data)

        items_a = await get_nomenclature(db_session, project.id)
        items_b = await get_nomenclature(db_session, other_project.id)
        assert len(items_a) >= 1
        assert all(n.barcode != "4000000000001" for n in items_b)


# ═══════════════════════════════════════════════════════════════════════════════
# get_missing_area_barcodes
# ═══════════════════════════════════════════════════════════════════════════════


def _vno() -> str:
    return f"V-{uuid.uuid4().hex[:8]}"


class TestGetMissingAreaBarcodes:
    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        assert await get_missing_area_barcodes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_filters_to_area_basis_missing_in_vehicles(self, db_session, project):
        """Only AREA-basis barcodes that are in a vehicle and have no area_m2."""
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.61")))
        db_session.add(DutyRule(project_id=pid, subject="Шторы", basis=DutyBasis.WEIGHT, rate=Decimal("0.5")))
        db_session.add_all(
            [
                Nomenclature(project_id=pid, barcode="BC-NEED", subject="Ковры", article_seller="A1"),
                Nomenclature(project_id=pid, barcode="BC-HAS-AREA", subject="Ковры", area_m2=Decimal("2.0")),
                Nomenclature(project_id=pid, barcode="BC-WEIGHT", subject="Шторы"),
                Nomenclature(project_id=pid, barcode="BC-NO-RULE", subject="Вазы"),
                Nomenclature(project_id=pid, barcode="BC-NOT-IN-VEHICLE", subject="Ковры"),
            ]
        )
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        for bc, qty in [("BC-NEED", 100), ("BC-HAS-AREA", 5), ("BC-WEIGHT", 7), ("BC-NO-RULE", 9)]:
            db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode=bc, qty=qty))
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        assert [r["barcode"] for r in result] == ["BC-NEED"]
        row = result[0]
        assert row["subject"] == "Ковры"
        assert row["article_seller"] == "A1"
        assert row["total_qty"] == 100
        assert row["vehicles"] == [vno]

    @pytest.mark.asyncio
    async def test_item_area_excludes_barcode(self, db_session, project):
        """If the cost item already carries area (Excel), it is not missing even with empty reference."""
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.38")))
        db_session.add(Nomenclature(project_id=pid, barcode="BC-ITEMAR", subject="Ковры"))  # no reference area
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-ITEMAR", qty=10, area_m2=Decimal("1.5")))
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-ITEMAR" for r in result)

    @pytest.mark.asyncio
    async def test_sibling_source_with_area_excludes_barcode(self, db_session, project):
        """Same barcode from 2 sources in one vehicle: a sibling carrying area covers the
        blank row (duty propagates it) — so it must NOT be flagged as missing."""
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.38")))
        db_session.add(Nomenclature(project_id=pid, barcode="BC-SIB", subject="Ковры"))  # no reference area
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-SIB", qty=10, area_m2=Decimal("2.0")))
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-SIB", qty=7))  # blank sibling
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-SIB" for r in result)

    @pytest.mark.asyncio
    async def test_sibling_in_other_vehicle_does_not_exclude(self, db_session, project):
        """Propagation is per-vehicle: area on the barcode in vehicle A does NOT cover a
        blank row of the same barcode in vehicle B — B is still missing."""
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.38")))
        db_session.add(Nomenclature(project_id=pid, barcode="BC-PV", subject="Ковры"))
        va, vb = _vno(), _vno()
        db_session.add(CostOrder(project_id=pid, order_no=va, status=VehicleStatus.FORMING))
        db_session.add(CostOrder(project_id=pid, order_no=vb, status=VehicleStatus.FORMING))
        db_session.add(CostOrderItem(project_id=pid, order_no=va, barcode="BC-PV", qty=10, area_m2=Decimal("2.0")))
        db_session.add(CostOrderItem(project_id=pid, order_no=vb, barcode="BC-PV", qty=7))  # blank, other vehicle
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        row = next((r for r in result if r["barcode"] == "BC-PV"), None)
        assert row is not None
        assert row["total_qty"] == 7
        assert row["vehicles"] == [vb]

    @pytest.mark.asyncio
    async def test_zero_area_treated_as_missing(self, db_session, project):
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.61")))
        db_session.add(Nomenclature(project_id=pid, barcode="BC-ZERO", subject="Ковры", area_m2=Decimal("0")))
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.CUSTOMS))
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-ZERO", qty=12))
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        assert [r["barcode"] for r in result] == ["BC-ZERO"]

    @pytest.mark.asyncio
    async def test_aggregates_across_vehicles_including_delivered(self, db_session, project):
        """Qty summed and vehicles collected across all statuses (incl. DELIVERED)."""
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.61")))
        db_session.add(Nomenclature(project_id=pid, barcode="BC-MULTI", subject="Ковры"))
        v1, v2 = _vno(), _vno()
        db_session.add(CostOrder(project_id=pid, order_no=v1, status=VehicleStatus.FORMING))
        db_session.add(CostOrder(project_id=pid, order_no=v2, status=VehicleStatus.DELIVERED))
        db_session.add(CostOrderItem(project_id=pid, order_no=v1, barcode="BC-MULTI", qty=100))
        db_session.add(CostOrderItem(project_id=pid, order_no=v2, barcode="BC-MULTI", qty=50))
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        row = next(r for r in result if r["barcode"] == "BC-MULTI")
        assert row["total_qty"] == 150
        assert row["vehicles"] == sorted([v1, v2])

    @pytest.mark.asyncio
    async def test_excludes_deleted_vehicle(self, db_session, project):
        pid = project.id
        db_session.add(DutyRule(project_id=pid, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.61")))
        db_session.add(Nomenclature(project_id=pid, barcode="BC-DEL", subject="Ковры"))
        vno = _vno()
        order = CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING)
        order.soft_delete()
        db_session.add(order)
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-DEL", qty=10))
        await db_session.commit()

        result = await get_missing_area_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-DEL" for r in result)

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        for p in (project, other_project):
            db_session.add(DutyRule(project_id=p.id, subject="Ковры", basis=DutyBasis.AREA, rate=Decimal("0.61")))
            db_session.add(Nomenclature(project_id=p.id, barcode=f"BC-{p.id}", subject="Ковры"))
            vno = _vno()
            db_session.add(CostOrder(project_id=p.id, order_no=vno, status=VehicleStatus.FORMING))
            db_session.add(CostOrderItem(project_id=p.id, order_no=vno, barcode=f"BC-{p.id}", qty=10))
        await db_session.commit()

        res_a = await get_missing_area_barcodes(db_session, project.id)
        res_b = await get_missing_area_barcodes(db_session, other_project.id)
        assert [r["barcode"] for r in res_a] == [f"BC-{project.id}"]
        assert [r["barcode"] for r in res_b] == [f"BC-{other_project.id}"]


# ═══════════════════════════════════════════════════════════════════════════════
# get_missing_weight_barcodes
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetMissingWeightBarcodes:
    """Weight-missing list = Nomenclature без веса, у которых есть годный остаток (склад/ФФ)
    ИЛИ товар в пути в машине (SHIPPED/CUSTOMS/DISPATCHED). Новинки только в неотправленной
    (FORMING) машине без остатка отсекаются автоматически."""

    async def _mk_wh(self, db_session, pid: int) -> int:
        wh = Warehouse(project_id=pid, name=f"WH-{uuid.uuid4().hex[:6]}", warehouse_type=WarehouseType.FULFILLMENT.value)
        db_session.add(wh)
        await db_session.flush()
        return wh.id

    async def _mk_nom(self, db_session, pid: int, barcode: str, *, subject="Наволочки", article="A1", weight=None) -> int:
        nom = Nomenclature(project_id=pid, barcode=barcode, subject=subject, article_seller=article, weight_kg=weight)
        db_session.add(nom)
        await db_session.flush()
        return nom.id

    @pytest.mark.asyncio
    async def test_empty_for_new_project(self, db_session, project):
        assert await get_missing_weight_barcodes(db_session, project.id) == []

    @pytest.mark.asyncio
    async def test_shown_when_in_transit(self, db_session, project):
        """Товар без веса, едущий в машине (SHIPPED) — показан, с кол-вом и номером машины."""
        pid = project.id
        await self._mk_nom(db_session, pid, "BC-TRANSIT")
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.SHIPPED))
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-TRANSIT", qty=50))
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        assert [r["barcode"] for r in result] == ["BC-TRANSIT"]
        assert result[0]["total_qty"] == 50
        assert result[0]["vehicles"] == [vno]

    @pytest.mark.asyncio
    async def test_hidden_when_machine_has_weight(self, db_session, project):
        """Вес уже указан в машине (CostOrderItem.weight_kg) → НЕ в списке: авто-расчёт веса
        сборки возьмёт его из машины автоматически, дозаполнять в справочнике не нужно."""
        pid = project.id
        await self._mk_nom(db_session, pid, "BC-MACHW")
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.SHIPPED))
        db_session.add(
            CostOrderItem(project_id=pid, order_no=vno, barcode="BC-MACHW", qty=20, weight_kg=Decimal("1.5"))
        )
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-MACHW" for r in result)

    @pytest.mark.asyncio
    async def test_hidden_when_only_forming(self, db_session, project):
        """Товар только в неотправленной (FORMING) машине, без остатка — скрыт: это и есть
        «новинка, которая только формируется в заказе»."""
        pid = project.id
        await self._mk_nom(db_session, pid, "BC-FORMING")
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.FORMING))
        db_session.add(CostOrderItem(project_id=pid, order_no=vno, barcode="BC-FORMING", qty=10))
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-FORMING" for r in result)

    @pytest.mark.asyncio
    async def test_shown_when_warehouse_stock(self, db_session, project):
        """Есть годный остаток на нашем складе → показан (кол-во = остаток)."""
        pid = project.id
        nom_id = await self._mk_nom(db_session, pid, "BC-WH")
        wh = await self._mk_wh(db_session, pid)
        db_session.add(WarehouseStock(project_id=pid, warehouse_id=wh, nomenclature_id=nom_id, barcode="BC-WH", quantity=7))
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        row = next((r for r in result if r["barcode"] == "BC-WH"), None)
        assert row is not None
        assert row["total_qty"] == 7

    @pytest.mark.asyncio
    async def test_shown_when_ff_stock(self, db_session, project):
        """Есть годный остаток на ФФ → показан."""
        pid = project.id
        nom_id = await self._mk_nom(db_session, pid, "BC-FF")
        wh = await self._mk_wh(db_session, pid)
        db_session.add(
            FulfillmentStock(
                project_id=pid, warehouse_id=wh, provider=FulfillmentProvider.SKLADBOT.value,
                barcode="BC-FF", nomenclature_id=nom_id, qty_good=12,
            )
        )
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        row = next((r for r in result if r["barcode"] == "BC-FF"), None)
        assert row is not None
        assert row["total_qty"] == 12

    @pytest.mark.asyncio
    async def test_hidden_when_has_weight(self, db_session, project):
        """Есть остаток, но вес уже задан → не в списке."""
        pid = project.id
        nom_id = await self._mk_nom(db_session, pid, "BC-HASW", weight=Decimal("0.5"))
        wh = await self._mk_wh(db_session, pid)
        db_session.add(WarehouseStock(project_id=pid, warehouse_id=wh, nomenclature_id=nom_id, barcode="BC-HASW", quantity=7))
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-HASW" for r in result)

    @pytest.mark.asyncio
    async def test_hidden_when_no_presence(self, db_session, project):
        """Карточка без веса, но без остатка и не в машине — не показывается."""
        pid = project.id
        await self._mk_nom(db_session, pid, "BC-BARE")
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-BARE" for r in result)

    @pytest.mark.asyncio
    async def test_zero_stock_not_shown(self, db_session, project):
        """Нулевой годный остаток и не в пути → не показывается."""
        pid = project.id
        nom_id = await self._mk_nom(db_session, pid, "BC-ZEROSTOCK")
        wh = await self._mk_wh(db_session, pid)
        db_session.add(WarehouseStock(project_id=pid, warehouse_id=wh, nomenclature_id=nom_id, barcode="BC-ZEROSTOCK", quantity=0))
        await db_session.commit()

        result = await get_missing_weight_barcodes(db_session, pid)
        assert all(r["barcode"] != "BC-ZEROSTOCK" for r in result)

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project):
        for p in (project, other_project):
            await self._mk_nom(db_session, p.id, f"WBC-{p.id}")
            vno = _vno()
            db_session.add(CostOrder(project_id=p.id, order_no=vno, status=VehicleStatus.SHIPPED))
            db_session.add(CostOrderItem(project_id=p.id, order_no=vno, barcode=f"WBC-{p.id}", qty=5))
        await db_session.commit()

        res_a = await get_missing_weight_barcodes(db_session, project.id)
        res_b = await get_missing_weight_barcodes(db_session, other_project.id)
        assert [r["barcode"] for r in res_a] == [f"WBC-{project.id}"]
        assert [r["barcode"] for r in res_b] == [f"WBC-{other_project.id}"]


# ═══════════════════════════════════════════════════════════════════════════════
# bulk_update_nomenclature_weight
# ═══════════════════════════════════════════════════════════════════════════════


class TestBulkUpdateNomenclatureWeight:
    @pytest.mark.asyncio
    async def test_updates_by_barcode_and_reports_not_found(self, db_session, project):
        pid = project.id
        db_session.add(Nomenclature(project_id=pid, barcode="WB-1", subject="Наволочки"))
        await db_session.commit()

        updated, not_found = await bulk_update_nomenclature_weight(
            db_session, pid, [{"barcode": "WB-1", "weight_kg": 1.25}, {"barcode": "WB-MISSING", "weight_kg": 2}]
        )
        assert updated == 1
        assert not_found == ["WB-MISSING"]

        noms = await get_nomenclature(db_session, pid)
        nom = next(n for n in noms if n.barcode == "WB-1")
        assert nom.weight_kg == Decimal("1.25")

    @pytest.mark.asyncio
    async def test_backfills_weight_into_vehicle_lines(self, db_session, project):
        """Заполнение веса в справочнике проливается в позиции машины без веса; явные веса
        не перезаписываются."""
        pid = project.id
        db_session.add(Nomenclature(project_id=pid, barcode="WB-BF", subject="Наволочки"))
        vno = _vno()
        db_session.add(CostOrder(project_id=pid, order_no=vno, status=VehicleStatus.SHIPPED))
        blank = CostOrderItem(project_id=pid, order_no=vno, barcode="WB-BF", qty=10)  # no weight
        explicit = CostOrderItem(project_id=pid, order_no=vno, barcode="WB-BF", qty=5, weight_kg=Decimal("9.9"))
        db_session.add_all([blank, explicit])
        await db_session.commit()

        await bulk_update_nomenclature_weight(db_session, pid, [{"barcode": "WB-BF", "weight_kg": 1.25}])
        await db_session.refresh(blank)
        await db_session.refresh(explicit)
        assert blank.weight_kg == Decimal("1.25")  # blank line filled
        assert explicit.weight_kg == Decimal("9.9")  # explicit weight untouched
