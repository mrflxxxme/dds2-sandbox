# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты догона истории статусов FBS из кабинета WB (`order_history`).

Закрыто здесь:
  • разбор ISO-меток WB (в т.ч. миллисекунды и суффикс `Z`) в наивный UTC;
  • маппинг сырых имён статусов в вехи, включая курьерскую ветку и пару
    «собирает» / «собрал», которую легко перепутать подстрокой;
  • идемпотентность: повторный догон не плодит строки;
  • очередь догона — сначала ни разу не забранные, терминальные не тревожим;
  • ошибка одного задания не роняет прогон, а протухшая сессия — роняет;
  • изоляция по проекту и покрытие (`history_coverage`).
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from backend.integrations.wb_portal_client import WbPortalError, WbSessionExpired
from backend.models import FbsSupplierStatus, WbFbsOrder
from backend.models.wb_fbs import WbFbsOrderHistory
from backend.utils.time import utcnow
from backend.services.wb_fbs import order_history as oh

DAY = datetime(2026, 7, 20, 12, 0, 0)


class FakePortal:
    """Мок портального клиента: отдаёт заготовленную историю или падает."""

    def __init__(self, payloads=None, fail_with=None):
        self.payloads = payloads or {}
        self.fail_with = fail_with or {}
        self.asked: list[int] = []

    async def fetch_order_history(self, wb_order_id: int) -> dict:
        self.asked.append(wb_order_id)
        if wb_order_id in self.fail_with:
            raise self.fail_with[wb_order_id]
        return self.payloads.get(wb_order_id, {"deliveryDate": None, "statuses": []})


def _payload(*statuses, delivery=None) -> dict:
    return {
        "deliveryDate": delivery,
        "statuses": [
            {"date": d, "name": n, "place": p, "isFinal": False} for d, n, p in statuses
        ],
    }


async def _order(db_session, project_id, wb_order_id, **over) -> WbFbsOrder:
    fields = {
        "project_id": project_id,
        "wb_order_id": wb_order_id,
        "created_at_wb": DAY,
        "supplier_status": FbsSupplierStatus.COMPLETE.value,
        "wb_status": "waiting",
        "synced_at": DAY,
    }
    fields.update(over)
    order = WbFbsOrder(**fields)
    db_session.add(order)
    await db_session.flush()
    return order


@pytest_asyncio.fixture
async def order(db_session, project):
    o = await _order(db_session, project.id, 8001)
    await db_session.commit()
    return o


# ─── Разбор и классификация ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-07-30T10:47:53.721896Z", datetime(2026, 7, 30, 10, 47, 53, 721896)),
        ("2026-07-24T05:23:22Z", datetime(2026, 7, 24, 5, 23, 22)),
        ("2026-07-24T08:23:22+03:00", datetime(2026, 7, 24, 5, 23, 22)),
    ],
)
def test_parse_dt_returns_naive_utc(raw, expected):
    """Метки WB приводятся к наивному UTC — как все даты домена."""
    assert oh._parse_dt(raw) == expected


def test_parse_dt_survives_garbage():
    """Мусор не роняет прогон: строка без даты — просто None."""
    assert oh._parse_dt("") is None
    assert oh._parse_dt(None) is None
    assert oh._parse_dt("не дата") is None


def test_classify_distinguishes_assembling_from_assembled():
    """🔴 «Продавец собирает» и «Продавец собрал» — РАЗНЫЕ вехи.

    Обе строки начинаются одинаково, и правило по подстроке «собирает»,
    поставленное первым, перехватило бы обе — сборка схлопнулась бы в ноль.
    """
    assert oh.classify("Продавец собирает заказ") == oh.MILESTONE_CONFIRM
    assert oh.classify("Продавец собрал заказ: скоро передаст в доставку") == oh.MILESTONE_ASSEMBLED


def test_classify_courier_branch():
    """Курьерская доставка: «Передан курьеру» = готовность к вручению."""
    assert oh.classify("Передан курьеру") == oh.MILESTONE_READY
    # Назначение курьера — ещё не передача, вехой быть не должно.
    assert oh.classify("Курьер был назначен для выполнения доставки") is None
    assert oh.is_known("Курьер был назначен для выполнения доставки")


