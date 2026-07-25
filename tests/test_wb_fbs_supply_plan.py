"""
Тесты WB FBS — разбиение выделенных заданий на поставки (полоса A).

Единица работы в кабинете WB — ПОСТАВКА, и WB запрещает мешать в одной
поставке разные склады продавца и разные габариты. Значит при нескольких
складах пользователь физически обязан завести несколько поставок, а система
обязана разбить выделенное САМА, а не отдать сырую 409 от WB.

Что закрыто:
  • два склада в выделении → две группы (две будущие поставки);
  • разные cargoType на одном складе → тоже две группы;
  • задание, уже лежащее в другой поставке / не в `new` → blocked_reason;
  • план не ходит в WB вообще (клиент падает при любом вызове);
  • доклад в активную поставку той же группы вместо создания новой;
  • чанкование >100 заданий на PATCH;
  • частичный успех: упавшая группа пишет errors и не откатывает удачные;
  • повторный вызов не плодит пустых поставок (идемпотентность);
  • «пусто в зеркале» ≠ «пусто в WB»: непроверенная поставка не кандидат;
  • провал доклада в чужую поставку откатывается на создание своей;
  • гейт режима (409) и 429 пробрасываются наверх, а не глохнут в `errors`;
  • имя различает группы одного склада и по cargoType, и по crossBorderType;
  • поставки скоуплены по контуру, чужая/несуществующая — 404.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.wb_fbs_api import WbFbsRateLimited, WbFbsWriteBlocked
from backend.models import (
    FbsSupplierStatus,
    Nomenclature,
    WbFbsOrder,
    WbFbsSupply,
    WbFbsWarehouse,
)
from backend.models.warehouse import Warehouse, WarehouseType
from backend.services.wb_fbs import supplies_service
from backend.services.wb_fbs.contour import CONTOUR_KEY, CONTOUR_PROD, CONTOUR_SANDBOX
from backend.services.wb_fbs.supplies_service import FbsSupplyError
from backend.utils.time import utcnow

WH_A = 555001
WH_B = 555002
CHRT_ID = 991001
BARCODE = "FBS_PLAN_BC_1"


# ─── Мок клиента WB ──────────────────────────────────────────────────────────


class BulkFakeClient:
    """Мок `WbFbsClient` для bulk-пути: считает создания и PATCH-чанки.

    `fail_add_for` / `fail_create_for` позволяют уронить ОДНУ группу и
    проверить, что остальные доехали (частичный успех — норма).
    """

    def __init__(self, *, fail_add_for: set[str] | None = None, fail_create_names: set[str] | None = None):
        self.created: list[str] = []
        self.added: list[tuple[str, list[int]]] = []
        self.fail_add_for = fail_add_for or set()
        self.fail_create_names = fail_create_names or set()
        self._seq = 0

    async def create_supply(self, name) -> str:
        if name in self.fail_create_names:
            raise RuntimeError(f"WB отказал в создании поставки «{name}»")
        self._seq += 1
        supply_id = f"WB-GI-NEW-{self._seq}"
        self.created.append(name)
        return supply_id

    async def add_orders_to_supply(self, supply_id, order_ids) -> None:
        if supply_id in self.fail_add_for:
            raise RuntimeError("WB: SupplyCargoTypeMismatch")
        self.added.append((supply_id, list(order_ids)))


class ExplodingClient:
    """Любой поход в WB — провал теста. План обязан считаться локально."""

    def __getattr__(self, name):  # pragma: no cover - защитный барьер
        async def _boom(*args, **kwargs):
            raise AssertionError(f"plan_supplies не должен ходить в WB (вызван {name})")

        return _boom


class InfraFailClient:
    """Инфраструктурный отказ всего прогона: гейт режима `safe` или 429."""

    def __init__(self, exc: Exception):
        self.exc = exc

    async def create_supply(self, name) -> str:
        raise self.exc

    async def add_orders_to_supply(self, supply_id, order_ids) -> None:
        raise self.exc


class OrderIdsClient:
    """Отдаёт состав поставки из `/order-ids` — как поставка из кабинета WB."""

    def __init__(self, ids: list[int]):
        self.ids = ids
        self.calls: list[str] = []

    async def get_supply_order_ids(self, supply_id: str) -> list[int]:
        self.calls.append(supply_id)
        return list(self.ids)


def _patch_client(monkeypatch, client) -> object:
    async def fake_get(db, project_id):
        return client

    monkeypatch.setattr(supplies_service, "get_fbs_client", fake_get)
    return client


# ─── Фикстуры данных ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def env(db_session, project):
    """Два склада продавца WB (у пользователя их несколько) + номенклатура."""
    warehouse = Warehouse(
        project_id=project.id,
        name="FBS склад плана",
        warehouse_type=WarehouseType.FULFILLMENT,
        is_active=True,
    )
    db_session.add(warehouse)
    db_session.add(Nomenclature(project_id=project.id, barcode=BARCODE, chrt_id=CHRT_ID))
    for wb_id, name in ((WH_A, "Склад Москва"), (WH_B, "Склад Казань")):
        db_session.add(
            WbFbsWarehouse(project_id=project.id, wb_warehouse_id=wb_id, name=name, is_active=True)
        )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(project_id=project.id, warehouse_id=warehouse.id)


async def _seed_order(db_session, project_id: int, wb_order_id: int, **over) -> WbFbsOrder:
    fields = {
        "project_id": project_id,
        "wb_order_id": wb_order_id,
        "wb_warehouse_id": WH_A,
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


def _wb_empty_raw() -> dict:
    """`raw` поставки, состав которой WB подтвердил пустым (`/order-ids` → 0).

    Без этой метки поставка кандидатом на доклад не считается: «ноль заданий в
    нашем зеркале» ≠ «поставка пуста в WB» — состав, набранный в кабинете, к
    нам не приходит вовсе.
    """
    return {supplies_service.WB_ORDERS_KEY: 0}


async def _seed_supply(db_session, project_id: int, wb_supply_id: str, **over) -> WbFbsSupply:
    fields = {
        "project_id": project_id,
        "wb_supply_id": wb_supply_id,
        "name": wb_supply_id,
        "done": False,
        "orders_count": 0,
    }
    fields.update(over)
    supply = WbFbsSupply(**fields)
    db_session.add(supply)
    await db_session.commit()
    return supply


async def _orders_map(db_session, project_id: int) -> dict[int, WbFbsOrder]:
    db_session.expire_all()
    result = await db_session.execute(
        select(WbFbsOrder).where(WbFbsOrder.project_id == project_id).order_by(WbFbsOrder.wb_order_id)
    )
    return {o.wb_order_id: o for o in result.scalars().all()}


async def _supplies(db_session, project_id: int) -> list[WbFbsSupply]:
    db_session.expire_all()
    result = await db_session.execute(
        select(WbFbsSupply)
        .where(WbFbsSupply.project_id == project_id)
        .order_by(WbFbsSupply.wb_supply_id)
    )
    return list(result.scalars().all())


def _ok_groups(plan: dict) -> list[dict]:
    return [g for g in plan["groups"] if not g["blocked_reason"]]


def _blocked_groups(plan: dict) -> list[dict]:
    return [g for g in plan["groups"] if g["blocked_reason"]]


# ─── План: группировка ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_splits_by_warehouse(db_session, env, monkeypatch):
    """Два склада продавца → две группы: в одну поставку их класть нельзя."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_order(db_session, env.project_id, 8001, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8002, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8003, wb_warehouse_id=WH_B)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8001, 8002, 8003])

    assert plan["supplies_count"] == 2
    assert plan["total_orders"] == 3
    groups = _ok_groups(plan)
    assert [g["wb_warehouse_id"] for g in groups] == [WH_A, WH_B]
    assert [g["order_ids"] for g in groups] == [[8001, 8002], [8003]]
    assert [g["orders_count"] for g in groups] == [2, 1]
    # Имя склада продавца подтягивается для показа в UI.
    assert [g["wb_warehouse_name"] for g in groups] == ["Склад Москва", "Склад Казань"]
    assert all(g["existing_supply_id"] is None for g in groups)


