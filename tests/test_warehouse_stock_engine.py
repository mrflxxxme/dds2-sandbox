"""
Tests for warehouse_stock_engine.py — Stock balance updates, movements,
adjustments, summaries, unified stock.

This is the most critical service (1014 lines) — covers:
  - _update_stock (core engine)
  - _resolve_barcode
  - _next_number
  - get_warehouse_stock
  - get_stock_movements
  - create_adjustment
  - get_stock_summary
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import MovementType
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_outbound import create_shipment, ship_shipment
from backend.services.warehouse_stock_engine import (
    _next_number,
    _resolve_barcode,
    _update_stock,
    create_adjustment,
    get_stock_movements,
    get_stock_summary,
    get_warehouse_stock,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def wh(db_session: AsyncSession, project):
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"ENGINE-{_uid()}", "warehouse_type": "FULFILLMENT"},
    )


@pytest_asyncio.fixture
async def wh2(db_session: AsyncSession, project):
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"ENGINE2-{_uid()}", "warehouse_type": "EXTERNAL"},
    )


@pytest_asyncio.fixture
async def bc(db_session: AsyncSession, project):
    """Create nomenclature and return barcode."""
    barcode = f"ENG-{_uid()}"
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) " "VALUES (:pid, :bc, :subj, NOW())"),
        {"pid": project.id, "bc": barcode, "subj": "Engine Test"},
    )
    await db_session.commit()
    return barcode


@pytest_asyncio.fixture
async def bc2(db_session: AsyncSession, project):
    """Second barcode for multi-item tests."""
    barcode = f"ENG2-{_uid()}"
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) " "VALUES (:pid, :bc, :subj, NOW())"),
        {"pid": project.id, "bc": barcode, "subj": "Engine Test 2"},
    )
    await db_session.commit()
    return barcode


async def _receive(db_session, project, wh, barcode, qty):
    """Stock a warehouse via inbound receipt."""
    receipt = await create_receipt(
        db_session,
        project.id,
        wh.id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project.id, receipt.id)


# ═══════════════════════════════════════════════════════════════════════════════
# _resolve_barcode
# ═══════════════════════════════════════════════════════════════════════════════


class TestResolveBarcode:
    @pytest.mark.asyncio
    async def test_resolve_found(self, db_session, project, bc):
        nom = await _resolve_barcode(db_session, project.id, bc)
        assert nom is not None
        assert nom.barcode == bc
        assert nom.project_id == project.id

    @pytest.mark.asyncio
    async def test_resolve_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="Barcode not found"):
            await _resolve_barcode(db_session, project.id, "NONEXISTENT")

    @pytest.mark.asyncio
    async def test_resolve_project_isolation(self, db_session, project, other_project, bc):
        """Barcode from one project must not resolve in another."""
        with pytest.raises(ValueError, match="Barcode not found"):
            await _resolve_barcode(db_session, other_project.id, bc)


# ═══════════════════════════════════════════════════════════════════════════════
# _next_number
# ═══════════════════════════════════════════════════════════════════════════════


class TestNextNumber:
    @pytest.mark.asyncio
    async def test_sequential_numbers(self, db_session, project, wh, bc):
        from backend.models.warehouse import InboundReceipt

        num1 = await _next_number(db_session, project.id, "IN", InboundReceipt)
        assert num1.startswith("IN-")
        # The exact number depends on existing data, but it should be parseable
        parts = num1.split("-")
        assert len(parts) == 2
        assert parts[1].isdigit()


# ═══════════════════════════════════════════════════════════════════════════════
# _update_stock — core stock engine
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateStock:
    @pytest.mark.asyncio
    async def test_inbound_creates_stock(self, db_session, project, wh, bc):
        nom = await _resolve_barcode(db_session, project.id, bc)
        await _update_stock(
            db_session,
            project_id=project.id,
            warehouse_id=wh.id,
            nomenclature_id=nom.id,
            barcode=bc,
            delta=100,
            movement_type=MovementType.INBOUND,
            reference_type="TEST",
            reference_id=None,
        )
        await db_session.commit()

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert len(found) == 1
        assert found[0]["quantity"] == 100

    @pytest.mark.asyncio
    async def test_outbound_reduces_stock(self, db_session, project, wh, bc):
        nom = await _resolve_barcode(db_session, project.id, bc)
        await _update_stock(
            db_session,
            project.id,
            wh.id,
            nom.id,
            bc,
            100,
            MovementType.INBOUND,
            "TEST",
        )
        await _update_stock(
            db_session,
            project.id,
            wh.id,
            nom.id,
            bc,
            -30,
            MovementType.OUTBOUND,
            "TEST",
        )
        await db_session.commit()

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 70

    @pytest.mark.asyncio
    async def test_insufficient_stock_raises(self, db_session, project, wh, bc):
        nom = await _resolve_barcode(db_session, project.id, bc)
        await _update_stock(
            db_session,
            project.id,
            wh.id,
            nom.id,
            bc,
            10,
            MovementType.INBOUND,
            "TEST",
        )
        with pytest.raises(ValueError, match="Insufficient stock"):
            await _update_stock(
                db_session,
                project.id,
                wh.id,
                nom.id,
                bc,
                -20,
                MovementType.OUTBOUND,
                "TEST",
            )

    @pytest.mark.asyncio
    async def test_movement_audit_trail(self, db_session, project, wh, bc):
        nom = await _resolve_barcode(db_session, project.id, bc)
        await _update_stock(
            db_session,
            project.id,
            wh.id,
            nom.id,
            bc,
            50,
            MovementType.INBOUND,
            "RECEIPT",
            reference_id=1,
            comment="test receive",
        )
        await db_session.commit()

        movements = await get_stock_movements(db_session, project.id, wh.id)
        assert len(movements) >= 1
        latest = movements[0]
        assert latest.movement_type == MovementType.INBOUND
        assert latest.quantity == 50
        assert latest.comment == "test receive"


# ═══════════════════════════════════════════════════════════════════════════════
# Receive, Ship, Transfer via full service layer
# ═══════════════════════════════════════════════════════════════════════════════


class TestReceiveShipTransfer:
    @pytest.mark.asyncio
    async def test_receive_increases_stock(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 200)
        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 200

    @pytest.mark.asyncio
    async def test_ship_decreases_stock(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 100)
        shipment = await create_shipment(
            db_session,
            project.id,
            wh.id,
            {"items": [{"barcode": bc, "quantity": 40}]},
        )
        await ship_shipment(db_session, project.id, shipment.id)

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 60

    @pytest.mark.asyncio
    async def test_multiple_items(self, db_session, project, wh, bc, bc2):
        await _receive(db_session, project, wh, bc, 100)
        await _receive(db_session, project, wh, bc2, 200)
        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        barcodes = {s["barcode"]: s["quantity"] for s in stock}
        assert barcodes[bc] == 100
        assert barcodes[bc2] == 200


# ═══════════════════════════════════════════════════════════════════════════════
# create_adjustment
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdjustment:
    @pytest.mark.asyncio
    async def test_positive_adjustment(self, db_session, project, wh, bc):
        adjustment = await create_adjustment(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "delta": 50, "reason": "surplus"},
        )
        assert adjustment.id is not None
        assert adjustment.delta == 50
        assert adjustment.reason == "surplus"

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 50

    @pytest.mark.asyncio
    async def test_negative_adjustment_with_stock(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 100)
        adjustment = await create_adjustment(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "delta": -30, "reason": "shortage"},
        )
        assert adjustment.delta == -30

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 70

    @pytest.mark.asyncio
    async def test_negative_adjustment_insufficient(self, db_session, project, wh, bc):
        with pytest.raises(ValueError, match="Insufficient stock"):
            await create_adjustment(
                db_session,
                project.id,
                wh.id,
                {"barcode": bc, "delta": -10, "reason": "shortage"},
            )

    @pytest.mark.asyncio
    async def test_adjustment_warehouse_not_found(self, db_session, project, bc):
        with pytest.raises(ValueError, match="Warehouse not found"):
            await create_adjustment(
                db_session,
                project.id,
                999999,
                {"barcode": bc, "delta": 10, "reason": "test"},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# get_warehouse_stock
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetWarehouseStock:
    @pytest.mark.asyncio
    async def test_empty_warehouse(self, db_session, project, wh):
        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        assert stock == []

    @pytest.mark.asyncio
    async def test_stock_enriched_fields(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 100)
        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        item = found[0]
        assert "quantity" in item
        assert "reserved" in item
        assert "available" in item
        assert item["available"] == item["quantity"] - item["reserved"]

    @pytest.mark.asyncio
    async def test_stock_project_isolation(self, db_session, project, other_project, wh, bc):
        await _receive(db_session, project, wh, bc, 100)
        stock = await get_warehouse_stock(db_session, other_project.id, wh.id)
        assert stock == []


# ═══════════════════════════════════════════════════════════════════════════════
# get_stock_movements
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetStockMovements:
    @pytest.mark.asyncio
    async def test_movements_recorded(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 100)
        movements = await get_stock_movements(db_session, project.id, wh.id)
        assert len(movements) >= 1
        types = [m.movement_type for m in movements]
        assert MovementType.INBOUND in types

    @pytest.mark.asyncio
    async def test_movements_limit(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 100)
        movements = await get_stock_movements(db_session, project.id, wh.id, limit=1)
        assert len(movements) <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# get_stock_summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetStockSummary:
    @pytest.mark.asyncio
    async def test_summary_empty(self, db_session, project):
        summary = await get_stock_summary(db_session, project.id)
        assert isinstance(summary, list)

    @pytest.mark.asyncio
    async def test_summary_aggregates_across_warehouses(self, db_session, project, wh, wh2, bc):
        # wh2 is EXTERNAL — stock it manually via adjustment
        await _receive(db_session, project, wh, bc, 100)
        await create_adjustment(
            db_session,
            project.id,
            wh2.id,
            {"barcode": bc, "delta": 50, "reason": "test"},
        )

        summary = await get_stock_summary(db_session, project.id)
        # Find bc in summary
        nom = await _resolve_barcode(db_session, project.id, bc)
        found = [s for s in summary if s["nomenclature_id"] == nom.id]
        assert len(found) == 1
        assert found[0]["total"] == 150  # 100 + 50

    @pytest.mark.asyncio
    async def test_summary_includes_warehouse_breakdown(self, db_session, project, wh, bc):
        await _receive(db_session, project, wh, bc, 80)
        summary = await get_stock_summary(db_session, project.id)
        nom = await _resolve_barcode(db_session, project.id, bc)
        found = [s for s in summary if s["nomenclature_id"] == nom.id]
        assert wh.id in found[0]["warehouses"]
        assert found[0]["warehouses"][wh.id] == 80


# ═══════════════════════════════════════════════════════════════════════════════
# Snapshot: full receive → ship → verify stock and movements
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullSnapshot:
    @pytest.mark.asyncio
    async def test_full_receive_ship_cycle(self, db_session, project, wh, bc):
        """Full cycle: receive 200, ship 80, verify stock=120 and all movements."""
        await _receive(db_session, project, wh, bc, 200)

        shipment = await create_shipment(
            db_session,
            project.id,
            wh.id,
            {"items": [{"barcode": bc, "quantity": 80}]},
        )
        await ship_shipment(db_session, project.id, shipment.id)

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 120

        movements = await get_stock_movements(db_session, project.id, wh.id)
        types = [m.movement_type for m in movements]
        assert MovementType.INBOUND in types
        assert MovementType.OUTBOUND in types
