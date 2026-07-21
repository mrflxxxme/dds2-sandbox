# ruff: noqa: RUF001, RUF002, RUF003
"""
Tests: почасовая история наполнения черновиков по категориям.

1. compute_category_stats — чистая агрегация: rows/prebook раздельно, позиции
   дедупятся между черновиками, категория-фолбэк, короба/паллеты по машинной
   кратности (BOX — дробный футпринт, MONOPALLET — целые), пары без машины
   дают штуки без коробов.
2. snapshot_project — идемпотентная перезапись часа, CategoryOverride перебивает
   subject, изоляция project_id, soft-deleted черновики не считаются.
3. get_history / purge_old_snapshots — окно дней и ретенция.
"""

from datetime import timedelta
from decimal import Decimal

import pytest

from backend.models import AssemblyDraftCategoryHourly
from backend.models.cost import Nomenclature
from backend.models.refs import CategoryOverride
from backend.schemas.assembly_draft import (
    AssemblyDraftCreate,
    AssemblyDraftDistribution,
    AssemblyDraftRow,
)
from backend.services import assembly_draft_service
from backend.services.assembly.draft_category_history import (
    FALLBACK_CATEGORY,
    compute_category_stats,
    get_history,
    purge_old_snapshots,
    snapshot_project,
)
from backend.services.assembly.pallet_geo import (
    DEFAULT_MAX_PALLET_HEIGHT_CM,
    effective_boxes_per_pallet,
)
from backend.utils.time import utcnow

BOX_SIZE = "60x40x40"


def _dist(rows: list[dict] | None = None, prebook: list[dict] | None = None) -> dict:
    return {"rows": rows or [], "prebook": prebook or []}


# ─── compute_category_stats (чистая функция) ─────────────────────────────────


def test_compute_units_buckets_and_position_dedupe():
    """rows и prebook считаются раздельно; один nm в двух черновиках — одна позиция."""
    d1 = _dist(rows=[{"nm_id": 1, "barcode": "B1", "src": {"5": 10}, "tgt": {"Казань": 10}}])
    d2 = _dist(
        rows=[{"nm_id": 1, "barcode": "B1", "src": {"7": 4}, "tgt": {"Тула": 4}}],
        prebook=[{"nm_id": 2, "barcode": "B2", "src": {"5": 6}, "tgt": {"Тула": 6}}],
    )
    stats = compute_category_stats([d1, d2], {1: "Ковры", 2: "Ковры"}, {}, None)

    assert set(stats) == {"Ковры"}
    acc = stats["Ковры"]
    assert acc.units_rows == 14
    assert acc.units_prebook == 6
    assert len(acc.nm_ids) == 2  # nm 1 не задвоился
    assert acc.boxes == 0 and acc.pallets == Decimal("0.0")  # машины нет — только штуки


def test_compute_fallback_category_and_garbage_rows():
    """nm без категории → «Без категории»; мусорные строки/qty пропускаются."""
    d = _dist(
        rows=[
            {"nm_id": 9, "barcode": "B9", "src": {"5": 3}, "tgt": {}},
            {"nm_id": "мусор", "barcode": "BX", "src": {"5": 5}},
            {"nm_id": 10, "barcode": "B10", "src": {"5": 0, "7": None}},
            "не-словарь",
        ]
    )
    stats = compute_category_stats([d], {}, {}, None)
    assert set(stats) == {FALLBACK_CATEGORY}
    assert stats[FALLBACK_CATEGORY].units_rows == 3