def test_transit_legs_are_known_but_not_milestones():
    """Плечи логистики распознаны, но вехами не считаются."""
    for name in (
        "Отгружено сортировочным центром",
        "В пути в сортировочный центр",
        "Поступил в сортировочный центр-транзит",
        "Доставлен СЦ/РЦ",
        "В пути",
    ):
        assert oh.classify(name) is None, name
        assert oh.is_known(name), name


def test_needles_are_single_source_for_sql():
    """Подстроки вехи отдаются наружу — аналитика строит по ним SQL LIKE."""
    assert "отсортирован" in oh.needles_for(oh.MILESTONE_SORTED)
    assert "передан курьеру" in oh.needles_for(oh.MILESTONE_READY)
    assert oh.needles_for("нет такой вехи") == ()


# ─── Запись истории ──────────────────────────────────────────────────────────


async def test_stores_history_and_marks_order(db_session, project, order):
    """История пишется построчно, у задания проставляются метки догона."""
    client = FakePortal(
        {
            8001: _payload(
                ("2026-07-24T05:23:22Z", "Оформлен", ""),
                ("2026-07-24T08:26:45Z", "Продавец собирает заказ", ""),
                ("2026-07-26T13:59:39.598689Z", "Отсортирован", "Сынково"),
                delivery="2026-07-28T10:23:20Z",
            )
        }
    )

    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats == {"asked": 1, "orders": 1, "rows": 3, "failed": 0}
    rows = (
        await db_session.execute(
            select(WbFbsOrderHistory).where(WbFbsOrderHistory.order_id == order.id)
        )
    ).scalars().all()
    assert {r.name for r in rows} == {"Оформлен", "Продавец собирает заказ", "Отсортирован"}
    assert {r.place for r in rows if r.name == "Отсортирован"} == {"Сынково"}

    await db_session.refresh(order)
    assert order.history_synced_at is not None
    assert order.delivery_date_plan == datetime(2026, 7, 28, 10, 23, 20)


async def test_repeat_sync_is_idempotent(db_session, project, order):
    """Повторный догон дописывает только новые строки, старые не дублирует."""
    first = _payload(("2026-07-24T05:23:22Z", "Оформлен", ""))
    client = FakePortal({8001: first})
    await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    # Тот же ответ плюс новое плечо — веха прошлого прогона не должна задвоиться.
    client.payloads[8001] = _payload(
        ("2026-07-24T05:23:22Z", "Оформлен", ""),
        ("2026-07-26T13:59:39Z", "Отсортирован", "Сынково"),
    )
    order.history_synced_at = None  # вернуть в очередь принудительно
    await db_session.commit()
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats["rows"] == 1, "новой должна быть только одна строка"
    total = (
        await db_session.execute(
            select(func.count()).select_from(WbFbsOrderHistory).where(
                WbFbsOrderHistory.order_id == order.id
            )
        )
    ).scalar_one()
    assert total == 2


async def test_duplicate_rows_in_payload_collapse(db_session, project, order):
    """WB иногда дублирует строку плеча — ключ уникальности один, вставка одна."""
    client = FakePortal(
        {
            8001: _payload(
                ("2026-07-26T13:59:39Z", "Отсортирован", "Сынково"),
                ("2026-07-26T13:59:39Z", "Отсортирован", "Сынково"),
            )
        }
    )
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)
    assert stats["rows"] == 1


# ─── Очередь и устойчивость ──────────────────────────────────────────────────


async def test_queue_prefers_never_synced(db_session, project):
    """Сначала забираются задания, которых не касались ни разу."""
    fresh = await _order(db_session, project.id, 8010, history_synced_at=None)
    await _order(db_session, project.id, 8011, history_synced_at=DAY)
    await db_session.commit()

    client = FakePortal()
    await oh.sync_order_history(db_session, project.id, client=client, limit=1)

    assert client.asked == [fresh.wb_order_id]