@pytest.mark.asyncio
async def test_plan_splits_by_cargo_type_within_warehouse(db_session, env, monkeypatch):
    """Габаритный залипон: один склад, разные cargoType → всё равно две поставки."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_order(db_session, env.project_id, 8010, cargo_type=1)
    await _seed_order(db_session, env.project_id, 8011, cargo_type=2)
    await _seed_order(db_session, env.project_id, 8012, cargo_type=2)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8010, 8011, 8012])

    groups = _ok_groups(plan)
    assert plan["supplies_count"] == 2
    assert [(g["wb_warehouse_id"], g["cargo_type"], g["order_ids"]) for g in groups] == [
        (WH_A, 1, [8010]),
        (WH_A, 2, [8011, 8012]),
    ]


@pytest.mark.asyncio
async def test_plan_groups_none_and_zero_cargo_together(db_session, env, monkeypatch):
    """None и 0 у cargoType — «не указан», это ОДНА группа, а не две поставки."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_order(db_session, env.project_id, 8013, cargo_type=None)
    await _seed_order(db_session, env.project_id, 8014, cargo_type=0)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8013, 8014])

    groups = _ok_groups(plan)
    assert len(groups) == 1
    assert groups[0]["order_ids"] == [8013, 8014]
    # Наружу отдаём None, а не 0 — UI не должен рисовать «габарит 0».
    assert groups[0]["cargo_type"] is None


