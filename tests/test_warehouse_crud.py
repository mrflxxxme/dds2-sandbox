"""
Tests for warehouse_crud.py — Warehouse CRUD + delivery times.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.warehouse_crud import (
    create_warehouse,
    delete_warehouse,
    get_warehouse,
    list_warehouses,
    reorder_warehouses,
    update_warehouse,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ═══════════════════════════════════════════════════════════════════════════════
# create_warehouse
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateWarehouse:
    @pytest.mark.asyncio
    async def test_create_warehouse_happy_path(self, db_session: AsyncSession, project):
        wh = await create_warehouse(
            db_session,
            project.id,
            {
                "name": f"WH-{_uid()}",
                "warehouse_type": "EXTERNAL",
                "country": "RU",
                "address": "Moscow, test st.",
                "assembly_days": 3,
                "sort_order": 1,
            },
        )
        assert wh.id is not None
        assert wh.project_id == project.id
        assert wh.warehouse_type == "EXTERNAL"
        assert wh.assembly_days == 3
        assert wh.sort_order == 1
        assert wh.is_deleted is False

    @pytest.mark.asyncio
    async def test_create_warehouse_defaults(self, db_session: AsyncSession, project):
        wh = await create_warehouse(
            db_session,
            project.id,
            {"name": f"WH-{_uid()}", "warehouse_type": "FULFILLMENT"},
        )
        assert wh.sort_order == 0
        assert wh.country is None
        assert wh.address is None
        assert wh.assembly_days is None


# ═══════════════════════════════════════════════════════════════════════════════
# get_warehouse
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetWarehouse:
    @pytest.mark.asyncio
    async def test_get_warehouse_found(self, db_session: AsyncSession, project):
        wh = await create_warehouse(
            db_session,
            project.id,
            {"name": f"WH-{_uid()}", "warehouse_type": "EXTERNAL"},
        )
        found = await get_warehouse(db_session, project.id, wh.id)
        assert found is not None
        assert found.id == wh.id

    @pytest.mark.asyncio
    async def test_get_warehouse_not_found(self, db_session: AsyncSession, project):
        found = await get_warehouse(db_session, project.id, 999999)
        assert found is None

    @pytest.mark.asyncio
    async def test_get_warehouse_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Warehouse from one project must not be accessible from another."""
        wh = await create_warehouse(
            db_session,
            project.id,
            {"name": f"WH-{_uid()}", "warehouse_type": "EXTERNAL"},
        )
        found = await get_warehouse(db_session, other_project.id, wh.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_get_warehouse_soft_deleted_not_found(self, db_session: AsyncSession, project):
        wh = await create_warehouse(
            db_session,
            project.id,
            {"name": f"WH-{_uid()}", "warehouse_type": "EXTERNAL"},
        )
        await delete_warehouse(db_session, project.id, wh.id)
        found = await get_warehouse(db_session, project.id, wh.id)
        assert found is None


# ═══════════════════════════════════════════════════════════════════════════════
# list_warehouses
# ═══════════════════════════════════════════════════════════════════════════════


class TestListWarehouses:
    @pytest.mark.asyncio
    async def test_list_empty(self, db_session: AsyncSession, project):
        whs = await list_warehouses(db_session, project.id)
        assert isinstance(whs, list)

    @pytest.mark.asyncio
    async def test_list_ordered_by_sort_order(self, db_session: AsyncSession, project):
        await create_warehouse(db_session, project.id, {"name": "B", "warehouse_type": "EXTERNAL", "sort_order": 2})
        await create_warehouse(db_session, project.id, {"name": "A", "warehouse_type": "EXTERNAL", "sort_order": 1})
        whs = await list_warehouses(db_session, project.id)
        names = [w.name for w in whs]
        assert names.index("A") < names.index("B")

    @pytest.mark.asyncio
    async def test_list_excludes_soft_deleted(self, db_session: AsyncSession, project):
        wh = await create_warehouse(db_session, project.id, {"name": f"DEL-{_uid()}", "warehouse_type": "EXTERNAL"})
        await delete_warehouse(db_session, project.id, wh.id)
        whs = await list_warehouses(db_session, project.id)
        ids = [w.id for w in whs]
        assert wh.id not in ids

    @pytest.mark.asyncio
    async def test_list_project_isolation(self, db_session: AsyncSession, project, other_project):
        await create_warehouse(db_session, project.id, {"name": f"P1-{_uid()}", "warehouse_type": "EXTERNAL"})
        whs_other = await list_warehouses(db_session, other_project.id)
        ids = [w.id for w in whs_other]
        # None of project's warehouses should appear in other_project
        p1_whs = await list_warehouses(db_session, project.id)
        for w in p1_whs:
            assert w.id not in ids


# ═══════════════════════════════════════════════════════════════════════════════
# update_warehouse
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateWarehouse:
    @pytest.mark.asyncio
    async def test_update_name(self, db_session: AsyncSession, project):
        wh = await create_warehouse(db_session, project.id, {"name": "Old", "warehouse_type": "EXTERNAL"})
        updated = await update_warehouse(db_session, project.id, wh.id, {"name": "New"})
        assert updated is not None
        assert updated.name == "New"

    @pytest.mark.asyncio
    async def test_update_not_found(self, db_session: AsyncSession, project):
        result = await update_warehouse(db_session, project.id, 999999, {"name": "X"})
        assert result is None

    @pytest.mark.asyncio
    async def test_update_cannot_change_project_id(self, db_session: AsyncSession, project):
        wh = await create_warehouse(db_session, project.id, {"name": "Prot", "warehouse_type": "EXTERNAL"})
        updated = await update_warehouse(db_session, project.id, wh.id, {"project_id": 99999})
        assert updated.project_id == project.id


# ═══════════════════════════════════════════════════════════════════════════════
# delete_warehouse (soft delete)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeleteWarehouse:
    @pytest.mark.asyncio
    async def test_soft_delete(self, db_session: AsyncSession, project):
        wh = await create_warehouse(db_session, project.id, {"name": f"D-{_uid()}", "warehouse_type": "EXTERNAL"})
        result = await delete_warehouse(db_session, project.id, wh.id)
        assert result is True
        found = await get_warehouse(db_session, project.id, wh.id)
        assert found is None

    @pytest.mark.asyncio
    async def test_delete_not_found(self, db_session: AsyncSession, project):
        result = await delete_warehouse(db_session, project.id, 999999)
        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
# reorder_warehouses
# ═══════════════════════════════════════════════════════════════════════════════


class TestReorderWarehouses:
    @pytest.mark.asyncio
    async def test_reorder(self, db_session: AsyncSession, project):
        wh1 = await create_warehouse(
            db_session, project.id, {"name": "R1", "warehouse_type": "EXTERNAL", "sort_order": 1}
        )
        wh2 = await create_warehouse(
            db_session, project.id, {"name": "R2", "warehouse_type": "EXTERNAL", "sort_order": 2}
        )
        result = await reorder_warehouses(
            db_session, project.id, [{"id": wh1.id, "sort_order": 10}, {"id": wh2.id, "sort_order": 5}]
        )
        assert result is True
        whs = await list_warehouses(db_session, project.id)
        wh_map = {w.id: w for w in whs}
        if wh1.id in wh_map:
            assert wh_map[wh1.id].sort_order == 10
        if wh2.id in wh_map:
            assert wh_map[wh2.id].sort_order == 5