async def test_terminal_orders_not_refetched(db_session, project):
    """Отменённое задание с уже забранной историей повторно не тревожим."""
    await _order(
        db_session,
        project.id,
        8020,
        supplier_status=FbsSupplierStatus.CANCEL.value,
        history_synced_at=utcnow() - timedelta(days=30),
    )
    await db_session.commit()

    client = FakePortal()
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats["asked"] == 0
    assert client.asked == []


async def test_finished_path_not_refetched(db_session, project):
    """🔴 Путь завершён по истории — перезабирать нечего, даже если статус `complete`.

    `supplier_status` застывает на `complete` НАВСЕГДА, поэтому по нему живость
    определять нельзя: все доставленные задания вечно считались бы едущими и
    выедали бы всю пропускную способность джоба.
    """
    order = await _order(
        db_session,
        project.id,
        8021,
        created_at_wb=utcnow() - timedelta(days=5),
        history_synced_at=utcnow() - timedelta(days=3),
    )
    db_session.add(
        WbFbsOrderHistory(
            project_id=project.id,
            order_id=order.id,
            at=utcnow() - timedelta(days=4),
            name="Получен покупателем",
        )
    )
    await db_session.commit()

    client = FakePortal()
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats["asked"] == 0


async def test_old_orders_drop_out_of_tracking(db_session, project):
    """Задание старше потолка отслеживания в очередь не возвращается."""
    await _order(
        db_session,
        project.id,
        8022,
        created_at_wb=utcnow() - timedelta(days=oh._TRACK_MAX_DAYS + 10),
        history_synced_at=utcnow() - timedelta(days=30),
    )
    await db_session.commit()

    client = FakePortal()
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats["asked"] == 0


async def test_fresh_unfinished_order_is_refreshed(db_session, project):
    """Свежее незавершённое задание перезабирается по первой ступени (6 ч)."""
    await _order(
        db_session,
        project.id,
        8023,
        created_at_wb=utcnow() - timedelta(days=1),
        history_synced_at=utcnow() - timedelta(hours=8),
    )
    await db_session.commit()

    client = FakePortal()
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats["asked"] == 1


async def test_single_order_failure_does_not_stop_run(db_session, project):
    """Ошибка одного задания не роняет прогон — путь остальных от неё не зависит."""
    await _order(db_session, project.id, 8030)
    await _order(db_session, project.id, 8031)
    await db_session.commit()

    client = FakePortal(
        payloads={8031: _payload(("2026-07-24T05:23:22Z", "Оформлен", ""))},
        fail_with={8030: WbPortalError("кабинет ответил 500")},
    )
    stats = await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert stats["failed"] == 1
    assert stats["orders"] == 1
    assert stats["rows"] == 1


async def test_expired_session_aborts_run(db_session, project):
    """🔴 Протухшая сессия роняет прогон сразу: без неё не ответит НИ ОДНО задание.

    Молотить лимит хоста впустую бессмысленно, а причина обязана быть видна
    в SyncLog, а не выглядеть случайным таймаутом.
    """
    await _order(db_session, project.id, 8040)
    await _order(db_session, project.id, 8041)
    await db_session.commit()

    client = FakePortal(fail_with={8040: WbSessionExpired("401")})
    with pytest.raises(WbSessionExpired):
        await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert len(client.asked) == 1, "второе задание спрашивать уже незачем"


async def test_project_isolation(db_session, project, other_project):
    """Догон чужого проекта не трогает наши задания и наоборот."""
    await _order(db_session, project.id, 8050)
    await _order(db_session, other_project.id, 8051)
    await db_session.commit()

    client = FakePortal()
    await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    assert client.asked == [8050]


async def test_history_coverage_counts(db_session, project, order):
    """Покрытие показывает, на скольких заданиях уже есть история."""
    before = await oh.history_coverage(db_session, project.id)
    assert before["orders_total"] == 1
    assert before["orders_covered"] == 0

    client = FakePortal({8001: _payload(("2026-07-24T05:23:22Z", "Оформлен", ""))})
    await oh.sync_order_history(db_session, project.id, client=client, limit=10)

    after = await oh.history_coverage(db_session, project.id)
    assert after["orders_covered"] == 1
    assert after["rows"] == 1
    assert after["since"] == datetime(2026, 7, 24, 5, 23, 22)
