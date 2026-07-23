"""
Tests for warehouse extra counterparties (несколько юр. лиц на один ФФ склад).

Covers: add_extra_counterparty / remove_extra_counterparty, идемпотентность,
populate в get/list, project-изоляцию, и что критичный ETL-UNION запрос
(fulfillment_inns) видит ИНН доп. контрагента.
"""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.counterparty import Counterparty
from backend.models.warehouse import WarehouseCounterparty
from backend.services.warehouse_crud import (
    add_extra_counterparty,
    create_warehouse,
    get_warehouse,
    list_warehouses,
    remove_extra_counterparty,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _inn() -> str:
    # 12-значный ИНН, уникальный на тест
    return str(uuid.uuid4().int)[:12]


async def _mk_wh(db, project_id):
    return await create_warehouse(
        db, project_id, {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"}
    )


class TestAddExtraCounterparty:
    @pytest.mark.asyncio
    async def test_add_creates_fulfillment_cp_and_link(self, db_session: AsyncSession, project):
        wh = await _mk_wh(db_session, project.id)
        inn = _inn()
        res = await add_extra_counterparty(
            db_session, project.id, wh.id, inn=inn, name="ИП Мащенок Никита Сергеевич"
        )
        assert res is not None
        # counterparty создан как FULFILLMENT
        cp = (
            await db_session.execute(
                select(Counterparty).where(
                    Counterparty.project_id == project.id, Counterparty.inn == inn
                )
            )
        ).scalar_one()
        assert cp.primary_type == "FULFILLMENT"
        assert cp.name == "ИП Мащенок Никита Сергеевич"
        # link создан
        links = (
            await db_session.execute(
                select(WarehouseCounterparty).where(
                    WarehouseCounterparty.warehouse_id == wh.id,
                    WarehouseCounterparty.counterparty_id == cp.id,
                )
            )
        ).scalars().all()
        assert len(links) == 1
        # populate в ответе
        assert any(e["inn"] == inn for e in res.extra_counterparties)

    @pytest.mark.asyncio
    async def test_add_is_idempotent(self, db_session: AsyncSession, project):
        wh = await _mk_wh(db_session, project.id)
        inn = _inn()
        await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="A")
        res = await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="A")
        links = (
            await db_session.execute(
                select(WarehouseCounterparty).where(WarehouseCounterparty.warehouse_id == wh.id)
            )
        ).scalars().all()
        assert len(links) == 1
        assert len([e for e in res.extra_counterparties if e["inn"] == inn]) == 1

    @pytest.mark.asyncio
    async def test_add_bumps_existing_other_to_fulfillment(self, db_session: AsyncSession, project):
        inn = _inn()
        # существующий контрагент типа OTHER
        cp = Counterparty(project_id=project.id, inn=inn, name="X", primary_type="OTHER")
        db_session.add(cp)
        await db_session.flush()
        wh = await _mk_wh(db_session, project.id)
        await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="X")
        await db_session.refresh(cp)
        assert cp.primary_type == "FULFILLMENT"

    @pytest.mark.asyncio
    async def test_add_missing_warehouse_returns_none(self, db_session: AsyncSession, project):
        res = await add_extra_counterparty(db_session, project.id, 999999, inn=_inn(), name="Y")
        assert res is None


class TestRemoveExtraCounterparty:
    @pytest.mark.asyncio
    async def test_remove_deletes_link_keeps_counterparty(self, db_session: AsyncSession, project):
        wh = await _mk_wh(db_session, project.id)
        inn = _inn()
        await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="Z")
        cp = (
            await db_session.execute(select(Counterparty).where(Counterparty.inn == inn))
        ).scalar_one()

        res = await remove_extra_counterparty(db_session, project.id, wh.id, cp.id)
        assert res is not None
        assert res.extra_counterparties == []
        # связь удалена
        links = (
            await db_session.execute(
                select(WarehouseCounterparty).where(WarehouseCounterparty.warehouse_id == wh.id)
            )
        ).scalars().all()
        assert links == []
        # сам контрагент жив
        still = (
            await db_session.execute(select(Counterparty).where(Counterparty.id == cp.id))
        ).scalar_one_or_none()
        assert still is not None


class TestPopulateAndIsolation:
    @pytest.mark.asyncio
    async def test_list_populates_extra(self, db_session: AsyncSession, project):
        wh = await _mk_wh(db_session, project.id)
        inn = _inn()
        await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="L")
        rows = await list_warehouses(db_session, project.id)
        target = next(r for r in rows if r.id == wh.id)
        assert any(e["inn"] == inn for e in target.extra_counterparties)

    @pytest.mark.asyncio
    async def test_get_empty_when_none(self, db_session: AsyncSession, project):
        wh = await _mk_wh(db_session, project.id)
        got = await get_warehouse(db_session, project.id, wh.id)
        assert got is not None
        assert got.extra_counterparties == []

    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        wh = await _mk_wh(db_session, project.id)
        inn = _inn()
        await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="Iso")
        # чужой проект не видит склад/связь
        other_rows = await list_warehouses(db_session, other_project.id)
        assert all(wh.id != r.id for r in other_rows)


class TestEtlUnionQuery:
    """Критично: сборщик fulfillment_inns должен видеть ИНН доп. контрагента."""

    _FULFILLMENT_INNS_SQL = text("""
        SELECT DISTINCT cp.inn
        FROM warehouses wh
        JOIN counterparty cp ON cp.id = wh.counterparty_id
        WHERE wh.project_id = :pid
          AND wh.is_deleted = false AND cp.is_deleted = false AND cp.inn IS NOT NULL
        UNION
        SELECT DISTINCT cp.inn
        FROM warehouse_counterparty link
        JOIN warehouses wh ON wh.id = link.warehouse_id
        JOIN counterparty cp ON cp.id = link.counterparty_id
        WHERE link.project_id = :pid
          AND wh.is_deleted = false AND cp.is_deleted = false AND cp.inn IS NOT NULL
    """)

    @pytest.mark.asyncio
    async def test_extra_inn_is_in_fulfillment_set(self, db_session: AsyncSession, project):
        wh = await _mk_wh(db_session, project.id)
        inn = _inn()
        await add_extra_counterparty(db_session, project.id, wh.id, inn=inn, name="Ff")
        rows = (await db_session.execute(self._FULFILLMENT_INNS_SQL, {"pid": project.id})).fetchall()
        inns = {str(r[0]).strip() for r in rows}
        assert inn in inns
