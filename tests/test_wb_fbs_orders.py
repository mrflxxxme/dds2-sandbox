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

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from backend.models import (
    FBS_IN_DELIVERY_STATUS,
    FBS_SORTED_STATUS,
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
        period_orders=None,
        statuses=None,
        stickers=None,
        supplies=None,
        supply_order_ids=None,
        supply_barcode=None,
    ):
        self.new_orders = new_orders or []
        #: Ответ `GET /api/v3/orders` — список либо callable(date_from, date_to).
        self.period_orders = period_orders or []
        self.order_calls: list[tuple] = []
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

    async def get_orders(self, date_from=None, date_to=None, max_pages=50) -> list[dict]:
        self.order_calls.append((date_from, date_to, max_pages))
        if callable(self.period_orders):
            return list(self.period_orders(date_from, date_to))
        return list(self.period_orders)

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


# ─── Бэкфилл истории и догон недавнего окна (GET /api/v3/orders) ─────────────
#
# `GET /orders/new` отдаёт ТОЛЬКО задания, ещё не положенные в поставку: как
# только задание уезжает в поставку, оно исчезает оттуда навсегда. Зеркало,
# наполняемое одним этим методом, не видит ни истории, ни заданий, собранных
# между двумя опросами. Периодный `GET /orders` закрывает обе дыры, но несёт
# свой риск: в его payload'е НЕТ `supplierStatus`, и три месяца истории влетели
# бы как `new` → `complete` → списание со склада товара, проданного весной.


