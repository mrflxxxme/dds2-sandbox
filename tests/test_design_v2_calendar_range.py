# ruff: noqa: RUF001, RUF002, RUF003
"""Волна A v2: GET /calendar принимает произвольный диапазон дат (CONTRACT-V2 §1).

Правка аддитивна — вызов ?month=YYYY-MM обязан работать как до волны, это
закрепляет test_calendar_month_regression. Тексты ошибок сверяются ДОСЛОВНО:
они часть контракта, синонимы недопустимы.
"""

from datetime import date, timedelta

import pytest

from backend.services.design import queries
from tests.design_helpers import make_task
from tests.test_api_design_tasks import BASE, _msg, env  # noqa: F401

PAD = 6


async def test_calendar_month_regression(client, env, db_session):
    """Старый вызов не изменился: те же границы окна и тот же состав задач."""
    task = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 8, 15))

    resp = await client.get(f"{BASE}/calendar", params={"month": "2026-08"}, headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["month"] == "2026-08"
    assert date.fromisoformat(body["date_from"]) == date(2026, 8, 1) - timedelta(days=PAD)
    assert date.fromisoformat(body["date_to"]) == date(2026, 9, 1) + timedelta(days=PAD - 1)
    assert task.id in [t["id"] for t in body["tasks"]]
    assert body["truncated"] is False


async def test_calendar_range_covers_two_months(client, env, db_session):
    """Диапазон в два месяца отдаёт задачи обоих (референс заказчика Р22)."""
    july = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 7, 10))
    august = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 8, 20))
    october = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 10, 1))

    resp = await client.get(
        f"{BASE}/calendar",
        params={"date_from": "2026-07-01", "date_to": "2026-08-31"},
        headers=env.viewer.h,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [t["id"] for t in body["tasks"]]
    assert july.id in ids
    assert august.id in ids
    assert october.id not in ids

    # Окно шире запроса на ±6 дней — фронт дорисовывает недели соседних месяцев.
    assert date.fromisoformat(body["date_from"]) == date(2026, 7, 1) - timedelta(days=PAD)
    assert date.fromisoformat(body["date_to"]) == date(2026, 8, 31) + timedelta(days=PAD)
    # month при диапазоне — месяц его начала.
    assert body["month"] == "2026-07"


async def test_calendar_padding_applies_to_selection_not_just_echo(client, env, db_session):
    """±6 дней расширяют ВЫБОРКУ, а не только границы в теле ответа.

    Без этого теста реализация, которая эхает расширенные границы, но строит WHERE
    по сырым датам, проходила бы весь файл — а фронт терял бы хвосты соседних недель.
    """
    inside_pad = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 9, 3))
    beyond_pad = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 9, 7))

    resp = await client.get(
        f"{BASE}/calendar",
        params={"date_from": "2026-08-01", "date_to": "2026-08-31"},
        headers=env.viewer.h,
    )
    assert resp.status_code == 200, resp.text
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert inside_pad.id in ids, "задача в зоне паддинга обязана попасть в выборку"
    assert beyond_pad.id not in ids, "за паддингом выборка обязана обрываться"


