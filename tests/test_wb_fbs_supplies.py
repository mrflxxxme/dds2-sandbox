"""
Тесты WB FBS — список поставок: статус, состав по данным WB, пункт приёма.

Экран «Поставки» врал по трём колонкам разом, и все три вранья — из одного
корня: `GET /api/v3/supplies` не отдаёт ни состав, ни склад продавца, ни
статус. В payload'е поставки живут только `done` / `scanDt` / `rejectDt`.

Что закрыто:
  • `supply_status` раскладывает тройку в четыре состояния кабинета;
  • фильтр по каждому из статусов идёт SQL'ем (WHERE), а не постфильтром;
  • `rejectDt` доезжает из payload'а и не затирается NULL'ом следующим синком;
  • `wb_orders_count` — число заданий ПО ДАННЫМ WB, отдельное от `orders_count`;
  • закрытая поставка без метки состава спрашивается ОДИН раз, с меткой — нет;
  • активная поставка обновляется каждый прогон (её состав меняется);
  • потолок добора не молчит: остаток пишется в лог;
  • падение справочника офисов не роняет список поставок;
  • изоляция по `project_id`.
"""

import logging
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.models import WbFbsOrder, WbFbsSupply
from backend.models.wb_fbs import FbsSupplyStatus
from backend.services.wb_fbs import supplies_service, warehouse_service
from backend.services.wb_fbs.supplies_service import WB_ORDERS_KEY, FbsSupplyError

WB_WAREHOUSE_ID = 660001
OFFICE_ID = 10014


# ─── Моки внешних зависимостей ───────────────────────────────────────────────


class OfficesClient:
    """Справочник пунктов приёма WB (`GET /api/v3/offices`)."""

    def __init__(self, offices: list[dict] | None = None):
        self.calls = 0
        self.offices = offices if offices is not None else [
            {"id": OFFICE_ID, "name": "Коледино", "address": "Коледино, 6"},
            {"id": 99, "name": "Электросталь"},
        ]

    async def list_offices(self) -> list[dict]:
        self.calls += 1
        return list(self.offices)


class OrderIdsClient:
    """Состав поставки из `/order-ids` — по одному HTTP на поставку."""

    def __init__(self, by_supply: dict[str, list[int]] | None = None):
        self.by_supply = by_supply or {}
        self.calls: list[str] = []

    async def get_supply_order_ids(self, supply_id: str) -> list[int]:
        self.calls.append(supply_id)
        return list(self.by_supply.get(supply_id, []))


def _patch_offices(monkeypatch, client) -> object:
    """Подменяем клиента справочника офисов и гасим redis-кэш `list_offices`."""

    async def fake_get(db, project_id):
        return client

    monkeypatch.setattr(warehouse_service, "_get_client", fake_get)
    return client


def _patch_offices_failing(monkeypatch, exc: Exception) -> None:
    async def boom(db, project_id):
        raise exc

    monkeypatch.setattr(warehouse_service, "_get_client", boom)


# ─── Фикстуры данных ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def env(db_session, project):
    from types import SimpleNamespace

    return SimpleNamespace(project_id=project.id)


async def _seed_supply(db_session, project_id: int, wb_supply_id: str, **over) -> WbFbsSupply:
    fields: dict = {
        "project_id": project_id,
        "wb_supply_id": wb_supply_id,
        "name": wb_supply_id,
        "done": False,
        "orders_count": 0,
        "created_at_wb": datetime(2026, 7, 24, 8, 0, 0),
    }
    fields.update(over)
    supply = WbFbsSupply(**fields)
    db_session.add(supply)
    await db_session.commit()
    return supply


async def _four_statuses(db_session, project_id: int) -> None:
    """По одной поставке на каждое состояние кабинета WB."""
    await _seed_supply(db_session, project_id, "WB-GI-ACTIVE", done=False)
    await _seed_supply(db_session, project_id, "WB-GI-TOSHIP", done=True)
    await _seed_supply(
        db_session,
        project_id,
        "WB-GI-DELIVERY",
        done=True,
        scan_dt=datetime(2026, 7, 24, 16, 56, 55),
    )
    # Отклонённая остаётся done=true и СО сканом — `rejectDt` перебивает всё.
    await _seed_supply(
        db_session,
        project_id,
        "WB-GI-REJECTED",
        done=True,
        scan_dt=datetime(2026, 7, 24, 16, 56, 55),
        reject_dt=datetime(2026, 7, 25, 9, 0, 0),
    )


