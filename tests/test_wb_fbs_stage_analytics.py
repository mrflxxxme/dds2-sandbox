# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты аналитики этапов FBS (`stage_analytics`): длительности пути задания.

Закрыто здесь:
  • точные этапы считаются по якорям поставки (`closed_at` / `scan_dt`);
  • 🔴 гейт якорей поставки: заданию, отменённому ДО передачи, даты поставки
    не приписываются (иначе ему достался бы чужой путь);
  • журнальные этапы (`sorted` → `ready_for_pickup` → `sold`) считаются по
    ПЕРВОМУ переходу оси, повторный вход в статус длительность не переписывает;
  • этап попадает в период по дате ЗАВЕРШЕНИЯ, а не по дате заказа;
  • мусорные замеры (обратный порядок вех, длительность сверх потолка) уходят
    в `dropped`, а не в медиану;
  • разрез по складу продавца и изоляция по проекту;
  • очередь считает те же множества, что вкладка «Заказы», и игнорирует период;
  • контракт значений (`precision` / `zone` / `bucket`) совпадает со схемами.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from backend.models import FbsSupplierStatus, WbFbsOrder, WbFbsSupply, WbFbsWarehouse
from backend.models.wb_fbs import WbFbsOrderEvent
from backend.schemas.wb_fbs import (
    ALLOWED_STAGE_BUCKETS,
    ALLOWED_STAGE_PRECISION,
    ALLOWED_STAGE_ZONES,
    FbsStageAnalyticsOut,
)
from backend.services.wb_fbs import stage_analytics as sa

WH_A = 771001
WH_B = 771002

#: Опорные сутки МСК: 15:00 МСК = 12:00 UTC, далеко от границы суток, поэтому
#: сдвиг зон не перебрасывает вехи в соседний день и тесты не «мигают».
DAY = datetime(2026, 7, 20, 12, 0, 0)
PERIOD = {"date_from": "2026-07-01", "date_to": "2026-07-31"}


# ─── Фикстуры данных ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def warehouses(db_session, project):
    """Два склада продавца — чтобы разрез был разбиением, а не одной строкой."""
    for wid, name in ((WH_A, "Склад А"), (WH_B, "Склад Б")):
        db_session.add(
            WbFbsWarehouse(project_id=project.id, wb_warehouse_id=wid, name=name, is_active=True)
        )
    await db_session.commit()


async def _supply(db_session, project_id, sid, *, closed_at=None, scan_dt=None) -> WbFbsSupply:
    supply = WbFbsSupply(
        project_id=project_id,
        wb_supply_id=sid,
        done=closed_at is not None,
        created_at_wb=closed_at or DAY,
        closed_at=closed_at,
        scan_dt=scan_dt,
    )
    db_session.add(supply)
    await db_session.flush()
    return supply


async def _order(
    db_session,
    project_id,
    wb_order_id,
    *,
    created_at_wb=DAY,
    status=FbsSupplierStatus.COMPLETE.value,
    wb_status="waiting",
    supply_id=None,
    wb_warehouse_id=WH_A,
    written_off_at=None,
) -> WbFbsOrder:
    order = WbFbsOrder(
        project_id=project_id,
        wb_order_id=wb_order_id,
        wb_warehouse_id=wb_warehouse_id,
        created_at_wb=created_at_wb,
        supplier_status=status,
        wb_status=wb_status,
        supply_id=supply_id,
        written_off_at=written_off_at,
        synced_at=DAY,
    )
    db_session.add(order)
    await db_session.flush()
    return order


async def _event(db_session, project_id, order_id, axis, value, at) -> None:
    db_session.add(
        WbFbsOrderEvent(
            project_id=project_id,
            order_id=order_id,
            axis=axis,
            old_value=None,
            new_value=value,
            changed_at=at,
        )
    )


def _stage(result: dict, key: str) -> dict:
    return next(s for s in result["stages"] if s["key"] == key)


# ─── Точные этапы: якоря поставки ────────────────────────────────────────────