@pytest.mark.asyncio
async def test_plan_blocks_orders_already_in_supply_and_terminal(db_session, env, monkeypatch):
    """Задание в другой поставке / отменённое в группу не идёт — уходит в blocked."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_order(db_session, env.project_id, 8020)
    await _seed_order(
        db_session,
        env.project_id,
        8021,
        supply_id="WB-GI-OLD",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    await _seed_order(db_session, env.project_id, 8022, supplier_status=FbsSupplierStatus.CANCEL.value)

    plan = await supplies_service.plan_supplies(
        db_session, env.project_id, [8020, 8021, 8022, 999999]
    )

    assert plan["supplies_count"] == 1
    assert _ok_groups(plan)[0]["order_ids"] == [8020]

    reasons = {tuple(g["order_ids"]): g["blocked_reason"] for g in _blocked_groups(plan)}
    assert reasons[(8021,)] == "уже в поставке WB-GI-OLD"
    assert reasons[(8022,)] == "отменено"
    # Задание, которого нет в зеркале, тоже видно — молча терять выделенное нельзя.
    assert "синхронизируйте" in reasons[(999999,)]
    # Каждое выделенное задание попало ровно в одну группу.
    assert plan["total_orders"] == 4


@pytest.mark.asyncio
async def test_plan_reuses_active_supply_of_same_group(db_session, env, monkeypatch):
    """Активная поставка того же склада и габарита → доклад вместо создания."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_supply(
        db_session, env.project_id, "WB-GI-ACTIVE", wb_warehouse_id=WH_A, cargo_type=1, orders_count=1
    )
    await _seed_order(
        db_session,
        env.project_id,
        8030,
        supply_id="WB-GI-ACTIVE",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    await _seed_order(db_session, env.project_id, 8031)
    await _seed_order(db_session, env.project_id, 8032, wb_warehouse_id=WH_B)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8031, 8032])

    groups = {g["wb_warehouse_id"]: g for g in _ok_groups(plan)}
    assert groups[WH_A]["existing_supply_id"] == "WB-GI-ACTIVE"
    # Поставка чужого склада на роль кандидата не годится.
    assert groups[WH_B]["existing_supply_id"] is None


@pytest.mark.asyncio
async def test_plan_skips_closed_and_foreign_gabarit_supplies(db_session, env, monkeypatch):
    """Переданная поставка и поставка чужого габарита кандидатами не считаются."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_supply(
        db_session, env.project_id, "WB-GI-DONE", wb_warehouse_id=WH_A, cargo_type=1, done=True
    )
    await _seed_supply(
        db_session, env.project_id, "WB-GI-CARGO2", wb_warehouse_id=WH_A, cargo_type=2, orders_count=1
    )
    await _seed_order(
        db_session,
        env.project_id,
        8040,
        cargo_type=2,
        supply_id="WB-GI-CARGO2",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    await _seed_order(db_session, env.project_id, 8041, cargo_type=1)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8041])
    assert _ok_groups(plan)[0]["existing_supply_id"] is None


@pytest.mark.asyncio
async def test_plan_gives_empty_supply_to_only_one_group(db_session, env, monkeypatch):
    """Пустая поставка габарита не имеет — но достаётся ровно ОДНОЙ группе."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_supply(db_session, env.project_id, "WB-GI-EMPTY", raw=_wb_empty_raw())
    await _seed_order(db_session, env.project_id, 8050, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8051, wb_warehouse_id=WH_B)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8050, 8051])

    reused = [g["existing_supply_id"] for g in _ok_groups(plan)]
    assert reused.count("WB-GI-EMPTY") == 1
    assert reused.count(None) == 1