def _by_id(rows: list[dict]) -> dict[str, dict]:
    return {row["wb_supply_id"]: row for row in rows}


# ─── Статус в выдаче ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_supplies_exposes_all_four_statuses(db_session, env, monkeypatch):
    """`done`/`scan_dt`/`reject_dt` раскладываются в четыре ярлыка кабинета.

    До этого весь `done=true` схлопывался в одну «Передана» — 52 наших
    «переданных» против «В доставке 44» в кабинете.
    """
    _patch_offices(monkeypatch, OfficesClient())
    await _four_statuses(db_session, env.project_id)

    rows = _by_id(await supplies_service.list_supplies(db_session, env.project_id))

    assert rows["WB-GI-ACTIVE"]["status"] == FbsSupplyStatus.ACTIVE.value
    assert rows["WB-GI-TOSHIP"]["status"] == FbsSupplyStatus.TO_SHIP.value
    assert rows["WB-GI-DELIVERY"]["status"] == FbsSupplyStatus.IN_DELIVERY.value
    assert rows["WB-GI-REJECTED"]["status"] == FbsSupplyStatus.REJECTED.value
    # `done` остаётся в контракте: на нём завязаны кнопки «Передать»/«Удалить».
    assert rows["WB-GI-ACTIVE"]["done"] is False
    assert rows["WB-GI-REJECTED"]["done"] is True
    # Обе даты, из которых статус выведен, тоже едут наружу.
    assert rows["WB-GI-DELIVERY"]["scan_dt"] == datetime(2026, 7, 24, 16, 56, 55)
    assert rows["WB-GI-REJECTED"]["reject_dt"] == datetime(2026, 7, 25, 9, 0, 0)
    assert rows["WB-GI-TOSHIP"]["scan_dt"] is None


# ─── Фильтр по статусу — в SQL ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", "WB-GI-ACTIVE"),
        ("to_ship", "WB-GI-TOSHIP"),
        ("in_delivery", "WB-GI-DELIVERY"),
        ("rejected", "WB-GI-REJECTED"),
    ],
)
async def test_list_supplies_filters_by_status(db_session, env, monkeypatch, status, expected):
    """Каждый статус отбирает ровно свою поставку."""
    _patch_offices(monkeypatch, OfficesClient())
    await _four_statuses(db_session, env.project_id)

    rows = await supplies_service.list_supplies(db_session, env.project_id, status=status)

    assert [r["wb_supply_id"] for r in rows] == [expected]
    assert rows[0]["status"] == status


@pytest.mark.asyncio
async def test_status_filter_is_applied_in_sql_not_after_paging(db_session, env, monkeypatch):
    """Фильтр обязан стоять в WHERE, а не постфильтром по уже нарезанной странице.

    Постфильтр на `limit=1` вернул бы пусто: под потолок попала бы только
    самая свежая поставка, а нужная отсеклась бы уже в Python.
    """
    _patch_offices(monkeypatch, OfficesClient())
    await _seed_supply(
        db_session, env.project_id, "WB-GI-NEW-ACTIVE", done=False,
        created_at_wb=datetime(2026, 7, 25, 12, 0, 0),
    )
    await _seed_supply(
        db_session, env.project_id, "WB-GI-OLD-REJECTED", done=True,
        reject_dt=datetime(2026, 7, 20, 9, 0, 0),
        created_at_wb=datetime(2026, 7, 19, 12, 0, 0),
    )

    rows = await supplies_service.list_supplies(
        db_session, env.project_id, status="rejected", limit=1
    )

    assert [r["wb_supply_id"] for r in rows] == ["WB-GI-OLD-REJECTED"]


@pytest.mark.asyncio
async def test_done_filter_still_works_and_combines_with_status(db_session, env, monkeypatch):
    """`done` сохранён для обратной совместимости и складывается со статусом."""
    _patch_offices(monkeypatch, OfficesClient())
    await _four_statuses(db_session, env.project_id)

    closed = await supplies_service.list_supplies(db_session, env.project_id, done=True)
    assert len(closed) == 3

    both = await supplies_service.list_supplies(
        db_session, env.project_id, done=True, status="to_ship"
    )
    assert [r["wb_supply_id"] for r in both] == ["WB-GI-TOSHIP"]

    # Взаимоисключающая пара честно даёт пусто, а не «весь список».
    assert await supplies_service.list_supplies(
        db_session, env.project_id, done=False, status="in_delivery"
    ) == []