def _wb_ts(dt: datetime) -> str:
    """naive UTC → RFC3339 в форме, в которой даты отдаёт WB."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _backfill_env(db_session, project_id: int, *, cutoff_ago_days: int = 1) -> datetime:
    """Зеркало с одним заданием — момент его вставки и есть cutoff проекта."""
    from backend.utils.time import utcnow

    cutoff = utcnow() - timedelta(days=cutoff_ago_days)
    await _seed_order(db_session, project_id, 7199, created_at=cutoff)
    return cutoff


@pytest.mark.asyncio
async def test_backfill_slices_period_into_windows(db_session, env, monkeypatch):
    """Период режется на окна ≤30 дней (жёсткое ограничение WB), без дыр."""
    client = _patch_client(monkeypatch, FakeFbsClient())

    result = await orders_service.backfill_orders_history(db_session, env.project_id, days=90)

    assert result["ok"] is True
    assert result["windows"] == 3
    assert len(client.order_calls) == 3
    starts = [c[0] for c in client.order_calls]
    ends = [c[1] for c in client.order_calls]
    for start, end in zip(starts, ends, strict=True):
        assert end - start <= timedelta(days=30)
        # В WB уходит unix-timestamp: naive-дата тут трактовалась бы как
        # локальное время машины, а не UTC.
        assert start.tzinfo is not None and end.tzinfo is not None
    assert ends[:-1] == starts[1:], "окна обязаны идти встык — иначе дыра в истории"
    assert ends[-1] - starts[0] == timedelta(days=90)


@pytest.mark.asyncio
async def test_backfill_clamps_days_to_wb_depth(db_session, env, monkeypatch):
    """Глубина истории у WB — 3 месяца; больше запрашивать бессмысленно."""
    client = _patch_client(monkeypatch, FakeFbsClient())

    result = await orders_service.backfill_orders_history(db_session, env.project_id, days=365)
    assert result["windows"] == 3

    client.order_calls.clear()
    short = await orders_service.backfill_orders_history(db_session, env.project_id, days=1)
    assert short["windows"] == 1
    start, end, _pages = client.order_calls[0]
    assert end - start == timedelta(days=1)


@pytest.mark.asyncio
async def test_backfill_marks_history_written_off_but_not_live(db_session, env, monkeypatch):
    """🔴 Историю помечаем списанной СРАЗУ — иначе она спишет реальный склад.

    Задание старше cutoff'а (момента, с которого домен вообще начал видеть
    заказы) физически отгружено давно: его `written_off_at` проставляется при
    вставке, и штатная связка `sync_order_statuses` → `writeoff_completed_orders`
    его уже не тронет. Задание свежее cutoff'а идёт обычным путём.
    """
    from backend.utils.time import utcnow

    cutoff = await _backfill_env(db_session, env.project_id)
    now = utcnow()
    _patch_client(
        monkeypatch,
        FakeFbsClient(
            period_orders=[
                _raw_order(7200, createdAt=_wb_ts(now - timedelta(days=45))),  # до cutoff
                _raw_order(7201, createdAt=_wb_ts(now - timedelta(hours=2))),  # после cutoff
            ]
        ),
    )

    result = await orders_service.backfill_orders_history(db_session, env.project_id, days=90)

    assert result["ok"] is True
    # Фейк отдаёт один и тот же ответ на каждое окно: строки пишутся трижды,
    # а пометка ставится РОВНО на вставке — повторы её не задваивают.
    assert result["fetched"] == 6
    assert result["written_off_marked"] == 1

    rows = {r.wb_order_id: r for r in await _orders(db_session, env.project_id)}
    assert rows[7200].written_off_at is not None, "историческое задание обязано быть помечено"
    assert rows[7200].created_at_wb < cutoff
    assert rows[7201].written_off_at is None, "живое задание списывает штатный механизм"


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_keeps_written_off(db_session, env, monkeypatch):
    """Повторный прогон ничего не переписывает: `written_off_at` не в UPDATE.

    Ни в одну сторону: уже списанное не «разсписывается», а известное живое
    задание не получает пометку задним числом, даже если оно старше cutoff'а.
    """
    from backend.utils.time import utcnow

    await _backfill_env(db_session, env.project_id)
    now = utcnow()
    # Живое задание, известное зеркалу и созданное ДО cutoff'а: пометка ему не
    # положена — его уже ведёт штатный синк статусов.
    await _seed_order(
        db_session,
        env.project_id,
        7210,
        created_at_wb=now - timedelta(days=40),
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    _patch_client(
        monkeypatch,
        FakeFbsClient(
            period_orders=[
                _raw_order(7210, createdAt=_wb_ts(now - timedelta(days=40))),
                _raw_order(7211, createdAt=_wb_ts(now - timedelta(days=40))),
            ]
        ),
    )

    first = await orders_service.backfill_orders_history(db_session, env.project_id, days=30)
    assert first["written_off_marked"] == 1  # только НОВАЯ строка 7211

    db_session.expire_all()
    rows = {r.wb_order_id: r for r in await _orders(db_session, env.project_id)}
    stamp = rows[7211].written_off_at
    assert stamp is not None
    assert rows[7210].written_off_at is None, "известное задание не помечаем задним числом"

    second = await orders_service.backfill_orders_history(db_session, env.project_id, days=30)
    assert second["written_off_marked"] == 0, "второй прогон ничего не помечает"

    db_session.expire_all()
    again = {r.wb_order_id: r for r in await _orders(db_session, env.project_id)}
    assert again[7211].written_off_at == stamp, "повтор не перетирает метку списания"
    assert again[7210].written_off_at is None
    assert len(again) == 3  # 7199 (cutoff-якорь) + 7210 + 7211


@pytest.mark.asyncio
async def test_backfill_is_scoped_to_project(db_session, env, other_project, monkeypatch):
    """Изоляция: чужие задания не трогаем и cutoff считаем по СВОЕМУ проекту."""
    from backend.utils.time import utcnow

    now = utcnow()
    # У соседнего проекта зеркало древнее. Утечь оно не должно: иначе cutoff
    # нашего (пустого) проекта уехал бы в прошлое и история не пометилась бы.
    await _seed_order(db_session, other_project.id, 7220, created_at=now - timedelta(days=400))
    _patch_client(
        monkeypatch,
        FakeFbsClient(period_orders=[_raw_order(7221, createdAt=_wb_ts(now - timedelta(days=10)))]),
    )

    result = await orders_service.backfill_orders_history(db_session, env.project_id, days=30)

    assert result["written_off_marked"] == 1
    ours = await _orders(db_session, env.project_id)
    assert [r.wb_order_id for r in ours] == [7221]
    theirs = await _orders(db_session, other_project.id)
    assert [r.wb_order_id for r in theirs] == [7220]
    assert theirs[0].written_off_at is None


@pytest.mark.asyncio
async def test_backfill_survives_empty_response(db_session, env, monkeypatch):
    """Пустой ответ WB — это ноль строк, а не падение прогона."""
    await _backfill_env(db_session, env.project_id)
    _patch_client(monkeypatch, FakeFbsClient(period_orders=[]))

    result = await orders_service.backfill_orders_history(db_session, env.project_id, days=60)

    assert result == {
        "ok": True,
        "fetched": 0,
        "upserted": 0,
        "written_off_marked": 0,
        "windows": 2,
        "message": result["message"],
    }
    assert [r.wb_order_id for r in await _orders(db_session, env.project_id)] == [7199]


@pytest.mark.asyncio
async def test_backfill_reports_partial_failure(db_session, env, monkeypatch):
    """Отказ WB на втором окне не теряет первое: ok=False + частичный результат."""
    from backend.integrations.wb_fbs_api import WbFbsRateLimited
    from backend.utils.time import utcnow

    await _backfill_env(db_session, env.project_id)
    now = utcnow()
    client = FakeFbsClient()
    calls: list[int] = []

    def answer(date_from, date_to):
        calls.append(len(calls))
        if len(calls) > 1:
            raise WbFbsRateLimited(429, "TooManyRequests", "лимит WB исчерпан")
        return [_raw_order(7230, createdAt=_wb_ts(now - timedelta(days=80)))]

    client.period_orders = answer
    _patch_client(monkeypatch, client)

    result = await orders_service.backfill_orders_history(db_session, env.project_id, days=90)

    assert result["ok"] is False
    assert result["windows"] == 1
    assert result["upserted"] == 1
    assert result["written_off_marked"] == 1
    assert "лимит WB" in (result["message"] or "")
    # Данные первого окна доехали до БД — прогон деградировал, а не откатился.
    rows = {r.wb_order_id: r for r in await _orders(db_session, env.project_id)}
    assert rows[7230].written_off_at is not None


@pytest.mark.asyncio
async def test_sync_orders_recent_catches_up_window(db_session, env, monkeypatch):
    """Догон недавнего окна ловит задание, уехавшее в поставку между опросами.

    `GET /orders/new` его уже не отдаст, а `GET /orders` за 2 дня — отдаст.
    Свежее задание живое (создано ПОСЛЕ cutoff'а) → списывает его штатный
    механизм, пометка тут не ставится.
    """
    from backend.utils.time import utcnow

    await _backfill_env(db_session, env.project_id, cutoff_ago_days=3)
    now = utcnow()
    client = _patch_client(
        monkeypatch,
        FakeFbsClient(period_orders=[_raw_order(7240, createdAt=_wb_ts(now - timedelta(hours=5)))]),
    )

    count = await orders_service.sync_orders_recent(db_session, env.project_id)

    assert count == 1
    assert len(client.order_calls) == 1
    start, end, _pages = client.order_calls[0]
    assert end - start == timedelta(days=2)
    rows = {r.wb_order_id: r for r in await _orders(db_session, env.project_id)}
    assert rows[7240].nomenclature_id == env.nomenclature_id
    assert rows[7240].written_off_at is None


@pytest.mark.asyncio
async def test_wb_declined_order_is_not_counted_as_new(db_session, env, monkeypatch):
    """Отказ покупателя ДО сборки: `supplier_status` навсегда `new`, но задание мёртвое.

    Прод-кейс 26.07 (склад «Пушкино»): карточка показывала 5 «Новых», из них 3 —
    отказы покупателя. Такое задание не должно ни числиться в очереди сборки, ни
    держать остаток, ни опрашиваться синком статусов; во вкладке «Отменено» —
    наоборот, обязано быть.
    """
    await _seed_order(db_session, env.project_id, 9500)  # живое, wb_status пуст
    await _seed_order(
        db_session, env.project_id, 9501, wb_status="declined_by_client"
    )
    await _seed_order(
        db_session, env.project_id, 9502, wb_status="canceled_by_client"
    )

    listed = await orders_service.list_orders(db_session, env.project_id)

    # Живое одно, два ушли в «Отменено» — при том что supplier_status у всех `new`.
    assert listed["status_counts"].get(FbsSupplierStatus.NEW.value) == 1
    assert listed["status_counts"].get(FbsSupplierStatus.CANCEL.value) == 2

    cancelled = await orders_service.list_orders(
        db_session, env.project_id, status=FbsSupplierStatus.CANCEL.value
    )
    assert {o["wb_order_id"] for o in cancelled["items"]} == {9501, 9502}

    # Зеркало остаётся верным источнику: сырое поле WB мы не переписывали.
    rows = {r.wb_order_id: r for r in await _orders(db_session, env.project_id)}
    assert rows[9501].supplier_status == FbsSupplierStatus.NEW.value


@pytest.mark.asyncio
async def test_revenue_uses_converted_price_for_foreign_currency(db_session, env, monkeypatch):
    """Заказ в чужой валюте входит в выручку по `convertedPrice`, а не по `price`.

    Прод-кейс 26.07: WB торгует в СНГ, и `price`/`salePrice` приходят в валюте
    ПРОДАЖИ. Узбекский заказ на 2 595 300 сумов (≈16.8 k ₽) прибавлялся к рублёвой
    выручке целиком — «Диваны бескаркасные» показали 5.5 M ₽ вместо ~0.55 M.
    """
    from backend.services.wb_fbs import orders_stats

    _patch_client(
        monkeypatch,
        FakeFbsClient(
            new_orders=[
                # Рубль: платит покупатель по salePrice = 1 299 ₽.
                _raw_order(8410),
                # Узбекистан: 259 530 000 тийин = 2 595 300 сумов, пересчёт WB — 16 814.32 ₽.
                _raw_order(
                    8411,
                    currencyCode=860,
                    price=259_530_000,
                    salePrice=259_530_000,
                    convertedPrice=1_681_432,
                ),
            ]
        ),
    )
    await orders_service.sync_new_orders(db_session, env.project_id)

    total = (
        await db_session.execute(
            select(func.coalesce(func.sum(orders_stats._revenue_expr()), 0)).where(
                WbFbsOrder.project_id == env.project_id
            )
        )
    ).scalar_one()

    # 1 299 ₽ (рубль, со скидкой) + 16 814.32 ₽ (пересчёт WB) — сумы не подмешались.
    assert Decimal(str(total)) == Decimal("18113.32")


@pytest.mark.asyncio
async def test_upsert_locks_rows_in_key_order(db_session, env, monkeypatch):
    """Строки в UPSERT идут по возрастанию `wb_order_id`, каким бы ни был ответ WB.

    Порядок строк = порядок захвата блокировок в PG. Писателей в таблицу два
    (`sync_new_orders` и `sync_orders_recent`), их наборы заданий пересекаются,
    а WB отдаёт задания в разном порядке — при несортированной вставке два
    одновременных прогона брали одни строки встречно и ловили
    `DeadlockDetectedError` (прод 26.07: периодный синк падал КАЖДЫЙ раз).
    """
    captured: list[list[int]] = []
    original_chunks = orders_service._chunks

    def spy(items, size):
        # `_chunks` внутри `_upsert_orders` зовётся не только для строк UPSERT
        # (снапшот журнала переходов чанкует голые id) — ловим только dict-строки.
        if items and isinstance(items[0], dict) and "wb_order_id" in items[0]:
            captured.append([r["wb_order_id"] for r in items])
        return original_chunks(items, size)

    monkeypatch.setattr(orders_service, "_chunks", spy)
    # WB отдаёт вперемешку — намеренно не по возрастанию.
    shuffled = [_raw_order(oid) for oid in (9303, 9301, 9304, 9302)]

    await orders_service._upsert_orders(db_session, env.project_id, shuffled)

    assert captured, "не перехватили ни одной пачки значений UPSERT"
    assert captured[0] == sorted(captured[0]) == [9301, 9302, 9303, 9304]


@pytest.mark.asyncio
async def test_sync_orders_recent_protects_stock_on_first_run(db_session, env, monkeypatch):
    """Первый прогон на пустом зеркале не списывает склад задним числом.

    Зеркала нет → cutoff = «сейчас», и всё, что периодный метод отдаёт из
    прошлого, физически отгружено до того, как домен вообще начал смотреть.
    """
    from backend.utils.time import utcnow

    _patch_client(
        monkeypatch,
        FakeFbsClient(period_orders=[_raw_order(7250, createdAt=_wb_ts(utcnow() - timedelta(days=1)))]),
    )

    assert await orders_service.sync_orders_recent(db_session, env.project_id) == 1

    rows = await _orders(db_session, env.project_id)
    assert rows[0].written_off_at is not None
    # И проверка сквозная: задание в `complete` НЕ трогает реальный остаток.
    rows[0].supplier_status = FbsSupplierStatus.COMPLETE.value
    await db_session.commit()
    assert await orders_service.writeoff_completed_orders(db_session, env.project_id) == 0
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5


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
async def test_writeoff_does_not_write_to_soft_deleted_warehouse(db_session, env):
    """Привязка к мягко удалённому складу — НЕ привязка: в мёртвый остаток не списываем.

    Канон домена: удалённый склад выпадает из привязок (`get_linked_warehouse_ids`).
    Раньше `_active_links_subquery` мёртвых не фильтровал, и списание уходило в
    остаток склада, которого нет в интерфейсе; теперь задание честно blocked.
    """
    wh = (
        await db_session.execute(select(Warehouse).where(Warehouse.id == env.warehouse_id))
    ).scalar_one()
    wh.is_deleted = True
    await db_session.commit()

    await _seed_order(
        db_session,
        env.project_id,
        7046,
        supplier_status=FbsSupplierStatus.COMPLETE.value,
        nomenclature_id=env.nomenclature_id,
    )

    written = await orders_service.writeoff_completed_orders(db_session, env.project_id)
    assert written == 0
    # Остаток мёртвого склада не тронут, задание осталось неотмеченным.
    assert await _stock_qty(db_session, env.project_id, env.warehouse_id, env.nomenclature_id) == 5
    db_session.expire_all()
    rows = await _orders(db_session, env.project_id)
    assert all(r.written_off_at is None for r in rows)


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


@pytest.mark.asyncio
async def test_delivery_phases_split_in_transit_from_sorted(db_session, env):
    """Переданное делится на ДВЕ фазы по `wbStatus`, а не считается одной кучей.

    `supplierStatus` застывает на `complete` в момент передачи поставки и таким
    остаётся навсегда, поэтому на вопрос «что сейчас в пути» отвечает только
    вторая ось. Сортировочный центр — отдельный этап и отдельная зона
    ответственности: пока задание не отсортировано, вопросы по нему ещё к нам.
    Оба счётчика — отдельными полями, а не ключами `status_counts`: сумма
    счётчиков — это вкладка «Все», и синтетика внутри неё удвоила бы задания.
    """
    done = FbsSupplierStatus.COMPLETE.value
    await _seed_order(db_session, env.project_id, 9600, supplier_status=done, wb_status="sorted")
    await _seed_order(db_session, env.project_id, 9601, supplier_status=done, wb_status=None)
    await _seed_order(db_session, env.project_id, 9602, supplier_status=done, wb_status="sold")
    await _seed_order(db_session, env.project_id, 9603, supplier_status=done, wb_status="defect")
    # Передали, но покупатель отказался: effective — `cancel`, в пути ничего нет.
    await _seed_order(
        db_session, env.project_id, 9604, supplier_status=done, wb_status="canceled_by_client"
    )
    await _seed_order(db_session, env.project_id, 9605)  # new — к доставке отношения не имеет

    listed = await orders_service.list_orders(db_session, env.project_id)

    assert listed["in_delivery_count"] == 1  # только 9601 (пустой wbStatus)
    assert listed["sorted_count"] == 1  # 9600 ушло в свою фазу, а не в «в доставке»
    assert listed["status_counts"][FbsSupplierStatus.COMPLETE.value] == 4
    # Сумма счётчиков осталась равной вкладке «Все» — синтетика её не раздула.
    assert sum(listed["status_counts"].values()) == listed["total"] == 6

    # Цифра на карточке склада и выдача по клику обязаны совпадать до штуки.
    for status, expected in ((FBS_IN_DELIVERY_STATUS, {9601}), (FBS_SORTED_STATUS, {9600})):
        filtered = await orders_service.list_orders(db_session, env.project_id, status=status)
        assert filtered["total"] == len(expected)
        assert {o["wb_order_id"] for o in filtered["items"]} == expected


@pytest.mark.asyncio
async def test_in_delivery_is_whitelist_not_blacklist(db_session, env):
    """Фаза «в пути» — БЕЛЫЙ список до-сортировочных `wbStatus`, не чёрный.

    Чёрный список «complete минус sorted минус sold/defect» возвращал любой
    неизвестный пост-сортировочный статус обратно в «едет к СЦ»: 168 заказов в
    `ready_for_pickup` (лежат в ПВЗ) через 2 дня зажигали «зависло»
    (прод 30.07.2026). Теперь: `waiting`/пустой/NULL — в пути; `ready_for_pickup`/
    `postponed_delivery` — фаза «отсортировано»; НЕИЗВЕСТНЫЙ новый статус WB —
    ни в одной фазе; WB-отмена при complete — тоже ни в одной.
    """
    done = FbsSupplierStatus.COMPLETE.value
    await _seed_order(db_session, env.project_id, 9620, supplier_status=done, wb_status="ready_for_pickup")
    await _seed_order(db_session, env.project_id, 9621, supplier_status=done, wb_status="postponed_delivery")
    await _seed_order(db_session, env.project_id, 9622, supplier_status=done, wb_status="waiting")
    await _seed_order(db_session, env.project_id, 9623, supplier_status=done, wb_status="")
    await _seed_order(db_session, env.project_id, 9624, supplier_status=done, wb_status=None)
    # Неизвестный статус WB: белый список не пускает его в «едет к СЦ».
    await _seed_order(db_session, env.project_id, 9625, supplier_status=done, wb_status="new_wb_status_2027")
    # WB-отмена при complete: effective — cancel, ни одна фаза не считает.
    await _seed_order(db_session, env.project_id, 9626, supplier_status=done, wb_status="canceled_by_client")

    listed = await orders_service.list_orders(db_session, env.project_id)
    assert listed["in_delivery_count"] == 3  # waiting + "" + NULL
    assert listed["sorted_count"] == 2  # ready_for_pickup + postponed_delivery

    in_delivery = await orders_service.list_orders(
        db_session, env.project_id, status=FBS_IN_DELIVERY_STATUS
    )
    assert {o["wb_order_id"] for o in in_delivery["items"]} == {9622, 9623, 9624}
    sorted_phase = await orders_service.list_orders(
        db_session, env.project_id, status=FBS_SORTED_STATUS
    )
    assert {o["wb_order_id"] for o in sorted_phase["items"]} == {9620, 9621}


@pytest.mark.parametrize(
    ("wb_status", "expected"),
    [
        (None, True),
        ("", True),
        ("waiting", True),
        ("sent_to_carrier", True),
        ("accepted_by_carrier", True),
        ("sorted", False),
        ("ready_for_pickup", False),
        ("postponed_delivery", False),
        ("sold", False),
        ("defect", False),
        ("canceled_by_client", False),
        ("new_wb_status_2027", False),  # белый список: неизвестное — НЕ «в пути»
    ],
)
def test_is_in_delivery_row_mirrors_whitelist(wb_status, expected):
    """Питон-зеркало `_is_in_delivery_row` повторяет белый список SQL бит-в-бит."""
    order = WbFbsOrder(supplier_status=FbsSupplierStatus.COMPLETE.value, wb_status=wb_status)
    assert orders_service._is_in_delivery_row(order) is expected
    # Не-complete — False при любом wb_status.
    order_new = WbFbsOrder(supplier_status=FbsSupplierStatus.NEW.value, wb_status=wb_status)
    assert orders_service._is_in_delivery_row(order_new) is False


@pytest.mark.asyncio
async def test_warehouse_summary_windows_delivery_but_not_the_queue(db_session, env):
    """Период режет ТОЛЬКО фазы доставки; очередь сборки отдаётся целиком.

    Задание, созданное два месяца назад и до сих пор не собранное, обязано быть
    в цифре сборщика — иначе он его просто не увидит. А `complete` копится
    вечно (WB его уже не двинет), и без окна «В доставке» показывает всё, что
    когда-либо уезжало.
    """
    done = FbsSupplierStatus.COMPLETE.value
    old = datetime.utcnow() - timedelta(days=120)
    fresh = datetime.utcnow() - timedelta(days=1)
    await _seed_order(db_session, env.project_id, 9700, created_at_wb=old)  # старое НОВОЕ
    await _seed_order(
        db_session, env.project_id, 9701, supplier_status=done, wb_status=None, created_at_wb=old
    )
    await _seed_order(
        db_session, env.project_id, 9702, supplier_status=done, wb_status=None, created_at_wb=fresh
    )
    await _seed_order(
        db_session, env.project_id, 9703, supplier_status=done, wb_status="sorted", created_at_wb=fresh
    )

    window = date.today() - timedelta(days=30)
    summary = await orders_service.warehouse_summary(
        db_session, env.project_id, date_from=window, date_to=date.today()
    )

    totals = summary["totals"]
    assert totals["new"] == 1  # старое новое НЕ выпало из очереди
    assert totals["in_delivery"] == 1  # 9701 старше окна → не считается
    assert totals["sorted"] == 1
    # Верхняя граница включительна: сегодняшний день не должен теряться.
    assert summary["date_to"] == date.today()

    # Без окна — вся история фаз доставки.
    full = await orders_service.warehouse_summary(db_session, env.project_id)
    assert full["totals"]["in_delivery"] == 2


# ─── Зависшие в пути на СЦ: transit_days и псевдо-статус in_delivery_stuck ───


async def _seed_supply(db_session, project_id: int, wb_supply_id: str, **over) -> WbFbsSupply:
    fields = {"project_id": project_id, "wb_supply_id": wb_supply_id, "done": True}
    fields.update(over)
    supply = WbFbsSupply(**fields)
    db_session.add(supply)
    await db_session.commit()
    return supply


@pytest.mark.asyncio
async def test_transit_days_anchor_priority_and_fallback(db_session, env):
    """Якорь передачи: scan_dt → closed_at → written_off_at; без якоря — None.

    🔴 Дни считаются int-усечением от `total_seconds()/86400`, не `timedelta.days`
    в сравнении (грабля проекта): 4.5 суток в пути — это «4 дня», 0.5 — «0».
    """
    from backend.utils.time import utcnow

    now = utcnow()
    done = FbsSupplierStatus.COMPLETE.value
    # scan_dt перебивает closed_at.
    await _seed_supply(
        db_session,
        env.project_id,
        "WB-GI-T1",
        scan_dt=now - timedelta(days=4, hours=12),
        closed_at=now - timedelta(days=10),
    )
    # Нет scan_dt — берём closed_at.
    await _seed_supply(db_session, env.project_id, "WB-GI-T2", closed_at=now - timedelta(days=3, hours=5))
    await _seed_order(
        db_session, env.project_id, 9800, supplier_status=done, wb_status=None, supply_id="WB-GI-T1"
    )
    await _seed_order(
        db_session, env.project_id, 9801, supplier_status=done, wb_status=None, supply_id="WB-GI-T2"
    )
    # Поставки нет — фолбэк на written_off_at (списание = момент передачи).
    await _seed_order(
        db_session,
        env.project_id,
        9802,
        supplier_status=done,
        wb_status=None,
        written_off_at=now - timedelta(hours=12),
    )
    # Ни поставки, ни списания — точку отсчёта взять неоткуда.
    await _seed_order(db_session, env.project_id, 9803, supplier_status=done, wb_status=None)
    # Отсортировано — фаза «едет» кончилась, transit_days не считается.
    await _seed_order(
        db_session, env.project_id, 9804, supplier_status=done, wb_status="sorted", supply_id="WB-GI-T1"
    )
    # Не complete — тем более None.
    await _seed_order(db_session, env.project_id, 9805)

    listed = await orders_service.list_orders(db_session, env.project_id)
    by_id = {o["wb_order_id"]: o["transit_days"] for o in listed["items"]}
    assert by_id[9800] == 4
    assert by_id[9801] == 3
    assert by_id[9802] == 0
    assert by_id[9803] is None
    assert by_id[9804] is None
    assert by_id[9805] is None


@pytest.mark.asyncio
async def test_stuck_filter_window_and_counter(db_session, env):
    """Зависло = передано 2–30 дней назад, СЦ не принял; вне окна — не зависло."""
    from backend.models.wb_fbs import FBS_IN_DELIVERY_STUCK_STATUS
    from backend.utils.time import utcnow

    now = utcnow()
    done = FbsSupplierStatus.COMPLETE.value
    await _seed_supply(db_session, env.project_id, "WB-GI-S1", scan_dt=now - timedelta(days=1))
    await _seed_supply(db_session, env.project_id, "WB-GI-S2", scan_dt=now - timedelta(days=3))
    await _seed_supply(db_session, env.project_id, "WB-GI-S3", scan_dt=now - timedelta(days=40))
    # Моложе порога (1 день) — штатно едет.
    await _seed_order(
        db_session, env.project_id, 9810, supplier_status=done, wb_status=None, supply_id="WB-GI-S1"
    )
    # В окне 2–30 дней — зависло.
    await _seed_order(
        db_session, env.project_id, 9811, supplier_status=done, wb_status=None, supply_id="WB-GI-S2"
    )
    # Старше потолка — застывший wb_status, не живой груз.
    await _seed_order(
        db_session, env.project_id, 9812, supplier_status=done, wb_status=None, supply_id="WB-GI-S3"
    )
    # СЦ принял — уже не «едет», каким бы старым ни был якорь.
    await _seed_order(
        db_session, env.project_id, 9813, supplier_status=done, wb_status="sorted", supply_id="WB-GI-S2"
    )
    # Без якоря — зависшим не считается (точки отсчёта нет).
    await _seed_order(db_session, env.project_id, 9814, supplier_status=done, wb_status=None)

    listed = await orders_service.list_orders(
        db_session, env.project_id, status=FBS_IN_DELIVERY_STUCK_STATUS
    )
    assert listed["total"] == 1
    assert listed["in_delivery_stuck_count"] == 1
    assert {o["wb_order_id"] for o in listed["items"]} == {9811}
    # transit_days у зависшего задания заполнен от того же якоря.
    assert listed["items"][0]["transit_days"] == 3


@pytest.mark.asyncio
async def test_stuck_not_lit_by_post_sort_or_unknown_status(db_session, env):
    """`ready_for_pickup` (лежит в ПВЗ) и неизвестный статус НЕ зажигают «зависло».

    Прод 30.07.2026: чёрный список возвращал пост-сортировочные статусы в «едет
    к СЦ», и 168 заказов из ПВЗ через 2 дня светились зависшими. Белый список:
    при том же старом якоре зависшим остаётся только до-сортировочный `waiting`
    (и пустой статус — канон «в пути»).
    """
    from backend.models.wb_fbs import FBS_IN_DELIVERY_STUCK_STATUS
    from backend.utils.time import utcnow

    now = utcnow()
    done = FbsSupplierStatus.COMPLETE.value
    await _seed_supply(db_session, env.project_id, "WB-GI-RFP", scan_dt=now - timedelta(days=5))
    await _seed_order(
        db_session,
        env.project_id,
        9830,
        supplier_status=done,
        wb_status="ready_for_pickup",
        supply_id="WB-GI-RFP",
    )
    await _seed_order(
        db_session,
        env.project_id,
        9831,
        supplier_status=done,
        wb_status="new_wb_status_2027",
        supply_id="WB-GI-RFP",
    )
    await _seed_order(
        db_session, env.project_id, 9832, supplier_status=done, wb_status="waiting", supply_id="WB-GI-RFP"
    )

    listed = await orders_service.list_orders(
        db_session, env.project_id, status=FBS_IN_DELIVERY_STUCK_STATUS
    )
    assert listed["in_delivery_stuck_count"] == 1
    assert {o["wb_order_id"] for o in listed["items"]} == {9832}
    # ПВЗ-заказ — в фазе «отсортировано», не потерян.
    assert listed["sorted_count"] == 1


@pytest.mark.asyncio
async def test_stuck_filter_ignores_page_period(db_session, env):
    """Окно периода страницы НЕ режет зависшие: это очередь проблем, не история."""
    from backend.models.wb_fbs import FBS_IN_DELIVERY_STUCK_STATUS
    from backend.utils.time import utcnow

    now = utcnow()
    done = FbsSupplierStatus.COMPLETE.value
    await _seed_supply(db_session, env.project_id, "WB-GI-P1", scan_dt=now - timedelta(days=5))
    # Задание создано 20 дней назад — заведомо ВНЕ узкого окна страницы.
    await _seed_order(
        db_session,
        env.project_id,
        9820,
        supplier_status=done,
        wb_status=None,
        supply_id="WB-GI-P1",
        created_at_wb=now - timedelta(days=20),
    )

    day = date.today()
    listed = await orders_service.list_orders(
        db_session,
        env.project_id,
        status=FBS_IN_DELIVERY_STUCK_STATUS,
        date_from=day - timedelta(days=1),
        date_to=day,
    )
    # Период выкинул бы задание (создано 20 дней назад) — фильтр зависших его держит.
    assert listed["total"] == 1
    assert {o["wb_order_id"] for o in listed["items"]} == {9820}
    assert listed["in_delivery_stuck_count"] == 1
    # Обычные счётчики при этом окно уважают: в узком периоде заданий нет.
    assert listed["in_delivery_count"] == 0


@pytest.mark.asyncio
async def test_warehouse_summary_counts_stuck_without_period(db_session, env):
    """`in_delivery_stuck` в сводке складов — БЕЗ периода (как очередь сборки)."""
    from backend.utils.time import utcnow

    now = utcnow()
    done = FbsSupplierStatus.COMPLETE.value
    await _seed_supply(db_session, env.project_id, "WB-GI-W1", scan_dt=now - timedelta(days=5))
    await _seed_order(
        db_session,
        env.project_id,
        9830,
        supplier_status=done,
        wb_status=None,
        supply_id="WB-GI-W1",
        created_at_wb=now - timedelta(days=20),
    )
    # Свежее «едет» (вчера) — в периоде, но НЕ зависло.
    await _seed_supply(db_session, env.project_id, "WB-GI-W2", scan_dt=now - timedelta(days=1))
    await _seed_order(
        db_session,
        env.project_id,
        9831,
        supplier_status=done,
        wb_status=None,
        supply_id="WB-GI-W2",
        created_at_wb=now - timedelta(days=1),
    )

    day = date.today()
    summary = await orders_service.warehouse_summary(
        db_session, env.project_id, date_from=day - timedelta(days=2), date_to=day
    )
    totals = summary["totals"]
    # Период отрезал 9830 от «в доставке», но не от «зависло».
    assert totals["in_delivery"] == 1
    assert totals["in_delivery_stuck"] == 1
    row = next(r for r in summary["warehouses"] if r["wb_warehouse_id"] == WB_WAREHOUSE_ID)
    assert row["in_delivery_stuck"] == 1


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