@pytest.mark.asyncio
async def test_plan_skips_supply_with_unverified_wb_composition(db_session, env, monkeypatch):
    """Кабинетная поставка не предлагается к доклажу: её состав нам НЕ ВИДЕН.

    Зеркало заданий наполняется только из `GET /orders/new`, а задание,
    положенное в поставку прямо в кабинете WB, «новым» больше не приходит и в
    `wb_fbs_orders` не попадает никогда. Считать такую поставку пустой — значит
    вечно предлагать в неё доклад и вечно ловить 409 (цена 4XX — 10 запросов
    бакета), причём цикл не разрывается ничем.
    """
    _patch_client(monkeypatch, ExplodingClient())
    # Состав не проверяли ни разу — метки нет.
    await _seed_supply(db_session, env.project_id, "WB-GI-CABINET")
    # Состав проверяли, в WB заданий 5 — тем более не кандидат.
    await _seed_supply(
        db_session, env.project_id, "WB-GI-CABINET-2", raw={supplies_service.WB_ORDERS_KEY: 5}
    )
    await _seed_order(db_session, env.project_id, 8070)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8070])
    assert _ok_groups(plan)[0]["existing_supply_id"] is None

    # А подтверждённо пустая (WB вернул ноль) — годится.
    await _seed_supply(db_session, env.project_id, "WB-GI-CONFIRMED", raw=_wb_empty_raw())
    plan2 = await supplies_service.plan_supplies(db_session, env.project_id, [8070])
    assert _ok_groups(plan2)[0]["existing_supply_id"] == "WB-GI-CONFIRMED"


@pytest.mark.asyncio
async def test_pull_missing_order_ids_records_wb_composition(db_session, env):
    """Ответ `/order-ids` не выбрасывается: число заданий WB фиксируется в зеркале.

    Раньше id, не найденные у нас, просто терялись — факт «в WB заданий N»
    нигде не оставался, и поставка так и числилась пустой.
    """
    await _seed_supply(db_session, env.project_id, "WB-GI-CABINET")
    client = OrderIdsClient([777001, 777002])

    linked = await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)

    assert linked == 0  # ни одного из этих заданий в нашем зеркале нет
    assert client.calls == ["WB-GI-CABINET"]
    supplies = {s.wb_supply_id: s for s in await _supplies(db_session, env.project_id)}
    assert supplies["WB-GI-CABINET"].raw[supplies_service.WB_ORDERS_KEY] == 2


@pytest.mark.asyncio
async def test_sandbox_supply_is_invisible_in_prod_contour(db_session, env, monkeypatch):
    """Поставка песочницы не предлагается боевому плану и не резолвится по id.

    Иначе `bulk` шлёт PATCH боевым токеном по id песочницы и получает 404/409,
    а группа уходит в errors.
    """
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_supply(
        db_session,
        env.project_id,
        "WB-GI-SANDBOX",
        wb_warehouse_id=WH_A,
        cargo_type=1,
        raw={CONTOUR_KEY: CONTOUR_SANDBOX, supplies_service.WB_ORDERS_KEY: 0},
    )
    await _seed_order(db_session, env.project_id, 8090)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8090])
    assert _ok_groups(plan)[0]["existing_supply_id"] is None

    with pytest.raises(FbsSupplyError, match="не найдена"):
        await supplies_service.list_supply_orders(db_session, env.project_id, "WB-GI-SANDBOX")


def test_supply_row_stamps_contour():
    """Зеркало поставок размечено контуром — как зеркало заданий."""
    row = supplies_service._supply_row({"id": "WB-GI-1"}, 1, utcnow())
    assert row is not None
    assert row["raw"][CONTOUR_KEY] == CONTOUR_PROD


