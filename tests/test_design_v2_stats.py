# ruff: noqa: RUF001, RUF002, RUF003
"""Волна D v2: разрезы аналитики и раскладка дашборда (CONTRACT-V2 §4).

Главное, что здесь закрепляется, — совместимость: `GET /stats` обязан отдавать
ровно то же, что до волны, а новые разрезы обязаны сходиться с существующими
ручками (`/workload`) и друг с другом.
"""

from datetime import date, timedelta

import pytest

from backend.models.design import DesignTaskStatus
from backend.utils.time import utcnow
from tests.design_helpers import make_task
from tests.test_api_design_tasks import BASE, _create_project, _h, _mk_task, _msg, env  # noqa: F401

WIDGETS = ["metrics", "by_assignee", "funnel", "by_attribute"]


def _layout(order=None, hidden=()):
    ids = order or WIDGETS
    return {"widgets": [
        {"id": w, "visible": w not in hidden, "order": i} for i, w in enumerate(ids)
    ]}


# ─── Порядок роутов ──────────────────────────────────────────────────────────


async def test_stats_not_captured_by_task_id(client, env):
    for path in ("stats/by-assignee", "stats/funnel", "stats/by-attribute"):
        resp = await client.get(f"{BASE}/{path}", headers=env.viewer.h)
        assert resp.status_code == 200, (path, resp.text)