async def test_calendar_no_params_defaults_to_current_month(client, env, monkeypatch):
    """Без параметров — текущий месяц по МСК, а не 422 и не UTC-дата.

    Дату подменяем, иначе тест был бы тавтологией: он повторял бы ту же функцию,
    что и реализация, и откат на UTC ловил бы только ночью с 00:00 до 03:00 МСК.
    Момент выбран так, что по UTC ещё июль, а по МСК уже август.
    """
    monkeypatch.setattr(queries, "msk_today", lambda: date(2026, 8, 1))

    resp = await client.get(f"{BASE}/calendar", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["month"] == "2026-08"
    assert date.fromisoformat(body["date_from"]) == date(2026, 8, 1) - timedelta(days=PAD)


async def test_calendar_guards_texts(client, env):
    """Все четыре гварда CONTRACT-V2 §1 — дословно."""
    cases = [
        ({"month": "2026-08", "date_from": "2026-08-01"}, "Укажите либо month, либо диапазон дат"),
        ({"date_from": "2026-08-01"}, "Укажите обе границы диапазона"),
        ({"date_to": "2026-08-01"}, "Укажите обе границы диапазона"),
        ({"date_from": "2026-08-10", "date_to": "2026-08-01"}, "Начало диапазона позже конца"),
    ]
    for params, expected in cases:
        resp = await client.get(f"{BASE}/calendar", params=params, headers=env.viewer.h)
        assert resp.status_code == 400, f"{params}: {resp.text}"
        assert _msg(resp) == expected, params


async def test_calendar_range_length_boundary(client, env):
    """Ровно 400 дней — можно, 401 — нельзя (граница включительная)."""
    start = date(2026, 1, 1)
    ok = await client.get(
        f"{BASE}/calendar",
        params={"date_from": start.isoformat(), "date_to": (start + timedelta(days=399)).isoformat()},
        headers=env.viewer.h,
    )
    assert ok.status_code == 200, ok.text

    too_long = await client.get(
        f"{BASE}/calendar",
        params={"date_from": start.isoformat(), "date_to": (start + timedelta(days=400)).isoformat()},
        headers=env.viewer.h,
    )
    assert too_long.status_code == 400
    assert _msg(too_long) == "Диапазон не больше 400 дней"


@pytest.mark.parametrize(
    ("const_name", "params"),
    [
        ("_CALENDAR_LIMIT", {"month": "2026-08"}),
        ("_CALENDAR_RANGE_LIMIT", {"date_from": "2026-08-01", "date_to": "2026-08-31"}),
    ],
)
async def test_calendar_truncated_flag(client, env, db_session, monkeypatch, const_name, params):
    """Cap отдаётся явным флагом в ОБОИХ режимах: тихое усечение запрещено.

    Режимы параметризованы намеренно: у месяца свой cap (500, унаследован из v1),
    у диапазона свой (2000). Проверка одного режима не поймала бы регрессию,
    в которой месяц начал бы брать лимит диапазона.
    """
    monkeypatch.setattr(queries, const_name, 2)
    for i in range(3):
        await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 8, 10 + i))

    resp = await client.get(f"{BASE}/calendar", params=params, headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["tasks"]) == 2
    assert body["truncated"] is True


async def test_calendar_range_at_date_edges(client, env):
    """Край календаря не даёт 500: расширение окна на ±6 дней клампится.

    `date.max + 1 день` бросает OverflowError — НЕ ValueError, поэтому маппер
    ошибок роутера его бы не поймал и пользователь увидел бы 500 на запросе,
    который достижим ручным вводом в пикере.
    """
    resp = await client.get(
        f"{BASE}/calendar",
        params={"date_from": "9999-12-01", "date_to": "9999-12-31"},
        headers=env.viewer.h,
    )
    assert resp.status_code == 200, resp.text
    assert date.fromisoformat(resp.json()["date_to"]) == date.max

    resp = await client.get(
        f"{BASE}/calendar",
        params={"date_from": "0001-01-01", "date_to": "0001-01-31"},
        headers=env.viewer.h,
    )
    assert resp.status_code == 200, resp.text
    assert date.fromisoformat(resp.json()["date_from"]) == date.min


async def test_calendar_range_is_project_scoped(client, env, db_session, auth_headers):
    """Диапазон не протекает между проектами (Iron rule 1)."""
    from tests.test_api_design_tasks import _create_project, _h

    other = await _create_project(client, auth_headers, "Design F2 чужой")
    alien = await make_task(db_session, other["id"], env.lead.id, due_date=date(2026, 8, 15))
    mine = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 8, 15))

    params = {"date_from": "2026-08-01", "date_to": "2026-08-31"}
    resp = await client.get(f"{BASE}/calendar", params=params, headers=env.viewer.h)
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert mine.id in ids
    assert alien.id not in ids

    resp = await client.get(
        f"{BASE}/calendar", params=params, headers=_h(auth_headers, other["id"])
    )
    ids = [t["id"] for t in resp.json()["tasks"]]
    assert alien.id in ids
    assert mine.id not in ids