@pytest.mark.asyncio
async def test_plan_is_project_scoped(db_session, env, other_project, monkeypatch):
    """Задание чужого проекта с тем же wb_order_id в план не попадает."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_order(db_session, other_project.id, 8060)

    plan = await supplies_service.plan_supplies(db_session, env.project_id, [8060])
    assert plan["supplies_count"] == 0
    assert _blocked_groups(plan)[0]["order_ids"] == [8060]


@pytest.mark.asyncio
async def test_plan_rejects_empty_selection(db_session, env, monkeypatch):
    _patch_client(monkeypatch, ExplodingClient())
    with pytest.raises(FbsSupplyError, match="Не переданы"):
        await supplies_service.plan_supplies(db_session, env.project_id, [])


# ─── Bulk: создание поставок по плану ────────────────────────────────────────


@pytest.mark.asyncio
async def test_bulk_creates_one_supply_per_warehouse(db_session, env, monkeypatch):
    """Два склада → две поставки, задания разъехались по своим и стали `confirm`."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8100, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8101, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8102, wb_warehouse_id=WH_B)

    out = await supplies_service.create_supplies_bulk(
        db_session, env.project_id, [8100, 8101, 8102], name_prefix="Поставка 24.07"
    )

    assert out["errors"] == []
    assert out["orders_attached"] == 3
    assert len(out["created"]) == 2
    assert out["reused"] == []
    # Имя несёт склад — иначе две поставки одного прогона неразличимы.
    assert client.created == ["Поставка 24.07 · Склад Москва", "Поставка 24.07 · Склад Казань"]

    by_supply = {sid: sorted(ids) for sid, ids in client.added}
    assert sorted(by_supply.values()) == [[8100, 8101], [8102]]

    orders = await _orders_map(db_session, env.project_id)
    assert all(o.supplier_status == FbsSupplierStatus.CONFIRM.value for o in orders.values())
    assert orders[8100].supply_id == orders[8101].supply_id
    assert orders[8102].supply_id != orders[8100].supply_id

    # Зеркало обновлено сразу, не дожидаясь синка.
    supplies = {s.wb_supply_id: s for s in await _supplies(db_session, env.project_id)}
    counts = {s.wb_warehouse_id: s.orders_count for s in supplies.values()}
    assert counts == {WH_A: 2, WH_B: 1}
    assert {s.cargo_type for s in supplies.values()} == {1}


@pytest.mark.asyncio
async def test_bulk_chunks_orders_by_hundred(db_session, env, monkeypatch):
    """WB принимает максимум 100 заданий за PATCH — режем на чанки."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    ids = list(range(8200, 8200 + 205))
    for oid in ids:
        await _seed_order(db_session, env.project_id, oid)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, ids)

    assert out["orders_attached"] == 205
    assert len(out["created"]) == 1  # один склад — одна поставка
    sizes = [len(chunk) for _, chunk in client.added]
    assert sizes == [100, 100, 5]
    assert all(len(chunk) <= 100 for _, chunk in client.added)

    orders = await _orders_map(db_session, env.project_id)
    assert len(orders) == 205
    assert {o.supply_id for o in orders.values()} == {out["created"][0]["wb_supply_id"]}


@pytest.mark.asyncio
async def test_bulk_reuses_existing_active_supply(db_session, env, monkeypatch):
    """reuse_existing → докладываем в активную поставку, новую не создаём."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_supply(
        db_session, env.project_id, "WB-GI-ACTIVE", wb_warehouse_id=WH_A, cargo_type=1, orders_count=1
    )
    await _seed_order(
        db_session,
        env.project_id,
        8300,
        supply_id="WB-GI-ACTIVE",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    await _seed_order(db_session, env.project_id, 8301)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8301])

    assert client.created == []
    assert out["created"] == []
    assert [s["wb_supply_id"] for s in out["reused"]] == ["WB-GI-ACTIVE"]
    assert out["orders_attached"] == 1

    orders = await _orders_map(db_session, env.project_id)
    assert orders[8301].supply_id == "WB-GI-ACTIVE"

    # А с reuse_existing=False та же ситуация даёт НОВУЮ поставку.
    await _seed_order(db_session, env.project_id, 8302)
    out2 = await supplies_service.create_supplies_bulk(
        db_session, env.project_id, [8302], reuse_existing=False
    )
    assert len(out2["created"]) == 1
    assert out2["reused"] == []


