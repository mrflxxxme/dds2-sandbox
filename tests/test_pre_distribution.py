# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты предраспределения машины в пути (services/assembly/pre_distribution.py).

Проверяет хребет фичи: пул (net-математика), создание заявок PRE_DISTRIBUTED БЕЗ
реального стока, отсутствие фейк-резерва ФФ-стока (C1), over-commit guard (H3),
хард-блок без ФФ-склада назначения (M3), авто-перевод на разгрузке (H1) и ручной
фолбэк (H2). См. .claude/PREDIST_DESIGN.md.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.cost import CostOrder, CostOrderItem
from backend.models.enums import VehicleStatus
from backend.schemas.assembly import PreDistributionCreate, PreDistRow
from backend.schemas.assembly import AssemblyItemCreate, AssemblyRequestCreate
from backend.services.assembly.crud import create_assembly_request
from backend.services.assembly.pre_distribution import PRE_DIST_DELIVERED_WINDOW_DAYS
from backend.services.assembly_service import (
    _advance_pre_distribution_assemblies,
    advance_pre_distribution_manual,
    create_pre_distribution,
    get_pre_distribution_vehicles,
    get_vehicle_pre_dist_pool,
    merge_assembly_requests,
)
from backend.services.warehouse_crud import create_warehouse
from backend.services.warehouse_inbound import accept_receipt, accept_receipt_ff, create_receipt
from backend.services.warehouse_stock_engine import _get_reserved_map

WB_A = "Электросталь"
WB_B = "Коледино"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class _Env:
    def __init__(self, pid: int, ff_wh_id: int, bc1: str, bc2: str, nom1: int, nom2: int, vehicle: CostOrder):
        self.pid = pid
        self.ff_wh_id = ff_wh_id
        self.bc1 = bc1
        self.bc2 = bc2
        self.nom1 = nom1
        self.nom2 = nom2
        self.vehicle = vehicle


async def _make_nomenclature(db: AsyncSession, pid: int, barcode: str) -> int:
    result = await db.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, subject, updated_at) "
            "VALUES (:pid, :bc, :subj, NOW()) RETURNING id"
        ),
        {"pid": pid, "bc": barcode, "subj": "PreDist Item"},
    )
    return result.scalar()


@pytest_asyncio.fixture
async def env(db_session: AsyncSession, project) -> _Env:
    """ФФ-склад разгрузки + 2 ШК + машина CUSTOMS (BC1×100, BC2×50). Стока НЕТ намеренно."""
    pid = project.id
    ff_wh = await create_warehouse(db_session, pid, {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"})
    bc1, bc2 = f"PD-{_uid()}", f"PD-{_uid()}"
    nom1 = await _make_nomenclature(db_session, pid, bc1)
    nom2 = await _make_nomenclature(db_session, pid, bc2)

    vehicle = CostOrder(
        project_id=pid,
        order_no=f"PRDIST-{_uid()}",
        status=VehicleStatus.CUSTOMS,
        target_warehouse_id=ff_wh.id,
    )
    db_session.add(vehicle)
    await db_session.flush()
    db_session.add_all(
        [
            CostOrderItem(project_id=pid, order_no=vehicle.order_no, barcode=bc1, qty=100),
            CostOrderItem(project_id=pid, order_no=vehicle.order_no, barcode=bc2, qty=50),
        ]
    )
    await db_session.commit()
    return _Env(pid, ff_wh.id, bc1, bc2, nom1, nom2, vehicle)


# ─── Пул (net-математика) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pool_net_math(db_session, env):
    pool = await get_vehicle_pre_dist_pool(db_session, env.pid, env.vehicle.id)
    assert pool.vehicle.total_qty == 150
    assert pool.vehicle.sku_count == 2
    assert pool.vehicle.can_distribute is True
    by_bc = {r.barcode: r for r in pool.rows}
    assert by_bc[env.bc1].gross_qty == 100
    assert by_bc[env.bc1].available_qty == 100
    assert by_bc[env.bc1].distributed_qty == 0

    # После раскладки 30 шт BC1 — доступно 70, разнесено 30.
    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=30)]),
    )
    pool2 = await get_vehicle_pre_dist_pool(db_session, env.pid, env.vehicle.id)
    by_bc2 = {r.barcode: r for r in pool2.rows}
    assert by_bc2[env.bc1].distributed_qty == 30
    assert by_bc2[env.bc1].available_qty == 70