async def test_exact_stages_from_supply_anchors(db_session, project, warehouses):
    """`to_handover` и `handover_scan` считаются по `closed_at` / `scan_dt`."""
    supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-1",
        closed_at=DAY + timedelta(hours=10),
        scan_dt=DAY + timedelta(hours=13),
    )
    await _order(db_session, project.id, 9001, supply_id=supply.wb_supply_id)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)

    handover = _stage(res, "to_handover")
    assert handover["count"] == 1
    assert handover["median_hours"] == pytest.approx(10.0)
    assert handover["precision"] == sa.PRECISION_EXACT

    scan = _stage(res, "handover_scan")
    assert scan["count"] == 1
    assert scan["median_hours"] == pytest.approx(3.0)


async def test_supply_anchors_gated_by_supplier_status(db_session, project, warehouses):
    """🔴 Задание, отменённое ДО передачи, не получает дат поставки.

    `closed_at`/`scan_dt` — моменты ПОСТАВКИ. Приписать их заданию, которое с
    ней не уехало, значит показать чужой путь «собрали → сдали» у отменённого.
    """
    supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-2",
        closed_at=DAY + timedelta(hours=5),
        scan_dt=DAY + timedelta(hours=8),
    )
    await _order(
        db_session,
        project.id,
        9002,
        status=FbsSupplierStatus.CANCEL.value,
        wb_status="canceled",
        supply_id=supply.wb_supply_id,
    )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)

    assert _stage(res, "to_handover")["count"] == 0
    assert _stage(res, "handover_scan")["count"] == 0


async def test_cancel_carrier_keeps_supply_anchors(db_session, project, warehouses):
    """`cancel_carrier` случается ПОСЛЕ передачи — путь до неё реален и считается."""
    supply = await _supply(
        db_session, project.id, "WB-GI-SA-3", closed_at=DAY + timedelta(hours=6)
    )
    await _order(
        db_session,
        project.id,
        9003,
        status=FbsSupplierStatus.CANCEL_CARRIER.value,
        supply_id=supply.wb_supply_id,
    )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(res, "to_handover")["count"] == 1
    assert _stage(res, "to_handover")["median_hours"] == pytest.approx(6.0)


# ─── Журнальные этапы ────────────────────────────────────────────────────────


async def test_journal_stages_use_first_transition(db_session, project, warehouses):
    """Длительность меряется по ПЕРВОМУ входу в статус, повтор её не двигает."""
    supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-4",
        closed_at=DAY + timedelta(hours=2),
        scan_dt=DAY + timedelta(hours=4),
    )
    order = await _order(
        db_session,
        project.id,
        9004,
        wb_status="sold",
        supply_id=supply.wb_supply_id,
    )
    await _event(db_session, project.id, order.id, "wb_status", "sorted", DAY + timedelta(hours=28))
    # Повторный вход в тот же статус — возврат/переоткрытие, не новый замер.
    await _event(db_session, project.id, order.id, "wb_status", "sorted", DAY + timedelta(hours=50))
    await _event(
        db_session, project.id, order.id, "wb_status", "ready_for_pickup", DAY + timedelta(hours=52)
    )
    await _event(db_session, project.id, order.id, "wb_status", "sold", DAY + timedelta(hours=76))
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)

    assert _stage(res, "to_sorted")["median_hours"] == pytest.approx(24.0)  # 4ч → 28ч
    assert _stage(res, "to_pickup")["median_hours"] == pytest.approx(24.0)  # 28ч → 52ч
    assert _stage(res, "at_pickup")["median_hours"] == pytest.approx(24.0)  # 52ч → 76ч
    assert _stage(res, "total")["median_hours"] == pytest.approx(76.0)
    # Замер опёрся на журнал, а не на историю кабинета — это видно в `approx_count`.
    assert _stage(res, "to_sorted")["approx_count"] == 1
    assert _stage(res, "to_sorted")["count"] == 1


async def test_empty_journal_means_zero_count_not_zero_duration(db_session, project, warehouses):
    """Пустой журнал — это «истории нет» (count=0), а не «этап мгновенный»."""
    supply = await _supply(
        db_session, project.id, "WB-GI-SA-5", closed_at=DAY + timedelta(hours=3)
    )
    await _order(db_session, project.id, 9005, supply_id=supply.wb_supply_id)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)

    at_pickup = _stage(res, "at_pickup")
    assert at_pickup["count"] == 0
    assert at_pickup["median_hours"] is None
    assert res["journal"]["events"] == 0


# ─── Период считается по дате ЗАВЕРШЕНИЯ этапа ───────────────────────────────