@pytest.mark.asyncio
async def test_bulk_partial_failure_keeps_successful_groups(db_session, env, monkeypatch):
    """Упавшая группа пишет errors и НЕ откатывает уже уехавшие группы."""
    # Первая созданная поставка (склад Москва) отобьёт PATCH.
    client = _patch_client(monkeypatch, BulkFakeClient(fail_add_for={"WB-GI-NEW-1"}))
    await _seed_order(db_session, env.project_id, 8400, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8401, wb_warehouse_id=WH_B)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8400, 8401])

    assert out["orders_attached"] == 1
    assert len(out["errors"]) == 1
    # Вторая группа реально доехала до WB — падение первой её не отменило.
    assert [ids for _, ids in client.added] == [[8401]]
    assert "Склад Москва" in out["errors"][0]
    assert "SupplyCargoTypeMismatch" in out["errors"][0]

    orders = await _orders_map(db_session, env.project_id)
    # Удачная группа доехала…
    assert orders[8401].supply_id is not None
    assert orders[8401].supplier_status == FbsSupplierStatus.CONFIRM.value
    # …а упавшая осталась нетронутой и поедет следующим вызовом.
    assert orders[8400].supply_id is None
    assert orders[8400].supplier_status == FbsSupplierStatus.NEW.value


@pytest.mark.asyncio
async def test_bulk_partial_failure_inside_chunks(db_session, env, monkeypatch):
    """Сбой на втором чанке не отменяет первый: WB его уже принял."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    ids = list(range(8500, 8500 + 150))
    for oid in ids:
        await _seed_order(db_session, env.project_id, oid)

    original = client.add_orders_to_supply
    state = {"calls": 0}

    async def flaky(supply_id, order_ids):
        state["calls"] += 1
        if state["calls"] == 2:
            raise RuntimeError("WB 429")
        await original(supply_id, order_ids)

    client.add_orders_to_supply = flaky

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, ids)

    assert out["orders_attached"] == 100  # первый чанк уцелел
    assert len(out["errors"]) == 1
    orders = await _orders_map(db_session, env.project_id)
    attached = [o for o in orders.values() if o.supply_id]
    assert len(attached) == 100
    # Созданная поставка показана пользователю — она реально есть в WB.
    assert len(out["created"]) == 1
    assert out["created"][0]["orders_count"] == 100


@pytest.mark.asyncio
async def test_bulk_is_idempotent_and_makes_no_empty_supplies(db_session, env, monkeypatch):
    """Повторный вызов с теми же id не создаёт ни одной новой поставки."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8600)
    await _seed_order(db_session, env.project_id, 8601)

    first = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8600, 8601])
    assert first["orders_attached"] == 2
    assert len(first["created"]) == 1

    second = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8600, 8601])
    assert second["created"] == []
    assert second["reused"] == []
    assert second["orders_attached"] == 0
    # Причина видна пользователю: задания уже лежат в поставке.
    assert any("уже в поставке" in e for e in second["errors"])

    assert len(client.created) == 1
    assert len(await _supplies(db_session, env.project_id)) == 1


@pytest.mark.asyncio
async def test_bulk_supply_name_fits_wb_limit(db_session, env, monkeypatch):
    """Имя поставки ≤128 символов, склад в нём сохраняется (режем префикс)."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8700)

    await supplies_service.create_supplies_bulk(
        db_session, env.project_id, [8700], name_prefix="П" * 120
    )

    name = client.created[0]
    assert len(name) <= 128
    assert name.endswith("Склад Москва")


@pytest.mark.asyncio
async def test_bulk_names_differ_for_same_warehouse_two_cargo_types(db_session, env, monkeypatch):
    """Один склад, два габарита → две поставки с РАЗНЫМИ именами."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8710, cargo_type=1)
    await _seed_order(db_session, env.project_id, 8711, cargo_type=2)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8710, 8711])

    assert len(out["created"]) == 2
    assert len(set(client.created)) == 2
    assert all("Склад Москва" in n for n in client.created)