@pytest.mark.asyncio
async def test_pool_returns_machine_box_qty(db_session, project):
    """[B] Кратность/габарит короба берутся ИЗ САМОЙ машины (строки cost_order), qty-weighted
    mode — едущая машина ещё не в справочнике принятых приёмок, но её кратность уже видна."""
    pid = project.id
    ff_wh = await create_warehouse(db_session, pid, {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"})
    bc_k = f"PD-{_uid()}"  # с кратностью
    bc_none = f"PD-{_uid()}"  # без кратности
    await _make_nomenclature(db_session, pid, bc_k)
    await _make_nomenclature(db_session, pid, bc_none)
    vehicle = CostOrder(
        project_id=pid, order_no=f"PRDIST-{_uid()}", status=VehicleStatus.CUSTOMS, target_warehouse_id=ff_wh.id
    )
    db_session.add(vehicle)
    await db_session.flush()
    db_session.add_all(
        [
            # bc_k: две строки — ppb=6 (qty 100) доминирует над ppb=10 (qty 20) по qty-weighted mode.
            CostOrderItem(
                project_id=pid, order_no=vehicle.order_no, barcode=bc_k, qty=100,
                pcs_per_box_override=6, box_size_override="60x40x40",
            ),
            CostOrderItem(
                project_id=pid, order_no=vehicle.order_no, barcode=bc_k, qty=20,
                pcs_per_box_override=10, box_size_override="30x20x20",
            ),
            # bc_none: без override и без FOI → кратности нет.
            CostOrderItem(project_id=pid, order_no=vehicle.order_no, barcode=bc_none, qty=50),
        ]
    )
    await db_session.commit()

    pool = await get_vehicle_pre_dist_pool(db_session, pid, vehicle.id)
    by_bc = {r.barcode: r for r in pool.rows}
    assert by_bc[bc_k].box_qty == 6  # mode: ppb с наибольшей суммарной qty
    assert by_bc[bc_k].box_size == "60x40x40"  # габарит спарен с выбранной кратностью
    assert by_bc[bc_none].box_qty is None
    assert by_bc[bc_none].box_size is None


@pytest.mark.asyncio
async def test_pool_box_qty_tie_break_deterministic(db_session, project):
    """При точной ничьей Σqty двух кратностей выбираем МЕНЬШУЮ — детерминированно (не порядок БД)."""
    pid = project.id
    ff_wh = await create_warehouse(db_session, pid, {"name": f"FF-{_uid()}", "warehouse_type": "FULFILLMENT"})
    bc = f"PD-{_uid()}"
    await _make_nomenclature(db_session, pid, bc)
    vehicle = CostOrder(
        project_id=pid, order_no=f"PRDIST-{_uid()}", status=VehicleStatus.CUSTOMS, target_warehouse_id=ff_wh.id
    )
    db_session.add(vehicle)
    await db_session.flush()
    db_session.add_all(
        [
            # Равная суммарная qty (50) у ppb=12 и ppb=6 → ничья → побеждает МЕНЬШИЙ (6).
            CostOrderItem(project_id=pid, order_no=vehicle.order_no, barcode=bc, qty=50, pcs_per_box_override=12),
            CostOrderItem(project_id=pid, order_no=vehicle.order_no, barcode=bc, qty=50, pcs_per_box_override=6),
        ]
    )
    await db_session.commit()

    pool = await get_vehicle_pre_dist_pool(db_session, pid, vehicle.id)
    row = next(r for r in pool.rows if r.barcode == bc)
    assert row.box_qty == 6  # ничья Σqty → меньший ppb


# ─── Создание заявок ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_groups_by_wb_and_skips_stock(db_session, env):
    """Две WB-цели → 2 заявки PRE_DISTRIBUTED; склад-источник = ФФ машины; стока нет, но создаётся (C2: pallets=0)."""
    result = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[
                PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40),
                PreDistRow(barcode=env.bc2, wb_warehouse_name=WB_B, qty=20),
            ],
        ),
    )
    assert result.created == 2
    reqs = (
        (
            await db_session.execute(
                select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(reqs) == 2
    for r in reqs:
        assert r.status == AssemblyStatus.PRE_DISTRIBUTED
        assert r.is_pre_distribution is True
        assert r.warehouse_id == env.ff_wh_id  # источник = ФФ разгрузки машины
        assert r.pallets_count == 0  # C2: NOT NULL → 0
    by_wb = {r.wb_warehouse_name_manual: r for r in reqs}
    assert set(by_wb) == {WB_A, WB_B}


@pytest.mark.asyncio
async def test_create_sets_pallets_from_group(db_session, env):
    """pallets_by_group проставляет pallets_count на заявку (геометрию считает фронт).
    Вес НЕ форсится (pallet_weight_kg=0) — авто-вес (нетто+тара) считает _build_response,
    как у прочих поставок; ручной вес заблокировал бы авто-оценку. Нет ключа группы → 0."""
    result = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[
                PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40),
                PreDistRow(barcode=env.bc2, wb_warehouse_name=WB_B, qty=20),
            ],
            pallets_by_group={f"{WB_A}::BOX": 3},  # WB_B ключа нет → 0
        ),
    )
    assert result.created == 2
    by_wb = {r.wb_warehouse_name_manual: r for r in result.requests}
    assert by_wb[WB_A].pallets_count == 3
    assert by_wb[WB_B].pallets_count == 0
    # Вес не форсится — pallet_weight_kg=0 (авто-оценка идёт в total_weight_kg ответа).
    reqs = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalars().all()
    assert all(r.pallet_weight_kg == Decimal("0") for r in reqs)