async def test_period_filters_by_stage_end_not_order_date(db_session, project, warehouses):
    """Заказ прошлого месяца, завершивший этап внутри периода, — в выборке."""
    early = datetime(2026, 6, 28, 12, 0, 0)
    supply = await _supply(db_session, project.id, "WB-GI-SA-6", closed_at=DAY)
    await _order(db_session, project.id, 9006, created_at_wb=early, supply_id=supply.wb_supply_id)
    await db_session.commit()

    inside = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(inside, "to_handover")["count"] == 1

    # Тот же заказ, но период кончается до завершения этапа — замера нет.
    outside = await sa.stage_analytics(
        db_session, project.id, date_from="2026-06-01", date_to="2026-06-30"
    )
    assert _stage(outside, "to_handover")["count"] == 0


# ─── Мусорные замеры ─────────────────────────────────────────────────────────


async def test_reversed_and_overlong_measurements_are_dropped(db_session, project, warehouses):
    """Обратный порядок вех и длительность сверх потолка не попадают в медиану."""
    # Обратный порядок: поставка «закрыта» раньше, чем оформлен заказ.
    reversed_supply = await _supply(
        db_session, project.id, "WB-GI-SA-7", closed_at=DAY - timedelta(hours=5)
    )
    await _order(db_session, project.id, 9007, supply_id=reversed_supply.wb_supply_id)

    # Сверх потолка: застывший статус даёт «этап длиной в полгода».
    long_supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-8",
        closed_at=DAY + timedelta(days=sa._MAX_STAGE_DAYS + 5),
    )
    await _order(db_session, project.id, 9008, supply_id=long_supply.wb_supply_id)

    # Нормальный замер рядом — медиана обязана считаться по нему одному.
    ok_supply = await _supply(
        db_session, project.id, "WB-GI-SA-9", closed_at=DAY + timedelta(hours=8)
    )
    await _order(db_session, project.id, 9009, supply_id=ok_supply.wb_supply_id)
    await db_session.commit()

    res = await sa.stage_analytics(
        db_session, project.id, date_from="2026-07-01", date_to="2026-10-31"
    )
    handover = _stage(res, "to_handover")
    assert handover["count"] == 1
    assert handover["median_hours"] == pytest.approx(8.0)
    assert handover["dropped"] == 2


# ─── Разрезы ─────────────────────────────────────────────────────────────────


async def test_by_warehouse_splits_and_names(db_session, project, warehouses):
    """Разрез по складу продавца: у каждого своя длительность и своё имя."""
    fast = await _supply(db_session, project.id, "WB-GI-SA-A", closed_at=DAY + timedelta(hours=2))
    slow = await _supply(db_session, project.id, "WB-GI-SA-B", closed_at=DAY + timedelta(hours=20))
    await _order(db_session, project.id, 9010, supply_id=fast.wb_supply_id, wb_warehouse_id=WH_A)
    await _order(db_session, project.id, 9011, supply_id=slow.wb_supply_id, wb_warehouse_id=WH_B)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    rows = {r["wb_warehouse_id"]: r for r in res["by_warehouse"] if r["stage"] == "to_handover"}

    assert rows[WH_A]["median_hours"] == pytest.approx(2.0)
    assert rows[WH_A]["warehouse_name"] == "Склад А"
    assert rows[WH_B]["median_hours"] == pytest.approx(20.0)
    assert rows[WH_B]["warehouse_name"] == "Склад Б"


async def test_warehouse_filter_narrows_everything(db_session, project, warehouses):
    """Фильтр склада режет и сводку, и разрез — экран не должен смешивать склады."""
    fast = await _supply(db_session, project.id, "WB-GI-SA-C", closed_at=DAY + timedelta(hours=2))
    slow = await _supply(db_session, project.id, "WB-GI-SA-D", closed_at=DAY + timedelta(hours=20))
    await _order(db_session, project.id, 9012, supply_id=fast.wb_supply_id, wb_warehouse_id=WH_A)
    await _order(db_session, project.id, 9013, supply_id=slow.wb_supply_id, wb_warehouse_id=WH_B)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, wb_warehouse_id=WH_B, **PERIOD)
    assert _stage(res, "to_handover")["count"] == 1
    assert _stage(res, "to_handover")["median_hours"] == pytest.approx(20.0)
    assert {r["wb_warehouse_id"] for r in res["by_warehouse"]} == {WH_B}