@pytest.mark.asyncio
async def test_bulk_names_differ_for_same_cargo_two_cross_border_types(db_session, env, monkeypatch):
    """Один склад, один cargoType, РАЗНЫЙ crossBorderType → имена всё равно разные.

    Группа плана — кортеж (склад, cargoType, crossBorderType), значит и суффикс
    имени обязан нести оба габаритных поля: иначе в кабинете WB висят две
    активные поставки с идентичным именем и сборщику их не различить.
    """
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8720, cargo_type=1, cross_border_type=0)
    await _seed_order(db_session, env.project_id, 8721, cargo_type=1, cross_border_type=1)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8720, 8721])

    assert len(out["created"]) == 2
    assert len(set(client.created)) == 2
    assert all("Склад Москва" in n for n in client.created)


@pytest.mark.asyncio
async def test_bulk_falls_back_to_new_supply_when_reuse_rejected(db_session, env, monkeypatch):
    """Доклад в чужую поставку отбит → заводим свою, а не теряем группу целиком.

    Пользователь нажал «Создать 1 поставку» — он обязан получить поставку, а не
    ноль поставок и сырой жаргон WB.
    """
    client = _patch_client(monkeypatch, BulkFakeClient(fail_add_for={"WB-GI-CABINET"}))
    await _seed_supply(
        db_session, env.project_id, "WB-GI-CABINET", wb_warehouse_id=WH_A, cargo_type=1, orders_count=1
    )
    await _seed_order(
        db_session,
        env.project_id,
        8320,
        supply_id="WB-GI-CABINET",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    await _seed_order(db_session, env.project_id, 8321)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8321])

    assert out["orders_attached"] == 1
    assert len(out["created"]) == 1
    assert out["reused"] == []
    assert any("завели новую поставку" in e for e in out["errors"])

    orders = await _orders_map(db_session, env.project_id)
    assert orders[8321].supply_id == out["created"][0]["wb_supply_id"]
    # Откат ровно один: в WB ушло одно создание, а не бесконечный цикл.
    assert len(client.created) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [WbFbsWriteBlocked("safe"), WbFbsRateLimited("бакет исчерпан", retry_after=5)],
    ids=["write_blocked", "rate_limited"],
)
async def test_bulk_reraises_infra_failures_instead_of_swallowing(db_session, env, monkeypatch, exc):
    """Гейт режима (409) и 429 доходят до роутера, а не глохнут в `errors`.

    Это отказ ВСЕГО прогона: следующая группа ударилась бы в ту же стену, а
    фронт по 409 рисует баннер режима и по 429 — «подождите N с».
    """
    _patch_client(monkeypatch, InfraFailClient(exc))
    await _seed_order(db_session, env.project_id, 8330, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8331, wb_warehouse_id=WH_B)

    with pytest.raises(type(exc)):
        await supplies_service.create_supplies_bulk(db_session, env.project_id, [8330, 8331])


@pytest.mark.asyncio
async def test_bulk_infra_failure_on_reuse_does_not_burn_extra_create(db_session, env, monkeypatch):
    """429 на доклад в существующую поставку не откатывается на создание новой.

    Откат лечит «поставка не приняла габарит», а не исчерпанный бакет: лишний
    `create_supply` ударил бы в ту же стену и сжёг ещё один запрос.
    """

    class ReuseRateLimited(InfraFailClient):
        def __init__(self):
            super().__init__(WbFbsRateLimited("бакет исчерпан", retry_after=5))
            self.creates = 0

        async def create_supply(self, name) -> str:  # pragma: no cover - не должен вызываться
            self.creates += 1
            raise AssertionError("после 429 создавать новую поставку нельзя")

    client = _patch_client(monkeypatch, ReuseRateLimited())
    await _seed_supply(
        db_session, env.project_id, "WB-GI-ACTIVE-RL", wb_warehouse_id=WH_A, cargo_type=1, orders_count=1
    )
    await _seed_order(
        db_session,
        env.project_id,
        8340,
        supply_id="WB-GI-ACTIVE-RL",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )
    await _seed_order(db_session, env.project_id, 8341)

    with pytest.raises(WbFbsRateLimited):
        await supplies_service.create_supplies_bulk(db_session, env.project_id, [8341])
    assert client.creates == 0


