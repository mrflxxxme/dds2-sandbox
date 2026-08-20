# ruff: noqa: RUF001, RUF002, RUF003
"""Волна C v2: разметка задачи метками и реквизитами (CONTRACT-V2 §3).

Семантика — REPLACE набора. Метки хранят историю (removed_at), реквизиты — нет.
Терминальные статусы: метки менять можно, реквизиты нельзя (Р31).
"""

import pytest

from backend.models.design import DesignTaskStatus
from tests.design_helpers import make_task
from tests.test_api_design_tasks import BASE, _create_project, _h, _mk_task, _msg, env  # noqa: F401


async def _label(client, headers, name="Срочно", color="red"):
    resp = await client.post(
        f"{BASE}/refs/labels", json={"name": name, "color": color}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _attribute(client, headers, name="Бренд", *, is_multi=False, values=()):
    resp = await client.post(
        f"{BASE}/refs/attributes", json={"name": name, "is_multi": is_multi}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    attr = resp.json()
    made = []
    for value in values:
        r = await client.post(
            f"{BASE}/refs/attributes/{attr['id']}/values", json={"value": value}, headers=headers
        )
        assert r.status_code == 201, r.text
        made.append(r.json())
    return attr, made


# ─── Метки на задаче ─────────────────────────────────────────────────────────


async def test_two_labels_on_task(client, env):
    a = await _label(client, env.lead.h, "Срочно", "red")
    b = await _label(client, env.lead.h, "Переделка", "amber")
    task = await _mk_task(client, env.author.h)

    resp = await client.put(
        f"{BASE}/{task['id']}/labels", json={"label_ids": [a["id"], b["id"]]}, headers=env.lead.h
    )
    assert resp.status_code == 200, resp.text
    assert {lb["name"] for lb in resp.json()["labels"]} == {"Срочно", "Переделка"}

    listed = await client.get(f"{BASE}", headers=env.viewer.h)
    row = next(t for t in listed.json() if t["id"] == task["id"])
    assert {lb["name"] for lb in row["labels"]} == {"Срочно", "Переделка"}


async def test_label_history_counts_reattachments(client, env):
    """Р20: снятие и повторное назначение дают times = 2."""
    label = await _label(client, env.lead.h)
    task = await _mk_task(client, env.author.h)

    for ids in ([label["id"]], [], [label["id"]]):
        resp = await client.put(
            f"{BASE}/{task['id']}/labels", json={"label_ids": ids}, headers=env.lead.h
        )
        assert resp.status_code == 200, resp.text

    detail = (await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)).json()
    assert [lb["name"] for lb in detail["labels"]] == ["Срочно"]
    history = {h["label_id"]: h["times"] for h in detail["label_history"]}
    assert history[label["id"]] == 2


async def test_label_history_survives_rename(client, env):
    """Счётчик считается по label_id — переименование метки его не сбивает.

    Именно поэтому история не парсится из журнала событий: там свободный текст.
    """
    label = await _label(client, env.lead.h)
    task = await _mk_task(client, env.author.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": []}, headers=env.lead.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)

    resp = await client.put(
        f"{BASE}/refs/labels/{label['id']}",
        json={"name": "Совсем другое имя", "color": "blue"},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text

    detail = (await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)).json()
    history = {h["name"]: h["times"] for h in detail["label_history"]}
    assert history == {"Совсем другое имя": 2}


async def test_set_labels_is_idempotent(client, env):
    """Повторный вызов с тем же набором не пишет ни строк, ни событий."""
    label = await _label(client, env.lead.h)
    task = await _mk_task(client, env.author.h)

    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)
    events_before = len((await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)).json()["events"])

    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)
    detail = (await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)).json()
    assert len(detail["events"]) == events_before
    assert {h["times"] for h in detail["label_history"]} == {1}


async def test_empty_array_clears_labels(client, env):
    label = await _label(client, env.lead.h)
    task = await _mk_task(client, env.author.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)

    resp = await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": []}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["labels"] == []


async def test_archived_label_cannot_be_attached(client, env):
    label = await _label(client, env.lead.h)
    await client.delete(f"{BASE}/refs/labels/{label['id']}", headers=env.lead.h)
    task = await _mk_task(client, env.author.h)

    resp = await client.put(
        f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h
    )
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Метка в архиве"


