# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты географии доставки FBS (`geo_analytics`).

Закрыто здесь:
  • округ берётся сшивкой `rid = srid`, несшитые собираются отдельной строкой;
  • 🔴 отказы считаются ТОЛЬКО по созревшей когорте — иначе ошибка выжившего;
  • 🔴 узел с одним событием отдаёт `null`, а не ноль («неизвестно» ≠ «мгновенно»);
  • перевалки считаются по УЗЛАМ, а не по событиям (цепочка на узле повторяется);
  • матрица «склад × округ» и фильтр склада;
  • SLA против обещанной WB даты;
  • изоляция по проекту.
"""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio

from backend.models import FbsSupplierStatus, WbFbsOrder, WbFbsWarehouse
from backend.models.wb_fbs import WbFbsOrderHistory
from backend.models.wb_order import WbOrder
from backend.schemas.wb_fbs import FbsGeoAnalyticsOut
from backend.services.wb_fbs import geo_analytics as ga
from backend.utils.time import utcnow

WH_A = 881001
WH_B = 881002
DAY = datetime(2026, 7, 20, 12, 0, 0)
PERIOD = {"date_from": "2026-07-01", "date_to": "2026-07-31"}
CFO = "Центральный федеральный округ"
SFO = "Сибирский федеральный округ"


@pytest_asyncio.fixture
async def warehouses(db_session, project):
    for wid, name in ((WH_A, "Склад А"), (WH_B, "Склад Б")):
        db_session.add(
            WbFbsWarehouse(project_id=project.id, wb_warehouse_id=wid, name=name, is_active=True)
        )
    await db_session.commit()


async def _order(db_session, project_id, wb_order_id, *, okrug=None, created=DAY, **over):
    fields = {
        "project_id": project_id,
        "wb_order_id": wb_order_id,
        "wb_warehouse_id": WH_A,
        "rid": f"rid-{wb_order_id}",
        "created_at_wb": created,
        "supplier_status": FbsSupplierStatus.COMPLETE.value,
        "wb_status": "sorted",
        "synced_at": DAY,
    }
    fields.update(over)
    order = WbFbsOrder(**fields)
    db_session.add(order)
    await db_session.flush()
    if okrug:
        db_session.add(
            WbOrder(
                project_id=project_id,
                srid=fields["rid"],
                order_date=created,
                last_change_date=created,
                nm_id=1,
                total_price=0,
                oblast_okrug_name=okrug,
                region_name=okrug,
            )
        )
    return order


async def _leg(db_session, project_id, order_id, name, at, place=None):
    db_session.add(
        WbFbsOrderHistory(
            project_id=project_id, order_id=order_id, at=at, name=name, place=place, is_final=False
        )
    )


async def _path(db_session, project_id, order, *, sort_h, ready_h, places=("Сынково",)):
    """Полный путь: сортировка на каждом узле + готовность к вручению."""
    for idx, place in enumerate(places):
        await _leg(
            db_session, project_id, order.id, "Отсортирован",
            DAY + timedelta(hours=sort_h + idx), place,
        )
    await _leg(db_session, project_id, order.id, "Готов к выдаче", DAY + timedelta(hours=ready_h))


def _direction(res: dict, label: str) -> dict:
    return next(d for d in res["directions"] if d["label"] == label)


# ─── Направления ─────────────────────────────────────────────────────────────


async def test_direction_speed_by_okrug(db_session, project, warehouses):
    """Скорость считается по округу покупателя из зеркала статистики."""
    fast = await _order(db_session, project.id, 7001, okrug=CFO)
    slow = await _order(db_session, project.id, 7002, okrug=SFO)
    await _path(db_session, project.id, fast, sort_h=0, ready_h=24)
    await _path(db_session, project.id, slow, sort_h=0, ready_h=96)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)

    assert _direction(res, CFO)["median_days"] == pytest.approx(1.0)
    assert _direction(res, SFO)["median_days"] == pytest.approx(4.0)


async def test_unmatched_orders_get_own_row(db_session, project, warehouses):
    """Несшитые с зеркалом задания идут отдельной строкой, а не растворяются."""
    lost = await _order(db_session, project.id, 7010, okrug=None)
    await _path(db_session, project.id, lost, sort_h=0, ready_h=48)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)

    assert _direction(res, ga._NO_REGION)["orders"] == 1
    assert res["coverage"]["orders_total"] == 1
    assert res["coverage"]["orders_matched"] == 0


async def test_refusals_counted_only_on_matured_cohort(db_session, project, warehouses):
    """🔴 Свежий отказ в долю не идёт: когорта ещё не созрела.

    Без этого правила доля отказов считалась бы по выборке, где большинство
    заказов просто ещё в пути, — классическая ошибка выжившего.
    """
    now = utcnow()
    # Свежий отказ — моложе порога созревания.
    await _order(
        db_session, project.id, 7020, okrug=CFO,
        created=now - timedelta(days=2), wb_status="canceled_by_client",
    )
    # Старый отказ — уже созрел.
    await _order(
        db_session, project.id, 7021, okrug=CFO,
        created=now - timedelta(days=ga._MATURITY_DAYS + 5), wb_status="canceled_by_client",
    )
    await db_session.commit()

    res = await ga.geo_analytics(
        db_session, project.id, date_from="2026-01-01", date_to="2026-12-31"
    )
    row = _direction(res, CFO)

    assert row["matured"] == 1, "в знаменатель идёт только созревший заказ"
    assert row["refused"] == 1


# ─── Перевалки и узлы ────────────────────────────────────────────────────────


async def test_hops_count_nodes_not_events(db_session, project, warehouses):
    """Перевалки считаются по УЗЛАМ: повтор статуса на узле маршрут не удлиняет."""
    order = await _order(db_session, project.id, 7030, okrug=CFO)
    # Три события на двух узлах.
    await _leg(db_session, project.id, order.id, "Отсортирован", DAY, "Сынково")
    await _leg(db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=1), "Сынково")
    await _leg(db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=8), "Столбы")
    await _leg(db_session, project.id, order.id, "Готов к выдаче", DAY + timedelta(hours=24))
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)

    assert [h["hops"] for h in res["hops"]] == [2]
    assert _direction(res, CFO)["avg_hops"] == pytest.approx(2.0)


async def test_node_with_single_event_reports_unknown_not_zero(db_session, project, warehouses):
    """🔴 Узел с одним событием отдаёт `null`: «неизвестно» ≠ «прошёл мгновенно»."""
    for idx in range(ga._MIN_NODE_PASSES):
        order = await _order(db_session, project.id, 7100 + idx, okrug=CFO)
        # Один проход по узлу «Транзит» и два по «Сынково».
        await _leg(db_session, project.id, order.id, "Отсортирован", DAY, "Транзит")
        await _leg(db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=1), "Сынково")
        await _leg(
            db_session, project.id, order.id, "Отгружено сортировочным центром",
            DAY + timedelta(hours=5), "Сынково",
        )
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    nodes = {n["label"]: n for n in res["nodes"]}

    assert nodes["Транзит"]["measured"] == 0
    assert nodes["Транзит"]["median_hours"] is None, "ноль здесь означал бы «мгновенно»"
    assert nodes["Сынково"]["measured"] == ga._MIN_NODE_PASSES
    assert nodes["Сынково"]["median_hours"] == pytest.approx(4.0)


async def test_rare_nodes_filtered_out(db_session, project, warehouses):
    """Узел с единичными проходами — шум, в таблицу не попадает."""
    order = await _order(db_session, project.id, 7200, okrug=CFO)
    await _leg(db_session, project.id, order.id, "Отсортирован", DAY, "Редкий узел")
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    assert all(n["label"] != "Редкий узел" for n in res["nodes"])


# ─── Матрица, SLA, изоляция ──────────────────────────────────────────────────


async def test_matrix_splits_warehouse_and_okrug(db_session, project, warehouses):
    """Матрица «склад × округ» разводит одно направление по складам отгрузки."""
    a = await _order(db_session, project.id, 7300, okrug=CFO, wb_warehouse_id=WH_A)
    b = await _order(db_session, project.id, 7301, okrug=CFO, wb_warehouse_id=WH_B)
    await _path(db_session, project.id, a, sort_h=0, ready_h=24)
    await _path(db_session, project.id, b, sort_h=0, ready_h=96)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    cells = {(c["warehouse_name"], c["label"]): c for c in res["matrix"]}

    assert cells[("Склад А", CFO)]["median_days"] == pytest.approx(1.0)
    assert cells[("Склад Б", CFO)]["median_days"] == pytest.approx(4.0)


async def test_warehouse_filter_narrows_everything(db_session, project, warehouses):
    """Фильтр склада режет и направления, и матрицу."""
    a = await _order(db_session, project.id, 7310, okrug=CFO, wb_warehouse_id=WH_A)
    b = await _order(db_session, project.id, 7311, okrug=CFO, wb_warehouse_id=WH_B)
    await _path(db_session, project.id, a, sort_h=0, ready_h=24)
    await _path(db_session, project.id, b, sort_h=0, ready_h=96)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, wb_warehouse_id=WH_B, **PERIOD)

    assert _direction(res, CFO)["orders"] == 1
    assert _direction(res, CFO)["median_days"] == pytest.approx(4.0)
    assert {c["warehouse_name"] for c in res["matrix"]} == {"Склад Б"}


async def test_sla_against_wb_promise(db_session, project, warehouses):
    """SLA: готовность не позже обещанной WB даты."""
    in_time = await _order(
        db_session, project.id, 7320, okrug=CFO,
        delivery_date_plan=DAY + timedelta(hours=48),
    )
    late = await _order(
        db_session, project.id, 7321, okrug=CFO,
        delivery_date_plan=DAY + timedelta(hours=12),
    )
    await _path(db_session, project.id, in_time, sort_h=0, ready_h=24)
    await _path(db_session, project.id, late, sort_h=0, ready_h=36)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    row = _direction(res, CFO)

    assert row["sla_total"] == 2
    assert row["sla_in_time"] == 1


async def test_project_isolation(db_session, project, other_project, warehouses):
    """Задания чужого проекта в географию не попадают."""
    alien = await _order(db_session, other_project.id, 7400, okrug=CFO)
    await _path(db_session, other_project.id, alien, sort_h=0, ready_h=24)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    assert res["directions"] == []
    assert res["matrix"] == []


async def test_response_matches_schema(db_session, project, warehouses):
    """Ответ сервиса валиден по схеме роутера (контракт с фронтом)."""
    order = await _order(db_session, project.id, 7500, okrug=CFO)
    await _path(db_session, project.id, order, sort_h=0, ready_h=30)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    parsed = FbsGeoAnalyticsOut.model_validate(res)

    assert parsed.maturity_days == ga._MATURITY_DAYS
    assert parsed.directions[0].label == CFO


# ─── Города назначения (покрытие без дыр сшивки) ─────────────────────────────


async def test_destination_is_last_node_before_ready(db_session, project, warehouses):
    """Город назначения — ПОСЛЕДНИЙ узел перед готовностью, а не первый и не любой."""
    order = await _order(db_session, project.id, 7600, okrug=CFO)
    await _leg(db_session, project.id, order.id, "Отсортирован", DAY, "Сынково")
    await _leg(db_session, project.id, order.id, "Отсортирован", DAY + timedelta(hours=20), "Мурманск")
    await _leg(db_session, project.id, order.id, "Готов к выдаче", DAY + timedelta(hours=30))
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)

    assert [d["label"] for d in res["destinations"]] == ["Мурманск"]
    # 30 ч = 1.25 сут; в контракт длительности уходят с точностью до десятых.
    assert res["destinations"][0]["median_days"] == pytest.approx(1.2)


async def test_destinations_cover_unmatched_orders(db_session, project, warehouses):
    """🔴 Город назначения известен и для НЕсшитых заданий — в этом весь смысл.

    Сшивка с зеркалом статистики дырявая (отменённые в отчёт продаж не попадают
    вовсе), и без этого блока пятая часть выборки отвечала бы «куда едет»
    строкой «не сшито».
    """
    lost = await _order(db_session, project.id, 7601, okrug=None)
    await _leg(db_session, project.id, lost.id, "Отсортирован", DAY, "Ногинск")
    await _leg(db_session, project.id, lost.id, "Готов к выдаче", DAY + timedelta(hours=24))
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)

    assert res["coverage"]["orders_matched"] == 0
    assert [d["label"] for d in res["destinations"]] == ["Ногинск"]


async def test_matrix_excludes_unmatched_pseudo_okrug(db_session, project, warehouses):
    """Псевдо-округ «не сшито» в матрицу не идёт — это не направление."""
    lost = await _order(db_session, project.id, 7602, okrug=None)
    known = await _order(db_session, project.id, 7603, okrug=CFO)
    await _path(db_session, project.id, lost, sort_h=0, ready_h=24)
    await _path(db_session, project.id, known, sort_h=0, ready_h=24)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)

    assert {c["label"] for c in res["matrix"]} == {CFO, ga._ALL_DIRECTIONS}
    # В «Направлениях» строка остаётся — там она честно объясняет пробел.
    assert _direction(res, ga._NO_REGION)["orders"] == 1


async def test_warehouse_survives_when_all_its_paths_unmatched(db_session, project, warehouses):
    """🔴 Склад не исчезает из матрицы, даже если ВСЕ его пути не сшились.

    Регрессия: после исключения псевдо-округа «не сшито» из столбцов склад,
    у которого сшитых путей нет вовсе, пропадал из таблицы целиком — на живых
    данных так исчез «Газпром Москва». Итоговый столбец считается по всем
    путям и держит строку на месте.
    """
    lonely = await _order(db_session, project.id, 7700, okrug=None, wb_warehouse_id=WH_B)
    await _path(db_session, project.id, lonely, sort_h=0, ready_h=48)
    await db_session.commit()

    res = await ga.geo_analytics(db_session, project.id, **PERIOD)
    cells = [c for c in res["matrix"] if c["warehouse_name"] == "Склад Б"]

    assert cells, "склад обязан остаться в матрице"
    assert cells[0]["label"] == ga._ALL_DIRECTIONS
    assert cells[0]["median_days"] == pytest.approx(2.0)
