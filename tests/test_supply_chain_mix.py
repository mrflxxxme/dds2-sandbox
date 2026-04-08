"""
Tests for mix group functionality in supply_chain/factory_orders.py.

Covers: set_mix_group, remove_mix_group, delete_item (mix guard).
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.schemas.supply_chain import (
    FactoryOrderCreate,
    FactoryOrderItemCreate,
    MixGroupItemInput,
)
from backend.services.supply_chain.factory_orders import (
    create_factory_order,
    delete_item,
    get_factory_order,
    remove_mix_group,
    set_mix_group,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_order(db, project_id, n_items: int = 3):
    """Helper: create a factory order with n items (qty=10 each)."""
    data = FactoryOrderCreate(
        order_number=f"FO-MIX-{_uid()}",
        items=[
            FactoryOrderItemCreate(barcode=f"BC-{_uid()}", qty=10, price_cny=Decimal("5.00")) for _ in range(n_items)
        ],
    )
    return await create_factory_order(db, project_id, data)


def _mix_inputs(items, pcs_per_box: int = 3):
    """Build list of MixGroupItemInput from order items."""
    return [MixGroupItemInput(id=item.id, pcs_per_box=pcs_per_box) for item in items]


# ═══════════════════════════════════════════════════════════════════════════════
# set_mix_group — happy path
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetMixGroupHappyPath:
    @pytest.mark.asyncio
    async def test_set_mix_group_happy_path(self, db_session: AsyncSession, project):
        """All 3 items get mix_group_id, mix_box_size, and their own mix_pcs_per_box."""
        order = await _make_order(db_session, project.id, n_items=3)
        items = order.items

        mix_inputs = [
            MixGroupItemInput(id=items[0].id, pcs_per_box=2),
            MixGroupItemInput(id=items[1].id, pcs_per_box=4),
            MixGroupItemInput(id=items[2].id, pcs_per_box=6),
        ]
        mix_id, matched_ids = await set_mix_group(db_session, project.id, order.id, mix_inputs, box_size="60x40x30")

        assert isinstance(mix_id, str) and len(mix_id) == 36  # uuid4
        assert set(matched_ids) == {items[0].id, items[1].id, items[2].id}

        # Verify persisted state
        refreshed = await get_factory_order(db_session, project.id, order.id)
        by_id = {i.id: i for i in refreshed.items}

        assert by_id[items[0].id].mix_group_id == mix_id
        assert by_id[items[0].id].mix_box_size == "60x40x30"
        assert by_id[items[0].id].mix_pcs_per_box == 2

        assert by_id[items[1].id].mix_group_id == mix_id
        assert by_id[items[1].id].mix_box_size == "60x40x30"
        assert by_id[items[1].id].mix_pcs_per_box == 4

        assert by_id[items[2].id].mix_group_id == mix_id
        assert by_id[items[2].id].mix_box_size == "60x40x30"
        assert by_id[items[2].id].mix_pcs_per_box == 6


# ═══════════════════════════════════════════════════════════════════════════════
# set_mix_group — validation errors
# ═══════════════════════════════════════════════════════════════════════════════


class TestSetMixGroupValidation:
    @pytest.mark.asyncio
    async def test_min_two_items(self, db_session: AsyncSession, project):
        """Providing only 1 item raises ValueError."""
        order = await _make_order(db_session, project.id, n_items=2)
        single = [MixGroupItemInput(id=order.items[0].id, pcs_per_box=2)]

        with pytest.raises(ValueError, match="минимум 2"):
            await set_mix_group(db_session, project.id, order.id, single, box_size="50x30x20")

    @pytest.mark.asyncio
    async def test_empty_box_size_rejected(self, db_session: AsyncSession, project):
        """Empty box_size raises ValueError."""
        order = await _make_order(db_session, project.id, n_items=2)
        inputs = _mix_inputs(order.items[:2])

        with pytest.raises(ValueError, match="пустым"):
            await set_mix_group(db_session, project.id, order.id, inputs, box_size="")

    @pytest.mark.asyncio
    async def test_blocks_assigned_items(self, db_session: AsyncSession, project):
        """Item with assigned_qty > 0 cannot be added to a mix group."""
        order = await _make_order(db_session, project.id, n_items=2)
        # Directly set assigned_qty to simulate a distributed item
        order.items[0].assigned_qty = 5
        await db_session.commit()

        inputs = _mix_inputs(order.items[:2])
        with pytest.raises(ValueError, match="уже распределена"):
            await set_mix_group(db_session, project.id, order.id, inputs, box_size="50x30x20")

    @pytest.mark.asyncio
    async def test_blocks_already_mixed_item(self, db_session: AsyncSession, project):
        """Item already in a mix group cannot be added to another mix group."""
        order = await _make_order(db_session, project.id, n_items=3)

        # Create first mix with items[0] and items[1]
        first_inputs = _mix_inputs(order.items[:2])
        await set_mix_group(db_session, project.id, order.id, first_inputs, box_size="40x30x20")

        # Try to create second mix that includes items[0] (already mixed)
        second_inputs = [
            MixGroupItemInput(id=order.items[0].id, pcs_per_box=2),
            MixGroupItemInput(id=order.items[2].id, pcs_per_box=3),
        ]
        with pytest.raises(ValueError, match="уже входит в микс-группу"):
            await set_mix_group(db_session, project.id, order.id, second_inputs, box_size="50x40x25")

    @pytest.mark.asyncio
    async def test_zero_pcs_per_box_rejected(self, db_session: AsyncSession, project):
        """pcs_per_box == 0 raises ValueError."""
        order = await _make_order(db_session, project.id, n_items=2)
        inputs = [
            MixGroupItemInput(id=order.items[0].id, pcs_per_box=0),
            MixGroupItemInput(id=order.items[1].id, pcs_per_box=3),
        ]
        with pytest.raises(ValueError, match="больше 0"):
            await set_mix_group(db_session, project.id, order.id, inputs, box_size="50x30x20")


# ═══════════════════════════════════════════════════════════════════════════════
# remove_mix_group
# ═══════════════════════════════════════════════════════════════════════════════


class TestRemoveMixGroup:
    @pytest.mark.asyncio
    async def test_remove_clears_all_fields(self, db_session: AsyncSession, project):
        """After remove_mix_group, all three mix fields are None on every item."""
        order = await _make_order(db_session, project.id, n_items=2)
        inputs = _mix_inputs(order.items[:2], pcs_per_box=5)
        mix_id, _ = await set_mix_group(db_session, project.id, order.id, inputs, box_size="70x50x40")

        count = await remove_mix_group(db_session, project.id, order.id, mix_id)
        assert count == 2

        refreshed = await get_factory_order(db_session, project.id, order.id)
        for item in refreshed.items:
            assert item.mix_group_id is None
            assert item.mix_box_size is None
            assert item.mix_pcs_per_box is None

    @pytest.mark.asyncio
    async def test_remove_nonexistent_raises(self, db_session: AsyncSession, project):
        """Removing a mix group that doesn't exist raises ValueError."""
        order = await _make_order(db_session, project.id, n_items=2)
        with pytest.raises(ValueError, match="не найдена"):
            await remove_mix_group(db_session, project.id, order.id, str(uuid.uuid4()))


# ═══════════════════════════════════════════════════════════════════════════════
# delete_item — mix group guard
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteItemMixGuard:
    @pytest.mark.asyncio
    async def test_delete_blocked_when_in_mix(self, db_session: AsyncSession, project):
        """Cannot delete an item that belongs to a mix group."""
        order = await _make_order(db_session, project.id, n_items=2)
        inputs = _mix_inputs(order.items[:2])
        await set_mix_group(db_session, project.id, order.id, inputs, box_size="40x30x20")

        with pytest.raises(ValueError, match="микс-группу"):
            await delete_item(db_session, project.id, order.id, order.items[0].id)

    @pytest.mark.asyncio
    async def test_delete_allowed_after_mix_removed(self, db_session: AsyncSession, project):
        """After removing the mix, the item can be deleted normally."""
        order = await _make_order(db_session, project.id, n_items=2)
        inputs = _mix_inputs(order.items[:2])
        mix_id, _ = await set_mix_group(db_session, project.id, order.id, inputs, box_size="40x30x20")
        await remove_mix_group(db_session, project.id, order.id, mix_id)

        result = await delete_item(db_session, project.id, order.id, order.items[0].id)
        assert result is True