async def test_archived_label_stays_visible_on_old_tasks(client, env):
    """AC-6: архивная метка исчезает из выбора, но на старых задачах видна."""
    label = await _label(client, env.lead.h)
    task = await _mk_task(client, env.author.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)

    await client.delete(f"{BASE}/refs/labels/{label['id']}", headers=env.lead.h)

    detail = (await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)).json()
    assert [lb["name"] for lb in detail["labels"]] == ["Срочно"]
    assert (await client.get(f"{BASE}/refs/labels", headers=env.viewer.h)).json() == []


async def test_alien_label_is_404(client, env, auth_headers):
    other = await _create_project(client, auth_headers, "Design C метки чужие")
    alien = await _label(client, _h(auth_headers, other["id"]))
    task = await _mk_task(client, env.author.h)

    resp = await client.put(
        f"{BASE}/{task['id']}/labels", json={"label_ids": [alien["id"]]}, headers=env.lead.h
    )
    assert resp.status_code == 404, resp.text
    assert _msg(resp) == "Метка не найдена"


# ─── Права (Р29) ─────────────────────────────────────────────────────────────


async def test_author_and_assignee_may_set_labels(client, env, db_session):
    label = await _label(client, env.lead.h)
    task = await make_task(
        db_session, env.pid, env.author.id,
        status=DesignTaskStatus.IN_PROGRESS, assignee_id=env.designer.id,
    )

    for actor in (env.author, env.designer, env.lead):
        resp = await client.put(
            f"{BASE}/{task.id}/labels", json={"label_ids": [label["id"]]}, headers=actor.h
        )
        assert resp.status_code == 200, (actor.id, resp.text)
        await client.put(f"{BASE}/{task.id}/labels", json={"label_ids": []}, headers=actor.h)


async def test_outsider_cannot_set_labels(client, env, db_session):
    """Editor, который не автор и не исполнитель, разметку не ставит."""
    label = await _label(client, env.lead.h)
    task = await make_task(db_session, env.pid, env.lead.id, assignee_id=env.lead.id)

    resp = await client.put(
        f"{BASE}/{task.id}/labels", json={"label_ids": [label["id"]]}, headers=env.designer.h
    )
    assert resp.status_code == 403, resp.text
    assert _msg(resp) == "Метки ставит ведущий, автор или исполнитель"


# ─── Реквизиты на задаче ─────────────────────────────────────────────────────


async def test_single_value_attribute_rejects_two_values(client, env):
    attr, values = await _attribute(client, env.lead.h, values=("Меллори", "Уютопия"))
    task = await _mk_task(client, env.author.h)

    resp = await client.put(
        f"{BASE}/{task['id']}/attributes",
        json={"value_ids": [values[0]["id"], values[1]["id"]]},
        headers=env.lead.h,
    )
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Поле «Бренд» допускает одно значение"


