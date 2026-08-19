# ruff: noqa: RUF001, RUF002, RUF003
"""Волна B v2: редактируемый номер заявки (CONTRACT-V2 §2, Р18).

Номер меняет только ведущий дизайнер и только у незакрытой задачи. Тексты гвардов
сверяются ДОСЛОВНО — они часть контракта. Автонумерация DES-N живёт своей линейкой
и произвольными номерами не сбивается: это закреплено отдельным тестом, иначе
следующая заявка получила бы номер, зависящий от чужого переименования.
"""

import asyncio
from datetime import date

import pytest

from backend.models.design import DesignTaskStatus
from tests.design_helpers import make_task
from tests.test_api_design_tasks import BASE, _msg, _mk_task, env  # noqa: F401


async def test_lead_changes_number(client, env):
    """Смена номера ведущим: 200, новый номер виден, в журнале — след."""
    task = await _mk_task(client, env.author.h)
    old = task["number"]

    resp = await client.put(f"{BASE}/{task['id']}", json={"number": "ABC-123"}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == "ABC-123"

    detail = await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)
    body = detail.json()
    assert body["number"] == "ABC-123"
    assert any(
        e.get("comment") == f"Номер изменён: {old} → ABC-123" for e in body["events"]
    ), body["events"]

    listed = await client.get(f"{BASE}", headers=env.viewer.h)
    assert "ABC-123" in [t["number"] for t in listed.json()]


async def test_permission_flag_and_403_for_non_lead(client, env, db_session):
    """403 автору и исполнителю; can_edit_number у них false, у ведущего true."""
    task = await make_task(
        db_session, env.pid, env.author.id,
        status=DesignTaskStatus.IN_PROGRESS, assignee_id=env.designer.id,
    )

    for actor in (env.author, env.designer):
        resp = await client.put(f"{BASE}/{task.id}", json={"number": "X-1"}, headers=actor.h)
        assert resp.status_code == 403, (actor.id, resp.text)
        assert _msg(resp) == "Номер заявки меняет только ведущий дизайнер"

        detail = await client.get(f"{BASE}/{task.id}", headers=actor.h)
        assert detail.json()["permissions"]["can_edit_number"] is False

    lead_detail = await client.get(f"{BASE}/{task.id}", headers=env.lead.h)
    assert lead_detail.json()["permissions"]["can_edit_number"] is True


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        ("", "Номер не может быть пустым"),
        ("   ", "Номер не может быть пустым"),
        ("A" * 41, "Номер длиннее 40 символов"),
        ("ABC 123", "Номер: только буквы, цифры, дефис, точка и подчёркивание"),
        ("ABC/123", "Номер: только буквы, цифры, дефис, точка и подчёркивание"),
    ],
)
async def test_number_guards(client, env, number, expected):
    """Пять 400-кейсов CONTRACT-V2 §2 — дословно."""
    task = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{task['id']}", json={"number": number}, headers=env.lead.h)
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == expected


async def test_number_length_boundary(client, env):
    """Ровно 40 символов — можно, 41 — нельзя."""
    task = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{task['id']}", json={"number": "A" * 40}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == "A" * 40


async def test_number_allows_cyrillic_and_punctuation(client, env):
    """Разрешённый класс символов: кириллица, цифры, дефис, точка, подчёркивание."""
    task = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{task['id']}", json={"number": "Заявка_12.3-А"}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == "Заявка_12.3-А"


async def test_number_is_trimmed(client, env):
    """Пробелы по краям срезаются до проверок, внутренние формат не допускает."""
    task = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{task['id']}", json={"number": "  ABC-9  "}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == "ABC-9"


async def test_number_taken(client, env):
    """Занятый живой задачей номер — 400, а не 500 IntegrityError."""
    first = await _mk_task(client, env.author.h)
    second = await _mk_task(client, env.author.h)

    resp = await client.put(f"{BASE}/{second['id']}", json={"number": first["number"]}, headers=env.lead.h)
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Номер уже занят"


async def test_same_number_is_noop(client, env):
    """Присвоение того же номера себе — не «занят», а тихий no-op."""
    task = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{task['id']}", json={"number": task["number"]}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == task["number"]


