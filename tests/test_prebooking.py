# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты предзаявки на моно (services/assembly/prebooking.py).

Создание заявок с флагом is_prebooking из строк предброни: группировка по
(ФФ-источник, WB-склад, упаковка), обычная валидация стока (товар реально на ФФ),
fail-fast (пусто / неизвестный ШК / превышение стока), изоляция по проекту.
"""
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.schemas.assembly import PrebookingCreate, PrebookingRow
from backend.services.assembly_service import create_prebooking
from backend.services.warehouse_crud import create_warehouse

WB_A = "Электросталь"
WB_B = "Тула"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class _Env:
    def __init__(self, pid: int, ff_wh_id: int, bc1: str, bc2: str):
        self.pid = pid
        self.ff_wh_id = ff_wh_id
        self.bc1 = bc1
        self.bc2 = bc2


async def _nomenclature(db: AsyncSession, pid: int, bc: str) -> int:
    r = await db.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
            "VALUES (:pid, :bc, :s, NOW()) RETURNING id"
        ),
        {"pid": pid, "bc": bc, "s": "Prebooking Item"},
    )
    return r.scalar()


async def _stock(db: AsyncSession, pid: int, wid: int, nid: int, bc: str, qty: int) -> None:
    await db.execute(
        text(
            "INSERT INTO warehouse_stock (project_id, warehouse_id, nomenclature_id, barcode, quantity, in_transit, updated_at) "
            "VALUES (:p, :w, :n, :b, :q, 0, NOW())"
        ),
        {"p": pid, "w": wid, "n": nid, "b": bc, "q": qty},
    )


@pytest_asyncio.fixture
async def env(db_session: AsyncSession, project) -> _Env:
    """ФФ-склад + 2 ШК + реальный сток (100 каждого) — обычная валидация проходит."""
    pid = project.id
    ff = await create_warehouse(db_session, pid, {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"})
    bc1, bc2 = f"PB-{_uid()}", f"PB-{_uid()}"
    n1 = await _nomenclature(db_session, pid, bc1)
    n2 = await _nomenclature(db_session, pid, bc2)
    await _stock(db_session, pid, ff.id, n1, bc1, 100)
    await _stock(db_session, pid, ff.id, n2, bc2, 100)
    await db_session.commit()
    return _Env(pid, ff.id, bc1, bc2)


@pytest.mark.asyncio
async def test_create_marks_prebooking_and_groups(db_session, env):
    """Две WB-цели → 2 заявки МОНО с is_prebooking=True; источник = ФФ; статус IN_PROGRESS."""
    result = await create_prebooking(
        db_session,
        env.pid,
        PrebookingCreate(
            rows=[
                PrebookingRow(warehouse_id=env.ff_wh_id, barcode=env.bc1, wb_warehouse_name=WB_A, qty=30),
                PrebookingRow(warehouse_id=env.ff_wh_id, barcode=env.bc2, wb_warehouse_name=WB_B, qty=20),
            ]
        ),
    )
    assert result.created == 2
    assert all(r.is_prebooking for r in result.requests)
    reqs = (
        (await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id.in_(result.request_ids))))
        .scalars()
        .all()
    )
    assert len(reqs) == 2
    for r in reqs:
        assert r.is_prebooking is True
        assert r.is_pre_distribution is False  # не путать с предраспределением машины
        assert r.status == AssemblyStatus.IN_PROGRESS
        assert r.warehouse_id == env.ff_wh_id
        assert r.package_type == "MONOPALLET"
        assert r.pallets_count == 0
    assert {r.wb_warehouse_name_manual for r in reqs} == {WB_A, WB_B}


@pytest.mark.asyncio
async def test_same_wb_same_ff_one_request(db_session, env):
    """Разные ШК на один WB-склад с одного ФФ → одна заявка (группа) с двумя позициями."""
    result = await create_prebooking(
        db_session,
        env.pid,
        PrebookingCreate(
            rows=[
                PrebookingRow(warehouse_id=env.ff_wh_id, barcode=env.bc1, wb_warehouse_name=WB_A, qty=10),
                PrebookingRow(warehouse_id=env.ff_wh_id, barcode=env.bc2, wb_warehouse_name=WB_A, qty=10),
            ]
        ),
    )
    assert result.created == 1
    req = (
        (await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == result.request_ids[0])))
        .scalars()
        .one()
    )
    assert req.is_prebooking is True
    assert len(result.requests[0].items) == 2


@pytest.mark.asyncio
async def test_empty_rows_raises(db_session, env):
    with pytest.raises(ValueError, match="Нет строк"):
        await create_prebooking(db_session, env.pid, PrebookingCreate(rows=[]))


@pytest.mark.asyncio
async def test_unknown_barcode_raises(db_session, env):
    """ШК не в номенклатуре → fail-fast ДО создания (частичных заявок не остаётся)."""
    with pytest.raises(ValueError, match="номенклатуре"):
        await create_prebooking(
            db_session,
            env.pid,
            PrebookingCreate(
                rows=[PrebookingRow(warehouse_id=env.ff_wh_id, barcode=f"NOPE-{_uid()}", wb_warehouse_name=WB_A, qty=5)]
            ),
        )


@pytest.mark.asyncio
async def test_over_stock_raises(db_session, env):
    """Запрошено больше стока ФФ (100) → обычная валидация стока падает (это не машина в пути)."""
    with pytest.raises(ValueError):
        await create_prebooking(
            db_session,
            env.pid,
            PrebookingCreate(
                rows=[PrebookingRow(warehouse_id=env.ff_wh_id, barcode=env.bc1, wb_warehouse_name=WB_A, qty=999)]
            ),
        )


@pytest.mark.asyncio
async def test_project_isolation(db_session, env, other_project):
    """ШК проекта A из-под проекта B не резолвится → fail-fast (изоляция)."""
    with pytest.raises(ValueError, match="номенклатуре"):
        await create_prebooking(
            db_session,
            other_project.id,
            PrebookingCreate(
                rows=[PrebookingRow(warehouse_id=env.ff_wh_id, barcode=env.bc1, wb_warehouse_name=WB_A, qty=5)]
            ),
        )
