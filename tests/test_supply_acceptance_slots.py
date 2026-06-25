# ruff: noqa: RUF001, RUF002, RUF003
"""Unit-тесты чистого ассемблера слотов сдачи (per-supply acceptance slots).

Проверяем матч заявка→календарь приёмки по (нормализованное имя, box_type):
matched/unmatched, маппинг package_type, нормализацию имени склада, дефолты,
сортировку и проброс оси дат. I/O нет — функция чистая.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text

from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.models.wb_fbo import WbFboSupply
from backend.services.supply_acceptance_slots_service import (
    _build_supply_acceptance_slots,
    _load_active_supply_rows,
)


def _day(d: str, coef: float, *, free: bool, closed: bool = False) -> dict:
    return {
        "date": d,
        "coefficient": coef,
        "allow_unload": not closed,
        "is_free": free,
        "is_closed": closed,
        "storage_coef": None,
        "delivery_coef": None,
    }


def _limits() -> dict:
    """AcceptanceLimitsResponse-shaped календарь: Электросталь box+mono, Краснодар box."""
    return {
        "warehouses": [
            {
                "warehouse_id": 507,
                "warehouse_name": "Электросталь",
                "canonical_name": "Электросталь",
                "box_type": "box",
                "days": [_day("2026-06-19", 0, free=True), _day("2026-06-20", 2, free=False)],
            },
            {
                "warehouse_id": 507,
                "warehouse_name": "Электросталь",
                "canonical_name": "Электросталь",
                "box_type": "mono",
                "days": [_day("2026-06-19", -1, free=False, closed=True)],
            },
            {
                "warehouse_id": 130744,
                "warehouse_name": "Краснодар (Тихорецкая)",
                "canonical_name": "Краснодар",
                "box_type": "box",
                "days": [_day("2026-06-21", 1, free=True)],
            },
        ],
        "dates": ["2026-06-19", "2026-06-20", "2026-06-21"],
    }


def _req(**kw) -> dict:
    base = {
        "id": 1,
        "number": "ASM-485",
        "status": "READY",
        "package_type": "BOX",
        "effective_warehouse": "Электросталь",
        "wb_supply_id": "39950266",
        "planned_date": date(2026, 6, 22),
        "actual_date": None,
        "wb_status": "ACTIVE",
    }
    base.update(kw)
    return base


def test_matched_box_attaches_days():
    out = _build_supply_acceptance_slots([_req()], _limits())
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["matched"] is True
    assert row["box_type"] == "box"
    assert row["warehouse_id"] == 507
    assert row["canonical_name"] == "Электросталь"
    assert [d["date"] for d in row["days"]] == ["2026-06-19", "2026-06-20"]
    assert row["wb_supply_id"] == "39950266"
    assert row["assembly_number"] == "ASM-485"


def test_unmatched_warehouse_is_flagged_not_silent():
    out = _build_supply_acceptance_slots([_req(effective_warehouse="Неведомый склад")], _limits())
    row = out["rows"][0]
    assert row["matched"] is False
    assert row["days"] == []
    assert row["warehouse_id"] is None
    # canonical всё равно вычислен — для группировки/отображения
    assert row["canonical_name"] == "Неведомый склад"


def test_package_type_maps_to_box_key():
    out = _build_supply_acceptance_slots([_req(package_type="MONOPALLET")], _limits())
    row = out["rows"][0]
    assert row["box_type"] == "mono"
    assert row["matched"] is True
    assert [d["date"] for d in row["days"]] == ["2026-06-19"]
    assert row["days"][0]["is_closed"] is True


def test_supersafe_without_entry_is_unmatched():
    out = _build_supply_acceptance_slots([_req(package_type="SUPERSAFE")], _limits())
    row = out["rows"][0]
    assert row["box_type"] == "super"
    assert row["matched"] is False
    assert row["days"] == []


def test_warehouse_name_is_normalized_before_match():
    # ФБО хранит «Краснодар (Тихорецкая)» → нормализуется в «Краснодар» и матчится.
    out = _build_supply_acceptance_slots(
        [_req(effective_warehouse="Краснодар (Тихорецкая)")], _limits()
    )
    row = out["rows"][0]
    assert row["canonical_name"] == "Краснодар"
    assert row["matched"] is True
    assert row["warehouse_id"] == 130744


def test_missing_package_type_defaults_to_box():
    out = _build_supply_acceptance_slots([_req(package_type=None)], _limits())
    row = out["rows"][0]
    assert row["box_type"] == "box"
    assert row["package_type"] == "BOX"
    assert row["matched"] is True


def test_rows_sorted_by_warehouse_then_planned_date():
    reqs = [
        _req(id=1, number="ASM-1", effective_warehouse="Электросталь", planned_date=date(2026, 6, 25)),
        _req(id=2, number="ASM-2", effective_warehouse="Краснодар (Тихорецкая)", planned_date=date(2026, 6, 20)),
        _req(id=3, number="ASM-3", effective_warehouse="Электросталь", planned_date=date(2026, 6, 22)),
    ]
    out = _build_supply_acceptance_slots(reqs, _limits())
    order = [(r["canonical_name"], r["assembly_number"]) for r in out["rows"]]
    # Краснодар < Электросталь; внутри Электростали — по плановой дате (22 < 25)
    assert order == [
        ("Краснодар", "ASM-2"),
        ("Электросталь", "ASM-3"),
        ("Электросталь", "ASM-1"),
    ]


def test_dates_axis_passed_through():
    out = _build_supply_acceptance_slots([_req()], _limits())
    assert out["dates"] == ["2026-06-19", "2026-06-20", "2026-06-21"]


def test_empty_requests_yield_no_rows():
    out = _build_supply_acceptance_slots([], _limits())
    assert out["rows"] == []
    assert out["dates"] == ["2026-06-19", "2026-06-20", "2026-06-21"]


# ─── DB-тест: изоляция _load_active_supply_rows (tenant / soft-delete / status) ──


async def _mk_warehouse(db_session, pid: int, name: str) -> int:
    row = await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
            "VALUES (:pid, :n, 'FULFILLMENT', 1, true, false, NOW(), NOW()) RETURNING id"
        ),
        {"pid": pid, "n": name},
    )
    return row.scalar()


def _mk_req(pid: int, wh_id: int, number: str, status: str, **kw) -> AssemblyRequest:
    return AssemblyRequest(
        project_id=pid,
        warehouse_id=wh_id,
        number=number,
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("100"),
        package_type=kw.pop("package_type", "BOX"),
        **kw,
    )


async def test_load_active_supply_rows_isolation(db_session, project, other_project):
    """Возвращаются только активные, не-удалённые заявки своего проекта с складом."""
    wh = await _mk_warehouse(db_session, project.id, "Slots WH A")
    wh_other = await _mk_warehouse(db_session, other_project.id, "Slots WH B")

    supply = WbFboSupply(
        project_id=project.id,
        wb_supply_id="SLOT-S1",
        wb_status="ACTIVE",
        warehouse_name="Электросталь",
        planned_date=date(2026, 6, 22),
        created_at_wb=datetime(2026, 6, 1),
    )
    db_session.add(supply)
    await db_session.flush()

    db_session.add_all([
        # ✅ активная + ФБО-поставка → попадает
        _mk_req(project.id, wh, "SLOT-R1", AssemblyStatus.READY.value, wb_fbo_supply_id=supply.id),
        # ✅ активная + ручной склад (без ФБО) → попадает
        _mk_req(project.id, wh, "SLOT-R2", AssemblyStatus.IN_PROGRESS.value,
                wb_warehouse_name_manual="Краснодар", package_type="MONOPALLET"),
        # ✖ не-активный статус (отгружена) → исключается
        _mk_req(project.id, wh, "SLOT-R3", AssemblyStatus.SHIPPED.value, wb_warehouse_name_manual="Казань"),
        # ✖ soft-deleted → исключается
        _mk_req(project.id, wh, "SLOT-R4", AssemblyStatus.READY.value,
                wb_warehouse_name_manual="Тула", is_deleted=True),
        # ✖ нет склада назначения (ни ФБО, ни ручного) → пропускается
        _mk_req(project.id, wh, "SLOT-R5", AssemblyStatus.READY.value),
        # ✖ другой проект → исключается (tenant-изоляция)
        _mk_req(other_project.id, wh_other, "SLOT-R6", AssemblyStatus.READY.value,
                wb_warehouse_name_manual="Электросталь"),
    ])
    await db_session.commit()

    rows = await _load_active_supply_rows(db_session, project.id)
    by_num = {r["number"]: r for r in rows}

    assert set(by_num) == {"SLOT-R1", "SLOT-R2"}

    r1 = by_num["SLOT-R1"]
    assert r1["wb_supply_id"] == "SLOT-S1"
    assert r1["effective_warehouse"] == "Электросталь"
    assert r1["planned_date"] == date(2026, 6, 22)
    assert r1["package_type"] == "BOX"

    r2 = by_num["SLOT-R2"]
    assert r2["wb_supply_id"] is None
    assert r2["effective_warehouse"] == "Краснодар"
    assert r2["package_type"] == "MONOPALLET"
