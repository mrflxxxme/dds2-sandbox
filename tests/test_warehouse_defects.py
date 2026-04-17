"""
Tests for warehouse defect management — mark, receive, writeoff, recover,
defect transfers, and defect stock queries.

Covers:
  - mark_defect (good stock → defect)
  - receive_defect (external defect inbound)
  - writeoff_defect (destroy defective goods)
  - recover_defect (repair defective → good stock)
  - defect transfers via create_transfer / send_transfer / complete_transfer
  - get_defect_stock / get_defect_summary queries
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import MovementType, WarehouseStock
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_defect import (
    get_defect_stock,
    get_defect_summary,
    mark_defect,
    receive_defect,
    recover_defect,
    writeoff_defect,
)
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_outbound import (
    complete_transfer,
    create_transfer,
    send_transfer,
)
from backend.services.warehouse_stock_engine import (
    get_stock_movements,
    get_warehouse_stock,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def wh(db_session: AsyncSession, project):
    """FULFILLMENT warehouse for defect tests."""
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"DEFECT-{_uid()}", "warehouse_type": "FULFILLMENT"},
    )


@pytest_asyncio.fixture
async def wh2(db_session: AsyncSession, project):
    """Second warehouse (EXTERNAL) for defect transfer tests."""
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"DEFECT2-{_uid()}", "warehouse_type": "EXTERNAL"},
    )


@pytest_asyncio.fixture
async def bc(db_session: AsyncSession, project):
    """Create nomenclature and return barcode."""
    barcode = f"DEF-{_uid()}"
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) " "VALUES (:pid, :bc, :subj, NOW())"),
        {"pid": project.id, "bc": barcode, "subj": "Defect Test Item"},
    )
    await db_session.commit()
    return barcode


@pytest_asyncio.fixture
async def bc2(db_session: AsyncSession, project):
    """Second barcode for multi-item defect tests."""
    barcode = f"DEF2-{_uid()}"
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) " "VALUES (:pid, :bc, :subj, NOW())"),
        {"pid": project.id, "bc": barcode, "subj": "Defect Test Item 2"},
    )
    await db_session.commit()
    return barcode


async def _stock(db_session, project, wh, barcode, qty):
    """Stock a warehouse via inbound receipt."""
    receipt = await create_receipt(
        db_session,
        project.id,
        wh.id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project.id, receipt.id)


# ═══════════════════════════════════════════════════════════════════════════════
# TestMarkDefect
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarkDefect:
    @pytest.mark.asyncio
    async def test_mark_defect_happy_path(self, db_session, project, wh, bc):
        """stock 100, mark 30 -> qty=70, defect=30, creates DM-N document."""
        await _stock(db_session, project, wh, bc, 100)

        result = await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 30, "reason": "damaged packaging"},
        )
        assert result["status"] == "ok"
        assert result["processed"] == 1
        assert result["operation_id"] is not None
        assert result["number"].startswith("DM-")

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert len(found) == 1
        assert found[0]["quantity"] == 70
        assert found[0]["defect_quantity"] == 30

    @pytest.mark.asyncio
    async def test_mark_defect_insufficient(self, db_session, project, wh, bc):
        """stock 10, mark 20 -> status=error, no document created, stock unchanged."""
        await _stock(db_session, project, wh, bc, 10)

        result = await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 20, "reason": "test"},
        )
        assert result["status"] == "error"
        assert result["processed"] == 0
        assert result["failed"] == 1
        assert result["operation_id"] is None
        assert "Insufficient stock" in result["errors"][0]["error"]

        # Stock unchanged
        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 10
        assert found[0]["defect_quantity"] == 0

    @pytest.mark.asyncio
    async def test_mark_defect_creates_movement(self, db_session, project, wh, bc):
        """Verify StockMovement with DEFECT_MARK is created."""
        await _stock(db_session, project, wh, bc, 50)

        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 15, "reason": "scratched"},
        )

        movements = await get_stock_movements(db_session, project.id, wh.id)
        defect_moves = [m for m in movements if m.movement_type == MovementType.DEFECT_MARK]
        assert len(defect_moves) == 1
        assert defect_moves[0].quantity == -15
        assert defect_moves[0].defect_delta == 15
        assert defect_moves[0].comment == "scratched"


# ═══════════════════════════════════════════════════════════════════════════════
# TestReceiveDefect
# ═══════════════════════════════════════════════════════════════════════════════


class TestReceiveDefect:
    @pytest.mark.asyncio
    async def test_receive_defect_happy_path(self, db_session, project, wh, bc):
        """defect_qty += N, qty unchanged."""
        await _stock(db_session, project, wh, bc, 50)

        result = await receive_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 10, "reason": "WB return"},
        )
        assert result["status"] == "ok"
        assert result["processed"] == 1
        assert result["receipt_id"] is not None

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["quantity"] == 50  # unchanged
        assert found[0]["defect_quantity"] == 10

    @pytest.mark.asyncio
    async def test_receive_defect_creates_stock_row(self, db_session, project, wh, bc):
        """New barcode with no existing stock row gets created."""
        result = await receive_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 5, "reason": "return from marketplace"},
        )
        assert result["status"] == "ok"

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert len(found) == 1
        assert found[0]["quantity"] == 0
        assert found[0]["defect_quantity"] == 5


# ═══════════════════════════════════════════════════════════════════════════════
# TestWriteoffDefect
# ═══════════════════════════════════════════════════════════════════════════════


class TestWriteoffDefect:
    @pytest.mark.asyncio
    async def test_writeoff_happy_path(self, db_session, project, wh, bc):
        """defect 30, writeoff 30 -> defect=0."""
        await _stock(db_session, project, wh, bc, 100)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 30},
        )

        result = await writeoff_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 30, "reason": "unsalvageable"},
        )
        assert result["status"] == "ok"
        assert result["processed"] == 1
        assert result["shipment_id"] is not None

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert len(found) == 1
        assert found[0]["defect_quantity"] == 0
        assert found[0]["quantity"] == 70  # unchanged after writeoff

    @pytest.mark.asyncio
    async def test_writeoff_insufficient(self, db_session, project, wh, bc):
        """defect 10, writeoff 20 -> ValueError."""
        await _stock(db_session, project, wh, bc, 50)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 10},
        )

        with pytest.raises(ValueError, match="Insufficient defect stock"):
            await writeoff_defect(
                db_session,
                project.id,
                wh.id,
                {"barcode": bc, "quantity": 20},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TestRecoverDefect
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecoverDefect:
    @pytest.mark.asyncio
    async def test_recover_happy_path(self, db_session, project, wh, bc):
        """defect 30, recover 10 -> defect=20, qty+=10."""
        await _stock(db_session, project, wh, bc, 100)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 30},
        )
        # After mark: qty=70, defect=30

        result = await recover_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 10, "reason": "repaired"},
        )
        assert result["status"] == "ok"
        assert result["operation"] == "recover_defect"

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        assert found[0]["defect_quantity"] == 20
        assert found[0]["quantity"] == 80  # 70 + 10 recovered

    @pytest.mark.asyncio
    async def test_recover_insufficient(self, db_session, project, wh, bc):
        """defect 5, recover 10 -> ValueError."""
        await _stock(db_session, project, wh, bc, 50)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 5},
        )

        with pytest.raises(ValueError, match="Insufficient defect stock"):
            await recover_defect(
                db_session,
                project.id,
                wh.id,
                {"barcode": bc, "quantity": 10},
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TestDefectTransfer
# ═══════════════════════════════════════════════════════════════════════════════


class TestDefectTransfer:
    @pytest.mark.asyncio
    async def test_defect_transfer_full_flow(self, db_session, project, wh, wh2, bc):
        """
        create(is_defect=True) -> send -> complete.

        Verifies:
        - Source defect_qty decreases on send
        - Destination defect_in_transit increases on send
        - Destination defect_qty increases on complete
        - defect_in_transit clears on complete
        - Correct movement types: DEFECT_TRANSFER_OUT / DEFECT_TRANSFER_IN
        """
        # Prepare: stock 100, mark 40 as defect at source
        await _stock(db_session, project, wh, bc, 100)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 40},
        )
        # source: qty=60, defect=40

        # Step 1: Create defect transfer
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": wh.id,
                "to_warehouse_id": wh2.id,
                "items": [{"barcode": bc, "quantity": 25}],
                "is_defect": True,
                "defect_reason": "sending to repair center",
            },
        )
        assert transfer.is_defect is True
        assert transfer.defect_reason == "sending to repair center"

        # Step 2: Send transfer
        sent = await send_transfer(db_session, project.id, transfer.id)
        assert sent.status.value == "IN_TRANSIT"

        # Source: defect_qty should be 40 - 25 = 15
        src_stock = await get_warehouse_stock(db_session, project.id, wh.id)
        src = [s for s in src_stock if s["barcode"] == bc]
        assert src[0]["defect_quantity"] == 15
        assert src[0]["quantity"] == 60  # good stock unchanged

        # Destination: defect_in_transit should be 25
        # The destination may show up with defect_in_transit > 0 even if qty=0
        # get_warehouse_stock filters on qty > 0 OR defect_quantity > 0
        # defect_in_transit alone doesn't show up unless there's qty or defect_qty
        # So we check via direct query
        result = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == wh2.id,
                WarehouseStock.barcode == bc,
            )
        )
        dst_row = result.scalar_one()
        assert dst_row.defect_in_transit == 25
        assert dst_row.defect_quantity == 0

        # Step 3: Complete transfer
        completed = await complete_transfer(db_session, project.id, transfer.id)
        assert completed.status.value == "COMPLETED"

        # Destination: defect_qty should be 25, defect_in_transit should be 0
        dst_stock_after = await get_warehouse_stock(db_session, project.id, wh2.id)
        dst_after = [s for s in dst_stock_after if s["barcode"] == bc]
        assert len(dst_after) == 1
        assert dst_after[0]["defect_quantity"] == 25
        assert dst_after[0]["defect_in_transit"] == 0

        # Verify movement types
        src_movements = await get_stock_movements(db_session, project.id, wh.id)
        assert any(m.movement_type == MovementType.DEFECT_TRANSFER_OUT for m in src_movements)

        dst_movements = await get_stock_movements(db_session, project.id, wh2.id)
        assert any(m.movement_type == MovementType.DEFECT_TRANSFER_IN for m in dst_movements)


# ═══════════════════════════════════════════════════════════════════════════════
# TestGetDefectStock
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDefectStock:
    @pytest.mark.asyncio
    async def test_get_defect_stock(self, db_session, project, wh, bc, bc2):
        """Returns only rows with defect_quantity > 0."""
        await _stock(db_session, project, wh, bc, 100)
        await _stock(db_session, project, wh, bc2, 100)

        # Mark only bc as defective
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 20},
        )

        defects = await get_defect_stock(db_session, project.id, wh.id)
        barcodes = [d["barcode"] for d in defects]
        assert bc in barcodes
        assert bc2 not in barcodes
        assert defects[0]["defect_quantity"] == 20

    @pytest.mark.asyncio
    async def test_defect_excluded_from_available(self, db_session, project, wh, bc):
        """available = qty - reserved; defect is separate from available."""
        await _stock(db_session, project, wh, bc, 100)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 30},
        )
        # qty=70, defect=30

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        found = [s for s in stock if s["barcode"] == bc]
        item = found[0]

        # available = qty - reserved (no assembly reservations here, so reserved=0)
        assert item["available"] == item["quantity"] - item["reserved"]
        assert item["available"] == 70
        # defect is tracked separately, not subtracted from available
        assert item["defect_quantity"] == 30
        assert item["quantity"] == 70


# ═══════════════════════════════════════════════════════════════════════════════
# TestGetDefectSummary
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetDefectSummary:
    @pytest.mark.asyncio
    async def test_defect_summary_across_warehouses(self, db_session, project, wh, wh2, bc):
        """Summary groups defect stock by nomenclature across warehouses."""
        await _stock(db_session, project, wh, bc, 100)
        await mark_defect(
            db_session,
            project.id,
            wh.id,
            {"barcode": bc, "quantity": 20},
        )

        # Receive defect at wh2
        await receive_defect(
            db_session,
            project.id,
            wh2.id,
            {"barcode": bc, "quantity": 15},
        )

        summary = await get_defect_summary(db_session, project.id)
        found = [s for s in summary if s["barcode"] == bc]
        assert len(found) == 1
        assert found[0]["total_defect"] == 35  # 20 + 15
        assert wh.id in found[0]["warehouses"]
        assert wh2.id in found[0]["warehouses"]
        assert found[0]["warehouses"][wh.id] == 20
        assert found[0]["warehouses"][wh2.id] == 15