@pytest.mark.asyncio
async def test_create_emits_package_type_in_response(db_session, env):
    """Ответ заявки несёт реальный package_type (колонка «Тип поставки»), не дефолт BOX."""
    result = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[
                PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40, package_type="MONOPALLET"),
                PreDistRow(barcode=env.bc2, wb_warehouse_name=WB_B, qty=20, package_type="BOX"),
            ],
        ),
    )
    by_wb = {r.wb_warehouse_name_manual: r for r in result.requests}
    assert by_wb[WB_A].package_type == "MONOPALLET"
    assert by_wb[WB_B].package_type == "BOX"


@pytest.mark.asyncio
async def test_predist_does_not_reserve_ff_stock(db_session, env):
    """C1: PRE_DISTRIBUTED НЕ держит реальный ФФ-сток (_get_reserved_map исключает его)."""
    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40)]),
    )
    reserved = await _get_reserved_map(db_session, env.pid, env.ff_wh_id)
    assert reserved.get(env.nom1, 0) == 0  # фейк-резерва нет

    # А после разгрузки (→ IN_PROGRESS) — резерв уже законен.
    await _advance_pre_distribution_assemblies(db_session, env.pid, env.vehicle.id)
    await db_session.commit()
    reserved2 = await _get_reserved_map(db_session, env.pid, env.ff_wh_id)
    assert reserved2.get(env.nom1, 0) == 40


@pytest.mark.asyncio
async def test_over_commit_guard(db_session, env):
    """H3: запрошено больше, чем на машине → ValueError, ничего не создано."""
    with pytest.raises(ValueError, match="Превышение"):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(
                vehicle_id=env.vehicle.id,
                rows=[
                    PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=80),
                    PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_B, qty=40),  # 120 > 100
                ],
            ),
        )
    count = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalars().all()
    assert len(count) == 0


@pytest.mark.asyncio
async def test_fbo_link_requires_single_wb(db_session, env):
    """wb_fbo_supply_id при нескольких WB-складах → ValueError."""
    with pytest.raises(ValueError, match="одном складе"):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(
                vehicle_id=env.vehicle.id,
                wb_fbo_supply_id=123456,
                rows=[
                    PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=10),
                    PreDistRow(barcode=env.bc2, wb_warehouse_name=WB_B, qty=10),
                ],
            ),
        )


# ─── M3: хард-блок без ФФ-склада назначения ────────────────────────────────


@pytest.mark.asyncio
async def test_m3_block_no_target_warehouse(db_session, env):
    """Машина без target_warehouse_id → пул и создание падают; в списке can_distribute=False."""
    env.vehicle.target_warehouse_id = None
    await db_session.commit()

    with pytest.raises(ValueError, match="склад назначения"):
        await get_vehicle_pre_dist_pool(db_session, env.pid, env.vehicle.id)
    with pytest.raises(ValueError, match="склад назначения"):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=10)]),
        )

    vehicles = await get_pre_distribution_vehicles(db_session, env.pid)
    mine = [v for v in vehicles if v.id == env.vehicle.id]
    assert mine and mine[0].can_distribute is False
    assert mine[0].block_reason


