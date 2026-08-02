# ruff: noqa: RUF001, RUF002, RUF003
"""Переезд на статусной модели заявки на сборку (канон юзера 31.07.2026).

Файл проверяет ЛЕСТНИЦУ и то, что к ней прибито:
  • матрицу `TRANSFER_TRANSITIONS` целиком — разрешённое проходит, запрещённое падает;
  • сток движется РОВНО в двух переходах: → SHIPPED списывает, → DELIVERED приходует;
  • возврат восстанавливает сток на источнике и снимает транзит у получателя;
  • сигналы синка ФФ (авто-READY и авто-SHIPPED), включая гейт «завершены ВСЕ
    связанные заявки» и запрет авто-отгрузки без назначенной машины;
  • конвертация заявки наследует готовность;
  • правка разрешена ровно в трёх статусах.

Движения стока проверяем по ЖУРНАЛУ (`stock_movements`), а не только по
балансу: «списалось один раз» и «списалось дважды, потом вернулось» дают
одинаковый остаток, и отличить их можно только по количеству проводок.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.fulfillment import FulfillmentRequest
from backend.models.warehouse import (
    TRANSFER_EDITABLE_STATUSES,
    TRANSFER_TRANSITIONS,
    InboundReceipt,
    InboundStatus,
    MovementType,
    OutboundShipment,
    StockMovement,
    StockTransfer,
    StockTransferStatusHistory,
    TransferStatus,
    WarehouseStock,
)
from backend.schemas.warehouse import (
    AssemblyToTransfer,
    StockTransferUpdate,
    TransferAssignVehicle,
)
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_outbound import (
    _check_transfer_transition,
    assign_vehicle_transfer,
    cancel_transfer,
    close_transfer,
    complete_transfer,
    convert_assembly_to_transfer,
    create_transfer,
    mark_transfer_ready,
    receive_transfer_fact,
    return_transfer,
    send_transfer as _send_transfer_raw,
    unassign_vehicle_transfer,
    update_transfer,
)

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def send_transfer(db_session, project_id, transfer_id, **kwargs):
    """Отправка с явным «везём без оформления».

    Файл проверяет ЛЕСТНИЦУ, а не гейт логистики: с 01.08.2026 голый READY без
    машины/перевозчика/стоимости отправить нельзя (TR-32 — сток списывался, а
    забор не рождался), и без флага половина тестов лестницы падала бы на
    несвязанной проверке. Сам гейт живёт в `TestSendLogisticsGate` — там
    вызывается `_send_transfer_raw` напрямую.
    """
    kwargs.setdefault("allow_no_logistics", True)
    return await _send_transfer_raw(db_session, project_id, transfer_id, **kwargs)


# ─── Фикстуры ──────────────────────────────────────────────────────────────


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
    bc = f"BC-TRS-{_uid()}"
    await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
            "VALUES (:pid, :bc, :subj, NOW())"
        ),
        {"pid": project.id, "bc": bc, "subj": "Transfer Status Item"},
    )
    await db_session.commit()
    return bc


# ─── Хелперы ───────────────────────────────────────────────────────────────


async def _stock(db_session, project, warehouse, barcode, qty: int) -> None:
    receipt = await create_receipt(
        db_session,
        project.id,
        warehouse.id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project.id, receipt.id)


async def _nom_id(db_session, project, barcode: str) -> int:
    return (
        await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": project.id, "bc": barcode},
        )
    ).scalar()


async def _mk(db_session, project, src, dst, barcode, qty: int = 10, **kw) -> StockTransfer:
    """Свежий переезд в PENDING."""
    return await create_transfer(
        db_session,
        project.id,
        {
            "from_warehouse_id": src.id,
            "to_warehouse_id": dst.id,
            "items": [{"barcode": barcode, "quantity": qty}],
            **kw,
        },
    )


async def _force_status(db_session, transfer: StockTransfer, status: TransferStatus) -> None:
    """Поставить статус НАПРЯМУЮ, минуя переходы.

    Только для матрицы: чтобы проверить запрещённый переход из X, надо сначала
    оказаться в X, а легальным путём в некоторые статусы (CLOSED, CANCELLED)
    приходишь уже с движениями стока, которые к делу не относятся.
    """
    await db_session.execute(
        text("UPDATE stock_transfers SET status = :st WHERE id = :tid"),
        {"st": status.value, "tid": transfer.id},
    )
    await db_session.commit()
    await db_session.refresh(transfer)


async def _stock_row(db_session, project, warehouse_id, nomenclature_id) -> WarehouseStock | None:
    row = (
        await db_session.execute(
            select(WarehouseStock).where(
                WarehouseStock.project_id == project.id,
                WarehouseStock.warehouse_id == warehouse_id,
                WarehouseStock.nomenclature_id == nomenclature_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        await db_session.refresh(row)
    return row


async def _movements(db_session, project, transfer_id, movement_type: MovementType) -> list[int]:
    """Количества всех движений переезда данного типа — по одному числу на проводку."""
    rows = (
        await db_session.execute(
            select(StockMovement.quantity, StockMovement.defect_delta).where(
                StockMovement.project_id == project.id,
                StockMovement.reference_id == transfer_id,
                StockMovement.reference_type.in_(("TRANSFER", "TRANSFER_RETURN")),
                StockMovement.movement_type == movement_type.value,
            )
        )
    ).all()
    return [int(q or 0) + int(d or 0) for q, d in rows]


async def _pickup_count(db_session, project, transfer_id) -> int:
    """Сколько ЖИВЫХ заборов у переезда — «есть ли у перевозки денежный документ»."""
    return (
        await db_session.execute(
            select(func.count(OutboundShipment.id)).where(
                OutboundShipment.project_id == project.id,
                OutboundShipment.stock_transfer_id == transfer_id,
                OutboundShipment.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar()


async def _history(db_session, project, transfer_id) -> list[tuple]:
    rows = (
        await db_session.execute(
            select(
                StockTransferStatusHistory.old_status,
                StockTransferStatusHistory.new_status,
                StockTransferStatusHistory.changed_by,
            )
            .where(
                StockTransferStatusHistory.project_id == project.id,
                StockTransferStatusHistory.stock_transfer_id == transfer_id,
            )
            .order_by(StockTransferStatusHistory.id)
        )
    ).all()
    return [tuple(r) for r in rows]


async def _ff(
    db_session,
    project,
    warehouse,
    *,
    kind: str = "assembly",
    stage_code: str | None = "ready",
    is_completed: bool = False,
    transfer_id: int | None = None,
    provider: str = "migfull",
) -> FulfillmentRequest:
    req = FulfillmentRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        provider=provider,
        external_id=f"ff-{_uid()}",
        number=f"PVB-{_uid()[:5]}",
        kind=kind,
        status="in_progress",
        stage_code=stage_code,
        stage_title="Собран" if stage_code == "ready" else "В обработке",
        is_completed=is_completed,
        total_qty=10,
        stock_transfer_id=transfer_id,
    )
    db_session.add(req)
    await db_session.commit()
    return req


# ═══════════════════════════════════════════════════════════════════════════
# 1. Матрица переходов
# ═══════════════════════════════════════════════════════════════════════════


class TestTransitionMatrix:
    """`_check_transfer_transition` обязан быть ТОЧНЫМ отражением таблицы.

    Проверяем ВСЕ 81 пару (9 статусов × 9): разрешённая проходит молча,
    любая другая падает. Это единственный тест, который ловит расхождение
    сервиса с `TRANSFER_TRANSITIONS` — все ступени валидируются через него.
    """

    async def test_every_pair_matches_table(self):
        for current in TransferStatus:
            allowed = TRANSFER_TRANSITIONS[current]
            for target in TransferStatus:
                if target in allowed:
                    _check_transfer_transition(current, target)  # не бросает
                else:
                    with pytest.raises(ValueError, match="запрещён"):
                        _check_transfer_transition(current, target)

    async def test_terminals_have_no_exit(self):
        assert TRANSFER_TRANSITIONS[TransferStatus.CLOSED] == set()
        assert TRANSFER_TRANSITIONS[TransferStatus.CANCELLED] == set()

    async def test_shipped_cannot_go_back_to_ready(self):
        """Осознанное отличие от заявки: сток уже списан, откат — только RETURNED."""
        assert TransferStatus.READY not in TRANSFER_TRANSITIONS[TransferStatus.SHIPPED]
        assert TransferStatus.RETURNED in TRANSFER_TRANSITIONS[TransferStatus.SHIPPED]

    async def test_pending_can_skip_straight_to_ready(self):
        """Переезд без ФФ фазы «провайдер собирает» не проходит."""
        assert TransferStatus.READY in TRANSFER_TRANSITIONS[TransferStatus.PENDING]


# ═══════════════════════════════════════════════════════════════════════════
# 2. Ручные ступени и их гейты
# ═══════════════════════════════════════════════════════════════════════════


class TestManualLadder:
    async def test_full_happy_path(self, db_session, project, src_wh, dst_wh, barcode):
        """PENDING → READY → VEHICLE_ASSIGNED → SHIPPED → DELIVERED → CLOSED."""
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        assert transfer.status == TransferStatus.PENDING

        ready = await mark_transfer_ready(db_session, project.id, transfer.id)
        assert ready.status == TransferStatus.READY
        assert ready.actual_ready_date == date.today()

        assigned = await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        assert assigned.status == TransferStatus.VEHICLE_ASSIGNED

        sent = await send_transfer(db_session, project.id, transfer.id)
        assert sent.status == TransferStatus.SHIPPED
        assert sent.shipped_at is not None

        done = await complete_transfer(db_session, project.id, transfer.id)
        assert done.status == TransferStatus.DELIVERED

        closed = await close_transfer(db_session, project.id, transfer.id)
        assert closed.status == TransferStatus.CLOSED

        # Каждая ступень оставила след — «кто и когда двинул сток» разбирается
        # по журналу, а не по текущему статусу.
        assert [(o, n) for o, n, _ in await _history(db_session, project, transfer.id)] == [
            ("PENDING", "READY"),
            ("READY", "VEHICLE_ASSIGNED"),
            ("VEHICLE_ASSIGNED", "SHIPPED"),
            ("SHIPPED", "DELIVERED"),
            ("DELIVERED", "CLOSED"),
        ]

    async def test_send_from_ready_requires_logistics(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Карв-аут READY → SHIPPED жив, но голый READY не пускает (канон 01.08.2026).

        Отказ обязан случиться ДО единой проводки: иначе сток списан, а забор
        (носитель денег) не создан — переезд уехал бы мимо оплат навсегда.
        """
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)

        with pytest.raises(ValueError, match="Назначьте машину"):
            await _send_transfer_raw(db_session, project.id, transfer.id)
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.READY
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == []

        # Любой признак оформления открывает карв-аут — госномер не обязателен.
        transfer.pickup_cost = Decimal("7000.00")
        await db_session.commit()
        sent = await _send_transfer_raw(db_session, project.id, transfer.id)
        assert sent.status == TransferStatus.SHIPPED

    async def test_send_from_ready_with_explicit_allow_no_logistics(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """«Создать и увезти»: явный флаг — единственный путь для голого READY."""
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)

        sent = await _send_transfer_raw(
            db_session, project.id, transfer.id, allow_no_logistics=True
        )
        assert sent.status == TransferStatus.SHIPPED
        # И забора нет: платить за внутреннюю переброску некому.
        assert await _pickup_count(db_session, project, transfer.id) == 0

    async def test_send_rejected_before_ready(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        with pytest.raises(ValueError, match="PENDING → SHIPPED запрещён"):
            await send_transfer(db_session, project.id, transfer.id)
        # И ни одной проводки: отказ до мутаций.
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == []

    async def test_ready_rejected_from_shipped(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="SHIPPED → READY запрещён"):
            await mark_transfer_ready(db_session, project.id, transfer.id)

    async def test_ready_rejects_empty_composition(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        transfer = await create_transfer(
            db_session,
            project.id,
            {"from_warehouse_id": src_wh.id, "to_warehouse_id": dst_wh.id, "items": []},
        )
        with pytest.raises(ValueError, match="Состав переезда пуст"):
            await mark_transfer_ready(db_session, project.id, transfer.id)

    async def test_unassign_returns_to_ready(self, db_session, project, src_wh, dst_wh, barcode):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        cleared = await unassign_vehicle_transfer(db_session, project.id, transfer.id)
        assert cleared.status == TransferStatus.READY
        assert cleared.vehicle_assigned_at is None

    async def test_reassign_inside_vehicle_assigned(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Поменять госномер за день до забора — норма, а не повод снимать машину."""
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        again = await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="В222ВВ77")
        )
        assert again.status == TransferStatus.VEHICLE_ASSIGNED
        assert again.vehicle_info == "В222ВВ77"
        # Переход не состоялся — лишней строки в истории нет.
        assert [n for _, n, _ in await _history(db_session, project, transfer.id)] == [
            "READY",
            "VEHICLE_ASSIGNED",
        ]

    async def test_assign_rejected_before_ready(self, db_session, project, src_wh, dst_wh, barcode):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        with pytest.raises(ValueError, match="PENDING → VEHICLE_ASSIGNED запрещён"):
            await assign_vehicle_transfer(
                db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
            )

    async def test_close_rejected_from_shipped(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="SHIPPED → CLOSED запрещён"):
            await close_transfer(db_session, project.id, transfer.id)

    async def test_cancel_marks_status_and_soft_deletes(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await cancel_transfer(db_session, project.id, transfer.id)
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.CANCELLED
        assert transfer.is_deleted is True

    async def test_cancel_rejected_after_ship(self, db_session, project, src_wh, dst_wh, barcode):
        """После отгрузки отмены нет НИ в таблице, НИ в сервисе: сток уже уехал.

        Отмена его не возвращает — это делает только RETURNED. Раньше таблица
        переход разрешала, а рубил его лишь сервис; таблица подана как
        единственный источник истины, и первый же новый вызывающий, который ей
        доверится, отменил бы переезд поверх уехавшего товара.
        """
        assert TransferStatus.CANCELLED not in TRANSFER_TRANSITIONS[TransferStatus.SHIPPED]
        assert TransferStatus.CANCELLED not in TRANSFER_TRANSITIONS[TransferStatus.RETURNED]
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="не отменить"):
            await cancel_transfer(db_session, project.id, transfer.id)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Сток движется ровно дважды
# ═══════════════════════════════════════════════════════════════════════════


class TestStockMovesExactlyOnce:
    async def test_ladder_writes_off_once_and_books_once(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Ни одна ступень до SHIPPED и после DELIVERED сток не трогает."""
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)

        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        # До отгрузки — ноль проводок.
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == []
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        assert src.quantity == 100

        await send_transfer(db_session, project.id, transfer.id)
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == [-10]
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert (src.quantity, dst.quantity, dst.in_transit) == (90, 0, 10)

        await complete_transfer(db_session, project.id, transfer.id)
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_IN) == [10]
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert (dst.quantity, dst.in_transit) == (10, 0)

        # Закрытие сток не двигает.
        await close_transfer(db_session, project.id, transfer.id)
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == [-10]
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_IN) == [10]

    async def test_second_send_is_refused(self, db_session, project, src_wh, dst_wh, barcode):
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="SHIPPED → SHIPPED запрещён"):
            await send_transfer(db_session, project.id, transfer.id)
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == [-10]
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        assert src.quantity == 90

    async def test_complete_after_partial_fact_does_not_double_book(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """🔴 Ручное «Принять» поверх порционного авто-приёма ФФ.

        До статусной модели `complete_transfer` книжил ПЛАН целиком, не глядя на
        уже принятое: 4 единицы приходовались дважды, а транзит уходил в ноль по
        max(0, …) — расхождение не всплывало нигде.
        """
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)

        res = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 4})
        assert res["completed"] is False

        await complete_transfer(db_session, project.id, transfer.id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert (dst.quantity, dst.in_transit) == (10, 0)
        assert sum(await _movements(db_session, project, transfer.id, MovementType.TRANSFER_IN)) == 10

    async def test_full_fact_moves_to_delivered_with_history(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)

        res = await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 10})
        assert res["completed"] is True
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.DELIVERED
        assert ("SHIPPED", "DELIVERED", "ff_sync") in await _history(
            db_session, project, transfer.id
        )

    async def test_fact_rejected_before_ship(self, db_session, project, src_wh, dst_wh, barcode):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="только у отправленного"):
            await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 10})


