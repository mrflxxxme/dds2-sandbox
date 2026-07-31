# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты WB FBS — видимость незакрытых списаний (`orders_service.writeoff_issues`).

Задание `complete` без `written_off_at` молча висит в очереди списания, пока
жива причина (нет карточки / нет привязки склада / нет остатка) — раньше
единственным следом был warning в логе воркера. Ручка агрегирует такие задания
по товару с причиной и остатками привязанных складов.

Что закрыто:
  • классификация причин и её приоритет (no_card > no_link > no_stock);
  • пост-классификация queued: остатка хватает на ВСЕ задания группы — это
    очередь до ближайшего прогона, не алярм; частичный дефицит — no_stock;
  • коды причин — тест-связка с ALLOWED_WRITEOFF_REASONS (контракт схем);
  • NULL-склад продавца = no_link (в списание такое задание не попадает никогда);
  • мягко удалённый наш склад = привязки нет → no_link (канон домена);
  • агрегация по (склад продавца, товар, причина): stuck / oldest_at;
  • обогащение: имя склада WB, первый привязанный наш склад, our_qty/our_defect
    по ВСЕМ привязанным складам, ff_loose (None без зеркала вовсе);
  • cap строк + честный total_orders до среза + флаг truncated;
  • списанные и не-complete задания не попадают; contour-фильтр (песочница);
  • изоляция по project_id.
