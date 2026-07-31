# ruff: noqa: RUF001, RUF002, RUF003
"""Перемещение как полноценный переезд: машина, забор и конвертация из заявки.

Главный инвариант всего файла — ОДНО списание. Переезд списывает сток ровно
один раз (`TRANSFER_OUT`/`reference_type='TRANSFER'`); созданный при отправке
забор (`OutboundShipment`) — носитель логистики и денег, а не проводка. Любой
второй источник списания тех же единиц (забор, конвертация уже отгруженной
заявки, повторная конвертация) обязан быть закрыт гардом.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.counterparty import Counterparty
from backend.models.fulfillment import FulfillmentRequest
from backend.models.warehouse import (
    InboundReceipt,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockMovement,
    StockTransfer,
    TransferStatus,
)
from backend.schemas.warehouse import AssemblyToTransfer, TransferAssignVehicle
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_outbound import (
    assign_vehicle_transfer,
    cancel_shipment,
    convert_assembly_to_transfer,
    create_transfer,
    list_shipments,
    send_transfer,
)
from backend.services.warehouse_stock_engine import _update_stock


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def src_wh(db_session: AsyncSession, project):
    return await create_warehouse(
        db_session, project.id, {"name": f"SRC-{_uid()}", "warehouse_type": "FULFILLMENT"}
    )


@pytest_asyncio.fixture
async def dst_wh(db_session: AsyncSession, project):
    return await create_warehouse(
        db_session, project.id, {"name": f"DST-{_uid()}", "warehouse_type": "FULFILLMENT"}
    )


@pytest_asyncio.fixture
async def barcode(db_session: AsyncSession, project):
    bc = f"BC-TRV-{_uid()}"
    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
            "VALUES (:pid, :bc, :subj, NOW())"
        ),
        {"pid": project.id, "bc": bc, "subj": "Transfer Item"},
    )
    await db_session.commit()
    return bc


async def _nom_id(db_session, project, barcode: str) -> int:
    res = await db_session.execute(
        text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
        {"pid": project.id, "bc": barcode},
    )
    return res.scalar()


async def _stock(db_session, project, warehouse, barcode, qty: int) -> None:
    receipt = await create_receipt(
        db_session,
        project.id,
        warehouse.id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project.id, receipt.id)


async def _assembly(
    db_session,
    project,
    warehouse,
    barcode,
    qty: int,
    status: str = AssemblyStatus.CLOSED.value,
) -> AssemblyRequest:
    """Заявка на сборку в произвольном статусе (терминальные недостижимы флоу-переходами)."""
    nom_id = await _nom_id(db_session, project, barcode)
    req = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"ASM-{_uid()}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("100.00"),
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=project.id,
            assembly_request_id=req.id,
            nomenclature_id=nom_id,
            barcode=barcode,
            quantity=qty,
        )
    )
    await db_session.commit()
    await db_session.refresh(req, ["items"])
    return req


async def _ship_assembly_stock(db_session, project, assembly, barcode, qty: int) -> None:
    """Списание заявки — те же движения, что пишет `ship_request` (ASSEMBLY)."""
    nom_id = await _nom_id(db_session, project, barcode)
    await _update_stock(
        db_session,
        project_id=project.id,
        warehouse_id=assembly.warehouse_id,
        nomenclature_id=nom_id,
        barcode=barcode,
        delta=-qty,
        movement_type=MovementType.OUTBOUND,
        reference_type="ASSEMBLY",
        reference_id=assembly.id,
    )
    await db_session.commit()


async def _return_assembly_stock(db_session, project, assembly, barcode, qty: int) -> InboundReceipt:
    """Возврат «WB не принял» — приёмка, привязанная к заявке, + движения RECEIPT."""
    nom_id = await _nom_id(db_session, project, barcode)
    receipt = InboundReceipt(
        project_id=project.id,
        warehouse_id=assembly.warehouse_id,
        number=f"IN-{_uid()}",
        status=InboundStatus.ACCEPTED,
        assembly_request_id=assembly.id,
    )
    db_session.add(receipt)
    await db_session.flush()
    await _update_stock(
        db_session,
        project_id=project.id,
        warehouse_id=assembly.warehouse_id,
        nomenclature_id=nom_id,
        barcode=barcode,
        delta=+qty,
        movement_type=MovementType.INBOUND,
        reference_type="RECEIPT",
        reference_id=receipt.id,
    )
    await db_session.commit()
    return receipt


async def _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, qty: int):
    transfer = await create_transfer(
        db_session,
        project.id,
        {
            "from_warehouse_id": src_wh.id,
            "to_warehouse_id": dst_wh.id,
            "items": [{"barcode": barcode, "quantity": qty}],
        },
    )
    return await assign_vehicle_transfer(
        db_session,
        project.id,
        transfer.id,
        TransferAssignVehicle(
            vehicle_info="А123ВС77",
            vehicle_brand="Газель",
            driver_phone="79001234567",
            carrier_inn=f"77{_uid()[:8]}",
            carrier_name="ИП Перевозчик",
            pickup_cost=Decimal("15000.00"),
        ),
    )


async def _movements(db_session, project, reference_type: str, reference_id: int) -> list[StockMovement]:
    res = await db_session.execute(
        select(StockMovement).where(
            StockMovement.project_id == project.id,
            StockMovement.reference_type == reference_type,
            StockMovement.reference_id == reference_id,
        )
    )
    return list(res.scalars().all())


# ═══════════════════════════════════════════════════════════════════════════
# 1. Отправка переезда: ОДИН забор и ОДНО списание
# ═══════════════════════════════════════════════════════════════════════════


class TestTransferPickup:
    @pytest.mark.asyncio
    async def test_send_creates_single_pickup_and_single_writeoff(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Забор ровно один, движений TRANSFER_OUT ровно один комплект, движений
        забора (SHIPMENT) — ни одного. Второе списание = главный враг фичи."""
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 40)
        await send_transfer(db_session, project.id, transfer.id)

        pickups = list(
            (
                await db_session.execute(
                    select(OutboundShipment).where(
                        OutboundShipment.project_id == project.id,
                        OutboundShipment.stock_transfer_id == transfer.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(pickups) == 1
        pickup = pickups[0]
        assert pickup.warehouse_id == src_wh.id
        assert pickup.status == OutboundStatus.SHIPPED
        assert pickup.destination == dst_wh.name
        assert pickup.number.startswith("OUT-")
        assert pickup.shipped_date is not None
        # Снимок логистики переехал на забор — иначе денежный контур пуст.
        assert pickup.pickup_cost == Decimal("15000.00")
        assert pickup.vehicle_info == "А123ВС77"
        assert pickup.vehicle_brand == "Газель"
        assert pickup.driver_phone == "79001234567"
        assert pickup.counterparty_id is not None
        # Позиции забора — данные для истории, но НЕ проводки.
        item_qty = (
            await db_session.execute(
                select(func.coalesce(func.sum(OutboundShipmentItem.quantity), 0)).where(
                    OutboundShipmentItem.project_id == project.id,
                    OutboundShipmentItem.shipment_id == pickup.id,
                )
            )
        ).scalar()
        assert item_qty == 40

        out = await _movements(db_session, project, "TRANSFER", transfer.id)
        assert len(out) == 1
        assert out[0].movement_type == MovementType.TRANSFER_OUT
        assert out[0].quantity == -40
        assert await _movements(db_session, project, "SHIPMENT", pickup.id) == []

        # И сток списан ровно один раз.
        qty = (
            await db_session.execute(
                text(
                    "SELECT quantity FROM warehouse_stock "
                    "WHERE project_id = :pid AND warehouse_id = :wid AND barcode = :bc"
                ),
                {"pid": project.id, "wid": src_wh.id, "bc": barcode},
            )
        ).scalar()
        assert qty == 60

    @pytest.mark.asyncio
    async def test_send_without_vehicle_creates_no_pickup(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Переезд без машины — внутренняя переброска: пустой забор в листе оплаты не нужен."""
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
            },
        )
        await send_transfer(db_session, project.id, transfer.id)
        count = (
            await db_session.execute(
                select(func.count(OutboundShipment.id)).where(
                    OutboundShipment.project_id == project.id,
                    OutboundShipment.stock_transfer_id == transfer.id,
                )
            )
        ).scalar()
        assert count == 0

    @pytest.mark.asyncio
    async def test_pickup_keeps_assembly_request_id_null(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Даже у переезда ИЗ ЗАЯВКИ забор не носит assembly_request_id: логистические
        отчёты отсекают переезды ровно по пустоте этого поля."""
        await _stock(db_session, project, src_wh, barcode, 100)
        assembly = await _assembly(db_session, project, src_wh, barcode, 30)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        await assign_vehicle_transfer(
            db_session,
            project.id,
            result.transfer_id,
            TransferAssignVehicle(vehicle_info="В777АА99", pickup_cost=Decimal("9000.00")),
        )
        await send_transfer(db_session, project.id, result.transfer_id)

        pickup = (
            await db_session.execute(
                select(OutboundShipment).where(
                    OutboundShipment.project_id == project.id,
                    OutboundShipment.stock_transfer_id == result.transfer_id,
                )
            )
        ).scalar_one()
        assert pickup.assembly_request_id is None
        # И заявка не получила переезд как «попытку отгрузки».
        await db_session.refresh(assembly)
        assert assembly.outbound_shipment_id is None

    @pytest.mark.asyncio
    async def test_cancel_pickup_forbidden_and_no_movements(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """«Отменить отгрузку» на заборе переезда вернуло бы сток, который забор
        никогда не списывал → фантомный остаток. Запрещено."""
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 40)
        await send_transfer(db_session, project.id, transfer.id)
        pickup = (
            await db_session.execute(
                select(OutboundShipment).where(
                    OutboundShipment.project_id == project.id,
                    OutboundShipment.stock_transfer_id == transfer.id,
                )
            )
        ).scalar_one()

        before = (
            await db_session.execute(
                select(func.count(StockMovement.id)).where(StockMovement.project_id == project.id)
            )
        ).scalar()
        with pytest.raises(ValueError, match="переезд"):
            await cancel_shipment(db_session, project.id, pickup.id)
        after = (
            await db_session.execute(
                select(func.count(StockMovement.id)).where(StockMovement.project_id == project.id)
            )
        ).scalar()
        assert before == after

    @pytest.mark.asyncio
    async def test_pickup_hidden_from_warehouse_shipments_but_shippable(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Забор переезда не засоряет вкладку «Отгрузки» склада, но обязан быть
        в рабочем списке логиста — иначе заявку на оплату по переезду не создать."""
        from backend.services.payment_request_service import PaymentRequestService

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 40)
        await send_transfer(db_session, project.id, transfer.id)

        assert await list_shipments(db_session, project.id, src_wh.id) == []

        rows = await PaymentRequestService(db_session).list_shippable(project.id)
        mine = [r for r in rows if r.pickup_cost == Decimal("15000.00")]
        assert mine
        # Строка обязана НЕСТИ id перемещения: по нему UI ведёт на деталку переезда.
        # Косвенный признак «assembly_request_id пуст» для этого не годится — он
        # ловит любую отгрузку без заявки, поэтому пинним поле явно.
        assert mine[0].stock_transfer_id == transfer.id
        assert mine[0].assembly_request_id is None


# ═══════════════════════════════════════════════════════════════════════════
# 2. Назначение машины на переезд
# ═══════════════════════════════════════════════════════════════════════════


class TestAssignVehicleTransfer:
    @pytest.mark.asyncio
    async def test_logistics_by_warehouse_takes_source_counterparty(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """«Логистику оказывает склад забора» → перевозчик = контрагент склада-ИСТОЧНИКА."""
        cp = Counterparty(
            project_id=project.id, name=f"ФФ-склад {_uid()}", primary_type="CARRIER"
        )
        db_session.add(cp)
        await db_session.flush()
        src_wh.counterparty_id = cp.id
        await db_session.commit()

        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        updated = await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(
                vehicle_info="Е001КХ77",
                logistics_by_warehouse=True,
                carrier_inn="9999999999",  # игнорируется в этом режиме
                pickup_cost=Decimal("7000.00"),
            ),
        )
        assert updated.counterparty_id == cp.id
        assert updated.logistics_by_warehouse is True
        assert updated.vehicle_assigned_at is not None
        # Ступени VEHICLE_ASSIGNED у перемещений нет — статус остаётся черновиком.
        assert updated.status == TransferStatus.DRAFT

    @pytest.mark.asyncio
    async def test_logistics_by_warehouse_without_counterparty_fails(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        with pytest.raises(ValueError, match="контрагент"):
            await assign_vehicle_transfer(
                db_session,
                project.id,
                transfer.id,
                TransferAssignVehicle(vehicle_info="Е001КХ77", logistics_by_warehouse=True),
            )

    @pytest.mark.asyncio
    async def test_assign_in_transit_fails(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 10)
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="черновик"):
            await assign_vehicle_transfer(
                db_session,
                project.id,
                transfer.id,
                TransferAssignVehicle(vehicle_info="Х000ХХ00"),
            )

    @pytest.mark.asyncio
    async def test_assign_isolated_by_project(
        self, db_session, project, other_project, src_wh, dst_wh, barcode
    ):
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        with pytest.raises(ValueError, match="не найдено"):
            await assign_vehicle_transfer(
                db_session,
                other_project.id,
                transfer.id,
                TransferAssignVehicle(vehicle_info="Х000ХХ00"),
            )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Конвертация заявки в перемещение
# ═══════════════════════════════════════════════════════════════════════════


class TestConvertAssemblyToTransfer:
    @pytest.mark.asyncio
    async def test_blocked_when_stock_written_off(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Заявка отгружена и товар не вернулся → перемещение списало бы те же
        единицы второй раз. Отказ с указанием количества."""
        await _stock(db_session, project, src_wh, barcode, 100)
        assembly = await _assembly(
            db_session, project, src_wh, barcode, 40, status=AssemblyStatus.SHIPPED.value
        )
        await _ship_assembly_stock(db_session, project, assembly, barcode, 40)

        with pytest.raises(ValueError, match="40"):
            await convert_assembly_to_transfer(
                db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
            )
        # И ничего не создалось.
        count = (
            await db_session.execute(
                select(func.count(StockTransfer.id)).where(
                    StockTransfer.project_id == project.id,
                    StockTransfer.converted_from_assembly_id == assembly.id,
                )
            )
        ).scalar()
        assert count == 0

    @pytest.mark.asyncio
    async def test_allowed_when_writeoff_compensated_by_return(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Кейс ASM-807: списано 506, возвращено приёмкой ровно 506 → нетто 0 → можно."""
        await _stock(db_session, project, src_wh, barcode, 600)
        assembly = await _assembly(
            db_session, project, src_wh, barcode, 506, status=AssemblyStatus.RETURNED.value
        )
        await _ship_assembly_stock(db_session, project, assembly, barcode, 506)
        await _return_assembly_stock(db_session, project, assembly, barcode, 506)

        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assert result.units_total == 506
        assert result.items_count == 1
        assert result.transfer_number.startswith("TR-")
        assert result.assembly_number == assembly.number

        transfer = await db_session.get(StockTransfer, result.transfer_id)
        assert transfer.status == TransferStatus.DRAFT
        assert transfer.from_warehouse_id == src_wh.id
        assert transfer.to_warehouse_id == dst_wh.id
        assert transfer.converted_from_assembly_id == assembly.id

    @pytest.mark.asyncio
    async def test_partial_return_still_blocked(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 600)
        assembly = await _assembly(
            db_session, project, src_wh, barcode, 500, status=AssemblyStatus.RETURNED.value
        )
        await _ship_assembly_stock(db_session, project, assembly, barcode, 500)
        await _return_assembly_stock(db_session, project, assembly, barcode, 300)
        with pytest.raises(ValueError, match="200"):
            await convert_assembly_to_transfer(
                db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status", [AssemblyStatus.CLOSED.value, AssemblyStatus.RETURNED.value, AssemblyStatus.CANCELLED.value]
    )
    async def test_convert_from_terminal_status_keeps_assembly(
        self, db_session, project, src_wh, dst_wh, barcode, status
    ):
        """Из терминальных статусов конвертировать МОЖНО, и заявка не меняется."""
        assembly = await _assembly(db_session, project, src_wh, barcode, 25, status=status)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assert result.units_total == 25
        await db_session.refresh(assembly)
        assert assembly.status == status
        assert assembly.is_deleted is False

    @pytest.mark.asyncio
    async def test_double_conversion_blocked(self, db_session, project, src_wh, dst_wh, barcode):
        assembly = await _assembly(db_session, project, src_wh, barcode, 25)
        await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        with pytest.raises(ValueError, match="уже переделана"):
            await convert_assembly_to_transfer(
                db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
            )

    @pytest.mark.asyncio
    async def test_same_warehouse_rejected(self, db_session, project, src_wh, barcode):
        assembly = await _assembly(db_session, project, src_wh, barcode, 25)
        with pytest.raises(ValueError, match="совпадает"):
            await convert_assembly_to_transfer(
                db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=src_wh.id)
            )

    @pytest.mark.asyncio
    async def test_project_isolation(
        self, db_session, project, other_project, src_wh, dst_wh, barcode
    ):
        assembly = await _assembly(db_session, project, src_wh, barcode, 25)
        with pytest.raises(ValueError, match="не найдена"):
            await convert_assembly_to_transfer(
                db_session,
                other_project.id,
                assembly.id,
                AssemblyToTransfer(to_warehouse_id=dst_wh.id),
            )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Зеркала ФФ при конвертации
# ═══════════════════════════════════════════════════════════════════════════


class TestConvertFfLinks:
    async def _mirror(self, db_session, project, warehouse, assembly) -> FulfillmentRequest:
        req = FulfillmentRequest(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="migfull",
            external_id=f"ff-{_uid()}",
            kind="assembly",
            assembly_request_id=assembly.id,
        )
        db_session.add(req)
        await db_session.commit()
        return req

    @pytest.mark.asyncio
    async def test_links_stay_on_assembly_by_default(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        assembly = await _assembly(db_session, project, src_wh, barcode, 25)
        mirror = await self._mirror(db_session, project, src_wh, assembly)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assert result.ff_links_moved == 0
        await db_session.refresh(mirror)
        assert mirror.assembly_request_id == assembly.id
        assert mirror.stock_transfer_id is None

    @pytest.mark.asyncio
    async def test_links_moved_when_requested(self, db_session, project, src_wh, dst_wh, barcode):
        assembly = await _assembly(db_session, project, src_wh, barcode, 25)
        mirror = await self._mirror(db_session, project, src_wh, assembly)
        result = await convert_assembly_to_transfer(
            db_session,
            project.id,
            assembly.id,
            AssemblyToTransfer(to_warehouse_id=dst_wh.id, move_ff_links=True),
        )
        assert result.ff_links_moved == 1
        await db_session.refresh(mirror)
        assert mirror.assembly_request_id is None
        assert mirror.stock_transfer_id == result.transfer_id


# ═══════════════════════════════════════════════════════════════════════════
# 5. Связка ФФ с обеих сторон переезда
# ═══════════════════════════════════════════════════════════════════════════


class TestFfLinkBothSides:
    async def _ff(self, db_session, project, warehouse, kind: str) -> FulfillmentRequest:
        req = FulfillmentRequest(
            project_id=project.id,
            warehouse_id=warehouse.id,
            provider="skladbot",
            external_id=f"ff-{kind}-{_uid()}",
            kind=kind,
        )
        db_session.add(req)
        await db_session.commit()
        return req

    @pytest.mark.asyncio
    async def test_assembly_side_links_and_stays_out_of_autoreceive(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Отгрузочное зеркало (kind=assembly) вяжется к переезду, но в кандидаты
        авто-приёма НЕ попадает — иначе факт отгрузки приходовался бы как приёмка."""
        from backend.services.fulfillment_service import (
            _collect_transfer_fact_candidates,
            link_request,
        )

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 40)
        await send_transfer(db_session, project.id, transfer.id)

        out_ff = await self._ff(db_session, project, src_wh, "assembly")
        row = await link_request(
            db_session, project.id, out_ff.id, stock_transfer_id=transfer.id, warehouse_id=src_wh.id
        )
        assert row is not None
        await db_session.refresh(out_ff)
        assert out_ff.stock_transfer_id == transfer.id

        # Авто-приём смотрит только на приёмочную сторону.
        cands = await _collect_transfer_fact_candidates(
            db_session, project.id, src_wh.id, "skladbot"
        )
        assert all(c[0] != out_ff.id for c in cands)

        # А приёмочное зеркало получателя — попадает (и не конфликтует с отгрузочным).
        in_ff = await self._ff(db_session, project, dst_wh, "inbound")
        await link_request(
            db_session, project.id, in_ff.id, stock_transfer_id=transfer.id, warehouse_id=dst_wh.id
        )
        cands_in = await _collect_transfer_fact_candidates(
            db_session, project.id, dst_wh.id, "skladbot"
        )
        assert any(c[0] == in_ff.id for c in cands_in)

    @pytest.mark.asyncio
    async def test_assembly_side_wrong_warehouse_rejected(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.fulfillment_service import link_request

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 40)
        await send_transfer(db_session, project.id, transfer.id)

        wrong = await self._ff(db_session, project, dst_wh, "assembly")
        with pytest.raises(ValueError, match="другой склад"):
            await link_request(
                db_session,
                project.id,
                wrong.id,
                stock_transfer_id=transfer.id,
                warehouse_id=dst_wh.id,
            )

    @pytest.mark.asyncio
    async def test_second_same_side_link_rejected(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.fulfillment_service import link_request

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 40)
        await send_transfer(db_session, project.id, transfer.id)

        first = await self._ff(db_session, project, src_wh, "assembly")
        await link_request(
            db_session, project.id, first.id, stock_transfer_id=transfer.id, warehouse_id=src_wh.id
        )
        second = await self._ff(db_session, project, src_wh, "assembly")
        with pytest.raises(ValueError, match="уже связано"):
            await link_request(
                db_session,
                project.id,
                second.id,
                stock_transfer_id=transfer.id,
                warehouse_id=src_wh.id,
            )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Отчёт «Логистика переездов»
# ═══════════════════════════════════════════════════════════════════════════


class TestTransferLogisticsReport:
    async def _sent_transfer(
        self, db_session, project, src, dst, barcode, qty: int, cost: str
    ) -> StockTransfer:
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src.id,
                "to_warehouse_id": dst.id,
                "items": [{"barcode": barcode, "quantity": qty}],
            },
        )
        await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(
                vehicle_info="К555ОО77",
                carrier_inn=f"77{_uid()[:8]}",
                carrier_name=f"Перевозчик {_uid()}",
                pickup_cost=Decimal(cost),
            ),
        )
        return await send_transfer(db_session, project.id, transfer.id)

    async def _pickup(self, db_session, project, transfer_id: int) -> OutboundShipment:
        return (
            await db_session.execute(
                select(OutboundShipment).where(
                    OutboundShipment.project_id == project.id,
                    OutboundShipment.stock_transfer_id == transfer_id,
                )
            )
        ).scalar_one()

    @pytest.mark.asyncio
    async def test_totals_and_cost_per_unit(self, db_session, project, src_wh, dst_wh, barcode):
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 300)
        await self._sent_transfer(db_session, project, src_wh, dst_wh, barcode, 100, "10000.00")
        await self._sent_transfer(db_session, project, src_wh, dst_wh, barcode, 50, "5000.00")

        rep = await get_transfer_logistics_report(db_session, project.id)
        assert rep.summary.transfers_count == 2
        assert rep.summary.total_cost == Decimal("15000.00")
        assert rep.summary.total_units == 150
        assert rep.summary.cost_per_unit == Decimal("100.00")
        assert rep.summary.avg_cost == Decimal("7500.00")
        # Оплаты нет ни у одного — всё в unpaid.
        assert rep.summary.unpaid_cost == Decimal("15000.00")
        assert rep.summary.paid_cost == Decimal("0")

        assert len(rep.by_route) == 1
        route = rep.by_route[0]
        assert (route.from_warehouse, route.to_warehouse) == (src_wh.name, dst_wh.name)
        assert route.transfers_count == 2
        assert route.cost_per_unit == Decimal("100.00")
        assert len(rep.by_carrier) == 2
        assert sum(p.transfers_count for p in rep.by_period) == 2
        assert {r.units_total for r in rep.rows} == {100, 50}
        assert all(r.sku_count == 1 and r.is_paid is False for r in rep.rows)

    @pytest.mark.asyncio
    async def test_paid_split_by_matched_transaction_and_request(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Оплачено = авто-матч выписки ИЛИ заявка на оплату в решённом статусе."""
        from backend.models.payment_request import (
            PaymentRequest,
            PaymentRequestShipment,
            PaymentRequestStatus,
        )
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 300)
        t_matched = await self._sent_transfer(
            db_session, project, src_wh, dst_wh, barcode, 10, "1000.00"
        )
        t_request = await self._sent_transfer(
            db_session, project, src_wh, dst_wh, barcode, 10, "2000.00"
        )
        t_draft = await self._sent_transfer(
            db_session, project, src_wh, dst_wh, barcode, 10, "4000.00"
        )

        # 1) Забор, заматченный с дебетом выписки напрямую.
        account = f"ACC-{_uid()}"
        await db_session.execute(
            text(
                "INSERT INTO accounts (project_id, account, bank, currency, is_our_account, "
                "is_deleted, is_customs_payee) VALUES (:pid, :acc, 'TEST', 'RUB', true, false, false)"
            ),
            {"pid": project.id, "acc": account},
        )
        txn_id = (
            await db_session.execute(
                text(
                    "INSERT INTO transactions (project_id, date, bank, account, currency, txn_id, "
                    "income, expense, is_internal, is_fx, is_cashflow2, is_deleted) "
                    "VALUES (:pid, NOW(), 'TEST', :acc, 'RUB', :txn, 0, 1000.00, false, false, 1, false) "
                    "RETURNING id"
                ),
                {"pid": project.id, "acc": account, "txn": f"txn-{_uid()}"},
            )
        ).scalar()
        matched_pickup = await self._pickup(db_session, project, t_matched.id)
        matched_pickup.matched_transaction_id = txn_id

        # 2) Забор в ПРОВЕДЁННОЙ заявке на оплату и 3) в черновике.
        for transfer, status, amount in (
            (t_request, PaymentRequestStatus.PAID.value, "2000.00"),
            (t_draft, PaymentRequestStatus.DRAFT.value, "4000.00"),
        ):
            pr = PaymentRequest(
                project_id=project.id,
                number=f"ОПЛ-{_uid()}",
                status=status,
                amount=Decimal(amount),
            )
            db_session.add(pr)
            await db_session.flush()
            pickup = await self._pickup(db_session, project, transfer.id)
            db_session.add(
                PaymentRequestShipment(
                    project_id=project.id,
                    payment_request_id=pr.id,
                    outbound_shipment_id=pickup.id,
                )
            )
        await db_session.commit()

        rep = await get_transfer_logistics_report(db_session, project.id)
        assert rep.summary.paid_cost == Decimal("3000.00")  # 1000 матч + 2000 заявка PAID
        assert rep.summary.unpaid_cost == Decimal("4000.00")  # черновик оплатой не считается
        paid_flags = {r.transfer_id: r.is_paid for r in rep.rows}
        assert paid_flags[t_matched.id] is True
        assert paid_flags[t_request.id] is True
        assert paid_flags[t_draft.id] is False
        numbers = {r.transfer_id: r.payment_request_number for r in rep.rows}
        assert numbers[t_request.id] is not None
        assert numbers[t_matched.id] is None

    @pytest.mark.asyncio
    async def test_marketplace_shipments_excluded(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Обычная отгрузка на маркетплейс (stock_transfer_id IS NULL) в отчёт не входит."""
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 300)
        plain = OutboundShipment(
            project_id=project.id,
            warehouse_id=src_wh.id,
            number=f"OUT-X-{_uid()}",
            status=OutboundStatus.SHIPPED,
            pickup_cost=Decimal("99999.00"),
        )
        db_session.add(plain)
        await db_session.commit()

        rep = await get_transfer_logistics_report(db_session, project.id)
        assert rep.summary.transfers_count == 0
        assert rep.summary.total_cost == Decimal("0")
        assert rep.rows == []

        await self._sent_transfer(db_session, project, src_wh, dst_wh, barcode, 10, "3000.00")
        rep = await get_transfer_logistics_report(db_session, project.id)
        assert rep.summary.total_cost == Decimal("3000.00")

    @pytest.mark.asyncio
    async def test_filters_by_warehouse_and_carrier(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.transfer_logistics import get_transfer_logistics_report

        third = await create_warehouse(
            db_session, project.id, {"name": f"THIRD-{_uid()}", "warehouse_type": "FULFILLMENT"}
        )
        await _stock(db_session, project, src_wh, barcode, 300)
        t_a = await self._sent_transfer(db_session, project, src_wh, dst_wh, barcode, 10, "1000.00")
        t_b = await self._sent_transfer(db_session, project, src_wh, third, barcode, 10, "2000.00")

        only_dst = await get_transfer_logistics_report(
            db_session, project.id, to_warehouse_id=dst_wh.id
        )
        assert [r.transfer_id for r in only_dst.rows] == [t_a.id]

        none_from_third = await get_transfer_logistics_report(
            db_session, project.id, from_warehouse_id=third.id
        )
        assert none_from_third.summary.transfers_count == 0

        pickup_b = await self._pickup(db_session, project, t_b.id)
        by_carrier = await get_transfer_logistics_report(
            db_session, project.id, counterparty_id=pickup_b.counterparty_id
        )
        assert [r.transfer_id for r in by_carrier.rows] == [t_b.id]
        assert by_carrier.summary.total_cost == Decimal("2000.00")

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session, project, other_project, src_wh, dst_wh, barcode):
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 100)
        await self._sent_transfer(db_session, project, src_wh, dst_wh, barcode, 10, "1000.00")
        rep = await get_transfer_logistics_report(db_session, other_project.id)
        assert rep.summary.transfers_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# 7. Транспортная единица переезда (паллеты / короба)
# ═══════════════════════════════════════════════════════════════════════════


class TestTransferTransportUnit:
    @pytest.mark.asyncio
    async def test_conversion_inherits_unit_from_assembly(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Единица уже посчитана логистом на заявке — конвертация её не теряет."""
        assembly = await _assembly(db_session, project, src_wh, barcode, 30)
        assembly.pallets_count = 5
        assembly.pallet_weight_kg = Decimal("320.50")
        assembly.shipped_as_boxes = False
        await db_session.commit()

        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        transfer = await db_session.get(StockTransfer, result.transfer_id)
        assert transfer.pallets_count == 5
        assert transfer.pallet_weight_kg == Decimal("320.50")
        assert transfer.shipped_as_boxes is False

    @pytest.mark.asyncio
    async def test_conversion_inherits_boxes_unit(self, db_session, project, src_wh, dst_wh, barcode):
        assembly = await _assembly(db_session, project, src_wh, barcode, 30)
        assembly.pallets_count = 12
        assembly.shipped_as_boxes = True
        await db_session.commit()

        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        transfer = await db_session.get(StockTransfer, result.transfer_id)
        assert transfer.pallets_count == 12
        assert transfer.shipped_as_boxes is True

    @pytest.mark.asyncio
    async def test_assign_vehicle_sets_unit_but_none_keeps_previous(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Логист уточняет единицу при назначении машины; пустое поле не затирает."""
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
                "pallets_count": 3,
                "pallet_weight_kg": Decimal("200.00"),
            },
        )
        assert transfer.pallets_count == 3

        # Уточнение: стало 4 паллеты.
        updated = await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(vehicle_info="О123ОО77", pallets_count=4),
        )
        assert updated.pallets_count == 4
        assert updated.pallet_weight_kg == Decimal("200.00")  # не затёрто

        # Повторное назначение без единицы — прежние значения на месте.
        updated = await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(vehicle_info="О123ОО77"),
        )
        assert updated.pallets_count == 4
        assert updated.pallet_weight_kg == Decimal("200.00")
        assert updated.shipped_as_boxes is False

    @pytest.mark.asyncio
    async def test_pickup_snapshots_unit(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
                "pallets_count": 2,
                "pallet_weight_kg": Decimal("150.00"),
                "shipped_as_boxes": True,
            },
        )
        await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(vehicle_info="Р555РР77", pickup_cost=Decimal("8000.00")),
        )
        await send_transfer(db_session, project.id, transfer.id)

        pickup = (
            await db_session.execute(
                select(OutboundShipment).where(
                    OutboundShipment.project_id == project.id,
                    OutboundShipment.stock_transfer_id == transfer.id,
                )
            )
        ).scalar_one()
        assert pickup.pallets_count == 2
        assert pickup.pallet_weight_kg == Decimal("150.00")
        assert pickup.shipped_as_boxes is True

    @pytest.mark.asyncio
    async def test_report_pallet_metric_excludes_boxes(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """₽/паллета считается по паллетным переездам; коробочные — только в total_boxes."""
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 300)
        for unit_count, as_boxes, cost in ((4, False, "8000.00"), (30, True, "5000.00")):
            transfer = await create_transfer(
                db_session,
                project.id,
                {
                    "from_warehouse_id": src_wh.id,
                    "to_warehouse_id": dst_wh.id,
                    "items": [{"barcode": barcode, "quantity": 20}],
                    "pallets_count": unit_count,
                    "shipped_as_boxes": as_boxes,
                },
            )
            await assign_vehicle_transfer(
                db_session,
                project.id,
                transfer.id,
                TransferAssignVehicle(vehicle_info="Т111ТТ77", pickup_cost=Decimal(cost)),
            )
            await send_transfer(db_session, project.id, transfer.id)

        rep = await get_transfer_logistics_report(db_session, project.id)
        assert rep.summary.total_pallets == 4  # 30 коробов НЕ приплюсованы
        assert rep.summary.total_boxes == 30
        # 8000 ₽ паллетного переезда / 4 паллеты — деньги коробочного не подмешаны.
        assert rep.summary.cost_per_pallet == Decimal("2000.00")
        assert rep.summary.total_cost == Decimal("13000.00")
        assert rep.by_route[0].cost_per_pallet == Decimal("2000.00")
        assert rep.by_route[0].total_pallets == 4
        assert sum(p.total_pallets for p in rep.by_period) == 4
        by_unit = {r.pallets_count: r.shipped_as_boxes for r in rep.rows}
        assert by_unit == {4: False, 30: True}

    @pytest.mark.asyncio
    async def test_report_pallet_metric_none_when_all_boxes(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 20}],
                "pallets_count": 10,
                "shipped_as_boxes": True,
            },
        )
        await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(vehicle_info="У222УУ77", pickup_cost=Decimal("3000.00")),
        )
        await send_transfer(db_session, project.id, transfer.id)

        rep = await get_transfer_logistics_report(db_session, project.id)
        assert rep.summary.total_pallets == 0
        assert rep.summary.cost_per_pallet is None
        assert rep.summary.total_boxes == 10