# ═══════════════════════════════════════════════════════════════════════════
# 4. Возврат
# ═══════════════════════════════════════════════════════════════════════════


class TestReturnTransfer:
    async def test_return_from_shipped_restores_source_and_clears_transit(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(vehicle_info="А111АА77", pickup_cost=Decimal("9000.00")),
        )
        await send_transfer(db_session, project.id, transfer.id)

        returned = await return_transfer(db_session, project.id, transfer.id)
        assert returned.status == TransferStatus.RETURNED
        # Отгрузочная веха снята — следующая попытка проставит свою.
        assert returned.shipped_at is None

        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert src.quantity == 100  # вернулось ровно то, что уехало
        assert (dst.quantity, dst.in_transit) == (0, 0)

    async def test_return_creates_accepted_receipt_on_source(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        await return_transfer(db_session, project.id, transfer.id, comment="натали не приняла")

        receipt = (
            await db_session.execute(
                select(InboundReceipt)
                .where(
                    InboundReceipt.project_id == project.id,
                    InboundReceipt.warehouse_id == src_wh.id,
                    InboundReceipt.comment.like(f"%{transfer.number}%"),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        assert receipt is not None
        assert receipt.status == InboundStatus.ACCEPTED
        assert "натали не приняла" in receipt.comment

    async def test_return_from_delivered_takes_stock_back_off_destination(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """🔴 У переезда получатель — ТОЖЕ наш склад.

        Возврат из DELIVERED обязан снять уже зачисленное получателю, иначе те
        же единицы лягут разом на оба склада (у заявки такой проблемы нет —
        там на приёмной стороне маркетплейс).
        """
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        await complete_transfer(db_session, project.id, transfer.id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert dst.quantity == 10

        await return_transfer(db_session, project.id, transfer.id)
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert (src.quantity, dst.quantity, dst.in_transit) == (100, 0, 0)

    async def test_return_after_partial_fact_balances_both_warehouses(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 4})

        await return_transfer(db_session, project.id, transfer.id)
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert (src.quantity, dst.quantity, dst.in_transit) == (100, 0, 0)

    async def test_return_keeps_pickup_shipment(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Перевозка состоялась и оплачена — забор остаётся в истории и в оплатах."""
        from backend.models.warehouse import OutboundShipment

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session,
            project.id,
            transfer.id,
            TransferAssignVehicle(vehicle_info="А111АА77", pickup_cost=Decimal("9000.00")),
        )
        await send_transfer(db_session, project.id, transfer.id)
        await return_transfer(db_session, project.id, transfer.id)

        pickup = (
            await db_session.execute(
                select(OutboundShipment).where(
                    OutboundShipment.project_id == project.id,
                    OutboundShipment.stock_transfer_id == transfer.id,
                )
            )
        ).scalar_one()
        assert pickup.is_deleted is False
        assert pickup.pickup_cost == Decimal("9000.00")

    async def test_returned_can_be_reshipped_or_closed(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        await return_transfer(db_session, project.id, transfer.id)

        # Переотправка: RETURNED → READY разрешён таблицей.
        again = await mark_transfer_ready(db_session, project.id, transfer.id)
        assert again.status == TransferStatus.READY
        resent = await send_transfer(db_session, project.id, transfer.id)
        assert resent.status == TransferStatus.SHIPPED
        # И вторая отгрузка снова списала ровно 10 — не 20.
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        assert src.quantity == 90

    async def test_reship_after_return_credits_destination(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Переотправка после возврата ДОВОЗИТ товар получателю.

        Регрессия на баг «карта принятого не нетто»: она суммировала только
        приходные движения, поэтому после возврата навсегда помнила принятое в
        прошлом круге. Бюджет прихода считается как «план − принято», значит на
        втором круге он оказывался нулевым: сток списывался с источника и не
        зачислялся НИКОМУ — 10 единиц висели вечным фантомным транзитом, а
        переезд рапортовал DELIVERED и бейдж «принято 10 из 10» это подтверждал.
        """
        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        await complete_transfer(db_session, project.id, transfer.id)
        await return_transfer(db_session, project.id, transfer.id)

        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        done = await complete_transfer(db_session, project.id, transfer.id)

        assert done.status == TransferStatus.DELIVERED
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert src.quantity == 90, "источник списан ровно на один круг"
        assert dst.quantity == 10, "получателю довезли — товар не растворился"
        assert dst.in_transit == 0, "фантомного транзита не осталось"

    async def test_return_rejected_before_ship(self, db_session, project, src_wh, dst_wh, barcode):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        with pytest.raises(ValueError, match="READY → RETURNED запрещён"):
            await return_transfer(db_session, project.id, transfer.id)

    async def test_defect_transfer_returns_into_defect_bucket(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Возврат бракового переезда обязан лечь в БРАК, а не в годный сток.

        Иначе переезд «в ремонт», который не приняли, тихо отбелил бы 25 единиц
        брака в годный остаток — и они уехали бы следующей поставкой на WB.
        """
        from backend.services.warehouse_defect import mark_defect

        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        await mark_defect(db_session, project.id, src_wh.id, {"barcode": barcode, "quantity": 40})

        transfer = await _mk(
            db_session,
            project,
            src_wh,
            dst_wh,
            barcode,
            25,
            is_defect=True,
            defect_reason="в ремонт",
        )
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert (src.defect_quantity, dst.defect_in_transit) == (15, 25)

        await return_transfer(db_session, project.id, transfer.id)
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert src.defect_quantity == 40  # брак вернулся браком
        assert src.quantity == 60  # годный сток не тронут
        assert (dst.defect_in_transit, dst.defect_quantity) == (0, 0)

        receipt = (
            await db_session.execute(
                select(InboundReceipt)
                .where(
                    InboundReceipt.project_id == project.id,
                    InboundReceipt.warehouse_id == src_wh.id,
                    InboundReceipt.comment.like(f"%{transfer.number}%"),
                )
                .limit(1)
            )
        ).scalar_one()
        assert receipt.is_defect is True

    async def test_defect_transfer_return_from_delivered(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Тот же откат, но получатель уже оприходовал брак себе."""
        from backend.services.warehouse_defect import mark_defect

        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        await mark_defect(db_session, project.id, src_wh.id, {"barcode": barcode, "quantity": 40})

        transfer = await _mk(
            db_session, project, src_wh, dst_wh, barcode, 25, is_defect=True
        )
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)
        await complete_transfer(db_session, project.id, transfer.id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert dst.defect_quantity == 25

        await return_transfer(db_session, project.id, transfer.id)
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        dst = await _stock_row(db_session, project, dst_wh.id, nom_id)
        assert src.defect_quantity == 40
        assert (dst.defect_quantity, dst.defect_in_transit) == (0, 0)


# ═══════════════════════════════════════════════════════════════════════════
# 5. Правка — ровно в трёх статусах
# ═══════════════════════════════════════════════════════════════════════════


class TestEditableStatuses:
    @pytest.mark.parametrize(
        "status", [TransferStatus.PENDING, TransferStatus.IN_PROGRESS, TransferStatus.READY]
    )
    async def test_editable(self, db_session, project, src_wh, dst_wh, barcode, status):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await _force_status(db_session, transfer, status)
        updated = await update_transfer(
            db_session,
            project.id,
            transfer.id,
            StockTransferUpdate(comment="правка").model_dump(exclude_unset=True),
        )
        assert updated.comment == "правка"

    @pytest.mark.parametrize(
        "status",
        [
            TransferStatus.VEHICLE_ASSIGNED,
            TransferStatus.SHIPPED,
            TransferStatus.DELIVERED,
            TransferStatus.RETURNED,
            TransferStatus.CLOSED,
        ],
    )
    async def test_not_editable(self, db_session, project, src_wh, dst_wh, barcode, status):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await _force_status(db_session, transfer, status)
        with pytest.raises(ValueError, match="не правится"):
            await update_transfer(
                db_session,
                project.id,
                transfer.id,
                StockTransferUpdate(comment="поздно").model_dump(exclude_unset=True),
            )

    async def test_editable_set_matches_model(self):
        assert {
            TransferStatus.PENDING,
            TransferStatus.IN_PROGRESS,
            TransferStatus.READY,
        } == TRANSFER_EDITABLE_STATUSES


# ═══════════════════════════════════════════════════════════════════════════
# 6. Сигналы синка ФФ
# ═══════════════════════════════════════════════════════════════════════════


class TestFfSignals:
    async def test_auto_ready_on_assembled_signal(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.fulfillment_service import _mark_linked_transfers_ready

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        req = await _ff(db_session, project, src_wh, transfer_id=transfer.id, stage_code="ready")

        moved = await _mark_linked_transfers_ready(db_session, project.id, [req])
        await db_session.commit()
        assert moved == 1
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.READY
        assert transfer.actual_ready_date == date.today()
        assert ("PENDING", "READY", "ff_sync") in await _history(db_session, project, transfer.id)

    async def test_no_auto_ready_while_provider_still_assembling(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.fulfillment_service import _mark_linked_transfers_ready

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        req = await _ff(db_session, project, src_wh, transfer_id=transfer.id, stage_code="new")

        assert await _mark_linked_transfers_ready(db_session, project.id, [req]) == 0
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.PENDING

    async def test_auto_ready_waits_for_all_linked_requests(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Натали дробит один переезд на пару «короба + штучные»."""
        from backend.services.fulfillment_service import _mark_linked_transfers_ready

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        boxes = await _ff(db_session, project, src_wh, transfer_id=transfer.id, stage_code="ready")
        loose = await _ff(db_session, project, src_wh, transfer_id=transfer.id, stage_code="new")

        assert await _mark_linked_transfers_ready(db_session, project.id, [boxes, loose]) == 0
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.PENDING

        loose.stage_code = "ready"
        await db_session.commit()
        assert await _mark_linked_transfers_ready(db_session, project.id, [boxes, loose]) == 1
        await db_session.commit()
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.READY

    async def test_inbound_side_never_signals_ready(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Приёмочное зеркало получателя про сборку у источника ничего не говорит."""
        from backend.services.fulfillment_service import _mark_linked_transfers_ready

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        req = await _ff(
            db_session, project, dst_wh, kind="inbound", transfer_id=transfer.id, is_completed=True
        )
        assert await _mark_linked_transfers_ready(db_session, project.id, [req]) == 0

    async def test_auto_ship_only_from_vehicle_assigned(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.fulfillment_service import _collect_transfer_ship_candidates

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        req = await _ff(db_session, project, src_wh, transfer_id=transfer.id, is_completed=True)

        # READY без машины — переездом ещё никто не занимался, авто-отгрузки нет.
        await mark_transfer_ready(db_session, project.id, transfer.id)
        assert await _collect_transfer_ship_candidates(db_session, project.id, [req]) == []

        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        assert await _collect_transfer_ship_candidates(db_session, project.id, [req]) == [
            transfer.id
        ]

    async def test_auto_ship_waits_for_all_linked_requests(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.fulfillment_service import _collect_transfer_ship_candidates

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        done = await _ff(db_session, project, src_wh, transfer_id=transfer.id, is_completed=True)
        open_req = await _ff(db_session, project, src_wh, transfer_id=transfer.id)

        assert await _collect_transfer_ship_candidates(db_session, project.id, [done, open_req]) == []

        open_req.is_completed = True
        await db_session.commit()
        assert await _collect_transfer_ship_candidates(
            db_session, project.id, [done, open_req]
        ) == [transfer.id]

    async def test_cancelled_ff_request_does_not_ship(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Отменённая у провайдера заявка (archived без is_completed) — не сигнал."""
        from backend.services.fulfillment_service import _collect_transfer_ship_candidates

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        req = await _ff(db_session, project, src_wh, transfer_id=transfer.id)
        req.archived = True
        await db_session.commit()
        assert await _collect_transfer_ship_candidates(db_session, project.id, [req]) == []

    async def test_auto_ship_writes_off_stock_once(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Кандидат авто-шипа отгружается тем же `send_transfer` — списание одно."""
        from backend.services.fulfillment_service import _collect_transfer_ship_candidates

        await _stock(db_session, project, src_wh, barcode, 100)
        nom_id = await _nom_id(db_session, project, barcode)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await assign_vehicle_transfer(
            db_session, project.id, transfer.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        req = await _ff(db_session, project, src_wh, transfer_id=transfer.id, is_completed=True)

        for tid in await _collect_transfer_ship_candidates(db_session, project.id, [req]):
            await send_transfer(db_session, project.id, tid)

        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.SHIPPED
        assert await _movements(db_session, project, transfer.id, MovementType.TRANSFER_OUT) == [-10]
        src = await _stock_row(db_session, project, src_wh.id, nom_id)
        assert src.quantity == 90

    async def test_unlinked_transfer_is_untouched_by_sync(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Переезд без связки с ФФ синк не двигает вообще — им управляют кнопками."""
        from backend.services.fulfillment_service import (
            _collect_transfer_ship_candidates,
            _mark_linked_transfers_ready,
        )

        lonely = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        # Заявка провайдера того же склада, но НЕ привязанная к переезду.
        req = await _ff(db_session, project, src_wh, is_completed=True)

        assert await _mark_linked_transfers_ready(db_session, project.id, [req]) == 0
        assert await _collect_transfer_ship_candidates(db_session, project.id, [req]) == []
        await db_session.refresh(lonely)
        assert lonely.status == TransferStatus.PENDING

    async def test_link_moves_pending_transfer_to_ready(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Привязка уже собранной заявки не должна ждать следующего синка."""
        from backend.services.fulfillment_service import link_request

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        req = await _ff(db_session, project, src_wh, stage_code="ready")

        await link_request(
            db_session, project.id, req.id, stock_transfer_id=transfer.id, warehouse_id=src_wh.id
        )
        await db_session.refresh(transfer)
        assert transfer.status == TransferStatus.READY


# ═══════════════════════════════════════════════════════════════════════════
# 7. Конвертация заявки наследует готовность
# ═══════════════════════════════════════════════════════════════════════════


async def _assembly(db_session, project, warehouse, barcode, qty: int, status: str):
    nom_id = await _nom_id(db_session, project, barcode)
    req = AssemblyRequest(
        project_id=project.id,
        warehouse_id=warehouse.id,
        number=f"ASM-{_uid()}",
        status=status,
        pallets_count=2,
        pallet_weight_kg=Decimal("300.00"),
        actual_ready_date=date(2026, 7, 20),
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


class TestConvertInheritsReadiness:
    @pytest.mark.parametrize(
        "assembly_status",
        [
            AssemblyStatus.READY.value,
            AssemblyStatus.VEHICLE_ASSIGNED.value,
            AssemblyStatus.CLOSED.value,
            AssemblyStatus.RETURNED.value,
        ],
    )
    async def test_ready_assembly_makes_ready_transfer(
        self, db_session, project, src_wh, dst_wh, barcode, assembly_status
    ):
        """ФФ уже собрал груз — ступени «собирается → собран» гонять заново незачем."""
        assembly = await _assembly(db_session, project, src_wh, barcode, 10, assembly_status)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        transfer = await db_session.get(StockTransfer, result.transfer_id)
        assert transfer.status == TransferStatus.READY
        # Дата готовности — из заявки, а не «сегодня, когда нажали».
        assert transfer.actual_ready_date == date(2026, 7, 20)

    @pytest.mark.parametrize(
        "assembly_status", [AssemblyStatus.PENDING.value, AssemblyStatus.IN_PROGRESS.value]
    )
    async def test_unfinished_assembly_makes_pending_transfer(
        self, db_session, project, src_wh, dst_wh, barcode, assembly_status
    ):
        assembly = await _assembly(db_session, project, src_wh, barcode, 10, assembly_status)
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        transfer = await db_session.get(StockTransfer, result.transfer_id)
        assert transfer.status == TransferStatus.PENDING
        assert transfer.actual_ready_date is None

    async def test_converted_ready_transfer_can_be_sent_straight_away(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        """Кейс ASM-726/727: переделал в переезд — и сразу сажай на машину."""
        await _stock(db_session, project, src_wh, barcode, 100)
        assembly = await _assembly(
            db_session, project, src_wh, barcode, 10, AssemblyStatus.READY.value
        )
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assigned = await assign_vehicle_transfer(
            db_session, project.id, result.transfer_id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        assert assigned.status == TransferStatus.VEHICLE_ASSIGNED
        sent = await send_transfer(db_session, project.id, result.transfer_id)
        assert sent.status == TransferStatus.SHIPPED

    async def test_creation_is_logged_in_history(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        assembly = await _assembly(
            db_session, project, src_wh, barcode, 10, AssemblyStatus.READY.value
        )
        result = await convert_assembly_to_transfer(
            db_session, project.id, assembly.id, AssemblyToTransfer(to_warehouse_id=dst_wh.id)
        )
        assert (None, "READY", "system") in await _history(db_session, project, result.transfer_id)


# ═══════════════════════════════════════════════════════════════════════════
# 8. Список: срез Листа логиста и прогресс приёмки
# ═══════════════════════════════════════════════════════════════════════════


class TestListSlices:
    async def test_waiting_for_vehicle_slice(self, db_session, project, src_wh, dst_wh, barcode):
        """Основной рабочий запрос: `?status=READY&has_vehicle=false`."""
        from backend.services.warehouse_outbound import list_transfers

        waiting = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, waiting.id)
        busy = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, busy.id)
        await assign_vehicle_transfer(
            db_session, project.id, busy.id, TransferAssignVehicle(vehicle_info="А111АА77")
        )
        # Ещё не собранный в срез не попадает — машину на него назначать рано.
        await _mk(db_session, project, src_wh, dst_wh, barcode, 10)

        rows = await list_transfers(db_session, project.id, status="READY", has_vehicle=False)
        assert [t.id for t in rows] == [waiting.id]

    async def test_in_transit_slice_is_shipped_only(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.warehouse_outbound import list_transfers

        await _stock(db_session, project, src_wh, barcode, 100)
        moving = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, moving.id)
        await send_transfer(db_session, project.id, moving.id)
        arrived = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, arrived.id)
        await send_transfer(db_session, project.id, arrived.id)
        await complete_transfer(db_session, project.id, arrived.id)

        rows = await list_transfers(db_session, project.id, in_transit_only=True)
        assert [t.id for t in rows] == [moving.id]

    async def test_received_units_progress(self, db_session, project, src_wh, dst_wh, barcode):
        """«Принято X из Y» — X по журналу движений, Y это units_total."""
        from backend.services.warehouse_outbound import get_transfer, list_transfers

        await _stock(db_session, project, src_wh, barcode, 100)
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        await send_transfer(db_session, project.id, transfer.id)

        rows = await list_transfers(db_session, project.id)
        row = next(r for r in rows if r.id == transfer.id)
        assert (row.received_units, row.units_total) == (0, 10)

        await receive_transfer_fact(db_session, project.id, transfer.id, {barcode: 4})
        rows = await list_transfers(db_session, project.id)
        row = next(r for r in rows if r.id == transfer.id)
        assert (row.received_units, row.units_total) == (4, 10)
        # И в карточке — блок «принято X из Y» рисуется там же.
        card = await get_transfer(db_session, project.id, transfer.id)
        assert (card.received_units, card.units_total) == (4, 10)

    async def test_cancelled_transfer_leaves_the_list(
        self, db_session, project, src_wh, dst_wh, barcode
    ):
        from backend.services.warehouse_outbound import list_transfers

        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await cancel_transfer(db_session, project.id, transfer.id)
        rows = await list_transfers(db_session, project.id)
        assert transfer.id not in {t.id for t in rows}


# ═══════════════════════════════════════════════════════════════════════════
# 9. Изоляция проекта на новых ручках
# ═══════════════════════════════════════════════════════════════════════════


class TestProjectIsolation:
    @pytest.mark.parametrize("fn", [mark_transfer_ready, return_transfer, close_transfer])
    async def test_foreign_project_sees_nothing(
        self, db_session, project, other_project, src_wh, dst_wh, barcode, fn
    ):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        with pytest.raises(ValueError, match="не найдено"):
            await fn(db_session, other_project.id, transfer.id)

    async def test_history_is_scoped_to_project(
        self, db_session, project, other_project, src_wh, dst_wh, barcode
    ):
        transfer = await _mk(db_session, project, src_wh, dst_wh, barcode, 10)
        await mark_transfer_ready(db_session, project.id, transfer.id)
        rows = (
            await db_session.execute(
                select(func.count(StockTransferStatusHistory.id)).where(
                    StockTransferStatusHistory.project_id == other_project.id,
                    StockTransferStatusHistory.stock_transfer_id == transfer.id,
                )
            )
        ).scalar()
        assert rows == 0


# ═══════════════════════════════════════════════════════════════════════════
# 10. HTTP-контур новых ступеней
# ═══════════════════════════════════════════════════════════════════════════


async def _api_headers(client, auth_headers) -> dict:
    resp = await client.post(
        "/api/v1/projects", json={"name": f"TRS {_uid()}"}, headers=auth_headers
    )
    return {**auth_headers, "X-Project-Id": str(resp.json()["id"])}


class TestStatusRoutesOverHttp:
    """Провод схема → роутер → сервис для ступеней, которых раньше не было.

    Сервисные тесты выше зовут функции напрямую и не ловят ни опечатку в пути,
    ни несовпадение имени параметра тела, ни маршрут, съеденный `/{transfer_id}`.
    """

    async def _setup(self, client, db_session, headers):
        pid = int(headers["X-Project-Id"])
        wh_ids = []
        for name in (f"SRC-H-{_uid()}", f"DST-H-{_uid()}"):
            resp = await client.post(
                "/api/v1/warehouse",
                json={"name": name, "warehouse_type": "FULFILLMENT"},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            wh_ids.append(resp.json()["id"])
        bc = f"BC-H-{_uid()}"
        await db_session.execute(
            text(
                "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
                "VALUES (:pid, :bc, 'HTTP Item', NOW())"
            ),
            {"pid": pid, "bc": bc},
        )
        await db_session.commit()
        return pid, wh_ids, bc

    async def test_ladder_endpoints_round_trip(self, client, auth_headers, db_session):
        headers = await _api_headers(client, auth_headers)
        _pid, wh_ids, bc = await self._setup(client, db_session, headers)

        # Немного стока на источнике, иначе отгрузка упрётся в дефицит.
        resp = await client.post(
            f"/api/v1/warehouse/{wh_ids[0]}/receipts",
            json={"items": [{"barcode": bc, "expected_qty": 50, "actual_qty": 50}]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        receipt_id = resp.json()["id"]
        assert (
            await client.post(
                f"/api/v1/warehouse/receipts/{receipt_id}/accept", headers=headers
            )
        ).status_code == 200

        resp = await client.post(
            "/api/v1/warehouse/transfers",
            json={
                "from_warehouse_id": wh_ids[0],
                "to_warehouse_id": wh_ids[1],
                "items": [{"barcode": bc, "quantity": 10}],
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        tid = resp.json()["id"]
        assert resp.json()["status"] == "PENDING"

        # PENDING → SHIPPED напрямую нельзя: 400, а не 500 и не молчаливый успех.
        resp = await client.post(f"/api/v1/warehouse/transfers/{tid}/send", headers=headers)
        assert resp.status_code == 400, resp.text

        resp = await client.post(f"/api/v1/warehouse/transfers/{tid}/ready", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "READY"
        assert resp.json()["actual_ready_date"] is not None

        # READY без логистики — тоже 400: гейт живёт и на HTTP, а не только в
        # сервисе (иначе «Отправить» из Листа логиста увезло бы мимо оплат).
        resp = await client.post(f"/api/v1/warehouse/transfers/{tid}/send", headers=headers)
        assert resp.status_code == 400, resp.text
        # Единый конверт ошибок проекта (backend/exceptions.py), а НЕ дефолтный
        # FastAPI-«detail»: текст лежит в error.message, и фронт читает именно его.
        assert "Назначьте машину" in resp.json()["error"]["message"]

        resp = await client.post(
            f"/api/v1/warehouse/transfers/{tid}/send",
            json={"allow_no_logistics": True},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "SHIPPED"
        assert resp.json()["shipped_at"] is not None

        # Возврат — с телом-комментарием (тело опционально, но фронт его шлёт).
        resp = await client.post(
            f"/api/v1/warehouse/transfers/{tid}/return",
            json={"comment": "получатель не принял"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "RETURNED"

        resp = await client.post(
            f"/api/v1/warehouse/transfers/{tid}/close", json={}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "CLOSED"

    async def test_ready_on_missing_transfer_is_404(self, client, auth_headers):
        headers = await _api_headers(client, auth_headers)
        resp = await client.post("/api/v1/warehouse/transfers/99999999/ready", headers=headers)
        assert resp.status_code == 404, resp.text

    async def test_status_filter_whitelist(self, client, auth_headers):
        """Мусорный статус — честные 422, а не пустой список, читаемый как «нет переездов»."""
        headers = await _api_headers(client, auth_headers)
        assert (
            await client.get("/api/v1/warehouse/transfers?status=READY", headers=headers)
        ).status_code == 200
        # Статусы старой шкалы больше не существуют.
        for bad in ("DRAFT", "IN_TRANSIT", "COMPLETED", "МУСОР"):
            resp = await client.get(
                f"/api/v1/warehouse/transfers?status={bad}", headers=headers
            )
            assert resp.status_code == 422, f"{bad}: {resp.text}"
