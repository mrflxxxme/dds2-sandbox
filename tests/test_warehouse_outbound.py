"""
Tests for warehouse_outbound.py — Outbound shipments and stock transfers.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.warehouse import OutboundStatus, StockTransfer, TransferStatus
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
    mark_transfer_ready,
    send_transfer as _send_transfer_raw,
    ship_shipment,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def send_transfer(db_session, project_id, transfer_id, **kwargs):
    """Отправка переезда с доводкой до READY — см. шапку tests/test_transfer_vehicle.py.

    Переезд рождается в PENDING, а `send_transfer` работает из READY /
    VEHICLE_ASSIGNED. Тесты этого файла проверяют движения стока, а не
    лестницу, поэтому ступень «собран» проставляем шорткатом. Из отгруженных
    статусов обёртка ничего не делает — отказ доходит до сервиса как есть.

    `allow_no_logistics` взведён по умолчанию: с 01.08.2026 голый READY без
    машины/перевозчика/стоимости отправить нельзя (TR-32), а здесь ни один тест
    логистику не оформляет — она к движениям стока отношения не имеет.
    """
    transfer = await db_session.get(StockTransfer, transfer_id)
    if transfer is not None and TransferStatus(transfer.status) in (
        TransferStatus.PENDING,
        TransferStatus.IN_PROGRESS,
    ):
        await mark_transfer_ready(db_session, project_id, transfer_id)
    kwargs.setdefault("allow_no_logistics", True)
    return await _send_transfer_raw(db_session, project_id, transfer_id, **kwargs)


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
        assert transfer.status == TransferStatus.PENDING
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
        assert sent.status == TransferStatus.SHIPPED

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
        # Пустой состав отбивается уже на ступени «собран»: отмечать готовым
        # нечего, а до `send_transfer` такой переезд теперь просто не доходит.
        with pytest.raises(ValueError, match="Состав переезда пуст"):
            await send_transfer(db_session, project.id, transfer.id)


class TestTransferInUnifiedTotal:
    @pytest.mark.asyncio
    async def test_in_transit_transfer_counts_into_unified_total(
        self, db_session, project, fulfillment_wh, external_wh, barcode
    ):
        """Отправленное перемещение — наш товар в пути между нашими складами:
        обязано попасть в «Итого»-капитал единых остатков отдельным полем
        transfer_transit (кейс TR-21: 9 234 шт висели невидимыми 12 дней)."""
        from backend.services.warehouse_stock_engine import get_unified_stock_summary

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
        await send_transfer(db_session, project.id, transfer.id)

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next(r for r in rows if r["barcode"] == barcode)
        assert row["total_own"] == 60  # осталось на источнике
        assert row["transfer_transit"] == 40  # едет между нашими складами
        assert row["total"] == 100  # капитал не потерял ни штуки

    @pytest.mark.asyncio
    async def test_transfer_only_sku_still_visible(
        self, db_session, project, fulfillment_wh, external_wh, barcode
    ):
        """SKU, ПОЛНОСТЬЮ уехавший в перемещение (на источнике 0), не должен
        пропасть из сводки — раньше own-цикл скипал строки без quantity/defect."""
        from backend.services.warehouse_stock_engine import get_unified_stock_summary

        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 40)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 40}],
            },
        )
        await send_transfer(db_session, project.id, transfer.id)

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next((r for r in rows if r["barcode"] == barcode), None)
        assert row is not None
        assert row["total_own"] == 0
        assert row["transfer_transit"] == 40
        assert row["total"] == 40

    @pytest.mark.asyncio
    async def test_completed_transfer_leaves_transit(
        self, db_session, project, fulfillment_wh, external_wh, barcode
    ):
        from backend.services.warehouse_stock_engine import get_unified_stock_summary

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
        await send_transfer(db_session, project.id, transfer.id)
        await complete_transfer(db_session, project.id, transfer.id)

        rows = await get_unified_stock_summary(db_session, project.id, group_by="sku")
        row = next(r for r in rows if r["barcode"] == barcode)
        assert row["transfer_transit"] == 0
        assert row["total_own"] == 100  # 60 на источнике + 40 принято на назначении
        assert row["total"] == 100


class TestReceiveTransferFact:
    """Приём перемещения ПО ФАКТУ связанной ФФ-приёмки (кейс PVB-0000121 → TR-21):
    порционный TRANSFER_IN по фактам, расхождение не приходуется, transfer
    закрывается только когда факт покрыл весь план."""

    async def _sent_transfer(self, db_session, project, fulfillment_wh, external_wh, barcode, qty=40):
        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": qty}],
            },
        )
        return await send_transfer(db_session, project.id, transfer.id)

    async def _dest_stock(self, db_session, project, wh, barcode):
        from backend.models.warehouse import WarehouseStock

        res = await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == wh.id,
                WarehouseStock.barcode == barcode,
            )
        )
        return res.scalar_one_or_none()

    @pytest.mark.asyncio
    async def test_partial_fact_keeps_in_transit(self, db_session, project, fulfillment_wh, external_wh, barcode):
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        result = await receive_transfer_fact(
            db_session, project.id, transfer.id, {barcode: 30}, note="PVB-1"
        )
        assert result["received_good"] == 30
        assert result["completed"] is False

        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.quantity == 30
        assert stock.in_transit == 10  # 40 ехало − 30 принято

    @pytest.mark.asyncio
    async def test_full_fact_completes_transfer(self, db_session, project, fulfillment_wh, external_wh, barcode):
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        result = await receive_transfer_fact(
            db_session, project.id, transfer.id, {barcode: 40}, note="PVB-1"
        )
        assert result["completed"] is True
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.DELIVERED
        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.quantity == 40
        assert stock.in_transit == 0

    @pytest.mark.asyncio
    async def test_portions_accumulate_and_close(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Две порции (две PVB): вторая доводит до плана и закрывает transfer."""
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        r1 = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 25}, note="PVB-1")
        assert r1["completed"] is False
        r2 = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 15}, note="PVB-2")
        assert r2["completed"] is True
        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.quantity == 40
        assert stock.in_transit == 0

    @pytest.mark.asyncio
    async def test_excess_fact_clamped_to_plan(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """ФФ принял больше плана перемещения — сверх плана не приходуем
        (излишек — предмет сверки зеркала, не транзита)."""
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        result = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 55}, note="PVB-1")
        assert result["received_good"] == 40
        assert result["completed"] is True
        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.quantity == 40
        assert stock.in_transit == 0

    @pytest.mark.asyncio
    async def test_defect_transfer_receives_into_defect(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Перемещение БРАКА (is_defect=True): весь факт приходуется браком."""
        from backend.services.warehouse_defect import mark_defect
        from backend.services.warehouse_outbound import receive_transfer_fact

        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        await mark_defect(db_session, project.id, fulfillment_wh.id, {"barcode": barcode, "quantity": 40})
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [{"barcode": barcode, "quantity": 40}],
                "is_defect": True,
            },
        )
        await send_transfer(db_session, project.id, transfer.id)
        result = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 40}, note="PVB-D")
        assert result["received_defect"] == 40
        assert result["received_good"] == 0
        assert result["completed"] is True
        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.defect_quantity == 40
        assert stock.defect_in_transit == 0

    @pytest.mark.asyncio
    async def test_marker_set_atomically_with_receive(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """transfer_fact_applied_at ставится той же транзакцией, что и движения."""
        from backend.models.fulfillment import FulfillmentRequest
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        req = FulfillmentRequest(
            project_id=project.id, warehouse_id=external_wh.id, provider="migfull",
            external_id=f"pvb-atomic-{project.id}", kind="inbound", stock_transfer_id=transfer.id,
            is_completed=True,
        )
        db_session.add(req)
        await db_session.commit()

        result = await receive_transfer_fact(
            db_session, project.id, transfer.id, {barcode: 30},
            note="PVB-A", mark_ff_request_applied=req.id,
        )
        assert result["applied_marker"] is True
        await db_session.refresh(req)
        assert req.transfer_fact_applied_at is not None

    @pytest.mark.asyncio
    async def test_marker_not_set_on_zero_receive(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Ни один ШК факта не совпал с составом → маркер НЕ ставится (повтор
        следующим синком), нестыкующиеся ШК возвращаются в unmatched."""
        from backend.models.fulfillment import FulfillmentRequest
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        req = FulfillmentRequest(
            project_id=project.id, warehouse_id=external_wh.id, provider="migfull",
            external_id=f"pvb-zero-{project.id}", kind="inbound", stock_transfer_id=transfer.id,
            is_completed=True,
        )
        db_session.add(req)
        await db_session.commit()

        result = await receive_transfer_fact(
            db_session, project.id, transfer.id, {"чужой-шк": 30},
            note="PVB-Z", mark_ff_request_applied=req.id,
        )
        assert result["applied_marker"] is False
        assert result["received_good"] == 0
        assert result["unmatched"] == ["чужой-шк"]
        await db_session.refresh(req)
        assert req.transfer_fact_applied_at is None

    @pytest.mark.asyncio
    async def test_duplicate_plan_rows_not_doubled(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Две строки TR с одним ШК: факт применяется к СВЁРНУТОМУ плану, а не
        к каждой строке (иначе «приём по факту» превращался в «приём по плану»)."""
        from backend.services.warehouse_outbound import receive_transfer_fact

        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": fulfillment_wh.id,
                "to_warehouse_id": external_wh.id,
                "items": [
                    {"barcode": barcode, "quantity": 20},
                    {"barcode": barcode, "quantity": 20},
                ],
            },
        )
        await send_transfer(db_session, project.id, transfer.id)
        result = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 30}, note="PVB-DUP")
        assert result["received_good"] == 30  # не 60
        assert result["completed"] is False
        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.quantity == 30
        assert stock.in_transit == 10

    @pytest.mark.asyncio
    async def test_defect_fact_lands_in_defect(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Часть факта — брак: приходуется в defect_quantity, транзит снимается."""
        from backend.services.warehouse_outbound import receive_transfer_fact

        transfer = await self._sent_transfer(db_session, project, fulfillment_wh, external_wh, barcode)
        result = await receive_transfer_fact(
            db_session, project.id, transfer.id, {barcode: 30}, defect_by_barcode={barcode: 10}, note="PVB-1"
        )
        assert result["received_good"] == 30
        assert result["received_defect"] == 10
        assert result["completed"] is True
        stock = await self._dest_stock(db_session, project, external_wh, barcode)
        assert stock.quantity == 30
        assert stock.defect_quantity == 10
        assert stock.in_transit == 0


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
        assert completed.status == TransferStatus.DELIVERED

    @pytest.mark.asyncio
    async def test_complete_transfer_inherits_box_qty(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Приёмка перемещения наследует ручную кратность на склад-получатель
        (жалоба юзера 2026-07-16: после TR кратность приходилось заносить заново)."""
        from backend.models.cost import BoxQtyPerWarehouse

        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        db_session.add(BoxQtyPerWarehouse(project_id=project.id, barcode=barcode, warehouse_id=fulfillment_wh.id, box_qty=20, box_size="60x40x50"))
        await db_session.commit()
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
        await complete_transfer(db_session, project.id, transfer.id)
        dst = (
            await db_session.execute(
                select(BoxQtyPerWarehouse).where(
                    BoxQtyPerWarehouse.project_id == project.id,
                    BoxQtyPerWarehouse.warehouse_id == external_wh.id,
                    BoxQtyPerWarehouse.barcode == barcode,
                )
            )
        ).scalar_one()
        assert dst.box_qty == 20
        assert dst.box_size == "60x40x50"

    @pytest.mark.asyncio
    async def test_complete_transfer_keeps_target_box_qty(self, db_session, project, fulfillment_wh, external_wh, barcode):
        """Своя кратность получателя НЕ перетирается наследованием."""
        from backend.models.cost import BoxQtyPerWarehouse

        await _stock_warehouse(db_session, project, fulfillment_wh, barcode, 100)
        db_session.add(BoxQtyPerWarehouse(project_id=project.id, barcode=barcode, warehouse_id=fulfillment_wh.id, box_qty=20))
        db_session.add(BoxQtyPerWarehouse(project_id=project.id, barcode=barcode, warehouse_id=external_wh.id, box_qty=18))
        await db_session.commit()
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
        await complete_transfer(db_session, project.id, transfer.id)
        dst = (
            await db_session.execute(
                select(BoxQtyPerWarehouse).where(
                    BoxQtyPerWarehouse.project_id == project.id,
                    BoxQtyPerWarehouse.warehouse_id == external_wh.id,
                    BoxQtyPerWarehouse.barcode == barcode,
                )
            )
        ).scalar_one()
        assert dst.box_qty == 18

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
        with pytest.raises(ValueError, match="PENDING → DELIVERED запрещён"):
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
        # Статус проставлен ЯВНО: «отменён» обязан быть отличим от «удалён по
        # ошибке» в истории и в отчётах, которые читают статус, а не is_deleted.
        raw = await db_session.get(StockTransfer, transfer.id)
        await db_session.refresh(raw)
        assert raw.status == TransferStatus.CANCELLED
        assert raw.is_deleted is True

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
        with pytest.raises(ValueError, match="не отменить"):
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
        # Сообщение русское: логист не должен видеть «Transfer not found» среди
        # русских ошибок, а роутер отличает по нему 404 от 400.
        with pytest.raises(ValueError, match="не найдено"):
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
