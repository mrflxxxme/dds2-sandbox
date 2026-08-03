# ruff: noqa: RUF001, RUF002, RUF003
"""
Учётное зеркало сборки FBS (`AssemblyRequest.kind = fbs`).

Джоб `wb_fbs.assembly_mirror.sync_fbs_assembly_mirror` зеркалит поставки FBS
(`WB-GI-…`) складов с `auto_assembly=true` учётными заявками на сборку.

ЖЕЛЕЗНЫЕ ИНВАРИАНТЫ (нарушение = двойной учёт денег/остатков):
  • зеркало НЕ резервирует сток (резерв держат открытые FBS-задания);
  • зеркало НЕ списывает сток и НЕ создаёт OutboundShipment НИКОГДА;
  • статусы двигаются только вперёд (IN_PROGRESS → SHIPPED → DELIVERED),
    CANCELLED терминален;
  • состав пересобирается до SHIPPED, после — заморожен;
  • ручные переходы по kind=fbs запрещены (422).
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import (
    AssemblyKind,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.warehouse import OutboundShipment, StockMovement, Warehouse
from backend.models.wb_fbs import WbFbsOrder, WbFbsSupply, WbFbsWarehouse, WbFbsWarehouseLink
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, create_receipt
from backend.services.warehouse_stock_engine import get_warehouse_stock
from backend.services.wb_fbs.assembly_mirror import (
    MIRROR_CHANGED_BY,
    MIRROR_WINDOW_DAYS,
    sync_fbs_assembly_mirror,
)
from backend.utils.time import utcnow


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ─── Фикстуры ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def wh(db_session: AsyncSession, project):
    return await create_warehouse(
        db_session,
        project.id,
        {"name": f"FBSMIR-{_uid()}", "warehouse_type": "FULFILLMENT"},
    )


@pytest_asyncio.fixture
async def nom(db_session: AsyncSession, project):
    barcode = f"FBSM-{_uid()}"
    chrt_id = int(uuid.uuid4().int % 1_000_000_000)
    row = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, chrt_id, updated_at) "
            "VALUES (:pid, :bc, :subj, :chrt, NOW()) RETURNING id"
        ),
        {"pid": project.id, "bc": barcode, "subj": "Ковёр зеркальный", "chrt": chrt_id},
    )
    nom_id = row.scalar_one()
    await db_session.commit()
    return SimpleNamespace(id=nom_id, barcode=barcode, chrt_id=chrt_id)


@pytest_asyncio.fixture
async def nom2(db_session: AsyncSession, project):
    barcode = f"FBSM2-{_uid()}"
    row = await db_session.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
            "VALUES (:pid, :bc, :subj, NOW()) RETURNING id"
        ),
        {"pid": project.id, "bc": barcode, "subj": "Плед зеркальный"},
    )
    nom_id = row.scalar_one()
    await db_session.commit()
    return SimpleNamespace(id=nom_id, barcode=barcode)


async def _mk_fbs_wh(
    db_session, project_id: int, warehouse_id: int, *, auto_assembly: bool = True
) -> int:
    """Склад продавца WB + активная привязка к нашему складу."""
    wb_warehouse_id = int(uuid.uuid4().int % 900_000) + 100_000
    db_session.add(
        WbFbsWarehouse(
            project_id=project_id,
            wb_warehouse_id=wb_warehouse_id,
            name=f"WB-FBS-{_uid()}",
            is_active=True,
            auto_assembly=auto_assembly,
        )
    )
    db_session.add(
        WbFbsWarehouseLink(
            project_id=project_id,
            wb_warehouse_id=wb_warehouse_id,
            warehouse_id=warehouse_id,
            is_active=True,
        )
    )
    await db_session.commit()
    return wb_warehouse_id


async def _mk_supply(
    db_session,
    project_id: int,
    *,
    done: bool = False,
    scan_dt=None,
    closed_at=None,
    reject_dt=None,
    created_at_wb=None,
    raw: dict | None = None,
) -> str:
    wb_supply_id = f"WB-GI-{uuid.uuid4().int % 10**9}"
    db_session.add(
        WbFbsSupply(
            project_id=project_id,
            wb_supply_id=wb_supply_id,
            done=done,
            scan_dt=scan_dt,
            closed_at=closed_at,
            reject_dt=reject_dt,
            created_at_wb=created_at_wb or utcnow(),
            raw=raw,
        )
    )
    await db_session.commit()
    return wb_supply_id


async def _mk_order(
    db_session,
    project_id: int,
    wb_warehouse_id: int,
    supply_id: str,
    nom,
    *,
    supplier_status: str = "confirm",
    wb_status: str | None = None,
    raw: dict | None = None,
) -> int:
    wb_order_id = int(uuid.uuid4().int % 9_000_000_000_000) + 1
    db_session.add(
        WbFbsOrder(
            project_id=project_id,
            wb_order_id=wb_order_id,
            wb_warehouse_id=wb_warehouse_id,
            supply_id=supply_id,
            nomenclature_id=nom.id if nom else None,
            barcode=nom.barcode if nom else None,
            supplier_status=supplier_status,
            wb_status=wb_status,
            raw=raw,
        )
    )
    await db_session.commit()
    return wb_order_id


async def _mirror_of(db_session, project_id: int, supply_id: str) -> AssemblyRequest | None:
    res = await db_session.execute(
        select(AssemblyRequest).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.fbs_supply_id == supply_id,
        )
    )
    return res.scalar_one_or_none()


async def _refresh(db_session, req: AssemblyRequest) -> AssemblyRequest:
    await db_session.refresh(req)
    return req


async def _stock_side_effects(db_session, project_id: int) -> tuple[int, int]:
    """(движений стока, отгрузок) проекта — у зеркала обязано быть (0, 0)."""
    movements = (
        await db_session.execute(
            select(func.count(StockMovement.id)).where(StockMovement.project_id == project_id)
        )
    ).scalar_one()
    shipments = (
        await db_session.execute(
            select(func.count(OutboundShipment.id)).where(OutboundShipment.project_id == project_id)
        )
    ).scalar_one()
    return int(movements), int(shipments)


async def _receive(db_session, project_id: int, warehouse_id: int, barcode: str, qty: int) -> None:
    receipt = await create_receipt(
        db_session,
        project_id,
        warehouse_id,
        {"items": [{"barcode": barcode, "expected_qty": qty, "actual_qty": qty}]},
    )
    await accept_receipt(db_session, project_id, receipt.id)
    await db_session.commit()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Создание зеркала
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMirrorCreation:
    async def test_creates_mirror_from_supply_with_orders(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        mutations = await sync_fbs_assembly_mirror(db_session, project.id)
        assert mutations == 1

        req = await _mirror_of(db_session, project.id, supply_id)
        assert req is not None
        assert req.kind == AssemblyKind.FBS.value
        assert req.status == AssemblyStatus.IN_PROGRESS.value
        assert req.warehouse_id == wh.id, "склад заявки = первый привязанный живой"
        assert req.number.startswith("ASM-")

        items = (
            await db_session.execute(
                select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id)
            )
        ).scalars().all()
        assert len(items) == 1, "агрегат по номенклатуре"
        assert items[0].nomenclature_id == nom.id
        assert items[0].quantity == 2, "одно задание WB = одна единица"
        assert items[0].barcode == nom.barcode, "snapshot barcode из задания"

        history = (
            await db_session.execute(
                select(AssemblyStatusHistory).where(
                    AssemblyStatusHistory.assembly_request_id == req.id
                )
            )
        ).scalars().all()
        assert len(history) == 1
        assert history[0].changed_by == MIRROR_CHANGED_BY
        assert history[0].new_status == AssemblyStatus.IN_PROGRESS.value

        assert await _stock_side_effects(db_session, project.id) == (0, 0)

    async def test_idempotent_two_ticks(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        assert await sync_fbs_assembly_mirror(db_session, project.id) == 0

        count = (
            await db_session.execute(
                select(func.count(AssemblyRequest.id)).where(
                    AssemblyRequest.project_id == project.id,
                    AssemblyRequest.fbs_supply_id == supply_id,
                )
            )
        ).scalar_one()
        assert count == 1, "два тика — одна заявка (partial unique)"

    async def test_auto_assembly_false_noop(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id, auto_assembly=False)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 0
        assert await _mirror_of(db_session, project.id, supply_id) is None

    async def test_sandbox_contour_ignored(self, db_session, project, wh, nom):
        """Песочные поставка и задания не рождают боевое зеркало."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        sandbox_raw = {"_dds_contour": "sandbox"}
        supply_id = await _mk_supply(db_session, project.id, raw=sandbox_raw)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom, raw=sandbox_raw)

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 0
        assert await _mirror_of(db_session, project.id, supply_id) is None

    async def test_orders_without_card_are_skipped(self, db_session, project, wh, nom):
        """Задания без карточки не попадают в позиции; поставка только из них — без зеркала."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)
        await _mk_order(db_session, project.id, wb_wh, supply_id, None)  # без карточки

        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req is not None
        items = (
            await db_session.execute(
                select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id)
            )
        ).scalars().all()
        assert len(items) == 1 and items[0].quantity == 1

        # Поставка ТОЛЬКО из бескарточных заданий зеркала не рождает.
        supply2 = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply2, None)
        await sync_fbs_assembly_mirror(db_session, project.id)
        assert await _mirror_of(db_session, project.id, supply2) is None

    async def test_old_supply_outside_window_not_mirrored(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        old = utcnow() - timedelta(days=MIRROR_WINDOW_DAYS + 1)
        supply_id = await _mk_supply(db_session, project.id, created_at_wb=old)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 0
        assert await _mirror_of(db_session, project.id, supply_id) is None

    async def test_project_isolation(self, db_session, project, other_project, wh, nom):
        """Зеркало живёт в своём проекте; чужой проект без целевых складов — no-op."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        assert await sync_fbs_assembly_mirror(db_session, other_project.id) == 0
        assert await _mirror_of(db_session, project.id, supply_id) is None, (
            "чужой прогон не создаёт зеркал в нашем проекте"
        )

        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req is not None and req.project_id == project.id