# ─── H1: авто-перевод на разгрузке через accept_receipt ────────────────────


@pytest.mark.asyncio
async def test_advance_on_unload_via_accept_receipt(db_session, env):
    """H1: разгрузка машины (accept_receipt с её cost_order_id) → PRE_DISTRIBUTED авто IN_PROGRESS."""
    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40)]),
    )
    # Приёмка на ФФ-склад, привязанная к машине.
    receipt = await create_receipt(
        db_session,
        env.pid,
        env.ff_wh_id,
        {"items": [{"barcode": env.bc1, "expected_qty": 40, "actual_qty": 40}]},
    )
    await db_session.execute(
        text("UPDATE inbound_receipts SET cost_order_id = :cid WHERE id = :rid"),
        {"cid": env.vehicle.id, "rid": receipt.id},
    )
    await db_session.commit()

    await accept_receipt(db_session, env.pid, receipt.id)

    req = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalar_one()
    assert req.status == AssemblyStatus.IN_PROGRESS
    assert req.is_pre_distribution is True  # флаг остаётся (бейдж/отчёты)

    # Идемпотентно: повторный advance ничего не двигает.
    again = await _advance_pre_distribution_assemblies(db_session, env.pid, env.vehicle.id)
    assert again == 0


# ─── H2: ручной фолбэк ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_advance_manual(db_session, env):
    """H2: ручной перевод PRE_DISTRIBUTED→IN_PROGRESS."""
    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc2, wb_warehouse_name=WB_B, qty=15)]),
    )
    moved = await advance_pre_distribution_manual(db_session, env.pid, env.vehicle.id)
    assert moved == 1
    req = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalar_one()
    assert req.status == AssemblyStatus.IN_PROGRESS


# ─── Список машин ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_vehicles_list_aggregates(db_session, env):
    vehicles = await get_pre_distribution_vehicles(db_session, env.pid)
    mine = [v for v in vehicles if v.id == env.vehicle.id]
    assert mine
    v = mine[0]
    assert v.status == "CUSTOMS"
    assert v.total_qty == 150
    assert v.sku_count == 2
    assert v.distributed_qty == 0
    assert v.can_distribute is True

    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=25)]),
    )
    vehicles2 = await get_pre_distribution_vehicles(db_session, env.pid)
    v2 = [x for x in vehicles2 if x.id == env.vehicle.id][0]
    assert v2.distributed_qty == 25


@pytest.mark.asyncio
async def test_project_isolation(db_session, env, other_project):
    """Машина чужого проекта не видна и не предраспределяется."""
    with pytest.raises(ValueError, match="не найдена"):
        await get_vehicle_pre_dist_pool(db_session, other_project.id, env.vehicle.id)
    vehicles = await get_pre_distribution_vehicles(db_session, other_project.id)
    assert all(v.id != env.vehicle.id for v in vehicles)


