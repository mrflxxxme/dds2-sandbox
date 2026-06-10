"""
Tests for warehouse_outbound.py — Outbound shipments and stock transfers.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import OutboundStatus, TransferStatus
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_outbound import (
    cancel_shipment,
    cancel_transfer,
    complete_transfer,
    create_shipment,
    create_transfer,
    deliver_shipment,
    get_shipment,
    get_transfer,
    list_transfers,
    send_transfer,
    ship_shipment,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def fulfillment_wh(db_session: AsyncSession, project):
    """Create a FULFILLMENT warehouse (required for shipments)."""
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"},
    )


@pytest_asyncio.fixture
async def external_wh(db_session: AsyncSession, project):
    """Create an EXTERNAL warehouse."""
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"EX-{_uid()}", "warehouse_type": "EXTERNAL"},
    )


@pytest_asyncio.fixture
async def barcode(db_session: AsyncSession, project):
    """Create nomenclature and return barcode."""
    bc = f"BC-{_uid()}"
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, subject, updated_at) " "VALUES (:pid, :bc, :subj, NOW())"),
        {"pid": project.id, "bc": bc, "subj": "Outbound Item"},
    )
    await db_session.commit()
    return bc


async def _stock_warehouse(db_session, project, warehouse, barcode, qty):
    """Stock a warehouse by creating and accepting an inbound receipt."""
    receipt = await create_receipt(
        db_session,
        project.id,
        warehouse.id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project.id, receipt.id)


# ═══════════════════════════════════════════════════════════════════════════════
# Outbound Shipments
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateShipment:
    @pytest.mark.asyncio
    async def test_create_shipment_happy_path(self, db_session, project, fulfillment_wh, barcode):
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {
                "destination": "WB Koledino",
                "items": [{"barcode": barcode, "quantity": 10}],
            },
        )
        assert shipment.id is not None
        assert shipment.status == OutboundStatus.DRAFT
        assert shipment.number.startswith("OUT-")
        assert len(shipment.items) == 1

    @pytest.mark.asyncio
    async def test_create_shipment_external_warehouse_fails(self, db_session, project, external_wh, barcode):
        with pytest.raises(ValueError, match="FULFILLMENT"):
            await create_shipment(
                db_session,
                project.id,
                external_wh.id,
                {"items": [{"barcode": barcode, "quantity": 5}]},
            )

    @pytest.mark.asyncio
    async def test_create_shipment_warehouse_not_found(self, db_session, project):
        with pytest.raises(ValueError, match="Warehouse not found"):
            await create_shipment(db_session, project.id, 999999, {"items": []})


class TestShipShipment:
    @pytest.mark.asyncio
    async def test_ship_shipment_deducts_stock(self, db_session, project, fulfillment_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 50)
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 30}]},
        )
        shipped = await ship_shipment(db_session, project.id, shipment.id)
        assert shipped.status == OutboundStatus.SHIPPED
        assert shipped.shipped_date is not None

    @pytest.mark.asyncio
    async def test_ship_insufficient_stock(self, db_session, project, fulfillment_wh, barcode):
        # Stock only 5 but try to ship 50
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 5)
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 50}]},
        )
        with pytest.raises(ValueError, match="Insufficient stock"):
            await ship_shipment(db_session, project.id, shipment.id)

    @pytest.mark.asyncio
    async def test_ship_empty_shipment_fails(self, db_session, project, fulfillment_wh):
        shipment = await create_shipment(db_session, project.id, fulfillment_wh.id, {"items": []})
        with pytest.raises(ValueError, match="no items"):
            await ship_shipment(db_session, project.id, shipment.id)

    @pytest.mark.asyncio
    async def test_ship_already_shipped_fails(self, db_session, project, fulfillment_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 10}]},
        )
        await ship_shipment(db_session, project.id, shipment.id)
        with pytest.raises(ValueError, match="Cannot ship"):
            await ship_shipment(db_session, project.id, shipment.id)


class TestDeliverShipment:
    @pytest.mark.asyncio
    async def test_deliver(self, db_session, project, fulfillment_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 10}]},
        )
        await ship_shipment(db_session, project.id, shipment.id)
        delivered = await deliver_shipment(db_session, project.id, shipment.id)
        assert delivered.status == OutboundStatus.DELIVERED

    @pytest.mark.asyncio
    async def test_deliver_draft_fails(self, db_session, project, fulfillment_wh, barcode):
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 5}]},
        )
        with pytest.raises(ValueError, match="Cannot deliver"):
            await deliver_shipment(db_session, project.id, shipment.id)


class TestCancelShipment:
    @pytest.mark.asyncio
    async def test_cancel_returns_stock(self, db_session, project, fulfillment_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 30}]},
        )
        await ship_shipment(db_session, project.id, shipment.id)
        cancelled = await cancel_shipment(db_session, project.id, shipment.id)
        assert cancelled.status == OutboundStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_draft_fails(self, db_session, project, fulfillment_wh, barcode):
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 5}]},
        )
        with pytest.raises(ValueError, match="SHIPPED"):
            await cancel_shipment(db_session, project.id, shipment.id)


# ═══════════════════════════════════════════════════════════════════════════════
# Stock Transfers
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateTransfer:
    @pytest.mark.asyncio
    async def test_create_transfer_happy_path(self, db_session, project, fulfillment_wh, external_wh, barcode):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 25}],
            },
        )
        assert transfer.id is not None
        assert transfer.status == TransferStatus.DRAFT
        assert transfer.number.startswith("TR-")
        assert len(transfer.items) == 1

    @pytest.mark.asyncio
    async def test_transfer_same_warehouse_fails(self, db_session, project, fulfillment_wh, barcode):
        with pytest.raises(ValueError, match="same warehouse"):
            await create_transfer(
                db_session,
                project.id,
                {
                    "from_warehouse_id": fulfillment_wh.id,
                    "to_warehouse_id": fulfillment_wh.id,
                    "items": [{"barcode": barcode, "quantity": 10}],
                },
            )

    @pytest.mark.asyncio
    async def test_transfer_source_not_found(self, db_session, project, external_wh, barcode):
        with pytest.raises(ValueError, match="Source warehouse not found"):
            await create_transfer(
                db_session,
                project.id,
                {
                    "from_warehouse_id": 999999,
                    "to_warehouse_id": external_wh.id,
                    "items": [],
                },
            )


class TestSendTransfer:
    @pytest.mark.asyncio
    async def test_send_transfer(self, db_session, project, fulfillment_wh, external_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 40}],
            },
        )
        sent = await send_transfer(db_session, project.id, transfer.id)
        assert sent.status == TransferStatus.IN_TRANSIT

    @pytest.mark.asyncio
    async def test_send_empty_transfer_fails(self, db_session, project, fulfillment_wh, external_wh):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [],
            },
        )
        with pytest.raises(ValueError, match="no items"):
            await send_transfer(db_session, project.id, transfer.id)


class TestCompleteTransfer:
    @pytest.mark.asyncio
    async def test_complete_transfer(self, db_session, project, fulfillment_wh, external_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 30}],
            },
        )
        await send_transfer(db_session, project.id, transfer.id)
        completed = await complete_transfer(db_session, project.id, transfer.id)
        assert completed.status == TransferStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_complete_draft_fails(self, db_session, project, fulfillment_wh, external_wh, barcode):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
            },
        )
        with pytest.raises(ValueError, match="Cannot complete"):
            await complete_transfer(db_session, project.id, transfer.id)


class TestListTransfers:
    @pytest.mark.asyncio
    async def test_list_transfers_warehouse_filter(self, db_session, project, fulfillment_wh, external_wh, barcode):
        third_wh = await create_warehouse(
            db_session,
            project.id,
            {"name": f"TH-{_uid()}", "warehouse_type": "EXTERNAL"},
        )
        t1 = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        t2 = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": external_wh.id,
                "to_warehouse_id": third_wh.id,
                "items": [{"barcode": barcode, "quantity": 3}],
            },
        )
        # По fulfillment_wh — только t1 (источник); по external_wh — оба (получатель t1, источник t2)
        ids_ff = {t.id for t in await list_transfers(db_session, project.id, warehouse_id=fulfillment_wh.id)}
        assert ids_ff == {t1.id}
        ids_ex = {t.id for t in await list_transfers(db_session, project.id, warehouse_id=external_wh.id)}
        assert ids_ex == {t1.id, t2.id}


class TestCancelTransfer:
    @pytest.mark.asyncio
    async def test_cancel_draft(self, db_session, project, fulfillment_wh, external_wh, barcode):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
            },
        )
        await cancel_transfer(db_session, project.id, transfer.id)
        found = await get_transfer(db_session, project.id, transfer.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_cancel_in_transit_fails(self, db_session, project, fulfillment_wh, external_wh, barcode):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
            },
        )
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="Cannot cancel"):
            await cancel_transfer(db_session, project.id, transfer.id)

    @pytest.mark.asyncio
    async def test_cancel_transfer_isolation(
        self, db_session, project, other_project, fulfillment_wh, external_wh, barcode
    ):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        with pytest.raises(ValueError, match="not found"):
            await cancel_transfer(db_session, other_project.id, transfer.id)
        # И не удалён: в своём проекте всё ещё виден
        found = await get_transfer(db_session, project.id, transfer.id)
        assert found is not None


# ═══════════════════════════════════════════════════════════════════════════════
# project_id isolation
# ═══════════════════════════════════════════════════════════════════════════════


class TestOutboundProjectIsolation:
    @pytest.mark.asyncio
    async def test_get_shipment_isolation(self, db_session, project, other_project, fulfillment_wh, barcode):
        shipment = await create_shipment(
            db_session,
            project.id,
            fulfillment_wh.id,
            {"items": [{"barcode": barcode, "quantity": 5}]},
        )
        found = await get_shipment(db_session, other_project.id, shipment.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_get_transfer_isolation(
        self, db_session, project, other_project, fulfillment_wh, external_wh, barcode
    ):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        found = await get_transfer(db_session, other_project.id, transfer.id)
        assert found is None