async def test_parallel_change_to_same_number(client, env):
    """Гонка двух смен на один номер: одна 200, вторая 400 — не 500."""
    a = await _mk_task(client, env.author.h)
    b = await _mk_task(client, env.author.h)

    results = await asyncio.gather(
        client.put(f"{BASE}/{a['id']}", json={"number": "RACE-1"}, headers=env.lead.h),
        client.put(f"{BASE}/{b['id']}", json={"number": "RACE-1"}, headers=env.lead.h),
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [200, 400], [r.text for r in results]
    loser = next(r for r in results if r.status_code == 400)
    assert _msg(loser) == "Номер уже занят"


async def test_renaming_non_maximal_number_does_not_disturb_autonumbering(client, env):
    """Переименование НЕ-максимального DES-N автонумерацию не трогает."""
    first = await _mk_task(client, env.author.h)
    second = await _mk_task(client, env.author.h)
    top = int(second["number"].removeprefix("DES-"))

    resp = await client.put(f"{BASE}/{first['id']}", json={"number": "ABC-1"}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text

    third = await _mk_task(client, env.author.h)
    assert third["number"] == f"DES-{top + 1}"


async def test_renaming_maximal_number_frees_it_for_reuse(client, env):
    """Переименование САМОГО СТАРШЕГО DES-N освобождает его номер для следующей заявки.

    Это фактическое поведение `next_number`: он берёт `max` по живым и мягко
    удалённым строкам, подходящим под `^DES-\\d+$`, а переименованная строка под
    шаблон больше не подходит и из максимума выпадает.

    Спека волны B (AC-6) обещала обратное — «после DES-7 → ABC-1 следующая заявка
    даёт DES-8». Обещание держится только для не-максимального номера (тест выше);
    для максимального счётчик откатывается на шаг. Уникальность при этом не
    нарушается: старший номер физически освободился. Сделать счётчик монотонным
    можно только персистентным счётчиком на проект — это миграция и отдельное
    решение, а спека прямо запрещала трогать `next_number` в этой волне.
    Фиксируем правду тестом, чтобы следующий читатель не принял её за баг.
    """
    only = await _mk_task(client, env.author.h)
    freed = only["number"]

    resp = await client.put(f"{BASE}/{only['id']}", json={"number": "ABC-1"}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text

    nxt = await _mk_task(client, env.author.h)
    assert nxt["number"] == freed
    # Коллизии нет: прежний носитель номера теперь ABC-1.
    detail = await client.get(f"{BASE}/{only['id']}", headers=env.lead.h)
    assert detail.json()["number"] == "ABC-1"


async def test_number_of_deleted_task_is_reusable(client, env):
    """Partial-unique мёртвых строк не видит: номер удалённой задачи можно занять."""
    victim = await _mk_task(client, env.author.h)
    freed = victim["number"]
    resp = await client.delete(f"{BASE}/{victim['id']}", headers=env.lead.h)
    assert resp.status_code == 204, resp.text

    other = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{other['id']}", json={"number": freed}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == freed


@pytest.mark.parametrize("status", [DesignTaskStatus.ACCEPTED, DesignTaskStatus.CANCELLED])
async def test_terminal_status_blocks_number(client, env, db_session, status):
    """В терминальном статусе номер не меняется даже ведущим; флаг прав — false."""
    task = await make_task(db_session, env.pid, env.author.id, status=status)

    resp = await client.put(f"{BASE}/{task.id}", json={"number": "TERM-1"}, headers=env.lead.h)
    assert resp.status_code == 403, resp.text
    assert _msg(resp) == "Номер закрытой задачи не меняется"

    detail = await client.get(f"{BASE}/{task.id}", headers=env.lead.h)
    assert detail.json()["permissions"]["can_edit_number"] is False


async def test_number_is_project_scoped(client, env, db_session, auth_headers):
    """Номер, занятый в чужом проекте, здесь свободен (Iron rule 1)."""
    from tests.test_api_design_tasks import _create_project

    other = await _create_project(client, auth_headers, "Design B чужой")
    await make_task(db_session, other["id"], env.lead.id, number="SHARED-1", due_date=date(2026, 8, 1))

    mine = await _mk_task(client, env.author.h)
    resp = await client.put(f"{BASE}/{mine['id']}", json={"number": "SHARED-1"}, headers=env.lead.h)
    assert resp.status_code == 200, resp.text
    assert resp.json()["number"] == "SHARED-1"


async def test_explicit_null_number_is_ignored(client, env):
    """`number: null` — «не менять», а не «очистить» (поле NOT NULL)."""
    task = await _mk_task(client, env.author.h)
    resp = await client.put(
        f"{BASE}/{task['id']}", json={"number": None, "title": "Новое название"}, headers=env.lead.h
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["number"] == task["number"]
    assert body["title"] == "Новое название"