@pytest.mark.asyncio
async def test_unknown_status_is_rejected_not_ignored(db_session, env, monkeypatch):
    """Неизвестный статус — ошибка, а не молча снятый фильтр.

    Тихо проигнорированный фильтр показал бы ВЕСЬ список под ярлыком выбранной
    вкладки — ровно тот класс вранья, который эта правка и чинит.
    """
    _patch_offices(monkeypatch, OfficesClient())
    await _four_statuses(db_session, env.project_id)

    with pytest.raises(FbsSupplyError):
        await supplies_service.list_supplies(db_session, env.project_id, status="shipped")


# ─── reject_dt из payload'а ──────────────────────────────────────────────────


def _raw_supply(**over) -> dict:
    """Реальный payload `GET /api/v3/supplies` с прода."""
    base = {
        "id": "WB-GI-258098173",
        "done": True,
        "name": "b22fc696-подписи-нет",
        "isB2b": False,
        "scanDt": "2026-07-24T16:56:55Z",
        "closedAt": "2026-07-24T11:43:51Z",
        "rejectDt": None,
        "cargoType": 1,
        "createdAt": "2026-07-24T11:40:23Z",
        "crossBorderType": 0,
        "recommendedWhId": 0,
        "destinationOfficeId": OFFICE_ID,
        "isPickupPointShipmentAllowed": True,
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_supply_row_parses_reject_dt(env):
    """`rejectDt` больше не теряется при нормализации payload'а."""
    sync_ts = datetime(2026, 7, 25, 10, 0, 0)
    row = supplies_service._supply_row(
        _raw_supply(rejectDt="2026-07-25T09:00:00Z"), env.project_id, sync_ts
    )

    assert row is not None
    assert row["reject_dt"] == datetime(2026, 7, 25, 9, 0, 0)
    assert row["scan_dt"] == datetime(2026, 7, 24, 16, 56, 55)
    assert supplies_service._supply_row(_raw_supply(), env.project_id, sync_ts)["reject_dt"] is None


@pytest.mark.asyncio
async def test_reject_dt_survives_next_sync_without_it(db_session, env):
    """Отказ, доехавший однажды, не затирается NULL'ом следующего прогона.

    Тот же приём, что у `closed_at`/`cargo_type`: списочный метод WB отдаёт
    поля непостоянно, и прямая перезапись стирала бы уже известный факт.
    """
    sync_ts = datetime(2026, 7, 25, 10, 0, 0)
    with_reject = supplies_service._supply_row(
        _raw_supply(rejectDt="2026-07-25T09:00:00Z"), env.project_id, sync_ts
    )
    await supplies_service._upsert_supplies(db_session, env.project_id, [with_reject])
    await db_session.commit()

    without = supplies_service._supply_row(_raw_supply(), env.project_id, sync_ts)
    await supplies_service._upsert_supplies(db_session, env.project_id, [without])
    await db_session.commit()

    db_session.expire_all()
    supply = (
        await db_session.execute(
            select(WbFbsSupply).where(
                WbFbsSupply.project_id == env.project_id,
                WbFbsSupply.wb_supply_id == "WB-GI-258098173",
            )
        )
    ).scalar_one()
    assert supply.reject_dt == datetime(2026, 7, 25, 9, 0, 0)


# ─── wb_orders_count: число заданий ПО ДАННЫМ WB ─────────────────────────────


@pytest.mark.asyncio
async def test_wb_orders_count_is_separate_from_mirror_count(db_session, env, monkeypatch):
    """Два РАЗНЫХ числа: WB-состав и наше зеркало. Оба едут наружу.

    Зеркало наполняется только из `GET /orders/new`, поэтому у кабинетной
    поставки `orders_count` = 0 при 22 заданиях в WB.
    """
    _patch_offices(monkeypatch, OfficesClient())
    await _seed_supply(
        db_session, env.project_id, "WB-GI-CABINET", done=True, orders_count=0,
        raw={WB_ORDERS_KEY: 22},
    )
    # Состав не спрашивали ни разу — метки нет.
    await _seed_supply(db_session, env.project_id, "WB-GI-UNKNOWN", done=True, orders_count=0)

    rows = _by_id(await supplies_service.list_supplies(db_session, env.project_id))

    assert rows["WB-GI-CABINET"]["wb_orders_count"] == 22
    assert rows["WB-GI-CABINET"]["orders_count"] == 0
    # None ≠ 0: «не спрашивали» и «в WB пусто» — разные факты.
    assert rows["WB-GI-UNKNOWN"]["wb_orders_count"] is None


@pytest.mark.asyncio
async def test_orders_count_semantics_unchanged(db_session, env, monkeypatch):
    """`orders_count` по-прежнему считает НАШИ задания — на нём висит доклад."""
    _patch_offices(monkeypatch, OfficesClient())
    await _seed_supply(db_session, env.project_id, "WB-GI-MINE", raw={WB_ORDERS_KEY: 0})
    db_session.add(
        WbFbsOrder(
            project_id=env.project_id,
            wb_order_id=990001,
            supply_id="WB-GI-MINE",
            supplier_status="confirm",
            wb_warehouse_id=WB_WAREHOUSE_ID,
        )
    )
    await db_session.commit()
    await supplies_service._recount_orders(db_session, env.project_id)
    await db_session.commit()

    rows = _by_id(await supplies_service.list_supplies(db_session, env.project_id))
    assert rows["WB-GI-MINE"]["orders_count"] == 1
    assert rows["WB-GI-MINE"]["wb_orders_count"] == 0


# ─── Добор состава: закрытые поставки ────────────────────────────────────────


@pytest.mark.asyncio
async def test_closed_supply_without_mark_is_asked_once(db_session, env):
    """Закрытая поставка спрашивается один раз: неизменна, долбить WB незачем.

    Гейт `done == False` не пускал к `/order-ids` ни одну из 52 закрытых —
    колонка «ЗАДАНИЙ» была нулём у всех.
    """
    await _seed_supply(db_session, env.project_id, "WB-GI-CLOSED", done=True)
    client = OrderIdsClient({"WB-GI-CLOSED": [770001, 770002, 770003]})

    await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)
    assert client.calls == ["WB-GI-CLOSED"]

    db_session.expire_all()
    supply = (
        await db_session.execute(
            select(WbFbsSupply).where(
                WbFbsSupply.project_id == env.project_id,
                WbFbsSupply.wb_supply_id == "WB-GI-CLOSED",
            )
        )
    ).scalar_one()
    assert supply.raw[WB_ORDERS_KEY] == 3

    # Второй прогон в WB уже не идёт — метка есть, поставка закрыта.
    await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)
    assert client.calls == ["WB-GI-CLOSED"]