async def test_multi_value_attribute_accepts_two(client, env):
    attr, values = await _attribute(
        client, env.lead.h, name="Площадки", is_multi=True, values=("WB", "Ozon")
    )
    task = await _mk_task(client, env.author.h)

    resp = await client.put(
        f"{BASE}/{task['id']}/attributes",
        json={"value_ids": [values[0]["id"], values[1]["id"]]},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text
    assert {a["value"] for a in resp.json()["attributes"]} == {"WB", "Ozon"}


async def test_attributes_appear_in_list(client, env):
    attr, values = await _attribute(client, env.lead.h, values=("Меллори",))
    task = await _mk_task(client, env.author.h)
    await client.put(
        f"{BASE}/{task['id']}/attributes", json={"value_ids": [values[0]["id"]]}, headers=env.lead.h
    )

    listed = await client.get(f"{BASE}", headers=env.viewer.h)
    row = next(t for t in listed.json() if t["id"] == task["id"])
    assert row["attributes"] == [
        {
            "attribute_id": attr["id"],
            "attribute_name": "Бренд",
            "value_id": values[0]["id"],
            "value": "Меллори",
        }
    ]


# ─── Терминальные статусы (Р31) ──────────────────────────────────────────────


@pytest.mark.parametrize("status", [DesignTaskStatus.ACCEPTED, DesignTaskStatus.CANCELLED])
async def test_terminal_allows_labels_but_not_attributes(client, env, db_session, status):
    label = await _label(client, env.lead.h)
    attr, values = await _attribute(client, env.lead.h, values=("Меллори",))
    task = await make_task(db_session, env.pid, env.author.id, status=status)

    resp = await client.put(
        f"{BASE}/{task.id}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h
    )
    assert resp.status_code == 200, resp.text

    resp = await client.put(
        f"{BASE}/{task.id}/attributes", json={"value_ids": [values[0]["id"]]}, headers=env.lead.h
    )
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Реквизиты закрытой задачи не меняются"

    detail = (await client.get(f"{BASE}/{task.id}", headers=env.lead.h)).json()
    assert detail["permissions"]["can_set_labels"] is True
    assert detail["permissions"]["can_set_attributes"] is False


# ─── Массовое проставление (Р33) ─────────────────────────────────────────────


async def test_bulk_add_labels(client, env):
    label = await _label(client, env.lead.h)
    tasks = [await _mk_task(client, env.author.h) for _ in range(3)]

    resp = await client.post(
        f"{BASE}/bulk/labels",
        json={"task_ids": [t["id"] for t in tasks], "label_ids": [label["id"]], "mode": "add"},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 3
    assert body["skipped"] == 0
    assert body["errors"] == []


async def test_bulk_skips_tasks_without_rights(client, env, db_session):
    """Нет прав на задачу → skipped, а не 403 на весь вызов."""
    label = await _label(client, env.lead.h)
    mine = await _mk_task(client, env.designer.h)
    foreign = await make_task(db_session, env.pid, env.lead.id, assignee_id=env.lead.id)

    resp = await client.post(
        f"{BASE}/bulk/labels",
        json={"task_ids": [mine["id"], foreign.id], "label_ids": [label["id"]], "mode": "add"},
        headers=env.designer.h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped"] == 1


async def test_bulk_missing_task_goes_to_errors(client, env):
    label = await _label(client, env.lead.h)
    resp = await client.post(
        f"{BASE}/bulk/labels",
        json={"task_ids": [99999999], "label_ids": [label["id"]], "mode": "add"},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["errors"] == [{"task_id": 99999999, "message": "Задача не найдена"}]


async def test_bulk_remove_mode(client, env):
    label = await _label(client, env.lead.h)
    task = await _mk_task(client, env.author.h)
    await client.put(f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h)

    resp = await client.post(
        f"{BASE}/bulk/labels",
        json={"task_ids": [task["id"]], "label_ids": [label["id"]], "mode": "remove"},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text
    detail = (await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)).json()
    assert detail["labels"] == []


async def test_bulk_cap(client, env):
    resp = await client.post(
        f"{BASE}/bulk/labels",
        json={"task_ids": list(range(501)), "label_ids": [], "mode": "add"},
        headers=env.lead.h,
    )
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Не больше 500 задач за раз"


# ─── N+1 ─────────────────────────────────────────────────────────────────────


async def test_no_n_plus_one_on_list(client, env, db_session):
    """Число SQL-запросов списка не зависит от числа задач (инвариант §6.10)."""
    from sqlalchemy import event

    from backend.database import async_engine

    label = await _label(client, env.lead.h)
    attr, values = await _attribute(client, env.lead.h, values=("Меллори",))

    async def _measure(task_count: int) -> int:
        for _ in range(task_count):
            task = await _mk_task(client, env.author.h)
            await client.put(
                f"{BASE}/{task['id']}/labels", json={"label_ids": [label["id"]]}, headers=env.lead.h
            )
            await client.put(
                f"{BASE}/{task['id']}/attributes",
                json={"value_ids": [values[0]["id"]]},
                headers=env.lead.h,
            )
        counter = {"n": 0}

        def _count(*_args, **_kwargs):
            counter["n"] += 1

        engine = async_engine.sync_engine
        event.listen(engine, "before_cursor_execute", _count)
        try:
            resp = await client.get(f"{BASE}", headers=env.viewer.h)
            assert resp.status_code == 200, resp.text
        finally:
            event.remove(engine, "before_cursor_execute", _count)
        return counter["n"]

    few = await _measure(2)
    many = await _measure(6)
    assert many <= few, f"запросов стало больше при росте числа задач: {few} → {many}"