async def test_dynamics_buckets_by_stage_end(db_session, project, warehouses):
    """Динамика раскладывает замеры по МСК-суткам ЗАВЕРШЕНИЯ этапа."""
    first = await _supply(db_session, project.id, "WB-GI-SA-E", closed_at=DAY + timedelta(hours=4))
    second = await _supply(db_session, project.id, "WB-GI-SA-F", closed_at=DAY + timedelta(days=2))
    await _order(db_session, project.id, 9014, supply_id=first.wb_supply_id)
    await _order(db_session, project.id, 9015, supply_id=second.wb_supply_id)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, bucket="day", **PERIOD)
    points = [d for d in res["dynamics"] if d["stage"] == "to_handover"]

    assert res["bucket"] == "day"
    assert len(points) == 2
    assert [p["count"] for p in points] == [1, 1]
    assert points[0]["period_start"] < points[1]["period_start"]


async def test_project_isolation(db_session, project, other_project, warehouses):
    """Задания чужого проекта в выборку не попадают."""
    supply = await _supply(db_session, other_project.id, "WB-GI-SA-X", closed_at=DAY + timedelta(hours=9))
    await _order(db_session, other_project.id, 9016, supply_id=supply.wb_supply_id)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(res, "to_handover")["count"] == 0

    other = await sa.stage_analytics(db_session, other_project.id, **PERIOD)
    assert _stage(other, "to_handover")["count"] == 1


# ─── Очередь ─────────────────────────────────────────────────────────────────


async def test_queue_counts_current_phases_ignoring_period(db_session, project, warehouses):
    """Очередь — про настоящее: период её не режет, фазы те же, что на «Заказах»."""
    long_ago = datetime.utcnow() - timedelta(days=200)
    await _order(
        db_session,
        project.id,
        9020,
        created_at_wb=long_ago,
        status=FbsSupplierStatus.NEW.value,
        wb_status="waiting",
    )
    scanned = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-Q",
        closed_at=long_ago,
        scan_dt=long_ago + timedelta(hours=2),
    )
    await _order(
        db_session,
        project.id,
        9021,
        created_at_wb=long_ago,
        supply_id=scanned.wb_supply_id,
        wb_status="waiting",
    )
    await db_session.commit()

    # Период заведомо не покрывает эти задания — очередь обязана их всё равно видеть.
    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    queue = {q["phase"]: q for q in res["queue"]}

    assert queue[sa.QUEUE_NEW]["count"] == 1
    assert queue[sa.QUEUE_IN_TRANSIT]["count"] == 1
    assert queue[sa.QUEUE_IN_TRANSIT]["median_age_hours"] > 24 * 190