@pytest.mark.asyncio
async def test_bulk_returns_errors_when_nothing_to_ship(db_session, env, monkeypatch):
    """Всё выделенное заблокировано → в WB не ходим вовсе."""
    _patch_client(monkeypatch, ExplodingClient())
    await _seed_order(
        db_session,
        env.project_id,
        8800,
        supply_id="WB-GI-OLD",
        supplier_status=FbsSupplierStatus.CONFIRM.value,
    )

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8800])
    assert out["created"] == []
    assert out["orders_attached"] == 0
    assert out["errors"] and "уже в поставке WB-GI-OLD" in out["errors"][0]


@pytest.mark.asyncio
async def test_output_matches_frozen_schemas(db_session, env, monkeypatch):
    """Сервис отдаёт ровно то, что описано схемами — контракт заморожен."""
    from backend.schemas.wb_fbs import FbsOrderOut, FbsSupplyBulkOut, FbsSupplyPlanOut

    _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8950, wb_warehouse_id=WH_A)
    await _seed_order(db_session, env.project_id, 8951, wb_warehouse_id=WH_B)

    plan = FbsSupplyPlanOut.model_validate(
        await supplies_service.plan_supplies(db_session, env.project_id, [8950, 8951])
    )
    assert plan.supplies_count == 2
    assert {g.wb_warehouse_id for g in plan.groups} == {WH_A, WH_B}

    bulk = FbsSupplyBulkOut.model_validate(
        await supplies_service.create_supplies_bulk(db_session, env.project_id, [8950, 8951])
    )
    assert bulk.orders_attached == 2
    assert len(bulk.created) == 2

    rows = await supplies_service.list_supply_orders(
        db_session, env.project_id, bulk.created[0].wb_supply_id
    )
    assert [FbsOrderOut.model_validate(r).wb_order_id for r in rows] == [8950]


# ─── Состав поставки ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_supply_orders_is_project_scoped(db_session, env, other_project, monkeypatch):
    """Строка поставки раскрывается составом из зеркала, чужой проект не видно."""
    client = _patch_client(monkeypatch, BulkFakeClient())
    await _seed_order(db_session, env.project_id, 8900)
    await _seed_order(db_session, env.project_id, 8901)
    await _seed_order(db_session, env.project_id, 8902, wb_warehouse_id=WH_B)

    out = await supplies_service.create_supplies_bulk(db_session, env.project_id, [8900, 8901, 8902])
    supply_a = next(s["wb_supply_id"] for s in out["created"] if s["wb_warehouse_id"] == WH_A)

    # Чужой проект с тем же id поставки — в выдачу не лезет.
    await _seed_supply(db_session, other_project.id, supply_a)
    await _seed_order(db_session, other_project.id, 8903, supply_id=supply_a)

    rows = await supplies_service.list_supply_orders(db_session, env.project_id, supply_a)
    assert sorted(r["wb_order_id"] for r in rows) == [8900, 8901]
    assert {r["supply_id"] for r in rows} == {supply_a}
    assert {r["supplier_status"] for r in rows} == {FbsSupplierStatus.CONFIRM.value}
    assert client.added  # состав действительно уезжал через bulk-путь


@pytest.mark.asyncio
async def test_list_supply_orders_404_on_unknown_and_foreign_supply(
    db_session, env, other_project, monkeypatch
):
    """Несуществующая и ЧУЖАЯ поставка — «не найдена» (404), а не пустой список.

    Иначе опечатка в id и поставка соседнего проекта рисуют «в поставке нет
    заданий», то есть ошибка адресации маскируется под валидное пустое состояние.
    """
    _patch_client(monkeypatch, ExplodingClient())

    with pytest.raises(FbsSupplyError, match="не найдена"):
        await supplies_service.list_supply_orders(db_session, env.project_id, "WB-GI-NOPE")

    # Поставка есть, но у другого проекта — для нашего её не существует.
    await _seed_supply(db_session, other_project.id, "WB-GI-ALIEN")
    await _seed_order(db_session, other_project.id, 8910, supply_id="WB-GI-ALIEN")
    with pytest.raises(FbsSupplyError, match="не найдена"):
        await supplies_service.list_supply_orders(db_session, env.project_id, "WB-GI-ALIEN")
