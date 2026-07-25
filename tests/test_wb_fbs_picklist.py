"""
Тесты WB FBS — ЛИСТ ПОДБОРА поставки (`supplies_service.pick_list`).

Лист подбора — документ для СБОРЩИКА: что и сколько физически снять со склада.
Отличие от состава поставки в том, что он агрегирован ПО ТОВАРУ, а не по
заказам: WB количество не агрегирует (одно задание = одна единица), и 47
заданий одного артикула сборщик обязан видеть одной строкой «снять 47 шт».

Что закрыто:
  • два задания одной позиции → одна строка qty=2 и сумма их цен;
  • РАЗНЫЕ БАРКОДЫ одного chrtId не схлопываются в одну позицию: матчинг идёт
    по chrtId, оба задания получают один nomenclature_id — без баркода в ключе
    сборщику печатался один баркод на всё количество и остаток одной из
    номенклатур;
  • `amount` считается по `sale_price` (фолбэк `price`) — тем же полем, что
    показывает колонка «Цена, ₽» списков заданий;
  • задание без нашей номенклатуры не теряется (группируется по chrtId/баркоду
    и помечается `nomenclature_id is None` — сборщику его тоже надо найти);
  • `stock_available` — сырой остаток привязанных к складу продавца наших
    складов; без привязок это None («неизвестно»), а не 0 («товара нет»);
  • отменённые задания в лист не попадают, но из шапки не исчезают;
  • изоляция по project_id: одноимённая поставка соседа не подмешивается;
  • пустая поставка → пустой список с корректной шапкой;
  • чужая / неизвестная поставка → доменная ошибка;
  • стабильная сортировка строк (артикул, затем баркод) — печать за печатью.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.warehouse import Warehouse, WarehouseStock
from backend.models.wb_fbs import (
    FbsSupplierStatus,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.services.wb_fbs import supplies_service
from backend.services.wb_fbs.supplies_service import FbsSupplyError
from backend.utils.time import utcnow

# ─── Хелперы ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _wb_id() -> int:
    """Уникальный id склада продавца (уникальность — в паре с project_id)."""
    return int(uuid.uuid4().int % 10_000_000) + 1


_ORDER_SEQ = iter(range(9_100_000, 9_200_000))


def _order_id() -> int:
    return next(_ORDER_SEQ)


async def _mk_nom(
    db: AsyncSession,
    project_id: int,
    *,
    article: str,
    chrt_id: int,
    brand: str | None = None,
    barcode: str | None = None,
) -> Nomenclature:
    nom = Nomenclature(
        project_id=project_id,
        barcode=barcode or f"PICK{_uid()}",
        chrt_id=chrt_id,
        article_seller=article,
        article_wb=_wb_id(),
        brand=brand,
        subject="Ковёр",
    )
    db.add(nom)
    await db.flush()
    return nom


async def _mk_order(
    db: AsyncSession,
    project_id: int,
    supply_id: str,
    *,
    wb_warehouse_id: int,
    nom: Nomenclature | None = None,
    barcode: str | None = None,
    chrt_id: int | None = None,
    price: Decimal | None = Decimal("100.50"),
    sale_price: Decimal | None = None,
    status: str = FbsSupplierStatus.CONFIRM.value,
    article: str | None = None,
) -> WbFbsOrder:
    """Одно сборочное задание = одна единица товара (так их отдаёт WB)."""
    order = WbFbsOrder(
        project_id=project_id,
        wb_order_id=_order_id(),
        supply_id=supply_id,
        wb_warehouse_id=wb_warehouse_id,
        nomenclature_id=nom.id if nom else None,
        barcode=barcode if barcode is not None else (nom.barcode if nom else None),
        chrt_id=chrt_id if chrt_id is not None else (nom.chrt_id if nom else None),
        nm_id=nom.article_wb if nom else None,
        article=article if article is not None else (nom.article_seller if nom else None),
        subject="Ковёр",
        price=price,
        sale_price=sale_price,
        cargo_type=1,
        cross_border_type=0,
        supplier_status=status,
    )
    db.add(order)
    await db.flush()
    return order


async def _mk_supply(db: AsyncSession, project_id: int, wb_supply_id: str, **over) -> WbFbsSupply:
    fields: dict = {
        "project_id": project_id,
        "wb_supply_id": wb_supply_id,
        "name": "Поставка на подбор",
        "done": False,
        "cargo_type": 1,
        "created_at_wb": utcnow(),
        "orders_count": 0,
    }
    fields.update(over)
    supply = WbFbsSupply(**fields)
    db.add(supply)
    await db.flush()
    return supply


@pytest_asyncio.fixture
async def env(db_session, project):
    """Склад продавца WB + привязанный наш склад + поставка."""
    from types import SimpleNamespace

    wb_warehouse_id = _wb_id()
    db_session.add(
        WbFbsWarehouse(
            project_id=project.id,
            wb_warehouse_id=wb_warehouse_id,
            name="Склад Москва",
            is_active=True,
        )
    )
    warehouse = Warehouse(project_id=project.id, name=f"Наш склад {_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(warehouse)
    await db_session.flush()
    db_session.add(
        WbFbsWarehouseLink(
            project_id=project.id,
            wb_warehouse_id=wb_warehouse_id,
            warehouse_id=warehouse.id,
            is_active=True,
        )
    )
    supply_id = f"WB-GI-PICK-{_uid()}"
    await _mk_supply(db_session, project.id, supply_id, wb_warehouse_id=wb_warehouse_id)
    await db_session.commit()

    return SimpleNamespace(
        project_id=project.id,
        wb_warehouse_id=wb_warehouse_id,
        warehouse_id=warehouse.id,
        supply_id=supply_id,
    )


def _row_by_article(result: dict, article: str) -> dict:
    return next(r for r in result["rows"] if r["article"] == article)


# ─── Агрегация ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pick_list_aggregates_orders_by_position(db_session, env):
    """Два задания одной позиции → ОДНА строка qty=2, amount = сумма цен."""
    nom = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555001)
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom)
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom)
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["positions_count"] == 1
    assert result["total_qty"] == 2
    assert result["orders_count"] == 2
    row = result["rows"][0]
    assert row["qty"] == 2
    assert row["nomenclature_id"] == nom.id
    assert row["barcode"] == nom.barcode
    assert row["chrt_id"] == 555001
    assert row["article"] == "ART-A"
    assert Decimal(row["amount"]) == Decimal("201.00")
    assert Decimal(result["total_amount"]) == Decimal("201.00")


@pytest.mark.asyncio
async def test_pick_list_sums_amount_across_positions(db_session, env):
    """`total_amount` — сумма по всем строкам, а не по первой попавшейся."""
    nom_a = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555002)
    nom_b = await _mk_nom(db_session, env.project_id, article="ART-B", chrt_id=555003)
    await _mk_order(
        db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_a,
        price=Decimal("10.00"),
    )
    await _mk_order(
        db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_a,
        price=Decimal("10.00"),
    )
    await _mk_order(
        db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_b,
        price=Decimal("2.50"),
    )
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["positions_count"] == 2
    assert result["total_qty"] == 3
    assert Decimal(_row_by_article(result, "ART-A")["amount"]) == Decimal("20.00")
    assert Decimal(_row_by_article(result, "ART-B")["amount"]) == Decimal("2.50")
    assert Decimal(result["total_amount"]) == Decimal("22.50")


@pytest.mark.asyncio
async def test_pick_list_splits_barcodes_of_one_chrt(db_session, env):
    """Разные баркоды одного chrtId — РАЗНЫЕ позиции со своими остатками.

    Матчинг заданий идёт по chrtId, а пары (barcode → chrtId) many-to-one:
    задания обоих баркодов получают ОДИН `nomenclature_id` (первая
    номенклатура chrt). Свёртка без баркода печатала сборщику одну строку
    «снять 4 шт по BCA…» с остатком одной номенклатуры — строка ещё и краснела
    как нехватка, хотя на полке под двумя баркодами лежит 8 шт.
    """
    chrt = 970001
    nom_a = await _mk_nom(
        db_session, env.project_id, article="ART-A", chrt_id=chrt, barcode=f"BCA{_uid()}"
    )
    nom_b = await _mk_nom(
        db_session, env.project_id, article="ART-A2", chrt_id=chrt, barcode=f"BCB{_uid()}"
    )
    for nom, qty in ((nom_a, 3), (nom_b, 5)):
        db_session.add(
            WarehouseStock(
                project_id=env.project_id,
                warehouse_id=env.warehouse_id,
                nomenclature_id=nom.id,
                barcode=nom.barcode,
                quantity=qty,
            )
        )
    # Все четыре задания сматчены ПО CHRT → nomenclature_id первой номенклатуры.
    for barcode in (nom_a.barcode, nom_a.barcode, nom_b.barcode, nom_b.barcode):
        await _mk_order(
            db_session,
            env.project_id,
            env.supply_id,
            wb_warehouse_id=env.wb_warehouse_id,
            nom=nom_a,
            barcode=barcode,
            chrt_id=chrt,
        )
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["positions_count"] == 2
    assert result["total_qty"] == 4
    by_barcode = {r["barcode"]: r for r in result["rows"]}
    assert by_barcode[nom_a.barcode]["qty"] == 2
    assert by_barcode[nom_a.barcode]["stock_available"] == 3
    assert by_barcode[nom_b.barcode]["qty"] == 2
    # Остаток — по номенклатуре ЭТОГО баркода, а не по сматченной по chrt.
    assert by_barcode[nom_b.barcode]["stock_available"] == 5
    assert by_barcode[nom_b.barcode]["article"] == "ART-A2"


@pytest.mark.asyncio
async def test_pick_list_amount_uses_sale_price(db_session, env):
    """`amount` — по `sale_price` (фолбэк `price`): им же подписана «Цена, ₽».

    Лист, сложенный по `price`, расходился со списком заданий той же поставки
    до ~40 %, хотя оба документа заявлены «для сверки при отгрузке».
    """
    nom_a = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555014)
    nom_b = await _mk_nom(db_session, env.project_id, article="ART-B", chrt_id=555015)
    await _mk_order(
        db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_a,
        price=Decimal("11438.00"), sale_price=Decimal("19278.00"),
    )
    # sale_price пуст → берём price, иначе позиция молча уходила бы в ноль.
    await _mk_order(
        db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_b,
        price=Decimal("3780.00"), sale_price=None,
    )
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert Decimal(_row_by_article(result, "ART-A")["amount"]) == Decimal("19278.00")
    assert Decimal(_row_by_article(result, "ART-B")["amount"]) == Decimal("3780.00")
    assert Decimal(result["total_amount"]) == Decimal("23058.00")


@pytest.mark.asyncio
async def test_pick_list_keeps_unmatched_position(db_session, env):
    """Задание без нашей номенклатуры не теряется: сборщику его тоже искать.

    Такая строка группируется по (chrtId, баркод), помечена
    `nomenclature_id is None`, а остаток по ней неизвестен — None, не 0.
    """
    nom = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555004)
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom)
    for _ in range(2):
        await _mk_order(
            db_session,
            env.project_id,
            env.supply_id,
            wb_warehouse_id=env.wb_warehouse_id,
            nom=None,
            barcode="ORPHAN-BC",
            chrt_id=777777,
            article="ART-ORPHAN",
        )
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["positions_count"] == 2
    assert result["total_qty"] == 3
    orphan = next(r for r in result["rows"] if r["nomenclature_id"] is None)
    assert orphan["qty"] == 2
    assert orphan["barcode"] == "ORPHAN-BC"
    assert orphan["chrt_id"] == 777777
    # Остаток посчитать нечем — честное «неизвестно», а не ложный ноль.
    assert orphan["stock_available"] is None


# ─── Остаток ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pick_list_stock_from_linked_warehouses(db_session, env):
    """`stock_available` — сырой остаток наших складов, привязанных к складу WB."""
    nom_a = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555005)
    nom_b = await _mk_nom(db_session, env.project_id, article="ART-B", chrt_id=555006)
    db_session.add(
        WarehouseStock(
            project_id=env.project_id,
            warehouse_id=env.warehouse_id,
            nomenclature_id=nom_a.id,
            barcode=nom_a.barcode,
            quantity=7,
            defect_quantity=3,
        )
    )
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_a)
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom_b)
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    # Брак в остаток не входит — снимают с полки только годное.
    assert _row_by_article(result, "ART-A")["stock_available"] == 7
    # Строки остатка нет вовсе → на складе физически ноль.
    assert _row_by_article(result, "ART-B")["stock_available"] == 0


@pytest.mark.asyncio
async def test_pick_list_stock_unknown_without_links(db_session, project):
    """Склад продавца без привязок → остаток None: смотреть просто негде."""
    wb_warehouse_id = _wb_id()
    db_session.add(
        WbFbsWarehouse(
            project_id=project.id, wb_warehouse_id=wb_warehouse_id, name="Склад без привязок", is_active=True
        )
    )
    supply_id = f"WB-GI-NOLINK-{_uid()}"
    await _mk_supply(db_session, project.id, supply_id, wb_warehouse_id=wb_warehouse_id)
    nom = await _mk_nom(db_session, project.id, article="ART-A", chrt_id=555007)
    await _mk_order(db_session, project.id, supply_id, wb_warehouse_id=wb_warehouse_id, nom=nom)
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, project.id, supply_id)

    assert result["rows"][0]["stock_available"] is None


# ─── Статусы, изоляция, край ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pick_list_skips_cancelled_orders(db_session, env):
    """Отменённое задание снимать со склада не надо — но из шапки оно не исчезает."""
    nom = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555008)
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom)
    await _mk_order(
        db_session,
        env.project_id,
        env.supply_id,
        wb_warehouse_id=env.wb_warehouse_id,
        nom=nom,
        status=FbsSupplierStatus.CANCEL.value,
    )
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["total_qty"] == 1  # снимаем одну штуку
    assert result["orders_count"] == 2  # но заданий в поставке два
    assert result["rows"][0]["qty"] == 1


@pytest.mark.asyncio
async def test_pick_list_isolated_by_project(db_session, env, other_project):
    """Одноимённая поставка соседнего проекта в лист не подмешивается."""
    nom = await _mk_nom(db_session, env.project_id, article="ART-A", chrt_id=555009)
    await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom)

    # Тот же wb_supply_id у соседа: natural key — пара (project_id, wb_supply_id).
    await _mk_supply(db_session, other_project.id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id)
    alien_nom = await _mk_nom(db_session, other_project.id, article="ART-ALIEN", chrt_id=555010)
    for _ in range(3):
        await _mk_order(
            db_session,
            other_project.id,
            env.supply_id,
            wb_warehouse_id=env.wb_warehouse_id,
            nom=alien_nom,
        )
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["positions_count"] == 1
    assert result["total_qty"] == 1
    assert [r["article"] for r in result["rows"]] == ["ART-A"]


@pytest.mark.asyncio
async def test_pick_list_empty_supply_keeps_header(db_session, env):
    """Пустая поставка → пустой список строк, но шапка обязана быть заполнена."""
    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert result["rows"] == []
    assert result["positions_count"] == 0
    assert result["total_qty"] == 0
    assert result["orders_count"] == 0
    assert result["total_amount"] is None
    assert result["wb_supply_id"] == env.supply_id
    assert result["supply_name"] == "Поставка на подбор"
    assert result["wb_warehouse_id"] == env.wb_warehouse_id
    assert result["wb_warehouse_name"] == "Склад Москва"
    assert result["cargo_type"] == 1
    assert result["done"] is False
    assert result["created_at_wb"] is not None


@pytest.mark.asyncio
async def test_pick_list_foreign_supply_raises(db_session, env, other_project):
    """Поставка чужого проекта / несуществующая — доменная ошибка, а не пустой лист."""
    with pytest.raises(FbsSupplyError):
        await supplies_service.pick_list(db_session, other_project.id, env.supply_id)

    with pytest.raises(FbsSupplyError):
        await supplies_service.pick_list(db_session, env.project_id, "WB-GI-НЕТ-ТАКОЙ")


@pytest.mark.asyncio
async def test_pick_list_sorted_by_article_then_barcode(db_session, env):
    """Порядок строк стабилен между печатями: артикул, затем баркод."""
    for article, chrt in (("ART-C", 555011), ("ART-A", 555012), ("ART-B", 555013)):
        nom = await _mk_nom(db_session, env.project_id, article=article, chrt_id=chrt)
        await _mk_order(db_session, env.project_id, env.supply_id, wb_warehouse_id=env.wb_warehouse_id, nom=nom)
    await db_session.commit()

    result = await supplies_service.pick_list(db_session, env.project_id, env.supply_id)

    assert [r["article"] for r in result["rows"]] == ["ART-A", "ART-B", "ART-C"]