@pytest.mark.asyncio
async def test_active_supply_is_refetched_every_run(db_session, env):
    """Активную спрашиваем каждый прогон: в неё докладывают, состав меняется."""
    await _seed_supply(db_session, env.project_id, "WB-GI-OPEN", done=False)
    client = OrderIdsClient({"WB-GI-OPEN": []})

    await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)
    await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)

    assert client.calls == ["WB-GI-OPEN", "WB-GI-OPEN"]


@pytest.mark.asyncio
async def test_confirmed_empty_active_supply_still_offered_for_topup(db_session, env):
    """Инвариант доклада не сломан: подтверждённо пустая активная — кандидат."""
    await _seed_supply(db_session, env.project_id, "WB-GI-EMPTY", done=False)
    await supplies_service._pull_missing_order_ids(
        db_session, env.project_id, OrderIdsClient({"WB-GI-EMPTY": []})
    )
    await db_session.commit()

    fits = await supplies_service._load_active_supply_fits(db_session, env.project_id)
    assert [f.wb_supply_id for f in fits] == ["WB-GI-EMPTY"]

    # А непроверенная активная кандидатом не становится.
    await _seed_supply(db_session, env.project_id, "WB-GI-UNCHECKED", done=False)
    fits2 = await supplies_service._load_active_supply_fits(db_session, env.project_id)
    assert "WB-GI-UNCHECKED" not in {f.wb_supply_id for f in fits2}