# ═══════════════════════════════════════════════════════════════════════════
# 2. Статусная цепочка (только вперёд) + ноль движений стока
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMirrorStatusChain:
    async def test_forward_chain_and_zero_stock_effects(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        order_id = await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req.status == AssemblyStatus.IN_PROGRESS.value

        # Передана: done=true + closed_at → SHIPPED (без ship_request/отгрузки/стока).
        closed = utcnow()
        supply = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_id,
                )
            )
        ).scalar_one()
        supply.done = True
        supply.closed_at = closed
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req = await _refresh(db_session, req)
        assert req.status == AssemblyStatus.SHIPPED.value
        assert req.shipped_at is not None
        assert req.outbound_shipment_id is None, "SHIPPED зеркала БЕЗ OutboundShipment"

        # Все живые задания прошли СЦ → DELIVERED.
        order = (
            await db_session.execute(
                select(WbFbsOrder).where(
                    WbFbsOrder.project_id == project.id, WbFbsOrder.wb_order_id == order_id
                )
            )
        ).scalar_one()
        order.supplier_status = "complete"
        order.wb_status = "sold"
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req = await _refresh(db_session, req)
        assert req.status == AssemblyStatus.DELIVERED.value

        # Назад никогда: даже если поставка «откатилась» в done=false.
        supply.done = False
        await db_session.commit()
        assert await sync_fbs_assembly_mirror(db_session, project.id) == 0
        req = await _refresh(db_session, req)
        assert req.status == AssemblyStatus.DELIVERED.value

        # На всём пути зеркало не тронуло ни сток, ни отгрузки.
        assert await _stock_side_effects(db_session, project.id) == (0, 0)

        history = (
            await db_session.execute(
                select(AssemblyStatusHistory)
                .where(AssemblyStatusHistory.assembly_request_id == req.id)
                .order_by(AssemblyStatusHistory.changed_at)
            )
        ).scalars().all()
        assert [h.new_status for h in history] == [
            AssemblyStatus.IN_PROGRESS.value,
            AssemblyStatus.SHIPPED.value,
            AssemblyStatus.DELIVERED.value,
        ]
        assert all(h.changed_by == MIRROR_CHANGED_BY for h in history)

    async def test_reject_cancels_mirror(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)
        supply = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_id,
                )
            )
        ).scalar_one()
        supply.reject_dt = utcnow()
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req.status == AssemblyStatus.CANCELLED.value
        assert await _stock_side_effects(db_session, project.id) == (0, 0)

    async def test_emptied_supply_cancels_mirror(self, db_session, project, wh, nom):
        """Поставку ОПУСТОШИЛИ (задания перенесли) — зеркало гасим.

        Прод 02.08.2026: WMS перенёс задания из WB-GI-260717413 в соседнюю
        поставку. Задания не отменены — они просто больше не принадлежат этой
        поставке, и список приходит пустым. Прежняя ветка отмены смотрела на
        `все задания отменены` и пустой список не ловила: ASM-1158 и ASM-1172
        остались висеть в «На сборке» с нулём заданий.
        """
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        order_id = await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)
        assert (await _mirror_of(db_session, project.id, supply_id)).status == (
            AssemblyStatus.IN_PROGRESS.value
        )

        # Задание уехало в другую поставку + WB подтвердил, что здесь пусто.
        order = (
            await db_session.execute(
                select(WbFbsOrder).where(
                    WbFbsOrder.project_id == project.id, WbFbsOrder.wb_order_id == order_id
                )
            )
        ).scalar_one()
        order.supply_id = "WB-GI-ELSEWHERE"
        supply = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_id,
                )
            )
        ).scalar_one()
        supply.raw = {**(supply.raw or {}), "_dds_wb_orders": 0}
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req.status == AssemblyStatus.CANCELLED.value
        assert await _stock_side_effects(db_session, project.id) == (0, 0)

    async def test_unchecked_empty_supply_is_not_cancelled(self, db_session, project, wh, nom):
        """Пусто У НАС ≠ пусто у WB: без подтверждения состав не гасим.

        Свежая поставка, чьи задания ещё не синкнулись, тоже отдаёт пустой
        список. Отменить её значило бы убить живое зеркало по таймингу синка,
        поэтому гейт — ТОЛЬКО подтверждённый ноль (`_dds_wb_orders == 0`).
        """
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        order_id = await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)

        order = (
            await db_session.execute(
                select(WbFbsOrder).where(
                    WbFbsOrder.project_id == project.id, WbFbsOrder.wb_order_id == order_id
                )
            )
        ).scalar_one()
        order.supply_id = "WB-GI-ELSEWHERE"
        await db_session.commit()  # маркер НЕ ставим — состав не проверен

        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req.status == AssemblyStatus.IN_PROGRESS.value

    async def test_all_orders_cancelled_cancels_mirror(self, db_session, project, wh, nom):
        """Effective cancel: WB-отмена при supplierStatus=new тоже гасит зеркало."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        order_id = await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)
        order = (
            await db_session.execute(
                select(WbFbsOrder).where(
                    WbFbsOrder.project_id == project.id, WbFbsOrder.wb_order_id == order_id
                )
            )
        ).scalar_one()
        order.wb_status = "declined_by_client"  # supplier_status остаётся confirm/new
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req.status == AssemblyStatus.CANCELLED.value

    async def test_two_supplies_advance_independently(self, db_session, project, wh, nom, nom2):
        """Каждое зеркало двигается по СВОЕЙ поставке (не по соседней)."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_a = await _mk_supply(db_session, project.id)
        supply_b = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_a, nom)
        await _mk_order(db_session, project.id, wb_wh, supply_b, nom2)

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 2

        # Передана только A; B остаётся активной.
        sup_a = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_a,
                )
            )
        ).scalar_one()
        sup_a.done = True
        sup_a.closed_at = utcnow()
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req_a = await _mirror_of(db_session, project.id, supply_a)
        req_b = await _mirror_of(db_session, project.id, supply_b)
        assert req_a.status == AssemblyStatus.SHIPPED.value
        assert req_b.status == AssemblyStatus.IN_PROGRESS.value, (
            "зеркало B не должно двигаться по фазе поставки A"
        )

    async def test_statuses_catch_up_outside_creation_window(self, db_session, project, wh, nom):
        """Поставка выпала из окна создания — статус УЖЕ созданного зеркала догоняет."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)
        await sync_fbs_assembly_mirror(db_session, project.id)

        supply = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_id,
                )
            )
        ).scalar_one()
        supply.created_at_wb = utcnow() - timedelta(days=MIRROR_WINDOW_DAYS + 10)
        supply.done = True
        supply.scan_dt = utcnow()
        await db_session.commit()

        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req.status == AssemblyStatus.SHIPPED.value


# ═══════════════════════════════════════════════════════════════════════════
# 3. Пересборка состава: до SHIPPED — живая, после — заморожена
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMirrorComposition:
    async def test_resync_until_shipped_then_frozen(self, db_session, project, wh, nom, nom2):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)

        # Доложили задание другой номенклатуры до передачи → состав пересобран.
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom2)
        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1
        items = (
            await db_session.execute(
                select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id)
            )
        ).scalars().all()
        assert {i.nomenclature_id: i.quantity for i in items} == {nom.id: 1, nom2.id: 1}

        # Передали поставку → SHIPPED; дальнейшие изменения состав не трогают.
        supply = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_id,
                )
            )
        ).scalar_one()
        supply.done = True
        supply.closed_at = utcnow()
        await db_session.commit()
        await sync_fbs_assembly_mirror(db_session, project.id)

        await _mk_order(db_session, project.id, wb_wh, supply_id, nom2)
        await sync_fbs_assembly_mirror(db_session, project.id)
        items = (
            await db_session.execute(
                select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == req.id)
            )
        ).scalars().all()
        assert {i.nomenclature_id: i.quantity for i in items} == {nom.id: 1, nom2.id: 1}, (
            "после SHIPPED состав заморожен"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Инвариант резерва: kind=fbs НЕ уменьшает available
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMirrorHoldsNoReserve:
    async def test_mirror_does_not_double_reserve(self, db_session, project, wh, nom):
        """Резерв держат открытые задания; зеркало НЕ вычитается вторым разом.

        stock=50, 2 открытых задания → available = 48 (только fbs_open),
        а не 46 (fbs_open + позиции зеркала).
        """
        await _receive(db_session, project.id, wh.id, nom.barcode, 50)
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)
        assert req is not None and req.status == AssemblyStatus.IN_PROGRESS.value

        stock = await get_warehouse_stock(db_session, project.id, wh.id)
        row = next(r for r in stock if r["nomenclature_id"] == nom.id)
        assert row["reserved"] == 0, "kind=fbs в резерв заявок не входит"
        assert row["available"] == 48, "вычет один — открытые FBS-задания"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Настройки склада: auto_assembly проходит PATCH и не попадает под 409-гейт
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestAutoAssemblySetting:
    async def test_update_settings_passthrough_and_no_mirror_gate(
        self, db_session, project, wh, nom
    ):
        """auto_assembly сохраняется setattr-циклом и НЕ гейтится 409
        «translate + ff_mirror» даже при зеркале ФФ выше учёта."""
        from backend.models.fulfillment import FulfillmentStock
        from backend.schemas.wb_fbs import FbsWarehouseSettingsUpdate
        from backend.services.wb_fbs.warehouse_service import update_settings

        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        row = (
            await db_session.execute(
                select(WbFbsWarehouse).where(
                    WbFbsWarehouse.project_id == project.id,
                    WbFbsWarehouse.wb_warehouse_id == wb_wh,
                )
            )
        ).scalar_one()
        # Рискованная тройка уже включена + зеркало ФФ выше учёта (ledger пуст).
        row.mode = "translate"
        row.stock_source = "ff_mirror"
        row.auto_assembly = False
        db_session.add(
            FulfillmentStock(
                project_id=project.id,
                warehouse_id=wh.id,
                provider="migfull",
                barcode=nom.barcode,
                nomenclature_id=nom.id,
                qty_good=5,
                units_per_box=1,
            )
        )
        await db_session.commit()

        result = await update_settings(
            db_session, project.id, wb_wh, FbsWarehouseSettingsUpdate(auto_assembly=True)
        )
        assert result["auto_assembly"] is True, "setattr-цикл подхватывает поле"
        await db_session.refresh(row)
        assert row.auto_assembly is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. Ручные переходы запрещены (422)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestManualTransitionsDenied:
    async def test_service_layer_raises(self, db_session, project, wh, nom):
        from backend.services.assembly.status import (
            FbsMirrorManualError,
            cancel_request,
            mark_ready,
            ship_request,
            start_assembly,
        )

        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)
        await sync_fbs_assembly_mirror(db_session, project.id)
        req = await _mirror_of(db_session, project.id, supply_id)

        for fn in (start_assembly, mark_ready, ship_request, cancel_request):
            with pytest.raises(FbsMirrorManualError):
                await fn(db_session, project.id, req.id)

    async def test_api_returns_422(self, client, auth_headers, db_session):
        resp = await client.post(
            "/api/v1/projects", json={"name": f"FBS Mirror {_uid()}"}, headers=auth_headers
        )
        assert resp.status_code in (200, 201), resp.text
        headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}
        pid = int(resp.json()["id"])

        warehouse = Warehouse(project_id=pid, name=f"FBSMIR-API-{_uid()}", warehouse_type="FULFILLMENT")
        db_session.add(warehouse)
        await db_session.flush()
        req = AssemblyRequest(
            project_id=pid,
            warehouse_id=warehouse.id,
            number=f"ASM-FBSAPI-{_uid()}",
            status=AssemblyStatus.IN_PROGRESS.value,
            kind=AssemblyKind.FBS.value,
            fbs_supply_id=f"WB-GI-{uuid.uuid4().int % 10**9}",
            pallets_count=0,
            pallet_weight_kg=0,
        )
        db_session.add(req)
        await db_session.commit()

        for path in ("ready", "cancel", "ship", "start"):
            resp = await client.post(
                f"/api/v1/warehouse/assembly/{req.id}/{path}", headers=headers
            )
            assert resp.status_code == 422, f"{path}: {resp.status_code} {resp.text}"
            # Единый конверт ошибок приложения: {"error": {code, message, …}}.
            assert "FBS" in resp.json()["error"]["message"]

    async def test_list_kind_filter(self, client, auth_headers, db_session):
        """GET списка: kind=fbs отдаёт только зеркала, мусорный kind — 422."""
        resp = await client.post(
            "/api/v1/projects", json={"name": f"FBS Kind {_uid()}"}, headers=auth_headers
        )
        headers = {**auth_headers, "X-Project-Id": str(resp.json()["id"])}
        pid = int(resp.json()["id"])

        warehouse = Warehouse(project_id=pid, name=f"FBSMIR-LIST-{_uid()}", warehouse_type="FULFILLMENT")
        db_session.add(warehouse)
        await db_session.flush()
        fbs_supply = f"WB-GI-{uuid.uuid4().int % 10**9}"
        db_session.add(
            AssemblyRequest(
                project_id=pid,
                warehouse_id=warehouse.id,
                number="ASM-1",
                status=AssemblyStatus.IN_PROGRESS.value,
                kind=AssemblyKind.FBS.value,
                fbs_supply_id=fbs_supply,
                pallets_count=0,
                pallet_weight_kg=0,
            )
        )
        db_session.add(
            AssemblyRequest(
                project_id=pid,
                warehouse_id=warehouse.id,
                number="ASM-2",
                status=AssemblyStatus.IN_PROGRESS.value,
                pallets_count=1,
                pallet_weight_kg=10,
            )
        )
        await db_session.commit()

        resp = await client.get("/api/v1/warehouse/assembly?kind=fbs", headers=headers)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["kind"] == "fbs"
        assert data["items"][0]["fbs_supply_id"] == fbs_supply

        resp = await client.get("/api/v1/warehouse/assembly?kind=fbo", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["items"][0]["kind"] == "fbo"

        resp = await client.get("/api/v1/warehouse/assembly", headers=headers)
        assert resp.json()["total"] == 2

        resp = await client.get("/api/v1/warehouse/assembly?kind=bogus", headers=headers)
        assert resp.status_code == 422
        message = resp.json()["error"]["message"]
        assert "fbo" in message and "fbs" in message


class TestReviewFixInvariants:
    """Фиксы ревью 30.07: sandbox-гейт, merge-гейт, фантомный транзит."""

    async def test_sandbox_contour_early_exit(self, db_session, project, wh, nom, monkeypatch):
        """В песочнице зеркало не работает ВООБЩЕ: у заявок нет метки контура,
        и sandbox-поставка рождала бы РЕАЛЬНУЮ заявку в общем реестре."""
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)

        from backend.services.wb_fbs import assembly_mirror as mirror_mod

        monkeypatch.setattr(mirror_mod, "is_sandbox_contour", lambda: True)
        assert await sync_fbs_assembly_mirror(db_session, project.id) == 0
        assert await _mirror_of(db_session, project.id, supply_id) is None

        # Возврат в боевой контур — зеркало создаётся штатно.
        monkeypatch.setattr(mirror_mod, "is_sandbox_contour", lambda: False)
        assert await sync_fbs_assembly_mirror(db_session, project.id) == 1

    async def test_merge_rejects_fbs_mirror(self, db_session, project, wh, nom):
        """merge_assembly_requests — путь ко ВТОРОМУ списанию: позиции зеркала
        в обычной сборке получили бы резерв и ship-списание. Гейт в сервисе."""
        from backend.services.assembly.crud import merge_assembly_requests

        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom)
        await sync_fbs_assembly_mirror(db_session, project.id)
        mirror = await _mirror_of(db_session, project.id, supply_id)
        assert mirror is not None

        plain = AssemblyRequest(
            project_id=project.id,
            warehouse_id=wh.id,
            number=f"ASM-PLAIN-{_uid()}",
            status=AssemblyStatus.IN_PROGRESS.value,
            pallets_count=1,
            pallet_weight_kg=10,
        )
        db_session.add(plain)
        await db_session.commit()

        with pytest.raises(ValueError, match="автоматически"):
            await merge_assembly_requests(db_session, project.id, [mirror.id, plain.id])

    async def test_shipped_mirror_not_in_transit_capital(self, db_session, project, wh, nom):
        """SHIPPED-зеркало — не транзит: его единицы уже списаны по complete и
        уехали покупателю. Без kind-фильтра фантом оседал в «Итого — капитал»."""
        from backend.services.warehouse_stock_engine import get_unified_stock_summary

        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        supply_id = await _mk_supply(db_session, project.id, done=True)
        await _mk_order(db_session, project.id, wb_wh, supply_id, nom, supplier_status="complete")
        await sync_fbs_assembly_mirror(db_session, project.id)
        mirror = await _mirror_of(db_session, project.id, supply_id)
        assert mirror is not None
        assert mirror.status == AssemblyStatus.SHIPPED.value

        rows = await get_unified_stock_summary(db_session, project.id)
        transit = {r.get("barcode"): r.get("in_transit", 0) for r in rows}
        assert transit.get(nom.barcode, 0) == 0, "зеркало не должно давать in_transit"


class TestSupplyPhaseWithoutScan:
    """WB не отдал scanDt (СЦ принял без скана QR) — фаза выводится из заданий."""

    async def test_delivered_mirror_lifts_to_ship_to_in_delivery(self, db_session, project, wh, nom):
        wb_wh = await _mk_fbs_wh(db_session, project.id, wh.id)
        # done=true, scan_dt пуст: тройка сказала бы «Отгрузите поставку».
        supply_id = await _mk_supply(db_session, project.id, done=True)
        await _mk_order(
            db_session, project.id, wb_wh, supply_id, nom,
            supplier_status="complete", wb_status="sold",
        )
        await sync_fbs_assembly_mirror(db_session, project.id)
        mirror = await _mirror_of(db_session, project.id, supply_id)
        assert mirror is not None
        assert mirror.status == AssemblyStatus.DELIVERED.value

        from backend.services.assembly.crud import _fbs_supply_status_value

        sup = (
            await db_session.execute(
                select(WbFbsSupply).where(
                    WbFbsSupply.project_id == project.id,
                    WbFbsSupply.wb_supply_id == supply_id,
                )
            )
        ).scalar_one()
        # Живой кейс WB-GI-258027541: задания прошли СЦ при пустом scanDt.
        assert _fbs_supply_status_value(sup, mirror.status) == "in_delivery"
        # Не-доставленное зеркало фазу не поднимает — тройка честна как есть.
        assert _fbs_supply_status_value(sup, AssemblyStatus.SHIPPED.value) == "to_ship"