# ═══════════════════════════════════════════════════════════════════════════
# 8. Лист логиста: bulk-назначение, снятие машины, срез «ждут машину»
# ═══════════════════════════════════════════════════════════════════════════


class TestLogisticianList:
    async def _draft(self, db_session, project, src, dst, barcode, qty: int = 10) -> StockTransfer:
        return await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src.id,
                "to_warehouse_id": dst.id,
                "items": [{"barcode": barcode, "quantity": qty}],
            },
        )

    @pytest.mark.asyncio
    async def test_bulk_assigns_one_vehicle_to_many(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Три переезда «транзит Питер» едут одной газелью — одно назначение."""
        from backend.services.warehouse_outbound import assign_vehicle_transfer_bulk

        drafts = [await self._draft(db_session, project, src_wh, dst_wh, barcode) for _ in range(3)]
        rows = await assign_vehicle_transfer_bulk(
            db_session,
            project.id,
            [d.id for d in drafts],
            TransferAssignVehicle(
                vehicle_info="М777ММ77",
                carrier_inn=f"77{_uid()[:8]}",
                carrier_name="ИП Одна машина",
                pickup_cost=Decimal("12000.00"),
            ),
        )
        assert len(rows) == 3
        assert {r.vehicle_info for r in rows} == {"М777ММ77"}
        assert len({r.counterparty_id for r in rows}) == 1  # один перевозчик на все
        assert all(r.vehicle_assigned_at is not None for r in rows)
        assert all(r.status == TransferStatus.DRAFT for r in rows)

    @pytest.mark.asyncio
    async def test_bulk_fails_entirely_on_bad_id(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Частичное назначение логист бы не заметил — падаем целиком."""
        from backend.services.warehouse_outbound import assign_vehicle_transfer_bulk

        ok = await self._draft(db_session, project, src_wh, dst_wh, barcode)
        with pytest.raises(ValueError, match="Ничего не назначено"):
            await assign_vehicle_transfer_bulk(
                db_session,
                project.id,
                [ok.id, 99999999],
                TransferAssignVehicle(vehicle_info="Н888НН77"),
            )
        # И ПЕРВЫЙ переезд тоже не назначен: вызов атомарный, откат общий.
        # Раньше цикл коммитил каждый id — первый оставался с машиной, а логист
        # видел 400 и был уверен, что не назначилось ничего.
        await db_session.refresh(ok)
        assert ok.vehicle_info is None
        assert ok.vehicle_assigned_at is None

    @pytest.mark.asyncio
    async def test_unassign_clears_logistics_but_keeps_cargo_unit(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Снятие машины меняет ТОЛЬКО перевозчика — груз остаётся тем же."""
        from backend.services.warehouse_outbound import unassign_vehicle_transfer

        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 10}],
                "pallets_count": 3,
                "pallet_weight_kg": Decimal("180.00"),
            },
        )
        await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(
                vehicle_info="С999СС77",
                vehicle_brand="Газель",
                driver_first_name="Иван",
                driver_last_name="Петров",
                driver_phone="79005554433",
                carrier_inn=f"77{_uid()[:8]}",
                carrier_name="ИП Снимаем",
                pickup_cost=Decimal("11000.00"),
            ),
        )
        cleared = await unassign_vehicle_transfer(db_session, project.id, transfer.id)

        assert cleared.vehicle_info is None
        assert cleared.vehicle_brand is None
        assert cleared.driver_first_name is None
        assert cleared.driver_last_name is None
        assert cleared.driver_phone is None
        assert cleared.counterparty_id is None
        assert cleared.pickup_cost is None
        assert cleared.pickup_date is None
        assert cleared.delivery_date is None
        assert cleared.vehicle_assigned_at is None
        assert cleared.logistics_by_warehouse is False
        # Груз не тронут.
        assert cleared.pallets_count == 3
        assert cleared.pallet_weight_kg == Decimal("180.00")
        assert cleared.shipped_as_boxes is False

    @pytest.mark.asyncio
    async def test_unassign_in_transit_forbidden(self, db_session, project, src_wh, dst_wh, barcode):
        from backend.services.warehouse_outbound import unassign_vehicle_transfer

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 10)
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="уже отправлен"):
            await unassign_vehicle_transfer(db_session, project.id, transfer.id)

    @pytest.mark.asyncio
    async def test_has_vehicle_filter_and_warehouse_names(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.warehouse_outbound import list_transfers

        waiting = await self._draft(db_session, project, src_wh, dst_wh, barcode)
        assigned = await self._draft(db_session, project, src_wh, dst_wh, barcode)
        await assign_vehicle_transfer(
            db_session, project.id, assigned.id, TransferAssignVehicle(vehicle_info="Ж111ЖЖ77")
        )

        free = await list_transfers(db_session, project.id, status="DRAFT", has_vehicle=False)
        assert [t.id for t in free] == [waiting.id]
        busy = await list_transfers(db_session, project.id, status="DRAFT", has_vehicle=True)
        assert [t.id for t in busy] == [assigned.id]
        both = await list_transfers(db_session, project.id, status="DRAFT")
        assert {t.id for t in both} == {waiting.id, assigned.id}

        # Имена концов маршрута едут в выдаче — Лист логиста не догружает справочник.
        assert free[0].from_warehouse_name == src_wh.name
        assert free[0].to_warehouse_name == dst_wh.name
        # И доезжают до ответа API: имена — немапленые атрибуты, схема обязана их прочитать.
        from backend.schemas.warehouse import StockTransferSchema

        dto = StockTransferSchema.model_validate(free[0])
        assert dto.from_warehouse_name == src_wh.name
        assert dto.to_warehouse_name == dst_wh.name


# ═══════════════════════════════════════════════════════════════════════════
# 9. HTTP-контур: порядок маршрутов и коды ответов
# ═══════════════════════════════════════════════════════════════════════════


async def _api_headers(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/projects", json={"name": f"TRV {_uid()}"}, headers=auth_headers
    )
    return {**auth_headers, "X-Project-Id": str(resp.json()["id"])}


class TestTransferRoutes:
    @pytest.mark.asyncio
    async def test_static_paths_win_over_transfer_id(self, client, auth_headers):
        """`logistics-report` и `assign-vehicle-bulk` не должны съедаться `{transfer_id}: int`
        (иначе FastAPI отдаёт 422 вместо отчёта / назначения)."""
        headers = await _api_headers(client, auth_headers)

        resp = await client.get("/api/v1/warehouse/transfers/logistics-report", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["transfers_count"] == 0
        assert body["rows"] == []

        resp = await client.post(
            "/api/v1/warehouse/transfers/assign-vehicle-bulk",
            json={"ids": [], "payload": {"vehicle_info": "А000АА00"}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_get_missing_transfer_is_404(self, client, auth_headers):
        headers = await _api_headers(client, auth_headers)
        resp = await client.get("/api/v1/warehouse/transfers/99999999", headers=headers)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_with_transport_unit_over_http(self, client, auth_headers, db_session):
        """Создание переезда С транспортной единицей по РЕАЛЬНОМУ телу запроса.

        Сервисные тесты зовут `create_transfer` словарём и не проверяют провод:
        схема → роутер → сервис. Форма создания шлёт флаг ВСЕГДА, а пустые
        количество/вес — как `null` («не задано»), и именно эта комбинация
        должна доезжать до БД неискажённой. На create семантика ДВУЗНАЧНАЯ:
        `shipped_as_boxes: false` — это «паллеты», а не «не трогай».
        """
        headers = await _api_headers(client, auth_headers)
        pid = int(headers["X-Project-Id"])

        wh_ids = []
        for name in (f"SRC-API-{_uid()}", f"DST-API-{_uid()}"):
            resp = await client.post(
                "/api/v1/warehouse",
                json={"name": name, "warehouse_type": "FULFILLMENT"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            wh_ids.append(resp.json()["id"])

        bc = f"BC-API-{_uid()}"
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
                "VALUES (:pid, :bc, 'API Item', NOW())"
            ),
            {"pid": pid, "bc": bc},
        )
        await db_session.commit()

        # Коробочный переезд: флаг явный, оценка не заполнена.
        resp = await client.post(
            "/api/v1/warehouse/transfers",
            json={
                "from_warehouse_id": wh_ids[0],
                "to_warehouse_id": wh_ids[1],
                "items": [{"barcode": bc, "quantity": 7}],
                "shipped_as_boxes": True,
                "pallets_count": None,
                "pallet_weight_kg": None,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["shipped_as_boxes"] is True
        assert body["pallets_count"] is None
        assert body["pallet_weight_kg"] is None

        # Паллетный с заполненной оценкой: Decimal уезжает строкой (контракт фронта).
        resp = await client.post(
            "/api/v1/warehouse/transfers",
            json={
                "from_warehouse_id": wh_ids[0],
                "to_warehouse_id": wh_ids[1],
                "items": [{"barcode": bc, "quantity": 3}],
                "shipped_as_boxes": False,
                "pallets_count": 2,
                "pallet_weight_kg": "175.50",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["shipped_as_boxes"] is False
        assert body["pallets_count"] == 2
        assert body["pallet_weight_kg"] == "175.50"

        # Единица доезжает и до карточки переезда, и до списка.
        resp = await client.get(
            f"/api/v1/warehouse/transfers/{body['id']}", headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["pallets_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# 10. Судьба заявки при конвертации (фантомный резерв / двойная отгрузка)
# ═══════════════════════════════════════════════════════════════════════════


class TestConvertAssemblyFate:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            AssemblyStatus.PENDING.value,
            AssemblyStatus.IN_PROGRESS.value,
            AssemblyStatus.READY.value,
            AssemblyStatus.VEHICLE_ASSIGNED.value,
        ],
    )
    async def test_active_assembly_cancelled(
        self, db_session, project, src_wh, dst_wh, barcode, status
    ):
        """АКТИВНАЯ заявка обязана отмениться: иначе её резерв остаётся висеть на
        складе (сток-то уедет переездом), а «Отгрузить» спишет те же единицы
        второй раз. Главный сценарий фичи — конвертация из READY."""
        assembly = await _assembly(db_session, project, src_wh, barcode, 30, status=status)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assert result.assembly_cancelled is True
        await db_session.refresh(assembly)
        assert assembly.status == AssemblyStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_writes_status_history(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Отмена не молчаливая — в истории заявки видно, куда делся товар."""
        from backend.models.assembly import AssemblyStatusHistory

        assembly = await _assembly(
            db_session, project, src_wh, barcode, 30, status=AssemblyStatus.READY.value
        )
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        events = list(
            (
                await db_session.execute(
                    select(AssemblyStatusHistory).where(
                        AssemblyStatusHistory.project_id == project.id,
                        AssemblyStatusHistory.assembly_request_id == assembly.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert events
        assert any(result.transfer_number in (e.comment or "") for e in events)

    @pytest.mark.asyncio
    async def test_active_assembly_stops_holding_reserve(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Проверка сути: после конвертации заявка не держит резерв склада."""
        from backend.services.warehouse_stock_engine import _get_reserved_map

        await _stock(db_session, project, src_wh, barcode, 100)
        assembly = await _assembly(
            db_session, project, src_wh, barcode, 30, status=AssemblyStatus.READY.value
        )
        nom_id = await _nom_id(db_session, project, barcode)
        before = await _get_reserved_map(db_session, project.id, src_wh.id)
        assert before.get(nom_id, 0) == 30

        await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        after = await _get_reserved_map(db_session, project.id, src_wh.id)
        assert after.get(nom_id, 0) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        [
            AssemblyStatus.CLOSED.value,
            AssemblyStatus.RETURNED.value,
            AssemblyStatus.CANCELLED.value,
        ],
    )
    async def test_terminal_assembly_untouched(
        self, db_session, project, src_wh, dst_wh, barcode, status
    ):
        """Терминальные не трогаем: резерва там нет, история должна остаться честной."""
        assembly = await _assembly(db_session, project, src_wh, barcode, 25, status=status)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assert result.assembly_cancelled is False
        await db_session.refresh(assembly)
        assert assembly.status == status

    @pytest.mark.asyncio
    async def test_fbs_mirror_conversion_denied(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Зеркало FBS списывает движениями FBS_ORDER — нетто-гард на них слеп,
        и конвертация прошла бы по уже списанному товару. Запрещено целиком."""
        from backend.services.assembly.status import FbsMirrorManualError

        assembly = await _assembly(
            db_session, project, src_wh, barcode, 30, status=AssemblyStatus.READY.value
        )
        assembly.kind = "fbs"
        assembly.fbs_supply_id = f"WB-GI-{_uid()}"
        await db_session.commit()

        with pytest.raises(FbsMirrorManualError):
            await convert_assembly_to_transfer(
                db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
            )


# ═══════════════════════════════════════════════════════════════════════════
# 11. Полнота денег в отчёте и лёгкость списка
# ═══════════════════════════════════════════════════════════════════════════


class TestReportCompletenessAndListWeight:
    @pytest.mark.asyncio
    async def test_summary_complete_when_detail_capped(
        self, db_session, project, src_wh, dst_wh, barcode, monkeypatch
    ):
        """🔴 Деньги НЕ усекаются лимитом детализации.

        Лимит опущен до 1: строк придёт одна, а сводка обязана посчитать ВСЕ
        три переезда. Раньше лимит стоял на базовой выборке, и с 1001-го забора
        отчёт молча показывал бы деньги только по 1000 свежайшим — заниженная
        сумма в денежном отчёте неотличима от «мало возили».
        """
        from backend.services import transfer_logistics
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 300)
        for cost in ("1000.00", "2000.00", "3000.00"):
            transfer = await create_transfer(
                db_session,
                project.id,
                {
                    "from_warehouse_id": src_wh.id,
                    "to_warehouse_id": dst_wh.id,
                    "items": [{"barcode": barcode, "quantity": 10}],
                },
            )
            await assign_vehicle_transfer(
                db_session,
                project.id,
                transfer.id,
                TransferAssignVehicle(vehicle_info="Л111ЛЛ77", pickup_cost=Decimal(cost)),
            )
            await send_transfer(db_session, project.id, transfer.id)

        monkeypatch.setattr(transfer_logistics, "_ROWS_LIMIT", 1)
        rep = await get_transfer_logistics_report(db_session, project.id)

        assert len(rep.rows) == 1
        assert rep.summary.rows_truncated is True
        # Деньги и счётчик — по всем трём, а не по одной строке.
        assert rep.summary.transfers_count == 3
        assert rep.summary.total_cost == Decimal("6000.00")
        assert rep.summary.total_units == 30
        assert rep.by_route[0].transfers_count == 3
        assert rep.by_route[0].total_cost == Decimal("6000.00")
        assert sum(p.transfers_count for p in rep.by_period) == 3

    @pytest.mark.asyncio
    async def test_summary_only_skips_rows(self, db_session, project, src_wh, dst_wh, barcode):
        from backend.services.transfer_logistics import get_transfer_logistics_report

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _transfer_with_vehicle(db_session, project, src_wh, dst_wh, barcode, 10)
        await send_transfer(db_session, project.id, transfer.id)

        rep = await get_transfer_logistics_report(db_session, project.id, summary_only=True)
        assert rep.summary.transfers_count == 1
        assert rep.summary.total_cost == Decimal("15000.00")
        assert rep.rows == []
        # Детализации нет ПО ЗАПРОСУ — это не усечение, ложную тревогу не поднимаем.
        assert rep.summary.rows_truncated is False

    @pytest.mark.asyncio
    async def test_list_gives_totals_without_items(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Список отдаёт два числа вместо состава: полный состав на потолке в
        500 переездов — мегабайты и десятки тысяч pydantic-моделей."""
        from backend.schemas.warehouse import StockTransferSchema
        from backend.services.warehouse_outbound import get_transfer, list_transfers

        second = f"BC2-{_uid()}"
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
                "VALUES (:pid, :bc, 'Second', NOW())"
            ),
            {"pid": project.id, "bc": second},
        )
        await db_session.commit()

        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [
                    {"barcode": barcode, "quantity": 40},
                    {"barcode": second, "quantity": 60},
                ],
            },
        )
        # Эмулируем прод-путь: там сессия своя на HTTP-запрос, и переезда ещё
        # нет в identity map. Без expunge объект остался бы тем же, что создал
        # `create_transfer` — с уже загруженным составом, и noload на него не
        # действует (это артефакт общей сессии в тестах, а не эндпоинта).
        db_session.expunge_all()

        rows = await list_transfers(db_session, project.id)
        row = next(r for r in rows if r.id == transfer.id)
        assert row.units_total == 100
        assert row.sku_count == 2
        assert list(row.items) == []  # состав в списке не отдаём

        dto = StockTransferSchema.model_validate(row)
        assert dto.units_total == 100 and dto.sku_count == 2 and dto.items == []

        # А в карточке состав на месте.
        full = await get_transfer(db_session, project.id, transfer.id)
        assert len(full.items) == 2

    @pytest.mark.asyncio
    async def test_list_carries_counterparty_name(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Без имени карточка показывала «Контрагент #12»."""
        from backend.services.warehouse_outbound import list_transfers

        transfer = await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        updated = await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(
                vehicle_info="Ц333ЦЦ77",
                carrier_inn=f"77{_uid()[:8]}",
                carrier_name="ООО Ромашка-Транс",
            ),
        )
        assert updated.counterparty_name == "ООО Ромашка-Транс"
        rows = await list_transfers(db_session, project.id)
        row = next(r for r in rows if r.id == transfer.id)
        assert row.counterparty_name == "ООО Ромашка-Транс"

    @pytest.mark.asyncio
    async def test_converted_from_assembly_filter(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.warehouse_outbound import list_transfers

        assembly = await _assembly(db_session, project, src_wh, barcode, 25)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        await create_transfer(
            db_session,
            project.id,
            {
                "from_warehouse_id": src_wh.id,
                "to_warehouse_id": dst_wh.id,
                "items": [{"barcode": barcode, "quantity": 5}],
            },
        )
        rows = await list_transfers(
            db_session, project.id, converted_from_assembly_id=assembly.id
        )
        assert [r.id for r in rows] == [result.transfer_id]


# ═══════════════════════════════════════════════════════════════════════════
# 12. Валидатор пустого тела назначения
# ═══════════════════════════════════════════════════════════════════════════


class TestAssignVehicleValidation:
    def test_empty_payload_rejected(self):
        """`{}` ставил бы vehicle_assigned_at и плодил машину-призрак: переезд
        попадал в срез «машина назначена» с блоком из «—», а при отправке
        порождал забор без перевозчика и без суммы — мусор в листе оплаты."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            TransferAssignVehicle()

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"vehicle_info": "А111АА77"},
            {"carrier_inn": "7712345678"},
            {"carrier_name": "ИП Перевозчик"},
            {"logistics_by_warehouse": True},
        ],
    )
    def test_any_identity_field_is_enough(self, kwargs):
        assert TransferAssignVehicle(**kwargs)

    @pytest.mark.asyncio
    async def test_empty_body_rejected_over_http(self, client, auth_headers):
        headers = await _api_headers(client, auth_headers)
        resp = await client.post(
            "/api/v1/warehouse/transfers/1/assign-vehicle", json={}, headers=headers
        )
        assert resp.status_code == 422, resp.text