"""

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import (
    FbsSupplierStatus,
    FulfillmentStock,
    Nomenclature,
    WbFbsOrder,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType
from backend.services.wb_fbs import orders_service

WB_WH = 771001
WB_WH_UNLINKED = 771002
BARCODE = "WOI_BC_1"
CHRT_ID = 881001


@pytest_asyncio.fixture
async def env(db_session, project):
    """Склад WB с привязкой к нашему складу + номенклатура (остаток НЕ создаём)."""
    warehouse = Warehouse(
        project_id=project.id,
        name="Наш склад WOI",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(warehouse)
    await db_session.flush()

    nom = Nomenclature(
        project_id=project.id, barcode=BARCODE, chrt_id=CHRT_ID, article_seller="ART-WOI"
    )
    db_session.add(nom)
    await db_session.flush()

    db_session.add(
        WbFbsWarehouse(
            project_id=project.id, wb_warehouse_id=WB_WH, name="Склад продавца WOI", is_active=True
        )
    )
    db_session.add(
        WbFbsWarehouseLink(
            project_id=project.id,
            wb_warehouse_id=WB_WH,
            warehouse_id=warehouse.id,
            is_active=True,
        )
    )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(project_id=project.id, warehouse_id=warehouse.id, nomenclature_id=nom.id)


async def _seed(db_session, project_id, wb_order_id, **over):
    fields = {
        "project_id": project_id,
        "wb_order_id": wb_order_id,
        "wb_warehouse_id": WB_WH,
        "barcode": BARCODE,
        "chrt_id": CHRT_ID,
        "supplier_status": FbsSupplierStatus.COMPLETE.value,
        "created_at_wb": datetime(2026, 7, 20, 12, 0, 0),
    }
    fields.update(over)
    order = WbFbsOrder(**fields)
    db_session.add(order)
    await db_session.commit()
    return order


# ─── Классификация причин ────────────────────────────────────────────────────


def test_reason_codes_match_schema_contract():
    """Локальные коды причин сервиса ≡ ALLOWED_WRITEOFF_REASONS (контракт схем).

    Сервис держит коды локальными константами (а не литералами врассыпную);
    равенство набора со схемой — этот тест, чтобы новый код не разъехался с
    фронтовым словарём WRITEOFF_REASON_LABEL.
    """
    from backend.schemas.wb_fbs import ALLOWED_WRITEOFF_REASONS

    assert {
        orders_service._WRITEOFF_ISSUE_NO_CARD,
        orders_service._WRITEOFF_ISSUE_NO_LINK,
        orders_service._WRITEOFF_ISSUE_NO_STOCK,
        orders_service._WRITEOFF_ISSUE_QUEUED,
    } == set(ALLOWED_WRITEOFF_REASONS)


@pytest.mark.asyncio
async def test_reason_no_stock_for_linked_card(db_session, env):
    """Карточка есть, привязка есть, остатка нет → no_stock."""
    await _seed(db_session, env.project_id, 90001, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["total_orders"] == 1
    assert out["truncated"] is False
    (row,) = out["rows"]
    assert row["reason"] == "no_stock"
    assert row["wb_warehouse_id"] == WB_WH
    assert row["wb_warehouse_name"] == "Склад продавца WOI"
    assert row["warehouse_id"] == env.warehouse_id
    assert row["warehouse_name"] == "Наш склад WOI"
    assert row["nomenclature_id"] == env.nomenclature_id
    assert row["article"] == "ART-WOI"
    assert row["barcode"] == BARCODE
    assert row["stuck"] == 1
    assert row["our_qty"] == 0
    assert row["our_defect"] == 0
    # Зеркала ФФ у привязанного склада нет вовсе → None, а не 0.
    assert row["ff_loose"] is None


@pytest.mark.asyncio
async def test_reason_no_card_wins_over_no_link(db_session, env):
    """Нет карточки → no_card, даже если склад продавца тоже не привязан."""
    await _seed(
        db_session,
        env.project_id,
        90002,
        wb_warehouse_id=WB_WH_UNLINKED,
        nomenclature_id=None,
        article="RAW-ART",
    )

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    (row,) = out["rows"]
    assert row["reason"] == "no_card"
    # Фолбэк артикула/ШК — из самого задания (карточки нет).
    assert row["article"] == "RAW-ART"
    assert row["barcode"] == BARCODE
    assert row["warehouse_id"] is None


@pytest.mark.asyncio
async def test_reason_no_link_for_unlinked_and_null_warehouse(db_session, env):
    """Карточка есть, а склад продавца не привязан (или NULL) → no_link."""
    await _seed(
        db_session,
        env.project_id,
        90003,
        wb_warehouse_id=WB_WH_UNLINKED,
        nomenclature_id=env.nomenclature_id,
    )
    # NULL-склад: `IN (подзапрос)` его никогда не матчит → списание невозможно.
    await _seed(
        db_session,
        env.project_id,
        90004,
        wb_warehouse_id=None,
        nomenclature_id=env.nomenclature_id,
    )

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["total_orders"] == 2
    reasons = {(r["wb_warehouse_id"], r["reason"]) for r in out["rows"]}
    assert reasons == {(WB_WH_UNLINKED, "no_link"), (None, "no_link")}
    # Не привязано — привязанного склада и остатков нет.
    unlinked = next(r for r in out["rows"] if r["wb_warehouse_id"] == WB_WH_UNLINKED)
    assert unlinked["warehouse_id"] is None
    assert unlinked["our_qty"] == 0
    assert unlinked["ff_loose"] is None


@pytest.mark.asyncio
async def test_inactive_link_counts_as_no_link(db_session, env):
    """Деактивированная привязка = привязки нет (как в самом списании)."""
    link = (
        await db_session.execute(
            select(WbFbsWarehouseLink).where(
                WbFbsWarehouseLink.project_id == env.project_id,
                WbFbsWarehouseLink.wb_warehouse_id == WB_WH,
            )
        )
    ).scalar_one()
    link.is_active = False
    await db_session.commit()

    await _seed(db_session, env.project_id, 90005, nomenclature_id=env.nomenclature_id)
    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["rows"][0]["reason"] == "no_link"


@pytest.mark.asyncio
async def test_soft_deleted_warehouse_counts_as_no_link(db_session, env):
    """Мягко удалённый наш склад = привязки нет → no_link, а не ложный no_stock.

    Канон домена («Мягко удалённый наш склад выпадает из привязок»): раньше
    подзапрос привязок мёртвых не фильтровал, задание получало no_stock с
    пустыми полями, а списание уходило в мёртвый остаток.
    """
    wh = (
        await db_session.execute(select(Warehouse).where(Warehouse.id == env.warehouse_id))
    ).scalar_one()
    wh.is_deleted = True
    # Остаток на мёртвом складе есть — но привязкой он быть перестал.
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            nomenclature_id=env.nomenclature_id,
            barcode=BARCODE,
            quantity=500,
        )
    )
    await db_session.commit()

    await _seed(db_session, env.project_id, 90006, nomenclature_id=env.nomenclature_id)
    out = await orders_service.writeoff_issues(db_session, env.project_id)
    (row,) = out["rows"]
    assert row["reason"] == "no_link"
    # Обогащение мёртвый склад тоже не видит: ни привязки, ни остатков.
    assert row["warehouse_id"] is None
    assert row["our_qty"] == 0
    assert row["ff_loose"] is None


# ─── Пост-классификация queued ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queued_when_stock_covers_all(db_session, env):
    """Остатка хватает на ВСЕ задания группы → queued (очередь, не алярм).

    Задание с полным остатком, ещё не подхваченное 5-минутным джобом, рядом с
    колонкой «Остаток у нас: 500» читалось как алярм no_stock на пустом месте.
    """
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            nomenclature_id=env.nomenclature_id,
            barcode=BARCODE,
            quantity=500,
        )
    )
    await db_session.commit()
    await _seed(db_session, env.project_id, 90070, nomenclature_id=env.nomenclature_id)
    await _seed(db_session, env.project_id, 90071, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    (row,) = out["rows"]
    assert row["reason"] == "queued"
    assert row["stuck"] == 2
    assert row["our_qty"] == 500


@pytest.mark.asyncio
async def test_no_stock_kept_on_partial_deficit(db_session, env):
    """1 ≤ our_qty < stuck — частичный дефицит важнее: остаётся no_stock."""
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            nomenclature_id=env.nomenclature_id,
            barcode=BARCODE,
            quantity=2,
        )
    )
    await db_session.commit()
    for i in range(3):
        await _seed(db_session, env.project_id, 90080 + i, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    (row,) = out["rows"]
    assert row["reason"] == "no_stock"
    assert row["stuck"] == 3
    assert row["our_qty"] == 2


# ─── Агрегация, сортировка, cap ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregation_stuck_and_oldest(db_session, env):
    """Одинаковые (склад, товар, причина) схлопываются: stuck=count, oldest=min."""
    await _seed(
        db_session,
        env.project_id,
        90010,
        nomenclature_id=env.nomenclature_id,
        created_at_wb=datetime(2026, 7, 10, 8, 0, 0),
    )
    await _seed(
        db_session,
        env.project_id,
        90011,
        nomenclature_id=env.nomenclature_id,
        created_at_wb=datetime(2026, 7, 25, 8, 0, 0),
    )

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["total_orders"] == 2
    (row,) = out["rows"]
    assert row["stuck"] == 2
    assert row["oldest_at"] == datetime(2026, 7, 10, 8, 0, 0)


@pytest.mark.asyncio
async def test_cap_rows_but_full_total(db_session, env, monkeypatch):
    """Строк не больше капа (крупнейшие первыми), total_orders — полный."""
    monkeypatch.setattr(orders_service, "_WRITEOFF_ISSUES_MAX_ROWS", 2)
    # Три группы разного размера: 3, 2 и 1 задание.
    for i, (nom_bc, count) in enumerate([("WOI_A", 3), ("WOI_B", 2), ("WOI_C", 1)]):
        nom = Nomenclature(project_id=env.project_id, barcode=nom_bc, chrt_id=882000 + i)
        db_session.add(nom)
        await db_session.flush()
        for j in range(count):
            await _seed(
                db_session,
                env.project_id,
                90100 + i * 10 + j,
                barcode=nom_bc,
                nomenclature_id=nom.id,
            )

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["total_orders"] == 6
    assert [r["stuck"] for r in out["rows"]] == [3, 2]
    # Срез виден флагом — фронт обязан сказать «показаны первые N».
    assert out["truncated"] is True


# ─── Обогащение остатками ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_our_qty_sums_all_linked_warehouses(db_session, env):
    """our_qty/our_defect — Σ по ВСЕМ активным привязкам склада продавца."""
    second = Warehouse(
        project_id=env.project_id,
        name="Наш склад WOI-2",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        WbFbsWarehouseLink(
            project_id=env.project_id,
            wb_warehouse_id=WB_WH,
            warehouse_id=second.id,
            is_active=True,
        )
    )
    db_session.add_all(
        [
            WarehouseStock(
                project_id=env.project_id,
                warehouse_id=env.warehouse_id,
                nomenclature_id=env.nomenclature_id,
                barcode=BARCODE,
                quantity=0,
                defect_quantity=2,
            ),
            WarehouseStock(
                project_id=env.project_id,
                warehouse_id=second.id,
                nomenclature_id=env.nomenclature_id,
                barcode=BARCODE,
                quantity=0,
                defect_quantity=1,
            ),
        ]
    )
    await db_session.commit()
    await _seed(db_session, env.project_id, 90020, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    (row,) = out["rows"]
    assert row["reason"] == "no_stock"
    assert row["our_qty"] == 0
    assert row["our_defect"] == 3
    # Первая активная привязка — исходный склад env.
    assert row["warehouse_id"] == env.warehouse_id


@pytest.mark.asyncio
async def test_ff_loose_from_mirror_excludes_boxes(db_session, env):
    """ff_loose = россыпь зеркала (base_barcode IS NULL), короб не считается."""
    db_session.add_all(
        [
            FulfillmentStock(
                project_id=env.project_id,
                warehouse_id=env.warehouse_id,
                provider="migfull",
                barcode=BARCODE,
                nomenclature_id=env.nomenclature_id,
                qty_good=7,
                units_per_box=1,
            ),
            # Короб (base_barcode задан) — НЕ россыпь, в ff_loose не идёт.
            FulfillmentStock(
                project_id=env.project_id,
                warehouse_id=env.warehouse_id,
                provider="migfull",
                barcode="10000000000017",
                base_barcode=BARCODE,
                units_per_box=10,
                qty_good=2,
                nomenclature_id=env.nomenclature_id,
            ),
        ]
    )
    await db_session.commit()
    await _seed(db_session, env.project_id, 90030, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    (row,) = out["rows"]
    assert row["ff_loose"] == 7


@pytest.mark.asyncio
async def test_ff_loose_zero_when_mirror_exists_without_sku(db_session, env):
    """Зеркало у склада есть, но без этого товара → 0 (а не None)."""
    db_session.add(
        FulfillmentStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            provider="skladbot",
            barcode="OTHER_BC",
            qty_good=99,
        )
    )
    await db_session.commit()
    await _seed(db_session, env.project_id, 90031, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["rows"][0]["ff_loose"] == 0


# ─── Скоуп выборки ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_written_off_and_non_complete_excluded(db_session, env):
    """Списанные и не-complete задания в сводку не попадают."""
    await _seed(
        db_session,
        env.project_id,
        90040,
        nomenclature_id=env.nomenclature_id,
        written_off_at=datetime(2026, 7, 21, 0, 0, 0),
    )
    await _seed(
        db_session,
        env.project_id,
        90041,
        nomenclature_id=env.nomenclature_id,
        supplier_status=FbsSupplierStatus.NEW.value,
    )

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["total_orders"] == 0
    assert out["rows"] == []


@pytest.mark.asyncio
async def test_sandbox_contour_excluded(db_session, env):
    """Задание песочницы (метка контура в raw) не участвует в боевой сводке."""
    await _seed(
        db_session,
        env.project_id,
        90050,
        nomenclature_id=env.nomenclature_id,
        raw={"_dds_contour": "sandbox"},
    )
    await _seed(db_session, env.project_id, 90051, nomenclature_id=env.nomenclature_id)

    out = await orders_service.writeoff_issues(db_session, env.project_id)
    assert out["total_orders"] == 1
    assert out["rows"][0]["stuck"] == 1


@pytest.mark.asyncio
async def test_project_isolation(db_session, env, other_project):
    """Чужие задания не видны — и наоборот."""
    await _seed(db_session, env.project_id, 90060, nomenclature_id=env.nomenclature_id)
    await _seed(db_session, other_project.id, 90061, nomenclature_id=None)

    ours = await orders_service.writeoff_issues(db_session, env.project_id)
    theirs = await orders_service.writeoff_issues(db_session, other_project.id)
    assert ours["total_orders"] == 1
    assert ours["rows"][0]["reason"] == "no_stock"
    assert theirs["total_orders"] == 1
    assert theirs["rows"][0]["reason"] == "no_card"