async def test_queue_phases_split_orders_tab_in_delivery(db_session, project, warehouses):
    """🔴 `to_ship + in_transit` == псевдо-статус `in_delivery` вкладки «Заказы».

    Фазы очереди — РАЗБИЕНИЕ того же множества по факту скана QR, а не своя
    выборка. Разойдись они — карточка на одном экране противоречила бы счётчику
    на соседнем, и цифры читались бы как ошибка расчёта.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from backend.models import WbFbsOrder as Order
    from backend.services.wb_fbs.orders_service import in_delivery_condition

    unscanned = await _supply(db_session, project.id, "WB-GI-SA-P1", closed_at=DAY)
    await _order(db_session, project.id, 9040, supply_id=unscanned.wb_supply_id, wb_status="waiting")
    scanned = await _supply(
        db_session, project.id, "WB-GI-SA-P2", closed_at=DAY, scan_dt=DAY + timedelta(hours=1)
    )
    await _order(db_session, project.id, 9041, supply_id=scanned.wb_supply_id, wb_status="")
    await _order(db_session, project.id, 9042, supply_id=scanned.wb_supply_id, wb_status="sorted")
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    queue = {q["phase"]: q for q in res["queue"]}

    tab_count = (
        await db_session.execute(
            sa_select(sa_func.count())
            .select_from(Order)
            .join(
                WbFbsSupply,
                (WbFbsSupply.project_id == project.id)
                & (WbFbsSupply.wb_supply_id == Order.supply_id),
                isouter=True,
            )
            .where(Order.project_id == project.id, in_delivery_condition())
        )
    ).scalar_one()

    assert queue[sa.QUEUE_TO_SHIP]["count"] + queue[sa.QUEUE_IN_TRANSIT]["count"] == tab_count


async def test_queue_separates_to_ship_from_in_transit(db_session, project, warehouses):
    """«Собрано, ждёт отгрузки» ≠ «едет к СЦ»: границу задаёт скан QR."""
    unscanned = await _supply(db_session, project.id, "WB-GI-SA-R", closed_at=DAY)
    await _order(db_session, project.id, 9022, supply_id=unscanned.wb_supply_id, wb_status="waiting")

    scanned = await _supply(
        db_session, project.id, "WB-GI-SA-S", closed_at=DAY, scan_dt=DAY + timedelta(hours=1)
    )
    await _order(db_session, project.id, 9023, supply_id=scanned.wb_supply_id, wb_status="waiting")
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    queue = {q["phase"]: q for q in res["queue"]}

    assert queue[sa.QUEUE_TO_SHIP]["count"] == 1
    assert queue[sa.QUEUE_IN_TRANSIT]["count"] == 1


async def test_queue_ignores_cancelled(db_session, project, warehouses):
    """Отменённое очередью не является — иначе фазы копили бы мёртвые задания."""
    await _order(
        db_session,
        project.id,
        9024,
        status=FbsSupplierStatus.NEW.value,
        wb_status="declined_by_client",
    )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert all(q["count"] == 0 for q in res["queue"])


# ─── Контракт ────────────────────────────────────────────────────────────────


async def test_response_matches_schema(db_session, project, warehouses):
    """Ответ сервиса валиден по схеме роутера (контракт с фронтом)."""
    supply = await _supply(db_session, project.id, "WB-GI-SA-T", closed_at=DAY + timedelta(hours=7))
    await _order(db_session, project.id, 9030, supply_id=supply.wb_supply_id)
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    parsed = FbsStageAnalyticsOut.model_validate(res)

    assert parsed.date_from.isoformat() == "2026-07-01"
    assert {s.key for s in parsed.stages} == {s.key for s in sa.STAGES}


def test_stage_definitions_match_schema_contract():
    """Значения `precision` / `zone` / `bucket` — те же, что объявлены схемам."""
    assert {s.precision for s in sa.STAGES} <= set(ALLOWED_STAGE_PRECISION)
    assert {s.zone for s in sa.STAGES} <= set(ALLOWED_STAGE_ZONES)
    assert set(sa.ALLOWED_BUCKETS) == set(ALLOWED_STAGE_BUCKETS)
    assert len({s.key for s in sa.STAGES}) == len(sa.STAGES)


def test_bucket_resolution_scales_with_period():
    """Шаг динамики подбирается по длине периода: сутки → недели → месяцы."""
    from datetime import date as _date

    assert sa.resolve_bucket(_date(2026, 7, 1), _date(2026, 7, 14)) == sa.BUCKET_DAY
    assert sa.resolve_bucket(_date(2026, 1, 1), _date(2026, 4, 1)) == sa.BUCKET_WEEK
    assert sa.resolve_bucket(_date(2025, 1, 1), _date(2026, 1, 1)) == sa.BUCKET_MONTH
    # Явный шаг сильнее автоподбора.
    assert sa.resolve_bucket(_date(2026, 7, 1), _date(2026, 7, 5), "month") == sa.BUCKET_MONTH


# ─── Паритет с вкладкой «Заказы» ─────────────────────────────────────────────


async def test_queue_sorted_phases_cover_orders_tab_sorted(db_session, project, warehouses):
    """🔴 `sorted + ready` == `sorted_condition()` — включая `postponed_delivery`.

    `FBS_WB_SORTED_STATUSES` содержит три значения, и `postponed_delivery`
    заведён в белый список осознанно (инцидент 30.07). Сверяй фаза очереди
    голый литерал `sorted`, такое задание не попало бы НИ В ОДНУ фазу и
    исчезло бы с экрана молча.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select as sa_select

    from backend.models import WbFbsOrder as Order
    from backend.services.wb_fbs.orders_service import sorted_condition

    supply = await _supply(
        db_session, project.id, "WB-GI-SA-P3", closed_at=DAY, scan_dt=DAY + timedelta(hours=1)
    )
    for idx, wb_status in enumerate(("sorted", "ready_for_pickup", "postponed_delivery")):
        await _order(
            db_session,
            project.id,
            9050 + idx,
            supply_id=supply.wb_supply_id,
            wb_status=wb_status,
        )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    queue = {q["phase"]: q for q in res["queue"]}

    tab_count = (
        await db_session.execute(
            sa_select(sa_func.count())
            .select_from(Order)
            .where(Order.project_id == project.id, sorted_condition())
        )
    ).scalar_one()

    assert tab_count == 3
    assert queue[sa.QUEUE_SORTED]["count"] + queue[sa.QUEUE_READY]["count"] == tab_count