@pytest.mark.asyncio
async def test_create_pre_distribution_dedup_folds_second_call_same_direction(db_session, env):
    """Повторное распределение ТОЙ ЖЕ машины на то же направление доливает позиции в
    существующую сборку, а не плодит дубль (инкрементальный «дозабор» частями)."""
    r1 = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=30)],
            pallets_by_group={f"{WB_A}::BOX": 2},
        ),
    )
    assert r1.created == 1
    first_id = r1.requests[0].id

    r2 = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[
                PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=10),  # тот же ШК
                PreDistRow(barcode=env.bc2, wb_warehouse_name=WB_A, qty=5),  # новый ШК на то же направление
            ],
            pallets_by_group={f"{WB_A}::BOX": 1},
        ),
    )
    # Дедуп: та же сборка, не новая.
    assert r2.created == 1
    assert r2.requests[0].id == first_id

    reqs = (
        (
            await db_session.execute(
                select(AssemblyRequest).where(
                    AssemblyRequest.source_vehicle_id == env.vehicle.id,
                    AssemblyRequest.wb_warehouse_name_manual == WB_A,
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(reqs) == 1 and reqs[0].id == first_id
    assert reqs[0].pallets_count == 3  # 2 + 1

    items = (
        (
            await db_session.execute(
                select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == first_id)
            )
        )
        .scalars()
        .all()
    )
    qty_by_bc = {it.barcode: it.quantity for it in items}
    assert qty_by_bc[env.bc1] == 40  # 30 + 10
    assert qty_by_bc[env.bc2] == 5


@pytest.mark.asyncio
async def test_create_pre_distribution_fold_survives_rollback(db_session, env):
    """Чистый fold-путь (все направления уже существуют) обязан коммитить сам: get_db
    за сервис не коммитит, и без commit'а доливка молча откатывается при закрытии
    сессии — 200 OK и «Создано 1 заявок», но БД не изменилась (прод-кейс V-0033)."""
    r1 = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=30)],
            pallets_by_group={f"{WB_A}::BOX": 2},
        ),
    )
    first_id = r1.requests[0].id

    # Второй заход целиком в существующее направление → ни одного create_assembly_request,
    # который коммитил бы «попутно», — судьба доливки зависит только от самого сервиса.
    r2 = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(
            vehicle_id=env.vehicle.id,
            rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=10)],
            pallets_by_group={f"{WB_A}::BOX": 1},
        ),
    )
    assert r2.created == 1 and r2.requests[0].id == first_id

    # Конец HTTP-запроса: get_db закрывает сессию без commit → всё незакоммиченное гибнет.
    await db_session.rollback()

    items = (
        (
            await db_session.execute(
                select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == first_id)
            )
        )
        .scalars()
        .all()
    )
    assert {it.barcode: it.quantity for it in items} == {env.bc1: 40}  # 30 + 10 пережили rollback
    req = (await db_session.execute(select(AssemblyRequest).where(AssemblyRequest.id == first_id))).scalar_one()
    assert req.pallets_count == 3  # 2 + 1 тоже закоммичено


async def _mk_pre_dist_request(db_session, env, wb: str, bc: str, qty: int) -> int:
    """Создать одну PRE_DISTRIBUTED сборку напрямую (в обход дедупа) — для тестов мёрджа."""
    req = await create_assembly_request(
        db_session,
        env.pid,
        AssemblyRequestCreate(
            warehouse_id=env.ff_wh_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("0"),
            wb_warehouse_name_manual=wb,
            package_type="BOX",
            items=[AssemblyItemCreate(barcode=bc, quantity=qty)],
        ),
        skip_stock_validation=True,
        status_override=AssemblyStatus.PRE_DISTRIBUTED,
        source_vehicle_id=env.vehicle.id,
        is_pre_distribution=True,
    )
    return req.id


@pytest.mark.asyncio
async def test_merge_allows_pre_distributed_same_vehicle(db_session, env):
    """Мёрдж двух PRE_DISTRIBUTED одного направления/машины → одна сборка, суммирование."""
    a = await _mk_pre_dist_request(db_session, env, WB_A, env.bc1, 30)
    b = await _mk_pre_dist_request(db_session, env, WB_A, env.bc1, 10)
    survivor = await merge_assembly_requests(db_session, env.pid, [a, b])
    assert survivor.status == AssemblyStatus.PRE_DISTRIBUTED
    items = (
        (await db_session.execute(select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == survivor.id)))
        .scalars()
        .all()
    )
    assert {it.barcode: it.quantity for it in items}[env.bc1] == 40


@pytest.mark.asyncio
async def test_merge_rejects_mixing_pre_distributed_with_in_progress(db_session, env):
    """Смешивать PRE_DISTRIBUTED с обычной «В сборке» нельзя (нет реального стока)."""
    a = await _mk_pre_dist_request(db_session, env, WB_A, env.bc1, 30)
    # Обычная IN_PROGRESS на то же направление.
    ip = await create_assembly_request(
        db_session,
        env.pid,
        AssemblyRequestCreate(
            warehouse_id=env.ff_wh_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("0"),
            wb_warehouse_name_manual=WB_A,
            package_type="BOX",
            items=[AssemblyItemCreate(barcode=env.bc1, quantity=5)],
        ),
        skip_stock_validation=True,
        status_override=AssemblyStatus.IN_PROGRESS,
    )
    with pytest.raises(ValueError, match="смешивать"):
        await merge_assembly_requests(db_session, env.pid, [a, ip.id])


# ─── Принятые машины (окно N дней): распределение остатка ОБЫЧНЫМИ заявками ─


