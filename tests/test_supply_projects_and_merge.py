"""
Tests for the supply-chain orders UX feature:
- SupplyProject CRUD + assignment to factory orders (with tenant isolation)
- manual archive flag on factory orders
- merging factory orders (duplicate-barcode quantity merge + cost-link re-point)
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import CostOrderItem
from backend.models.supply_chain import FactoryOrder
from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    FactoryOrderUpdate,
    MergeOrdersRequest,
    SplitItem,
    SplitToVehiclesRequest,
    SupplyProjectCreate,
    SupplyProjectUpdate,
)
from backend.services.supply_chain.factory_orders import (
    create_factory_order,
    get_factory_order,
    get_factory_orders,
    merge_factory_orders,
    set_mix_group,
    set_order_archived,
    split_to_vehicles,
    update_factory_order,
)
from backend.services.supply_chain.supply_projects import (
    create_supply_project,
    delete_supply_project,
    get_supply_project,
    list_supply_projects,
    update_supply_project,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_order(db, project_id, *, items=None, number=None) -> FactoryOrder:
    data = FactoryOrderCreate(order_number=number or f"FO-{_uid()}", items=items)
    return await create_factory_order(db, project_id, data)


# ═══════════════════════════════════════════════════════════════════════════════
# SupplyProject CRUD
# ═══════════════════════════════════════════════════════════════════════════════


class TestSupplyProjectCrud:
    @pytest.mark.asyncio
    async def test_create_and_get(self, db_session: AsyncSession, project):
        sp = await create_supply_project(
            db_session, project.id, SupplyProjectCreate(name="Весна 2026", color="#0071e3")
        )
        assert sp.id is not None
        assert sp.project_id == project.id
        assert sp.name == "Весна 2026"
        fetched = await get_supply_project(db_session, project.id, sp.id)
        assert fetched is not None and fetched.id == sp.id

    @pytest.mark.asyncio
    async def test_list_with_orders_count(self, db_session: AsyncSession, project):
        sp = await create_supply_project(db_session, project.id, SupplyProjectCreate(name=f"P-{_uid()}"))
        o1 = await _make_order(db_session, project.id)
        await _make_order(db_session, project.id)  # not bound
        await update_factory_order(db_session, project.id, o1.id, FactoryOrderUpdate(supply_project_id=sp.id))

        projects = await list_supply_projects(db_session, project.id)
        target = next(p for p in projects if p.id == sp.id)
        assert target.orders_count == 1

    @pytest.mark.asyncio
    async def test_update(self, db_session: AsyncSession, project):
        sp = await create_supply_project(db_session, project.id, SupplyProjectCreate(name="old"))
        updated = await update_supply_project(db_session, project.id, sp.id, SupplyProjectUpdate(name="new"))
        assert updated is not None and updated.name == "new"

    @pytest.mark.asyncio
    async def test_delete_unbinds_orders(self, db_session: AsyncSession, project):
        sp = await create_supply_project(db_session, project.id, SupplyProjectCreate(name=f"P-{_uid()}"))
        order = await _make_order(db_session, project.id)
        await update_factory_order(db_session, project.id, order.id, FactoryOrderUpdate(supply_project_id=sp.id))

        ok = await delete_supply_project(db_session, project.id, sp.id)
        assert ok is True

        refreshed = await get_factory_order(db_session, project.id, order.id)
        assert refreshed is not None
        assert refreshed.supply_project_id is None  # unbound on delete
        assert await get_supply_project(db_session, project.id, sp.id) is None

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, db_session: AsyncSession, project, other_project):
        sp = await create_supply_project(db_session, other_project.id, SupplyProjectCreate(name="theirs"))
        # Not visible from `project`
        assert await get_supply_project(db_session, project.id, sp.id) is None
        ids = {p.id for p in await list_supply_projects(db_session, project.id)}
        assert sp.id not in ids


class TestAssignProjectToOrder:
    @pytest.mark.asyncio
    async def test_assign_and_clear(self, db_session: AsyncSession, project):
        sp = await create_supply_project(db_session, project.id, SupplyProjectCreate(name=f"P-{_uid()}"))
        order = await _make_order(db_session, project.id)

        updated = await update_factory_order(
            db_session, project.id, order.id, FactoryOrderUpdate(supply_project_id=sp.id)
        )
        assert updated is not None and updated.supply_project_id == sp.id
        assert updated.supply_project is not None and updated.supply_project.name == sp.name

        cleared = await update_factory_order(
            db_session, project.id, order.id, FactoryOrderUpdate(supply_project_id=None)
        )
        assert cleared is not None and cleared.supply_project_id is None

    @pytest.mark.asyncio
    async def test_assign_foreign_project_rejected(self, db_session: AsyncSession, project, other_project):
        foreign = await create_supply_project(db_session, other_project.id, SupplyProjectCreate(name="theirs"))
        order = await _make_order(db_session, project.id)
        with pytest.raises(ValueError, match="not found"):
            await update_factory_order(
                db_session, project.id, order.id, FactoryOrderUpdate(supply_project_id=foreign.id)
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Archive
# ═══════════════════════════════════════════════════════════════════════════════


class TestArchive:
    @pytest.mark.asyncio
    async def test_archive_and_unarchive(self, db_session: AsyncSession, project):
        order = await _make_order(db_session, project.id)
        assert order.is_archived is False

        archived = await set_order_archived(db_session, project.id, order.id, True)
        assert archived is not None and archived.is_archived is True

        unarchived = await set_order_archived(db_session, project.id, order.id, False)
        assert unarchived is not None and unarchived.is_archived is False

    @pytest.mark.asyncio
    async def test_archive_idempotent(self, db_session: AsyncSession, project):
        order = await _make_order(db_session, project.id)
        await set_order_archived(db_session, project.id, order.id, True)
        again = await set_order_archived(db_session, project.id, order.id, True)
        assert again is not None and again.is_archived is True

    @pytest.mark.asyncio
    async def test_archive_missing_order(self, db_session: AsyncSession, project):
        assert await set_order_archived(db_session, project.id, 999_999, True) is None

    @pytest.mark.asyncio
    async def test_archived_still_listed_with_flag(self, db_session: AsyncSession, project):
        # The list endpoint returns archived orders too (the UI filters them);
        # the flag must be present so the frontend can hide them.
        order = await _make_order(db_session, project.id)
        await set_order_archived(db_session, project.id, order.id, True)
        orders = await get_factory_orders(db_session, project.id)
        match = next(o for o in orders if o.id == order.id)
        assert match.is_archived is True


# ═══════════════════════════════════════════════════════════════════════════════
# Merge
# ═══════════════════════════════════════════════════════════════════════════════


class TestMerge:
    @pytest.mark.asyncio
    async def test_merge_distinct_barcodes_reparents(self, db_session: AsyncSession, project):
        target = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="A", qty=10, price_cny=Decimal("1"))]
        )
        source = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="B", qty=20, price_cny=Decimal("2"))]
        )
        result = await merge_factory_orders(db_session, project.id, target.id, [source.id])
        assert result["merged_orders"] == 1
        assert result["items_moved"] == 1
        assert result["items_merged"] == 0

        merged_target = await get_factory_order(db_session, project.id, target.id)
        assert merged_target is not None
        barcodes = sorted(i.barcode for i in merged_target.items)
        assert barcodes == ["A", "B"]
        # source soft-deleted
        assert await get_factory_order(db_session, project.id, source.id) is None

    @pytest.mark.asyncio
    async def test_merge_duplicate_barcode_sums_qty(self, db_session: AsyncSession, project):
        target = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="DUP", qty=10, price_cny=Decimal("5"))]
        )
        source = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="DUP", qty=7, price_cny=Decimal("5"))]
        )
        result = await merge_factory_orders(db_session, project.id, target.id, [source.id])
        assert result["items_merged"] == 1
        assert result["items_moved"] == 0

        merged_target = await get_factory_order(db_session, project.id, target.id)
        assert merged_target is not None
        active = [i for i in merged_target.items]
        assert len(active) == 1
        assert active[0].barcode == "DUP"
        assert active[0].qty == 17  # 10 + 7

    @pytest.mark.asyncio
    async def test_merge_repoints_distributed_cost_items(self, db_session: AsyncSession, project):
        # target has barcode X (undistributed), source has barcode X split to a vehicle.
        target = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="X", qty=100, price_cny=Decimal("3"))]
        )
        source = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="X", qty=50, price_cny=Decimal("3"))]
        )
        src_item_id = source.items[0].id
        await split_to_vehicles(
            db_session,
            project.id,
            source.id,
            SplitToVehiclesRequest(
                assignments=[SplitItem(factory_order_item_id=src_item_id, qty=30, vehicle_order_no=f"V-{_uid()}")]
            ),
        )

        target_item_id = target.items[0].id
        result = await merge_factory_orders(db_session, project.id, target.id, [source.id])
        assert result["items_merged"] == 1

        merged_target = await get_factory_order(db_session, project.id, target.id)
        survivor = next(i for i in merged_target.items if i.barcode == "X")
        assert survivor.id == target_item_id
        assert survivor.qty == 150  # 100 + 50
        assert survivor.assigned_qty == 30  # carried over from the source's distribution

        # The CostOrderItem must now point to the survivor, not the soft-deleted source item.
        rows = (
            (
                await db_session.execute(
                    select(CostOrderItem.factory_order_item_id).where(
                        CostOrderItem.project_id == project.id,
                        CostOrderItem.barcode == "X",
                        CostOrderItem.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        assert rows, "expected at least one cost item"
        assert all(fid == target_item_id for fid in rows)

    @pytest.mark.asyncio
    async def test_merge_mix_item_not_quantity_merged(self, db_session: AsyncSession, project):
        # source's duplicate-barcode item is in a mix group → must be re-parented, not summed.
        target = await _make_order(
            db_session, project.id, items=[FactoryOrderItemCreate(barcode="M", qty=10, price_cny=Decimal("1"))]
        )
        source = await _make_order(
            db_session,
            project.id,
            items=[
                FactoryOrderItemCreate(barcode="M", qty=5, price_cny=Decimal("1"), pcs_per_box=5),
                FactoryOrderItemCreate(barcode="N", qty=8, price_cny=Decimal("1"), pcs_per_box=8),
            ],
        )
        await set_mix_group(
            db_session,
            project.id,
            source.id,
            [
                _MixIn(source.items[0].id, 5),
                _MixIn(source.items[1].id, 8),
            ],
            "40x30x20",
        )
        result = await merge_factory_orders(db_session, project.id, target.id, [source.id])
        # both mix items re-parented (no quantity merge), zero merged
        assert result["items_merged"] == 0
        assert result["items_moved"] == 2

        merged_target = await get_factory_order(db_session, project.id, target.id)
        m_items = [i for i in merged_target.items if i.barcode == "M"]
        assert len(m_items) == 2  # original target line + re-parented mix line (kept separate)

    @pytest.mark.asyncio
    async def test_merge_missing_source_raises(self, db_session: AsyncSession, project):
        target = await _make_order(db_session, project.id)
        with pytest.raises(ValueError, match="не найдены"):
            await merge_factory_orders(db_session, project.id, target.id, [999_999])

    @pytest.mark.asyncio
    async def test_merge_tenant_isolation(self, db_session: AsyncSession, project, other_project):
        target = await _make_order(db_session, project.id)
        foreign = await _make_order(db_session, other_project.id)
        with pytest.raises(ValueError, match="не найдены"):
            await merge_factory_orders(db_session, project.id, target.id, [foreign.id])


class TestMergeRequestValidation:
    def test_target_in_sources_rejected(self):
        with pytest.raises(ValueError, match="must not be in"):
            MergeOrdersRequest(target_id=1, source_ids=[1, 2])

    def test_empty_sources_rejected(self):
        with pytest.raises(ValueError, match="must not be empty"):
            MergeOrdersRequest(target_id=1, source_ids=[])

    def test_duplicate_sources_rejected(self):
        with pytest.raises(ValueError, match="unique"):
            MergeOrdersRequest(target_id=1, source_ids=[2, 2])


class _MixIn:
    """Lightweight stand-in for the mix-group input object (.id + .pcs_per_box)."""

    def __init__(self, item_id: int, pcs_per_box: int):
        self.id = item_id
        self.pcs_per_box = pcs_per_box