async def test_queue_without_supply_mirror_counts_as_left_us(db_session, project, warehouses):
    """Списание есть, зеркала поставки нет — груз уехал, а не «ждёт отгрузки».

    Списание делается в момент передачи, и `in_delivery_stuck` на вкладке
    «Заказы» отсчитывает передачу от того же фолбэка. Сырой `t_scan IS NULL`
    держал бы такие задания в «Собрано — ждёт отгрузки» (на проде их 326).
    """
    await _order(
        db_session,
        project.id,
        9060,
        supply_id=None,
        wb_status="waiting",
        written_off_at=DAY + timedelta(hours=2),
    )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    queue = {q["phase"]: q for q in res["queue"]}

    assert queue[sa.QUEUE_IN_TRANSIT]["count"] == 1
    assert queue[sa.QUEUE_TO_SHIP]["count"] == 0


# ─── Границы суток и контур ──────────────────────────────────────────────────


async def test_period_boundary_is_msk_not_utc(db_session, project, warehouses):
    """Границы периода — МСК-сутки: 22:00 UTC 31.07 это уже 01:00 МСК 01.08."""
    late = await _supply(
        db_session, project.id, "WB-GI-SA-B1", closed_at=datetime(2026, 7, 31, 22, 0, 0)
    )
    await _order(
        db_session,
        project.id,
        9070,
        created_at_wb=datetime(2026, 7, 31, 20, 0, 0),
        supply_id=late.wb_supply_id,
    )
    await db_session.commit()

    july = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(july, "to_handover")["count"] == 0, "22:00 UTC — уже августовские МСК-сутки"

    august = await sa.stage_analytics(
        db_session, project.id, date_from="2026-08-01", date_to="2026-08-31"
    )
    assert _stage(august, "to_handover")["count"] == 1


async def test_period_lower_boundary_includes_msk_evening(db_session, project, warehouses):
    """21:30 UTC 30.06 = 00:30 МСК 01.07 — нижняя граница включает эти сутки."""
    edge = await _supply(
        db_session, project.id, "WB-GI-SA-B2", closed_at=datetime(2026, 6, 30, 21, 30, 0)
    )
    await _order(
        db_session,
        project.id,
        9071,
        created_at_wb=datetime(2026, 6, 30, 18, 0, 0),
        supply_id=edge.wb_supply_id,
    )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(res, "to_handover")["count"] == 1


async def test_sandbox_contour_excluded(db_session, project, warehouses):
    """Задания песочницы не попадают в боевые цифры этапов."""
    supply = await _supply(
        db_session, project.id, "WB-GI-SA-SB", closed_at=DAY + timedelta(hours=4)
    )
    order = await _order(db_session, project.id, 9080, supply_id=supply.wb_supply_id)
    order.raw = {"_dds_contour": "sandbox"}
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(res, "to_handover")["count"] == 0


async def test_week_and_month_buckets_group_measurements(db_session, project, warehouses):
    """Недельный и месячный шаг схлопывают замеры разных суток в одну точку."""
    for idx, day in enumerate((20, 21, 22)):
        supply = await _supply(
            db_session,
            project.id,
            f"WB-GI-SA-W{idx}",
            closed_at=datetime(2026, 7, day, 12, 0, 0),
        )
        await _order(
            db_session,
            project.id,
            9090 + idx,
            created_at_wb=datetime(2026, 7, day, 6, 0, 0),
            supply_id=supply.wb_supply_id,
        )
    await db_session.commit()

    for step in (sa.BUCKET_WEEK, sa.BUCKET_MONTH):
        res = await sa.stage_analytics(db_session, project.id, bucket=step, **PERIOD)
        points = [d for d in res["dynamics"] if d["stage"] == "to_handover"]
        assert res["bucket"] == step
        assert len(points) == 1, f"{step}: трое суток обязаны схлопнуться в одну точку"
        assert points[0]["count"] == 3