async def _accept_vehicle_receipt(db_session: AsyncSession, env: _Env, *, days_ago: int = 0):
    """Принять машину env: DISPATCHED → приёмка ACCEPTED (весь груз приходуется в
    WarehouseStock ФФ) → машина DELIVERED. days_ago сдвигает actual_date приёмки назад."""
    env.vehicle.status = VehicleStatus.DISPATCHED
    await db_session.commit()
    receipt = await create_receipt(
        db_session,
        env.pid,
        env.ff_wh_id,
        {
            "items": [
                {"barcode": env.bc1, "expected_qty": 100, "actual_qty": 100},
                {"barcode": env.bc2, "expected_qty": 50, "actual_qty": 50},
            ]
        },
    )
    await db_session.execute(
        text("UPDATE inbound_receipts SET cost_order_id = :cid WHERE id = :rid"),
        {"cid": env.vehicle.id, "rid": receipt.id},
    )
    await db_session.commit()
    await accept_receipt(db_session, env.pid, receipt.id)
    if days_ago:
        await db_session.execute(
            text("UPDATE inbound_receipts SET actual_date = :d WHERE id = :rid"),
            {"d": date.today() - timedelta(days=days_ago), "rid": receipt.id},
        )
        await db_session.commit()
    await db_session.refresh(env.vehicle)
    return receipt


@pytest.mark.asyncio
async def test_list_includes_recently_accepted_vehicle(db_session, env):
    """Принятая машина остаётся в списке ещё PRE_DIST_DELIVERED_WINDOW_DAYS дней (с датой приёмки)."""
    await _accept_vehicle_receipt(db_session, env)
    assert env.vehicle.status == VehicleStatus.DELIVERED

    vehicles = await get_pre_distribution_vehicles(db_session, env.pid)
    mine = [v for v in vehicles if v.id == env.vehicle.id]
    assert mine
    assert mine[0].status == "DELIVERED"
    assert mine[0].accepted_date == date.today()
    assert mine[0].can_distribute is True
    assert mine[0].total_qty == 150


@pytest.mark.asyncio
async def test_list_excludes_vehicle_accepted_beyond_window(db_session, env):
    """Приёмка старше окна → машины нет в списке, пул и создание закрыты."""
    await _accept_vehicle_receipt(db_session, env, days_ago=PRE_DIST_DELIVERED_WINDOW_DAYS + 1)
    vehicles = await get_pre_distribution_vehicles(db_session, env.pid)
    assert all(v.id != env.vehicle.id for v in vehicles)

    with pytest.raises(ValueError, match="машину в пути"):
        await get_vehicle_pre_dist_pool(db_session, env.pid, env.vehicle.id)
    with pytest.raises(ValueError, match="машину в пути"):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=10)]),
        )


@pytest.mark.asyncio
async def test_list_includes_vehicle_on_window_border(db_session, env):
    """Граница окна включительно: принята ровно N дней назад — ещё видна."""
    await _accept_vehicle_receipt(db_session, env, days_ago=PRE_DIST_DELIVERED_WINDOW_DAYS)
    vehicles = await get_pre_distribution_vehicles(db_session, env.pid)
    assert any(v.id == env.vehicle.id for v in vehicles)


@pytest.mark.asyncio
async def test_create_from_accepted_vehicle_is_regular_request(db_session, env):
    """Из принятой машины создаётся ОБЫЧНАЯ заявка: IN_PROGRESS, is_pre_distribution=False,
    метка source_vehicle_id остаётся, резерв реального ФФ-стока появляется сразу."""
    await _accept_vehicle_receipt(db_session, env)
    result = await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40)]),
    )
    assert result.created == 1

    req = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalar_one()
    assert req.status == AssemblyStatus.IN_PROGRESS
    assert req.is_pre_distribution is False

    # Резерв — реальный (обычная заявка), в отличие от PRE_DISTRIBUTED.
    reserved = await _get_reserved_map(db_session, env.pid, env.ff_wh_id)
    assert reserved.get(env.nom1, 0) == 40

    # Net-пул машины уменьшился: метка source_vehicle_id учитывается той же математикой.
    pool = await get_vehicle_pre_dist_pool(db_session, env.pid, env.vehicle.id)
    by_bc = {r.barcode: r for r in pool.rows}
    assert by_bc[env.bc1].available_qty == 60
    assert pool.vehicle.accepted_date == date.today()


