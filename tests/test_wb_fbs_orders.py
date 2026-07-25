"""
Тесты WB FBS — сборочные задания и поставки (полоса 3).

`WbFbsClient` замокан целиком: проверяем оркестрацию, нормализацию payload'а
и инварианты домена, без единого HTTP к WB.

Что закрыто:
  • upsert-идемпотентность синка (двойной прогон + дубли в одном payload'е);
  • цены приходят В КОПЕЙКАХ → Numeric(18,2) в рублях;
  • RFC3339 со смещением → naive UTC;
  • синк новых заданий не откатывает статус уже известного задания;
  • статусы опрашиваются только для НЕ-терминальных заданий;
  • add_orders отбивает смешанные склады / габариты (габаритный залипон);
  • writeoff строго идемпотентен и не уводит остаток в минус;
  • изоляция по project_id.
"""

from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import (
    FbsSupplierStatus,
    Nomenclature,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.models.warehouse import StockMovement, Warehouse, WarehouseStock, WarehouseType
from backend.services.wb_fbs import orders_service, supplies_service
from backend.services.wb_fbs.orders_service import FbsOrderError
from backend.services.wb_fbs.supplies_service import FbsSupplyError

WB_WAREHOUSE_ID = 555001
WB_WAREHOUSE_ID_2 = 555002
CHRT_ID = 991001
BARCODE = "FBS_TEST_BC_1"
SUPPLY_ID = "WB-GI-TEST-1"


# ─── Мок клиента WB ──────────────────────────────────────────────────────────


class FakeFbsClient:
    """Мок `WbFbsClient`: отдаёт заготовленные payload'ы, пишет вызовы."""

    def __init__(
        self,
        *,
        new_orders=None,
        statuses=None,
        stickers=None,
        supplies=None,
        supply_order_ids=None,
        supply_barcode=None,
    ):
        self.new_orders = new_orders or []
        self.statuses = statuses or []
        self.stickers = stickers or []
        self.supplies = supplies or []
        self.supply_order_ids = supply_order_ids or {}
        self.supply_barcode = supply_barcode or {}
        # Записи вызовов
        self.status_calls: list[list[int]] = []
        self.sticker_calls: list[tuple] = []
        self.cancelled: list[int] = []
        self.created: list[str] = []
        self.added: list[tuple[str, list[int]]] = []
        self.delivered: list[str] = []
        self.deleted: list[str] = []

    async def get_new_orders(self) -> list[dict]:
        return list(self.new_orders)

    async def get_orders_status(self, order_ids) -> list[dict]:
        self.status_calls.append(list(order_ids))
        asked = set(order_ids)
        return [s for s in self.statuses if s.get("id") in asked]

    async def cancel_order(self, order_id) -> None:
        self.cancelled.append(order_id)

    async def get_stickers(self, order_ids, sticker_type="png", width=58, height=40) -> list[dict]:
        self.sticker_calls.append((list(order_ids), sticker_type, width, height))
        return list(self.stickers)

    async def create_supply(self, name) -> str:
        self.created.append(name)
        return SUPPLY_ID

    async def list_supplies(self, max_pages=20) -> list[dict]:
        return list(self.supplies)

    async def get_supply_order_ids(self, supply_id) -> list[int]:
        return list(self.supply_order_ids.get(supply_id, []))

    async def add_orders_to_supply(self, supply_id, order_ids) -> None:
        self.added.append((supply_id, list(order_ids)))

    async def deliver_supply(self, supply_id) -> None:
        self.delivered.append(supply_id)

    async def delete_supply(self, supply_id) -> None:
        self.deleted.append(supply_id)

    async def get_supply_barcode(self, supply_id, sticker_type="png") -> dict:
        return dict(self.supply_barcode)


def _patch_client(monkeypatch, client: FakeFbsClient) -> FakeFbsClient:
    async def fake_get(db, project_id):
        return client

    monkeypatch.setattr(orders_service, "get_fbs_client", fake_get)
    monkeypatch.setattr(supplies_service, "get_fbs_client", fake_get)
    return client


# ─── Фикстуры данных ─────────────────────────────────────────────────────────


def _raw_order(wb_order_id: int, **over) -> dict:
    """Payload задания в форме WB: цены ×100, createdAt со смещением."""
    base = {
        "id": wb_order_id,
        "rid": f"rid-{wb_order_id}",
        "orderUid": f"uid-{wb_order_id}",
        "createdAt": "2026-07-20T15:30:00+03:00",
        "warehouseId": WB_WAREHOUSE_ID,
        "officeId": 15,
        "offices": ["Коледино"],
        "nmId": 123456,
        "chrtId": CHRT_ID,
        "skus": [BARCODE],
        "article": "ART-1",
        "price": 137900,  # копейки → 1379.00 ₽
        "convertedPrice": 137900,
        "salePrice": 129900,  # копейки → 1299.00 ₽
        "currencyCode": 643,
        "cargoType": 1,
        "crossBorderType": 0,
        "isZeroOrder": False,
        "isPickupPointShipmentAllowed": True,
        "ddate": "2026-07-22",
        "comment": "тест",
    }
    base.update(over)
    return base


@pytest_asyncio.fixture
async def env(db_session, project):
    """Наш склад + номенклатура с chrt_id + остаток + склад WB с привязкой."""
    warehouse = Warehouse(
        project_id=project.id,
        name="FBS Тестовый склад",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(warehouse)
    await db_session.flush()

    nom = Nomenclature(project_id=project.id, barcode=BARCODE, chrt_id=CHRT_ID, subject="Ковёр")
    db_session.add(nom)
    await db_session.flush()

    db_session.add(
        WarehouseStock(
            project_id=project.id,
            warehouse_id=warehouse.id,
            nomenclature_id=nom.id,
            barcode=BARCODE,
            quantity=5,
        )
    )
    db_session.add(
        WbFbsWarehouse(
            project_id=project.id,
            wb_warehouse_id=WB_WAREHOUSE_ID,
            name="Склад продавца",
            is_active=True,
        )
    )
    db_session.add(
        WbFbsWarehouseLink(
            project_id=project.id,
            wb_warehouse_id=WB_WAREHOUSE_ID,
            warehouse_id=warehouse.id,
            is_active=True,
        )
    )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(project_id=project.id, warehouse_id=warehouse.id, nomenclature_id=nom.id)


async def _orders(db_session, project_id: int) -> list[WbFbsOrder]:
    result = await db_session.execute(
        select(WbFbsOrder).where(WbFbsOrder.project_id == project_id).order_by(WbFbsOrder.wb_order_id)
    )
    return list(result.scalars().all())


async def _stock_qty(db_session, project_id: int, warehouse_id: int, nomenclature_id: int) -> int:
    result = await db_session.execute(
        select(WarehouseStock.quantity).where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id == warehouse_id,
            WarehouseStock.nomenclature_id == nomenclature_id,
        )
    )
    return int(result.scalar() or 0)


async def _seed_order(db_session, project_id: int, wb_order_id: int, **over) -> WbFbsOrder:
    """Прямая вставка задания (минуя синк) — для сценариев поставок/списания."""
    fields = {
        "project_id": project_id,
        "wb_order_id": wb_order_id,
        "wb_warehouse_id": WB_WAREHOUSE_ID,
        "barcode": BARCODE,
        "chrt_id": CHRT_ID,
        "cargo_type": 1,
        "cross_border_type": 0,
        "supplier_status": FbsSupplierStatus.NEW.value,
    }
    fields.update(over)
    order = WbFbsOrder(**fields)
    db_session.add(order)
    await db_session.commit()
    return order


# ─── Синк заданий: upsert, копейки, RFC3339 ──────────────────────────────────


@pytest.mark.asyncio
async def test_sync_new_orders_normalizes_payload(db_session, env, monkeypatch):
    """Копейки → рубли, RFC3339 со смещением → naive UTC, резолв по chrtId."""
    _patch_client(monkeypatch, FakeFbsClient(new_orders=[_raw_order(7001)]))

    count = await orders_service.sync_new_orders(db_session, env.project_id)
    assert count == 1

    rows = await _orders(db_session, env.project_id)
    assert len(rows) == 1
    order = rows[0]
    # Цены приходят ×100 — в БД рубли с двумя знаками.
    assert order.price == Decimal("1379.00")
    assert order.sale_price == Decimal("1299.00")
    assert order.converted_price == Decimal("1379.00")
    # 15:30 +03:00 → 12:30 UTC, naive (колонка DateTime без таймзоны).
    assert order.created_at_wb == datetime(2026, 7, 20, 12, 30, 0)
    assert order.created_at_wb.tzinfo is None
    # Резолв номенклатуры по chrtId + подтянутый из карточки предмет.
    assert order.nomenclature_id == env.nomenclature_id
    assert order.subject == "Ковёр"
    assert order.barcode == BARCODE
    assert order.office_name == "Коледино"
    assert order.supplier_status == FbsSupplierStatus.NEW.value
    assert order.raw["id"] == 7001


@pytest.mark.asyncio
async def test_sync_new_orders_is_idempotent(db_session, env, monkeypatch):
    """Двойной прогон + дубль ключа в одном payload'е → одна строка, без CardinalityViolation."""
    client = _patch_client(
        monkeypatch,
        # Тот же id дважды в одной пачке — дедуп обязан случиться ДО executemany.
        FakeFbsClient(new_orders=[_raw_order(7002), _raw_order(7002, comment="дубль"), _raw_order(7003)]),
    )

    first = await orders_service.sync_new_orders(db_session, env.project_id)
    assert first == 2  # дубль схлопнулся

    client.new_orders = [_raw_order(7002, price=150000), _raw_order(7003)]
    second = await orders_service.sync_new_orders(db_session, env.project_id)
    assert second == 2

    rows = await _orders(db_session, env.project_id)
    assert [r.wb_order_id for r in rows] == [7002, 7003]
    # Повторный синк обновил изменившиеся поля.
    assert rows[0].price == Decimal("1500.00")


@pytest.mark.asyncio
async def test_sync_new_orders_does_not_reset_status(db_session, env, monkeypatch):
    """Задание уже `complete` — синк новых заданий не откатывает его в `new`.

    Иначе списанное задание вернулось бы в открытые и списалось повторно.
    """
    await _seed_order(
        db_session,
        env.project_id,
        7004,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        supply_id=SUPPLY_ID,
    )
    _patch_client(monkeypatch, FakeFbsClient(new_orders=[_raw_order(7004)]))

    await orders_service.sync_new_orders(db_session, env.project_id)

    db_session.expire_all()
    rows = await _orders(db_session, env.project_id)
    assert rows[0].supplier_status == FbsSupplierStatus.COMPLETE.value
    assert rows[0].supply_id == SUPPLY_ID
    # Описательные поля при этом обновились.
    assert rows[0].price == Decimal("1379.00")


# ─── Синк статусов ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_order_statuses_skips_terminal(db_session, env, monkeypatch):
    """Терминальные задания WB больше не меняет — не тратим на них лимит."""
    await _seed_order(db_session, env.project_id, 7010, supplier_status=FbsSupplierStatus.NEW.value)
    await _seed_order(db_session, env.project_id, 7011, supplier_status=FbsSupplierStatus.CANCEL.value)
    await _seed_order(
        db_session, env.project_id, 7012, supplier_status=FbsSupplierStatus.CANCEL_CARRIER.value
    )

    client = _patch_client(
        monkeypatch,
        FakeFbsClient(
            statuses=[{"id": 7010, "supplierStatus": "confirm", "wbStatus": "sorted", "isCancellable": False}]
        ),
    )

    updated = await orders_service.sync_order_statuses(db_session, env.project_id)
    assert updated == 1
    assert client.status_calls == [[7010]]

    db_session.expire_all()
    rows = {o.wb_order_id: o for o in await _orders(db_session, env.project_id)}
    assert rows[7010].supplier_status == FbsSupplierStatus.CONFIRM.value
    assert rows[7010].wb_status == "sorted"
    assert rows[7011].supplier_status == FbsSupplierStatus.CANCEL.value


# ─── Стикеры ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_stickers_caches_parts_and_returns_file(db_session, env, monkeypatch):
    """partA/partB/barcode кэшируем в БД, base64-файл отдаём вызывающему."""
    await _seed_order(db_session, env.project_id, 7020, supplier_status=FbsSupplierStatus.CONFIRM.value)
    _patch_client(
        monkeypatch,
        FakeFbsClient(
            stickers=[{"orderId": 7020, "partA": "101", "partB": "9988", "barcode": "!uAB13", "file": "BASE64"}]
        ),
    )

    out = await orders_service.get_stickers(db_session, env.project_id, [7020])
    assert out == [
        {"order_id": 7020, "part_a": "101", "part_b": "9988", "barcode": "!uAB13", "file": "BASE64"}
    ]

    db_session.expire_all()
    rows = await _orders(db_session, env.project_id)
    assert rows[0].sticker_part_a == "101"
    assert rows[0].sticker_barcode == "!uAB13"
    # Файл в БД не кладём — только кэш частей стикера.
    assert rows[0].sticker_file_key is None


@pytest.mark.asyncio
async def test_get_stickers_rejects_new_orders(db_session, env, monkeypatch):
    """WB печатает стикер только для confirm/complete."""
    await _seed_order(db_session, env.project_id, 7021, supplier_status=FbsSupplierStatus.NEW.value)
    _patch_client(monkeypatch, FakeFbsClient())

    with pytest.raises(FbsOrderError, match="только для заданий"):
        await orders_service.get_stickers(db_session, env.project_id, [7021])


# ─── Поставки: габаритный залипон ────────────────────────────────────────────


async def _make_supply(db_session, monkeypatch, project_id: int, client: FakeFbsClient) -> str:
    _patch_client(monkeypatch, client)
    return await supplies_service.create_supply(db_session, project_id, "Тестовая поставка")


@pytest.mark.asyncio
async def test_add_orders_rejects_mixed_warehouses(db_session, env, monkeypatch):
    """Задания с разных складов продавца в одну поставку WB не принимает."""
    client = FakeFbsClient()
    supply_id = await _make_supply(db_session, monkeypatch, env.project_id, client)
    await _seed_order(db_session, env.project_id, 7030, wb_warehouse_id=WB_WAREHOUSE_ID)
    await _seed_order(db_session, env.project_id, 7031, wb_warehouse_id=WB_WAREHOUSE_ID_2)

    with pytest.raises(FbsSupplyError, match="разных складов"):
        await supplies_service.add_orders(db_session, env.project_id, supply_id, [7030, 7031])
    # До WB дело не дошло — 4XX стоит 10 запросов бакета.
    assert client.added == []


@pytest.mark.asyncio
async def test_add_orders_rejects_mixed_cargo_type(db_session, env, monkeypatch):
    """Габаритный залипон: в поставке допустим один cargoType."""
    client = FakeFbsClient()
    supply_id = await _make_supply(db_session, monkeypatch, env.project_id, client)
    await _seed_order(db_session, env.project_id, 7032, cargo_type=1)
    await _seed_order(db_session, env.project_id, 7033, cargo_type=2)

    with pytest.raises(FbsSupplyError, match="cargoType"):
        await supplies_service.add_orders(db_session, env.project_id, supply_id, [7032, 7033])
    assert client.added == []


@pytest.mark.asyncio
async def test_add_orders_rejects_foreign_cargo_after_first(db_session, env, monkeypatch):
    """Первое задание зафиксировало габарит — второе с другим уже не лезет."""
    client = FakeFbsClient()
    supply_id = await _make_supply(db_session, monkeypatch, env.project_id, client)
    await _seed_order(db_session, env.project_id, 7034, cargo_type=1)
    await _seed_order(db_session, env.project_id, 7035, cargo_type=3)

    await supplies_service.add_orders(db_session, env.project_id, supply_id, [7034])
    with pytest.raises(FbsSupplyError, match="зафиксирована на cargoType"):
        await supplies_service.add_orders(db_session, env.project_id, supply_id, [7035])
    assert client.added == [(supply_id, [7034])]


@pytest.mark.asyncio
async def test_add_orders_happy_path(db_session, env, monkeypatch):
    """Задания уезжают в поставку → `confirm`, поставка фиксирует склад и габарит."""
    client = FakeFbsClient()
    supply_id = await _make_supply(db_session, monkeypatch, env.project_id, client)
    await _seed_order(db_session, env.project_id, 7036)
    await _seed_order(db_session, env.project_id, 7037)

    added = await supplies_service.add_orders(db_session, env.project_id, supply_id, [7036, 7037])
    assert added == 2
    assert client.added == [(supply_id, [7036, 7037])]

    db_session.expire_all()
    rows = await _orders(db_session, env.project_id)
    assert all(r.supplier_status == FbsSupplierStatus.CONFIRM.value for r in rows)
    assert all(r.supply_id == supply_id for r in rows)

    supply = (
        await db_session.execute(
            select(WbFbsSupply).where(
                WbFbsSupply.project_id == env.project_id, WbFbsSupply.wb_supply_id == supply_id
            )
        )
    ).scalar_one()
    assert supply.wb_warehouse_id == WB_WAREHOUSE_ID
    assert supply.cargo_type == 1
    assert supply.orders_count == 2


# ─── Списание в ledger ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writeoff_is_idempotent(db_session, env):
    """Второй прогон не списывает повторно (`written_off_at`)."""
    await _seed_order(
        db_session,
        env.project_id,
        7040,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        supply_id=SUPPLY_ID,
        nomenclature_id=env.nomenclature_id,
    )
    await _seed_order(
        db_session,
        env.project_id,
        7041,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        supply_id=SUPPLY_ID,
        nomenclature_id=env.nomenclature_id,
    )

    written = await orders_service.writeoff_completed_orders(db_session, env.project_id)
    assert written == 2
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 3

    again = await orders_service.writeoff_completed_orders(db_session, env.project_id)
    assert again == 0
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 3

    movements = (
        await db_session.execute(
            select(StockMovement).where(
                StockMovement.project_id == env.project_id,
                StockMovement.reference_type == "FBS_ORDER",
            )
        )
    ).scalars().all()
    assert len(movements) == 2
    assert {m.quantity for m in movements} == {-1}
    assert {m.movement_type for m in movements} == {"OUTBOUND"}

    db_session.expire_all()
    rows = await _orders(db_session, env.project_id)
    assert all(r.written_off_at is not None for r in rows)


@pytest.mark.asyncio
async def test_writeoff_never_goes_negative(db_session, env):
    """Остатка нет — задание не списывается и остаётся неотмеченным до прихода."""
    await db_session.execute(
        WarehouseStock.__table__.update()
        .where(
            WarehouseStock.project_id == env.project_id,
            WarehouseStock.warehouse_id == env.warehouse_id,
        )
        .values(quantity=1)
    )
    await db_session.commit()

    await _seed_order(
        db_session,
        env.project_id,
        7042,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        nomenclature_id=env.nomenclature_id,
    )
    await _seed_order(
        db_session,
        env.project_id,
        7043,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        nomenclature_id=env.nomenclature_id,
    )

    written = await orders_service.writeoff_completed_orders(db_session, env.project_id)
    assert written == 1
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 0

    db_session.expire_all()
    rows = {o.wb_order_id: o for o in await _orders(db_session, env.project_id)}
    # Списывается СВЕЖЕЕ задание, а не самое старое: очередь идёт от новых к
    # старым намеренно. При обратном порядке хвост нерешаемых заданий (нет
    # карточки, нет привязки, нет прихода) занимал бы весь `_WRITEOFF_MAX_ORDERS`,
    # и новые продажи переставали бы списываться со склада вовсе.
    assert rows[7043].written_off_at is not None
    assert rows[7042].written_off_at is None  # уедет следующим прогоном, когда приход догонит


@pytest.mark.asyncio
async def test_writeoff_skips_orders_without_link(db_session, env):
    """Склад WB не привязан к нашему — списывать не с чего, но и не падаем."""
    await _seed_order(
        db_session,
        env.project_id,
        7044,
        wb_warehouse_id=WB_WAREHOUSE_ID_2,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        nomenclature_id=env.nomenclature_id,
    )

    written = await orders_service.writeoff_completed_orders(db_session, env.project_id)
    assert written == 0
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5


@pytest.mark.asyncio
async def test_writeoff_unwritable_orders_do_not_block_the_queue(db_session, env, monkeypatch):
    """Нерешаемые задания не занимают очередь списания.

    Прод-отказ, который это ловит: задание без карточки товара или со складом
    продавца без привязки НИКОГДА не получает `written_off_at`. Пока выборка шла
    от старых к новым, такие задания навсегда садились в голову `LIMIT`, и как
    только их набиралось на весь лимит, НОВЫЕ продажи переставали списываться со
    склада — молча, одним warning'ом в лог.
    """
    monkeypatch.setattr(orders_service, "_WRITEOFF_MAX_ORDERS", 2)

    # Два «вечных» задания: одно без номенклатуры, одно на непривязанном складе.
    await _seed_order(
        db_session, env.project_id, 7060,
        supplier_status=FbsSupplierStatus.COMPLETE.value, nomenclature_id=None,
    )
    await _seed_order(
        db_session, env.project_id, 7061, wb_warehouse_id=WB_WAREHOUSE_ID_2,
        supplier_status=FbsSupplierStatus.COMPLETE.value, nomenclature_id=env.nomenclature_id,
    )
    # И свежая продажа, которую списать МОЖНО.
    await _seed_order(
        db_session, env.project_id, 7062,
        supplier_status=FbsSupplierStatus.COMPLETE.value, nomenclature_id=env.nomenclature_id,
    )

    written = await orders_service.writeoff_completed_orders(db_session, env.project_id)

    assert written == 1, "свежая продажа обязана списаться, несмотря на очередь нерешаемых"
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 4
    rows = {o.wb_order_id: o for o in await _orders(db_session, env.project_id)}
    assert rows[7062].written_off_at is not None
    assert rows[7060].written_off_at is None
    assert rows[7061].written_off_at is None


@pytest.mark.asyncio
async def test_cancel_after_delivery_returns_unit_to_stock(db_session, env, monkeypatch):
    """Отмена ПОСЛЕ передачи поставки возвращает единицу на склад.

    `complete` не финальный статус: WB переводит уже переданное задание в
    `cancel_carrier`. Списание к этому моменту произошло, и без сторно минус на
    складе оставался бы навсегда — задание вдобавок выпадает из открытых, то
    есть перестаёт даже держать резерв.
    """
    await _seed_order(
        db_session, env.project_id, 7070,
        supplier_status=FbsSupplierStatus.COMPLETE.value, nomenclature_id=env.nomenclature_id,
    )
    assert await orders_service.writeoff_completed_orders(db_session, env.project_id) == 1
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 4

    _patch_client(
        monkeypatch,
        FakeFbsClient(statuses=[{"id": 7070, "supplierStatus": "cancel_carrier", "wbStatus": "canceled"}]),
    )
    await orders_service.sync_order_statuses(db_session, env.project_id)

    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5
    db_session.expire_all()
    rows = {o.wb_order_id: o for o in await _orders(db_session, env.project_id)}
    assert rows[7070].supplier_status == "cancel_carrier"
    # Метка снята — иначе повторный проход задвоил бы приход.
    assert rows[7070].written_off_at is None


@pytest.mark.asyncio
async def test_cancel_returns_unit_to_the_same_warehouse(db_session, env, monkeypatch):
    """При НЕСКОЛЬКИХ привязках возврат идёт на склад, с которого списали.

    Списание выбирает ту привязку, где есть остаток, поэтому «вернуть на первую»
    переложило бы товар с одного нашего склада на другой: оба остатка становятся
    неверными, а расхождение всплывает только на инвентаризации.
    """
    # Второй наш склад, привязанный к тому же складу продавца, БЕЗ остатка —
    # списание обязано выбрать первый, возврат обязан попасть туда же.
    second = Warehouse(
        project_id=env.project_id,
        name="FBS Второй склад",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(second)
    await db_session.flush()
    db_session.add(
        WbFbsWarehouseLink(
            project_id=env.project_id,
            wb_warehouse_id=WB_WAREHOUSE_ID,
            warehouse_id=second.id,
            is_active=True,
        )
    )
    await db_session.commit()

    await _seed_order(
        db_session, env.project_id, 7072,
        supplier_status=FbsSupplierStatus.COMPLETE.value, nomenclature_id=env.nomenclature_id,
    )
    assert await orders_service.writeoff_completed_orders(db_session, env.project_id) == 1
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 4

    _patch_client(
        monkeypatch,
        FakeFbsClient(statuses=[{"id": 7072, "supplierStatus": "cancel", "wbStatus": "canceled"}]),
    )
    await orders_service.sync_order_statuses(db_session, env.project_id)

    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5
    assert await _stock_qty(db_session, env.project_id, second.id, env.nomenclature_id) == 0, (
        "единица не должна была переехать на соседний склад"
    )


@pytest.mark.asyncio
async def test_cancel_of_never_written_order_does_not_add_stock(db_session, env, monkeypatch):
    """Отмена НЕсписанного задания склад не трогает — приход из ниоткуда."""
    await _seed_order(
        db_session, env.project_id, 7071,
        supplier_status=FbsSupplierStatus.NEW.value, nomenclature_id=env.nomenclature_id,
    )

    _patch_client(
        monkeypatch,
        FakeFbsClient(statuses=[{"id": 7071, "supplierStatus": "cancel", "wbStatus": "canceled"}]),
    )
    await orders_service.sync_order_statuses(db_session, env.project_id)

    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5


# ─── Изоляция по project_id ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_isolation(db_session, env, other_project, monkeypatch):
    """Задания чужого проекта не видны в списке и не списывают наш остаток."""
    await _seed_order(db_session, env.project_id, 7050)
    # Чужой проект: то же wb_order_id, тот же склад WB — natural key включает project_id.
    await _seed_order(
        db_session,
        other_project.id,
        7050,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        nomenclature_id=env.nomenclature_id,
    )

    listed = await orders_service.list_orders(db_session, env.project_id)
    assert listed["total"] == 1
    assert [i["wb_order_id"] for i in listed["items"]] == [7050]
    assert listed["status_counts"] == {FbsSupplierStatus.NEW.value: 1}

    # Списание чужого проекта не трогает наш склад: привязок у него нет.
    written = await orders_service.writeoff_completed_orders(db_session, other_project.id)
    assert written == 0
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5

    # И синк в наш проект не задевает строки чужого.
    _patch_client(monkeypatch, FakeFbsClient(new_orders=[_raw_order(7050, price=999900)]))
    await orders_service.sync_new_orders(db_session, env.project_id)

    db_session.expire_all()
    foreign = (
        await db_session.execute(
            select(WbFbsOrder).where(
                WbFbsOrder.project_id == other_project.id, WbFbsOrder.wb_order_id == 7050
            )
        )
    ).scalar_one()
    assert foreign.price is None
    assert foreign.supplier_status == FbsSupplierStatus.COMPLETE.value


# ─── Синк поставок ───────────────────────────────────────────────────────────


def _raw_supply(**over) -> dict:
    base = {
        "id": SUPPLY_ID,
        "name": "Поставка от 20.07",
        "done": False,
        "createdAt": "2026-07-20T15:30:00+03:00",
        "cargoType": 1,
        "crossBorderType": 0,
        "isB2b": False,
        "destinationOfficeId": 15,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_sync_supplies_upsert_idempotent(db_session, env, monkeypatch):
    """Дубль в payload'е и повторный прогон → одна строка; orders_count из наших заданий."""
    await _seed_order(
        db_session,
        env.project_id,
        7060,
        supply_id=SUPPLY_ID,
        supplier_status=FbsSupplierStatus.CONFIRM.value,
        nomenclature_id=env.nomenclature_id,
    )
    client = _patch_client(
        monkeypatch,
        FakeFbsClient(supplies=[_raw_supply(), _raw_supply(name="дубль")]),
    )

    first = await supplies_service.sync_supplies(db_session, env.project_id)
    assert first == 1  # дедуп ДО executemany

    client.supplies = [_raw_supply(done=True, closedAt="2026-07-21T10:00:00+03:00")]
    await supplies_service.sync_supplies(db_session, env.project_id)

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(WbFbsSupply).where(WbFbsSupply.project_id == env.project_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    supply = rows[0]
    assert supply.done is True
    assert supply.closed_at == datetime(2026, 7, 21, 7, 0, 0)  # +03:00 → UTC
    assert supply.orders_count == 1
    assert supply.wb_warehouse_id == WB_WAREHOUSE_ID


@pytest.mark.asyncio
async def test_deliver_supply_completes_orders_and_writes_off(db_session, env, monkeypatch):
    """Передали поставку → задания `complete`, товар ушёл из ledger'а."""
    client = FakeFbsClient()
    supply_id = await _make_supply(db_session, monkeypatch, env.project_id, client)
    await _seed_order(db_session, env.project_id, 7061, nomenclature_id=env.nomenclature_id)
    await supplies_service.add_orders(db_session, env.project_id, supply_id, [7061])

    await supplies_service.deliver_supply(db_session, env.project_id, supply_id)
    assert client.delivered == [supply_id]

    db_session.expire_all()
    order = (await _orders(db_session, env.project_id))[0]
    assert order.supplier_status == FbsSupplierStatus.COMPLETE.value
    assert order.written_off_at is not None
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 4


@pytest.mark.asyncio
async def test_supply_barcode_requires_delivered(db_session, env, monkeypatch):
    """QR доступен только после deliver — WB иначе отвечает 409 SupplyNotClosed."""
    client = FakeFbsClient(supply_barcode={"barcode": "WB-GI-QR", "file": "BASE64"})
    supply_id = await _make_supply(db_session, monkeypatch, env.project_id, client)

    with pytest.raises(FbsSupplyError, match="только после передачи"):
        await supplies_service.get_supply_barcode(db_session, env.project_id, supply_id)


# ─── Чтение списка ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_orders_filters_and_money_precision(db_session, env, monkeypatch):
    """Фильтр по статусу, счётчики вкладок и деньги без потери точности.

    `list_orders` ходит через JSON-кэш (`@cached`), поэтому цена отдаётся
    строкой: Decimal → float съел бы копейки (1379.99 → 1379.98999…).
    """
    _patch_client(
        monkeypatch,
        FakeFbsClient(new_orders=[_raw_order(7070, price=137999), _raw_order(7071)]),
    )
    await orders_service.sync_new_orders(db_session, env.project_id)
    await _seed_order(
        db_session, env.project_id, 7072, supplier_status=FbsSupplierStatus.CONFIRM.value
    )

    listed = await orders_service.list_orders(db_session, env.project_id)
    assert listed["total"] == 3
    assert listed["status_counts"] == {
        FbsSupplierStatus.NEW.value: 2,
        FbsSupplierStatus.CONFIRM.value: 1,
    }
    prices = {i["wb_order_id"]: i["price"] for i in listed["items"]}
    assert prices[7070] == "1379.99"  # копейки не потерялись на float
    assert prices[7071] == "1379.00"

    only_new = await orders_service.list_orders(
        db_session, env.project_id, status=FbsSupplierStatus.NEW.value
    )
    assert only_new["total"] == 2
    # Счётчики вкладок считаются БЕЗ фильтра статуса — иначе вкладка знала бы
    # только про саму себя.
    assert only_new["status_counts"][FbsSupplierStatus.CONFIRM.value] == 1


# ─── Контур: песочница не трогает боевые данные ──────────────────────────────


def _set_mode(monkeypatch, mode: str) -> None:
    """Режим контура в рантайме (`current_mode` читает settings напрямую)."""
    from backend.integrations import wb_fbs_api

    monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", mode)


@pytest.mark.asyncio
async def test_sandbox_orders_are_marked_and_do_not_write_off(db_session, env, monkeypatch):
    """Задания песочницы не списывают РЕАЛЬНЫЙ склад — ни в sandbox, ни после возврата в prod.

    Гейт режима закрывает только запись В WB: тестовое задание спокойно ложилось
    в общее зеркало, а `writeoff_completed_orders` (его же зовёт «Передать» на
    вкладке поставок) вычитал по нему боевой `WarehouseStock`. Дискриминатор
    контура — метка в `raw` (services/wb_fbs/contour.py).
    """
    _set_mode(monkeypatch, "sandbox")
    _patch_client(
        monkeypatch,
        FakeFbsClient(new_orders=[_raw_order(7090, supplierStatus=FbsSupplierStatus.COMPLETE.value)]),
    )
    await orders_service.sync_new_orders(db_session, env.project_id)

    rows = await _orders(db_session, env.project_id)
    assert rows[0].raw["_dds_contour"] == "sandbox", "задание песочницы обязано быть помечено"

    # 1) В самой песочнице ledger не трогаем вовсе.
    assert await orders_service.writeoff_completed_orders(db_session, env.project_id) == 0
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5

    # 2) И после возврата в боевой режим фантомное задание тоже не списывается.
    _set_mode(monkeypatch, "prod")
    assert await orders_service.writeoff_completed_orders(db_session, env.project_id) == 0
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5


@pytest.mark.asyncio
async def test_sandbox_orders_do_not_reserve_prod_stock(db_session, env, monkeypatch):
    """Открытые задания песочницы не вычитаются из боевого FBS-остатка и из сборки."""
    from backend.services.wb_fbs import stock_service

    _set_mode(monkeypatch, "sandbox")
    _patch_client(monkeypatch, FakeFbsClient(new_orders=[_raw_order(7091)]))
    await orders_service.sync_new_orders(db_session, env.project_id)

    # В песочнице задание своё — видно.
    assert await stock_service.get_open_fbs_qty(db_session, env.project_id, [env.warehouse_id]) == {
        env.nomenclature_id: 1
    }

    _set_mode(monkeypatch, "prod")
    assert await stock_service.get_open_fbs_qty(db_session, env.project_id, [env.warehouse_id]) == {}


@pytest.mark.asyncio
async def test_prod_orders_without_marker_stay_visible(db_session, env, monkeypatch):
    """Строки, записанные ДО появления метки, считаются боевыми — прод не меняется."""
    from backend.services.wb_fbs import stock_service

    await _seed_order(
        db_session,
        env.project_id,
        7092,
        nomenclature_id=env.nomenclature_id,
        supplier_status=FbsSupplierStatus.NEW.value,
    )  # raw = NULL, как у зеркала до этой правки

    _set_mode(monkeypatch, "prod")
    assert await stock_service.get_open_fbs_qty(db_session, env.project_id, [env.warehouse_id]) == {
        env.nomenclature_id: 1
    }
    listed = await orders_service.list_orders(db_session, env.project_id)
    assert listed["total"] == 1