@pytest.mark.asyncio
async def test_fetch_cap_leftover_is_logged_not_silent(db_session, env, monkeypatch, caplog):
    """Потолок добора не молчит: сколько осталось — в лог (правило «no silent caps»)."""
    monkeypatch.setattr(supplies_service, "_ORDER_IDS_FETCH_CAP", 2)
    for idx in range(5):
        await _seed_supply(db_session, env.project_id, f"WB-GI-C{idx}", done=True)

    client = OrderIdsClient()
    with caplog.at_level(logging.WARNING, logger="dds.wb_fbs.supplies"):
        await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)

    assert len(client.calls) == 2
    # Не просто «что-то в логе»: недобранный остаток назван числом.
    assert any("pending=3" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
async def test_pull_missing_order_ids_is_project_scoped(db_session, env, other_project):
    """Чужая поставка не спрашивается и не помечается."""
    await _seed_supply(db_session, other_project.id, "WB-GI-ALIEN", done=True)
    await _seed_supply(db_session, env.project_id, "WB-GI-OURS", done=True)

    client = OrderIdsClient({"WB-GI-OURS": [1], "WB-GI-ALIEN": [1, 2]})
    await supplies_service._pull_missing_order_ids(db_session, env.project_id, client)

    assert client.calls == ["WB-GI-OURS"]
    db_session.expire_all()
    alien = (
        await db_session.execute(
            select(WbFbsSupply).where(WbFbsSupply.wb_supply_id == "WB-GI-ALIEN")
        )
    ).scalar_one()
    assert supplies_service._wb_orders_count(alien.raw) is None


# ─── Пункт приёма WB ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_destination_office_name_resolved_once_for_whole_page(db_session, env, monkeypatch):
    """Имя пункта приёма — один поход в справочник на весь список, без N+1."""
    client = _patch_offices(monkeypatch, OfficesClient())
    for idx in range(3):
        await _seed_supply(
            db_session, env.project_id, f"WB-GI-OFF{idx}", destination_office_id=OFFICE_ID
        )
    await _seed_supply(db_session, env.project_id, "WB-GI-NOOFFICE", destination_office_id=None)

    rows = _by_id(await supplies_service.list_supplies(db_session, env.project_id))

    assert client.calls == 1  # три поставки — один запрос справочника
    assert rows["WB-GI-OFF0"]["destination_office_name"] == "Коледино"
    assert rows["WB-GI-OFF2"]["destination_office_id"] == OFFICE_ID
    assert rows["WB-GI-NOOFFICE"]["destination_office_name"] is None


@pytest.mark.asyncio
async def test_offices_lookup_is_skipped_without_office_ids(db_session, env, monkeypatch):
    """Ни у одной поставки нет пункта приёма → в WB не ходим вовсе."""
    client = _patch_offices(monkeypatch, OfficesClient())
    await _seed_supply(db_session, env.project_id, "WB-GI-BARE", destination_office_id=None)

    rows = await supplies_service.list_supplies(db_session, env.project_id)

    assert client.calls == 0
    assert rows[0]["destination_office_name"] is None


@pytest.mark.asyncio
async def test_offices_failure_does_not_break_the_list(db_session, env, monkeypatch):
    """Нет ключа / 429 у справочника → имя None, но список поставок жив."""
    _patch_offices_failing(monkeypatch, RuntimeError("WB: лимит запросов исчерпан"))
    await _seed_supply(
        db_session, env.project_id, "WB-GI-OFFDOWN", done=True, destination_office_id=OFFICE_ID
    )

    rows = await supplies_service.list_supplies(db_session, env.project_id)

    assert [r["wb_supply_id"] for r in rows] == ["WB-GI-OFFDOWN"]
    assert rows[0]["destination_office_name"] is None
    assert rows[0]["status"] == FbsSupplyStatus.TO_SHIP.value


@pytest.mark.asyncio
async def test_unknown_office_id_stays_none(db_session, env, monkeypatch):
    """Пункт приёма, которого нет в справочнике, — None, а не пустая строка."""
    _patch_offices(monkeypatch, OfficesClient())
    await _seed_supply(db_session, env.project_id, "WB-GI-STRANGE", destination_office_id=777777)

    rows = await supplies_service.list_supplies(db_session, env.project_id)
    assert rows[0]["destination_office_name"] is None


# ─── Изоляция по проекту ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_supplies_is_project_scoped(db_session, env, monkeypatch, other_project):
    """Поставки чужого проекта не видны ни в списке, ни под фильтром статуса."""
    _patch_offices(monkeypatch, OfficesClient())
    await _seed_supply(db_session, other_project.id, "WB-GI-ALIEN-LIST", done=True)
    await _seed_supply(db_session, env.project_id, "WB-GI-MY-LIST", done=True)

    rows = await supplies_service.list_supplies(db_session, env.project_id)
    assert [r["wb_supply_id"] for r in rows] == ["WB-GI-MY-LIST"]

    scoped = await supplies_service.list_supplies(db_session, env.project_id, status="to_ship")
    assert [r["wb_supply_id"] for r in scoped] == ["WB-GI-MY-LIST"]