# ─── История кабинета как точный источник ────────────────────────────────────


async def _history(db_session, project_id, order_id, name, at, place=None) -> None:
    from backend.models.wb_fbs import WbFbsOrderHistory

    db_session.add(
        WbFbsOrderHistory(
            project_id=project_id, order_id=order_id, at=at, name=name, place=place, is_final=False
        )
    )


async def test_history_beats_journal(db_session, project, warehouses):
    """🔴 История кабинета ПЕРЕБИВАЕТ журнал: у неё точное время от WB.

    Журнал фиксирует переход в момент обнаружения (± каденс синка), поэтому при
    наличии обоих источников замер обязан считаться по истории, а `approx_count`
    остаться нулевым.
    """
    supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-H1",
        closed_at=DAY + timedelta(hours=2),
        scan_dt=DAY + timedelta(hours=4),
    )
    order = await _order(db_session, project.id, 9100, supply_id=supply.wb_supply_id)
    # Журнал говорит «отсортирован через 24 ч», история — «через 10 ч».
    await _event(db_session, project.id, order.id, "wb_status", "sorted", DAY + timedelta(hours=28))
    await _history(db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=14), "Сынково")
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    stage = _stage(res, "to_sorted")

    assert stage["median_hours"] == pytest.approx(10.0), "должно считаться по истории (4ч → 14ч)"
    assert stage["approx_count"] == 0


async def test_history_milestone_takes_first_leg(db_session, project, warehouses):
    """Цепочка повторяется на каждом плече — веха берётся по ПЕРВОМУ скану."""
    supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-H2",
        closed_at=DAY + timedelta(hours=1),
        scan_dt=DAY + timedelta(hours=2),
    )
    order = await _order(db_session, project.id, 9101, supply_id=supply.wb_supply_id)
    for hours, place in ((8, "Сынково"), (30, "Столбы"), (54, "Пушкино")):
        await _history(
            db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=hours), place
        )
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(res, "to_sorted")["median_hours"] == pytest.approx(6.0), "2ч → 8ч, первое плечо"


async def test_courier_branch_maps_to_ready(db_session, project, warehouses):
    """Курьерская доставка: «Передан курьеру» — та же веха, что готовность к выдаче.

    У курьерской ветки ПВЗ нет вовсе, и без этого правила этап до вручения
    у таких заданий не считался бы совсем (найдено живым догоном 30.07).
    """
    supply = await _supply(
        db_session,
        project.id,
        "WB-GI-SA-H3",
        closed_at=DAY + timedelta(hours=1),
        scan_dt=DAY + timedelta(hours=2),
    )
    order = await _order(db_session, project.id, 9102, supply_id=supply.wb_supply_id)
    await _history(db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=10))
    await _history(db_session, project.id, order.id, "Передан курьеру", DAY + timedelta(hours=20))
    await db_session.commit()

    res = await sa.stage_analytics(db_session, project.id, **PERIOD)
    assert _stage(res, "to_pickup")["median_hours"] == pytest.approx(10.0)


def test_milestone_rules_cover_observed_wb_names():
    """Все имена, встреченные живьём, распознаны — либо веха, либо явное плечо."""
    from backend.services.wb_fbs import order_history as oh

    observed = (
        "Оформлен",
        "Продавец собирает заказ",
        "Продавец собрал заказ: скоро передаст в доставку",
        "Отсортирован",
        "Отгружено сортировочным центром",
        "Отгружен распределительным центром - транзит",
        "В пути в сортировочный центр",
        "Поступил в сортировочный центр-транзит",
        "В пути",
        "Доставлен СЦ/РЦ",
        "Передан курьеру",
        "Курьер был назначен для выполнения доставки",
    )
    unknown = [n for n in observed if not oh.is_known(n)]
    assert not unknown, f"нераспознанные статусы WB: {unknown}"
    # 🔴 «собрал» и «собирает» — разные вехи, порядок правил обязан их различать.
    assert oh.classify("Продавец собрал заказ: скоро передаст в доставку") == oh.MILESTONE_ASSEMBLED
    assert oh.classify("Продавец собирает заказ") == oh.MILESTONE_CONFIRM
