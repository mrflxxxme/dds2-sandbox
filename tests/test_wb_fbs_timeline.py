# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты таймлайна «Статус заказа» WB FBS: журнал переходов + якоря из точных дат.

WB истории статусов не отдаёт — журнал (`wb_fbs_order_events`) пишут наши
синки в момент обнаружения перехода. Здесь закрыто:
  • `sync_order_statuses` пишет событие на смене оси и молчит на повторе;
  • `_upsert_orders`: честная смена статуса через upsert даёт событие,
    фолбэк `new` не откатывает статус, свежая историческая вставка бэкфилла
    событий не рождает;
  • `deliver_supply` / `add_orders` / `cancel_order` журналируют переходы;
  • `get_order_timeline`: якоря created/assembled/scanned/written_off,
    сортировка DESC, изоляция по проекту и контуру, «не найдено»;
  • каскад: удаление задания уносит события (FK CASCADE).
"""

from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.models import (
    FbsSupplierStatus,
    Nomenclature,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType
from backend.models.wb_fbs import WbFbsOrderEvent
from backend.services.wb_fbs import orders_service, supplies_service
from backend.services.wb_fbs.orders_service import (
    EVENT_AXIS_SUPPLIER,
    EVENT_AXIS_WB,
    FbsOrderError,
)

WB_WAREHOUSE_ID = 556001
CHRT_ID = 992001
BARCODE = "FBS_TL_BC_1"
SUPPLY_ID = "WB-GI-TL-1"


# ─── Мок клиента WB ──────────────────────────────────────────────────────────


class FakeFbsClient:
    """Минимальный мок `WbFbsClient` для сценариев таймлайна."""

    def __init__(self, *, new_orders=None, period_orders=None, statuses=None):
        self.new_orders = new_orders or []
        self.period_orders = period_orders or []
        self.statuses = statuses or []
        self.cancelled: list[int] = []
        self.created: list[str] = []
        self.added: list[tuple[str, list[int]]] = []
        self.delivered: list[str] = []

    async def get_new_orders(self) -> list[dict]:
        return list(self.new_orders)

    async def get_orders(self, date_from=None, date_to=None, max_pages=50) -> list[dict]:
        return list(self.period_orders)

    async def get_orders_status(self, order_ids) -> list[dict]:
        asked = set(order_ids)
        return [s for s in self.statuses if s.get("id") in asked]

    async def cancel_order(self, order_id) -> None:
        self.cancelled.append(order_id)

    async def create_supply(self, name) -> str:
        self.created.append(name)
        return SUPPLY_ID

    async def add_orders_to_supply(self, supply_id, order_ids) -> None:
        self.added.append((supply_id, list(order_ids)))

    async def deliver_supply(self, supply_id) -> None:
        self.delivered.append(supply_id)


def _patch_client(monkeypatch, client: FakeFbsClient) -> FakeFbsClient:
    async def fake_get(db, project_id):
        return client

    monkeypatch.setattr(orders_service, "get_fbs_client", fake_get)
    monkeypatch.setattr(supplies_service, "get_fbs_client", fake_get)
    return client


# ─── Фикстуры данных ─────────────────────────────────────────────────────────


def _raw_order(wb_order_id: int, **over) -> dict:
    base = {
        "id": wb_order_id,
        "rid": f"rid-{wb_order_id}",
        "createdAt": "2026-07-20T15:30:00+03:00",
        "warehouseId": WB_WAREHOUSE_ID,
        "nmId": 123456,
        "chrtId": CHRT_ID,
        "skus": [BARCODE],
        "article": "ART-TL-1",
        "price": 137900,
        "salePrice": 129900,
        "currencyCode": 643,
        "cargoType": 1,
    }
    base.update(over)
    return base


@pytest_asyncio.fixture
async def env(db_session, project):
    """Наш склад + номенклатура + остаток + склад WB с привязкой (для списания)."""
    warehouse = Warehouse(
        project_id=project.id,
        name="FBS Таймлайн склад",
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
            name="Склад продавца ТЛ",
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


async def _seed_order(db_session, project_id: int, wb_order_id: int, **over) -> WbFbsOrder:
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


async def _events(db_session, project_id: int, order_pk: int | None = None) -> list[WbFbsOrderEvent]:
    query = select(WbFbsOrderEvent).where(WbFbsOrderEvent.project_id == project_id)
    if order_pk is not None:
        query = query.where(WbFbsOrderEvent.order_id == order_pk)
    result = await db_session.execute(query.order_by(WbFbsOrderEvent.id).limit(100))
    return list(result.scalars().all())


# ─── Журнал: sync_order_statuses ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_statuses_writes_events_once(db_session, project, monkeypatch):
    """Смена обеих осей → по событию на ось; повторный синк без изменений — ноль."""
    order = await _seed_order(db_session, project.id, 8001)
    _patch_client(
        monkeypatch,
        FakeFbsClient(statuses=[{"id": 8001, "supplierStatus": "confirm", "wbStatus": "waiting"}]),
    )

    await orders_service.sync_order_statuses(db_session, project.id)

    events = await _events(db_session, project.id, order.id)
    assert {(e.axis, e.old_value, e.new_value) for e in events} == {
        (EVENT_AXIS_SUPPLIER, "new", "confirm"),
        (EVENT_AXIS_WB, None, "waiting"),
    }

    # Идемпотентность: тот же ответ WB → ноль новых строк журнала.
    await orders_service.sync_order_statuses(db_session, project.id)
    assert len(await _events(db_session, project.id, order.id)) == 2


# ─── Журнал: _upsert_orders ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_honest_status_change_writes_event(db_session, project, monkeypatch):
    """Payload честно принёс supplierStatus=confirm → ось досылается + событие.

    Повторный синк того же payload'а событий не плодит (old == new).
    """
    order_pk = (await _seed_order(db_session, project.id, 8002)).id
    _patch_client(
        monkeypatch,
        FakeFbsClient(new_orders=[_raw_order(8002, supplierStatus="confirm")]),
    )

    await orders_service.sync_new_orders(db_session, project.id)

    db_session.expire_all()
    row = await db_session.get(WbFbsOrder, order_pk)
    assert row.supplier_status == FbsSupplierStatus.CONFIRM.value
    events = await _events(db_session, project.id, order_pk)
    assert [(e.axis, e.old_value, e.new_value) for e in events] == [
        (EVENT_AXIS_SUPPLIER, "new", "confirm")
    ]

    await orders_service.sync_new_orders(db_session, project.id)
    assert len(await _events(db_session, project.id, order_pk)) == 1


@pytest.mark.asyncio
async def test_upsert_fallback_new_keeps_status_and_silent(db_session, project, monkeypatch):
    """Payload БЕЗ supplierStatus (фолбэк `new`) не откатывает статус и не пишет событий."""
    order_pk = (
        await _seed_order(
            db_session, project.id, 8003, supplier_status=FbsSupplierStatus.COMPLETE.value
        )
    ).id
    _patch_client(monkeypatch, FakeFbsClient(new_orders=[_raw_order(8003)]))

    await orders_service.sync_new_orders(db_session, project.id)

    db_session.expire_all()
    row = await db_session.get(WbFbsOrder, order_pk)
    assert row.supplier_status == FbsSupplierStatus.COMPLETE.value
    assert await _events(db_session, project.id, order_pk) == []


@pytest.mark.asyncio
async def test_backfill_fresh_history_rows_without_events(db_session, project, monkeypatch):
    """Бэкфилл: свежая историческая вставка — без событий, смена существующей — событие."""
    existing_pk = (await _seed_order(db_session, project.id, 8004, wb_status="waiting")).id
    _patch_client(
        monkeypatch,
        FakeFbsClient(
            period_orders=[
                _raw_order(8004, wbStatus="sorted"),
                # Историческое: создано ДО cutoff (первая строка зеркала — сегодня).
                _raw_order(8005, createdAt="2026-05-01T10:00:00+03:00"),
            ]
        ),
    )

    result = await orders_service.backfill_orders_history(db_session, project.id, days=90)
    assert result["ok"] is True

    db_session.expire_all()
    fresh = (
        await db_session.execute(
            select(WbFbsOrder).where(
                WbFbsOrder.project_id == project.id, WbFbsOrder.wb_order_id == 8005
            )
        )
    ).scalar_one()
    # Историческая строка вставлена уже списанной, журнал у неё пуст.
    assert fresh.written_off_at is not None
    assert await _events(db_session, project.id, fresh.id) == []
    # Существующая строка: честная смена wb_status зафиксирована.
    events = await _events(db_session, project.id, existing_pk)
    assert [(e.axis, e.old_value, e.new_value) for e in events] == [
        (EVENT_AXIS_WB, "waiting", "sorted")
    ]


# ─── Журнал: поставки и отмена ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_supply_flow_writes_confirm_and_complete_events(db_session, env, monkeypatch):
    """add_orders → `new→confirm`, deliver_supply → `confirm→complete`."""
    client = FakeFbsClient()
    _patch_client(monkeypatch, client)
    supply_id = await supplies_service.create_supply(db_session, env.project_id, "ТЛ поставка")
    await _seed_order(db_session, env.project_id, 8010, nomenclature_id=env.nomenclature_id)

    await supplies_service.add_orders(db_session, env.project_id, supply_id, [8010])
    await supplies_service.deliver_supply(db_session, env.project_id, supply_id)
    assert client.delivered == [supply_id]

    db_session.expire_all()
    order = (
        await db_session.execute(
            select(WbFbsOrder).where(
                WbFbsOrder.project_id == env.project_id, WbFbsOrder.wb_order_id == 8010
            )
        )
    ).scalar_one()
    assert order.supplier_status == FbsSupplierStatus.COMPLETE.value
    events = await _events(db_session, env.project_id, order.id)
    assert [(e.axis, e.old_value, e.new_value) for e in events] == [
        (EVENT_AXIS_SUPPLIER, "new", "confirm"),
        (EVENT_AXIS_SUPPLIER, "confirm", "complete"),
    ]


@pytest.mark.asyncio
async def test_cancel_order_writes_event(db_session, project, monkeypatch):
    """Отмена нашей кнопкой фиксируется в журнале: old → cancel."""
    order = await _seed_order(db_session, project.id, 8011, is_cancellable=True)
    _patch_client(monkeypatch, FakeFbsClient())

    await orders_service.cancel_order(db_session, project.id, 8011)

    events = await _events(db_session, project.id, order.id)
    assert [(e.axis, e.old_value, e.new_value) for e in events] == [
        (EVENT_AXIS_SUPPLIER, "new", "cancel")
    ]


# ─── Таймлайн ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_timeline_anchors_events_and_desc_sort(db_session, project):
    """Якоря из точных дат + журнал, всё отсортировано по времени DESC."""
    db_session.add(
        WbFbsSupply(
            project_id=project.id,
            wb_supply_id=SUPPLY_ID,
            done=True,
            closed_at=datetime(2026, 7, 21, 12, 0, 0),
            scan_dt=datetime(2026, 7, 21, 15, 0, 0),
        )
    )
    await db_session.commit()
    order = await _seed_order(
        db_session,
        project.id,
        8020,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        supply_id=SUPPLY_ID,
        created_at_wb=datetime(2026, 7, 20, 10, 0, 0),
        written_off_at=datetime(2026, 7, 21, 12, 30, 0),
        article="ART-TL-1",
        subject="Ковёр",
        nm_id=123456,
    )
    db_session.add_all(
        [
            WbFbsOrderEvent(
                project_id=project.id,
                order_id=order.id,
                axis=EVENT_AXIS_SUPPLIER,
                old_value="confirm",
                new_value="complete",
                changed_at=datetime(2026, 7, 21, 12, 5, 0),
            ),
            WbFbsOrderEvent(
                project_id=project.id,
                order_id=order.id,
                axis=EVENT_AXIS_WB,
                old_value=None,
                new_value="waiting",
                changed_at=datetime(2026, 7, 21, 13, 0, 0),
            ),
        ]
    )
    await db_session.commit()

    timeline = await orders_service.get_order_timeline(db_session, project.id, 8020)

    assert timeline["wb_order_id"] == 8020
    assert timeline["article"] == "ART-TL-1"
    codes = [e["code"] for e in timeline["events"]]
    # DESC: scanned 15:00 → wb:waiting 13:00 → written_off 12:30 →
    # supplier:complete 12:05 → assembled 12:00 → created 10:00 (20.07).
    assert codes == [
        "scanned",
        "wb:waiting",
        "written_off",
        "supplier:complete",
        "assembled",
        "created",
    ]
    ats = [e["at"] for e in timeline["events"]]
    assert ats == sorted(ats, reverse=True)
    by_code = {e["code"]: e for e in timeline["events"]}
    assert by_code["created"]["kind"] == "anchor" and by_code["created"]["approx"] is False
    assert by_code["wb:waiting"]["kind"] == "event" and by_code["wb:waiting"]["approx"] is True


@pytest.mark.asyncio
async def test_timeline_skips_empty_anchors(db_session, project):
    """Пустые даты якорей (нет поставки, не списано) в таймлайн не попадают."""
    await _seed_order(
        db_session, project.id, 8021, created_at_wb=datetime(2026, 7, 20, 10, 0, 0)
    )

    timeline = await orders_service.get_order_timeline(db_session, project.id, 8021)
    assert [e["code"] for e in timeline["events"]] == ["created"]


@pytest.mark.asyncio
async def test_timeline_project_isolation_and_missing(db_session, project, other_project):
    """Чужой проект и несуществующий id — «не найдено» (роутер отдаст 404)."""
    await _seed_order(db_session, project.id, 8022)

    with pytest.raises(FbsOrderError, match="не найдено"):
        await orders_service.get_order_timeline(db_session, other_project.id, 8022)
    with pytest.raises(FbsOrderError, match="не найдено"):
        await orders_service.get_order_timeline(db_session, project.id, 999999999)


@pytest.mark.asyncio
async def test_timeline_contour_isolation(db_session, project):
    """Задание песочницы невидимо в боевом контуре — таймлайн отвечает «не найдено»."""
    await _seed_order(db_session, project.id, 8023, raw={"_dds_contour": "sandbox"})

    with pytest.raises(FbsOrderError, match="не найдено"):
        await orders_service.get_order_timeline(db_session, project.id, 8023)


# ─── Каскад ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_events_cascade_on_order_delete(db_session, project):
    """FK CASCADE: удаление задания уносит его журнал."""
    order = await _seed_order(db_session, project.id, 8030)
    db_session.add(
        WbFbsOrderEvent(
            project_id=project.id,
            order_id=order.id,
            axis=EVENT_AXIS_SUPPLIER,
            old_value="new",
            new_value="confirm",
        )
    )
    await db_session.commit()
    assert len(await _events(db_session, project.id, order.id)) == 1

    # Зеркало WB без SoftDelete — строки удаляются физически (см. модель).
    await db_session.execute(
        delete(WbFbsOrder).where(WbFbsOrder.project_id == project.id, WbFbsOrder.id == order.id)
    )
    await db_session.commit()

    assert await _events(db_session, project.id, order.id) == []