def test_compute_boxes_and_pallets_box_vs_mono():
    """BOX — дробный футпринт паллеты; MONOPALLET — целые паллеты на SKU.
    Ожидания через effective_boxes_per_pallet — тест не дублирует геометрию."""
    bpp = effective_boxes_per_pallet(BOX_SIZE, DEFAULT_MAX_PALLET_HEIGHT_CM, None)
    assert bpp and bpp > 0
    bq = 10
    machine = {
        ("B1", 5): {"box_qty": bq, "box_size": BOX_SIZE},
        ("B2", 5): {"box_qty": bq, "box_size": BOX_SIZE},
    }
    half_pallet_qty = (bpp * bq) // 2  # ровно полпаллеты в штуках
    d = _dist(
        rows=[
            {"nm_id": 1, "barcode": "B1", "package_type": "BOX", "src": {"5": half_pallet_qty}},
            {"nm_id": 2, "barcode": "B2", "package_type": "MONOPALLET", "src": {"5": half_pallet_qty}},
        ]
    )
    stats = compute_category_stats([d], {1: "Ковры", 2: "Ковры"}, machine, None)
    acc = stats["Ковры"]
    assert acc.boxes == 2 * ((half_pallet_qty + bq - 1) // bq)  # ceil по каждой строке
    # BOX даёт 0.5 паллеты дробью, MONOPALLET округляется до целой.
    assert acc.pallets == (Decimal(half_pallet_qty) / Decimal(bpp * bq) + 1).quantize(Decimal("0.1"))


def test_compute_pair_without_machine_counts_units_only():
    """ФФ-пара без машинного резолва: штуки в счёте, короба/паллеты — нет."""
    machine = {("B1", 5): {"box_qty": 10, "box_size": BOX_SIZE}}
    d = _dist(rows=[{"nm_id": 1, "barcode": "B1", "src": {"5": 20, "7": 30}}])
    stats = compute_category_stats([d], {1: "Ковры"}, machine, None)
    acc = stats["Ковры"]
    assert acc.units_rows == 50
    assert acc.boxes == 2  # только склад 5 (20/10); склад 7 без машины


# ─── snapshot_project / get_history / purge (БД) ─────────────────────────────


def _payload(rows: list[AssemblyDraftRow], category_scope: list[str] | None = None) -> AssemblyDraftCreate:
    return AssemblyDraftCreate(
        distribution=AssemblyDraftDistribution(rows=rows, category_scope=category_scope)
    )


@pytest.mark.asyncio
async def test_snapshot_idempotent_override_and_isolation(db_session, project, other_project):
    """Срез пишет категории всех черновиков проекта (основной + категорийный),
    CategoryOverride перебивает subject, повторный прогон часа не дублирует,
    чужой проект и soft-deleted черновик не попадают."""
    db_session.add(
        Nomenclature(project_id=project.id, barcode="H1", article_wb=101, subject="Ковры")
    )
    db_session.add(
        Nomenclature(project_id=project.id, barcode="H2", article_wb=102, subject="Шторы")
    )
    db_session.add(
        CategoryOverride(project_id=project.id, nm_id=102, category_value="Шторы блэкаут")
    )
    await db_session.commit()

    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload([AssemblyDraftRow(nm_id=101, barcode="H1", src={"5": 10}, tgt={"Казань": 10})]),
    )
    await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload(
            [AssemblyDraftRow(nm_id=102, barcode="H2", src={"5": 7}, tgt={"Тула": 7})],
            category_scope=["Шторы блэкаут"],
        ),
    )
    dead = await assembly_draft_service.create_draft(
        db_session,
        project.id,
        _payload([AssemblyDraftRow(nm_id=101, barcode="H1", src={"5": 99}, tgt={"Казань": 99})]),
    )
    await assembly_draft_service.delete_draft(db_session, project.id, dead.id)

    hour = utcnow()
    n1 = await snapshot_project(db_session, project.id, hour)
    n2 = await snapshot_project(db_session, project.id, hour)  # idempotent
    assert n1 == n2 == 2

    points = await get_history(db_session, project.id, 1)
    by_cat = {p.category: p for p in points}
    assert set(by_cat) == {"Ковры", "Шторы блэкаут"}
    assert by_cat["Ковры"].units_rows == 10  # без soft-deleted (99 не попало)
    assert by_cat["Шторы блэкаут"].units_rows == 7  # override перебил subject
    assert by_cat["Ковры"].positions == 1

    assert await get_history(db_session, other_project.id, 1) == []  # изоляция


@pytest.mark.asyncio
async def test_history_window_and_retention(db_session, project):
    """get_history режет по окну дней; purge_old_snapshots чистит старше ретенции."""
    now = utcnow().replace(minute=0, second=0, microsecond=0)
    for age_days, cat in ((0, "Свежая"), (10, "Средняя"), (120, "Древняя")):
        db_session.add(
            AssemblyDraftCategoryHourly(
                project_id=project.id,
                taken_at=now - timedelta(days=age_days),
                category=cat,
                positions=1,
                units_rows=1,
                units_prebook=0,
                boxes=0,
                pallets=Decimal("0"),
            )
        )
    await db_session.commit()

    assert {p.category for p in await get_history(db_session, project.id, 7)} == {"Свежая"}
    assert {p.category for p in await get_history(db_session, project.id, 30)} == {"Свежая", "Средняя"}

    await purge_old_snapshots(db_session, retention_days=90)
    cats_after = {p.category for p in await get_history(db_session, project.id, 365)}
    assert cats_after == {"Свежая", "Средняя"}  # «Древняя» вычищена