@pytest.mark.asyncio
async def test_create_from_accepted_vehicle_validates_ff_stock(db_session, env):
    """Остаток ФФ уже разобран другими заявками → создание из принятой машины падает целиком."""
    await _accept_vehicle_receipt(db_session, env)
    # Обычная заявка без машины съедает 80 из 100 BC1.
    await create_assembly_request(
        db_session,
        env.pid,
        AssemblyRequestCreate(
            warehouse_id=env.ff_wh_id,
            pallets_count=1,
            pallet_weight_kg=Decimal("0"),
            wb_warehouse_name_manual=WB_B,
            package_type="BOX",
            items=[AssemblyItemCreate(barcode=env.bc1, quantity=80)],
        ),
    )
    with pytest.raises(ValueError, match="Недостаточно"):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40)]),
        )
    reqs = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalars().all()
    assert len(reqs) == 0


@pytest.mark.asyncio
async def test_create_from_accepted_vehicle_folds_same_direction(db_session, env):
    """Повторное распределение принятой машины на то же направление доливает в ту же заявку."""
    await _accept_vehicle_receipt(db_session, env)
    for qty in (20, 10):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=qty)]),
        )
    reqs = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalars().all()
    assert len(reqs) == 1
    items = (
        await db_session.execute(
            select(AssemblyRequestItem).where(AssemblyRequestItem.assembly_request_id == reqs[0].id)
        )
    ).scalars().all()
    assert {it.barcode: it.quantity for it in items}[env.bc1] == 30
    # Fold-долив тоже под стеклом стока: реальный резерв вырос до 30.
    reserved = await _get_reserved_map(db_session, env.pid, env.ff_wh_id)
    assert reserved.get(env.nom1, 0) == 30


@pytest.mark.asyncio
async def test_create_from_accepted_vehicle_fold_validates_ff_stock(db_session, env):
    """Fold-долив в существующую заявку принятой машины тоже валидирует остаток ФФ."""
    await _accept_vehicle_receipt(db_session, env)
    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=20)]),
    )
    # Сток BC1 усох до 30 (инвентаризация/отгрузки): долив 40 должен упасть, 20+40 > 30.
    await db_session.execute(
        text(
            "UPDATE warehouse_stock SET quantity = 30 "
            "WHERE project_id = :pid AND warehouse_id = :wid AND nomenclature_id = :nid"
        ),
        {"pid": env.pid, "wid": env.ff_wh_id, "nid": env.nom1},
    )
    await db_session.commit()
    with pytest.raises(ValueError, match="Недостаточно"):
        await create_pre_distribution(
            db_session,
            env.pid,
            PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40)]),
        )


@pytest.mark.asyncio
async def test_accept_receipt_ff_advances_predist(db_session, env):
    """Приёмка через ФФ-портал тоже переводит PRE_DISTRIBUTED → IN_PROGRESS (зеркало accept_receipt)."""
    await create_pre_distribution(
        db_session,
        env.pid,
        PreDistributionCreate(vehicle_id=env.vehicle.id, rows=[PreDistRow(barcode=env.bc1, wb_warehouse_name=WB_A, qty=40)]),
    )
    env.vehicle.status = VehicleStatus.DISPATCHED
    await db_session.commit()
    receipt = await create_receipt(
        db_session,
        env.pid,
        env.ff_wh_id,
        {"items": [{"barcode": env.bc1, "expected_qty": 100}]},
    )
    await db_session.execute(
        text("UPDATE inbound_receipts SET cost_order_id = :cid WHERE id = :rid"),
        {"cid": env.vehicle.id, "rid": receipt.id},
    )
    await db_session.commit()
    await db_session.refresh(receipt, ["items"])

    await accept_receipt_ff(
        db_session,
        env.pid,
        receipt.id,
        [{"item_id": receipt.items[0].id, "actual_qty": 100, "defect_qty": 0}],
    )

    req = (
        await db_session.execute(
            select(AssemblyRequest).where(AssemblyRequest.source_vehicle_id == env.vehicle.id)
        )
    ).scalar_one()
    assert req.status == AssemblyStatus.IN_PROGRESS
    await db_session.refresh(env.vehicle)
    assert env.vehicle.status == VehicleStatus.DELIVERED