async def test_dashboard_not_captured_by_task_id(client, env):
    resp = await client.get(f"{BASE}/dashboard/layout", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text


# ─── Совместимость GET /stats ────────────────────────────────────────────────


async def test_stats_semantics_unchanged(client, env, db_session):
    """Регресс: одиночный date_from по-прежнему работает и даёт те же поля.

    Панель метрик шлёт именно такой вызов — запрет «обе границы или ни одной»
    сломал бы FROZEN-контракт v1.
    """
    await make_task(db_session, env.pid, env.author.id)
    date_from = (utcnow().date() - timedelta(days=30)).isoformat()

    resp = await client.get(f"{BASE}/stats", params={"date_from": date_from}, headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    assert set(resp.json()) == {
        "on_time_share", "avg_versions_to_accept", "median_cycle_days",
        "unassigned_over_2d", "outsourced_share", "tracked_share",
    }


async def test_window_guards(client, env):
    """Окно периода — общее правило для всех ручек статистики."""
    only_to = await client.get(
        f"{BASE}/stats/by-assignee", params={"date_to": "2026-08-01"}, headers=env.viewer.h
    )
    assert only_to.status_code == 400, only_to.text
    assert _msg(only_to) == "Укажите обе границы диапазона"

    reversed_ = await client.get(
        f"{BASE}/stats/funnel",
        params={"date_from": "2026-08-10", "date_to": "2026-08-01"},
        headers=env.viewer.h,
    )
    assert reversed_.status_code == 400
    assert _msg(reversed_) == "Начало диапазона позже конца"

    start = date(2026, 1, 1)
    ok = await client.get(
        f"{BASE}/stats/by-attribute",
        params={"date_from": start.isoformat(), "date_to": (start + timedelta(days=399)).isoformat()},
        headers=env.viewer.h,
    )
    assert ok.status_code == 200, ok.text

    too_long = await client.get(
        f"{BASE}/stats/by-attribute",
        params={"date_from": start.isoformat(), "date_to": (start + timedelta(days=400)).isoformat()},
        headers=env.viewer.h,
    )
    assert too_long.status_code == 400
    assert _msg(too_long) == "Диапазон не больше 400 дней"


async def test_empty_window_gives_none_not_zero(client, env):
    """Пустое окно: метрики приёмки — None, а не нули; страница не падает."""
    resp = await client.get(
        f"{BASE}/stats",
        params={"date_from": "2020-01-01", "date_to": "2020-01-31"},
        headers=env.viewer.h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["on_time_share"] is None
    assert body["median_cycle_days"] is None


# ─── Разрез по исполнителям ──────────────────────────────────────────────────


async def test_by_assignee_matches_workload(client, env, db_session):
    """`active` сходится с GET /workload по тому же user_id (AC-8)."""
    for _ in range(3):
        await make_task(
            db_session, env.pid, env.author.id,
            status=DesignTaskStatus.IN_PROGRESS, assignee_id=env.designer.id,
        )

    stats_resp = await client.get(f"{BASE}/stats/by-assignee", headers=env.viewer.h)
    assert stats_resp.status_code == 200, stats_resp.text
    row = next(r for r in stats_resp.json()["rows"] if r["user_id"] == env.designer.id)

    workload_resp = await client.get(f"{BASE}/workload", headers=env.viewer.h)
    wl = next(r for r in workload_resp.json() if r["user_id"] == env.designer.id)
    assert row["active"] == wl["active_tasks"] == 3


async def test_by_assignee_accepted_metrics(client, env, db_session):
    """Принятое считается по окну, активные — снимком; None при пустом знаменателе."""
    now = utcnow()
    await make_task(
        db_session, env.pid, env.author.id,
        status=DesignTaskStatus.ACCEPTED, assignee_id=env.designer.id,
        accepted_at=now, due_date=now.date() + timedelta(days=1),
    )

    resp = await client.get(f"{BASE}/stats/by-assignee", headers=env.viewer.h)
    row = next(r for r in resp.json()["rows"] if r["user_id"] == env.designer.id)
    assert row["accepted"] == 1
    assert row["on_time_share"] == 1.0
    assert row["avg_versions"] == 0.0  # версий не сдавали


# ─── Воронка ─────────────────────────────────────────────────────────────────


async def test_funnel_has_six_board_statuses_in_order(client, env, db_session):
    await make_task(db_session, env.pid, env.author.id, status=DesignTaskStatus.NEW)
    await make_task(db_session, env.pid, env.author.id, status=DesignTaskStatus.ON_HOLD)

    resp = await client.get(f"{BASE}/stats/funnel", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert [r["status"] for r in rows] == [
        "NEW", "ASSIGNED", "IN_PROGRESS", "REVIEW", "REVISION", "ACCEPTED",
    ]
    #  ON_HOLD и CANCELLED вне доски — в воронку не попадают.
    assert next(r for r in rows if r["status"] == "NEW")["count"] >= 1


# ─── Разрез по реквизитам и меткам ───────────────────────────────────────────


async def test_by_attribute_counts_and_no_value_row(client, env):
    """Задачи без выбранного значения уходят в строку «Без значения» (Р33)."""
    attr = (
        await client.post(f"{BASE}/refs/attributes", json={"name": "Бренд"}, headers=env.lead.h)
    ).json()
    value = (
        await client.post(
            f"{BASE}/refs/attributes/{attr['id']}/values",
            json={"value": "Меллори"},
            headers=env.lead.h,
        )
    ).json()

    filled = await _mk_task(client, env.author.h)
    await client.put(
        f"{BASE}/{filled['id']}/attributes", json={"value_ids": [value["id"]]}, headers=env.lead.h
    )
    await _mk_task(client, env.author.h)  # без значения

    resp = await client.get(f"{BASE}/stats/by-attribute", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    group = next(g for g in resp.json()["attributes"] if g["attribute_id"] == attr["id"])
    rows = {r["value"]: r["count"] for r in group["rows"]}
    assert rows["Меллори"] == 1
    assert rows["Без значения"] == 1


async def test_by_attribute_keeps_archived_labels(client, env):
    """Архивная метка из аналитики НЕ исчезает (Р30) — иначе ломается история."""
    label = (
        await client.post(
            f"{BASE}/refs/labels", json={"name": "Срочно", "color": "red"}, headers=env.lead.h
        )
    ).json()
    task = await _mk_task(client, env.author.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)
    await client.delete(f"{BASE}/refs/labels/{label['id']}", headers=env.lead.h)

    resp = await client.get(f"{BASE}/stats/by-attribute", headers=env.viewer.h)
    labels = {r["name"]: r["count"] for r in resp.json()["labels"]}
    assert labels.get("Срочно") == 1


# ─── Раскладка дашборда ──────────────────────────────────────────────────────


async def test_layout_default_then_saved(client, env):
    resp = await client.get(f"{BASE}/dashboard/layout", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_default"] is True
    assert [w["id"] for w in body["widgets"]] == WIDGETS

    resp = await client.put(
        f"{BASE}/dashboard/layout",
        json=_layout(order=["funnel", "metrics", "by_attribute", "by_assignee"], hidden={"metrics"}),
        headers=env.viewer.h,
    )
    assert resp.status_code == 200, resp.text
    saved = resp.json()
    assert saved["is_default"] is False
    assert [w["id"] for w in saved["widgets"]] == ["funnel", "metrics", "by_attribute", "by_assignee"]
    assert [w["order"] for w in saved["widgets"]] == [0, 1, 2, 3]
    assert next(w for w in saved["widgets"] if w["id"] == "metrics")["visible"] is False

    again = await client.get(f"{BASE}/dashboard/layout", headers=env.viewer.h)
    assert [w["id"] for w in again.json()["widgets"]] == [
        "funnel", "metrics", "by_attribute", "by_assignee"
    ]


async def test_layout_is_personal(client, env):
    """Раскладка одного пользователя не видна другому и не затирает его."""
    await client.put(f"{BASE}/dashboard/layout", json=_layout(hidden={"funnel"}), headers=env.lead.h)

    other = await client.get(f"{BASE}/dashboard/layout", headers=env.author.h)
    assert other.json()["is_default"] is True


async def test_layout_is_project_scoped(client, env, auth_headers):
    alien = await _create_project(client, auth_headers, "Design D чужой")
    await client.put(f"{BASE}/dashboard/layout", json=_layout(hidden={"funnel"}), headers=env.lead.h)

    resp = await client.get(f"{BASE}/dashboard/layout", headers=_h(auth_headers, alien["id"]))
    assert resp.json()["is_default"] is True


@pytest.mark.parametrize(
    ("widgets", "expected"),
    [
        ([{"id": "nope", "visible": True, "order": 0}], "Неизвестный виджет: nope"),
        (
            [{"id": w, "visible": True, "order": i} for i, w in enumerate([*WIDGETS, "metrics"])],
            "Виджет повторяется: metrics",
        ),
        (
            [{"id": w, "visible": True, "order": i} for i, w in enumerate(WIDGETS[:-1])],
            "Не хватает виджета: by_attribute",
        ),
    ],
)
async def test_layout_validation(client, env, widgets, expected):
    """Набор обязан быть полным и точным: иначе новый виджет молча остался бы скрытым."""
    resp = await client.put(
        f"{BASE}/dashboard/layout", json={"widgets": widgets}, headers=env.lead.h
    )
    #  400, а не 422: CONTRACT-V2 §4 обещает дословный текст в конверте модуля,
    #  а 422 отдаёт FastAPI своим `{"detail": [...]}`. Поэтому набор проверяет
    #  сервис (analytics.validate_widget_set), а не Pydantic-валидатор схемы.
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == expected


async def test_viewer_can_read_and_save_layout(client, env):
    """Р32: аналитика доступна viewer'у, включая сохранение СВОЕЙ раскладки."""
    resp = await client.get(f"{BASE}/stats/by-assignee", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    resp = await client.put(f"{BASE}/dashboard/layout", json=_layout(), headers=env.viewer.h)
    assert resp.status_code == 200, resp.text


async def test_nopage_user_is_denied(client, env):
    """Page-гейт остаётся: без ключа страницы аналитика недоступна."""
    resp = await client.get(f"{BASE}/stats/by-assignee", headers=env.nopage.h)
    assert resp.status_code == 403, resp.text
