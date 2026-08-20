# ruff: noqa: RUF001, RUF002, RUF003
"""Волна C v2: справочники меток и реквизитов (CONTRACT-V2 §3).

Ведёт справочники только ведущий дизайнер; «Удалить» — это архивирование (Р30).
Тексты гвардов сверяются ДОСЛОВНО: они часть контракта.
"""

import importlib.util
import os
from pathlib import Path

import pytest

from tests.test_api_design_tasks import BASE, _create_project, _h, _mk_task, _msg, env  # noqa: F401

LABEL = {"name": "Срочно", "color": "red", "sort_order": 0}


# ─── Порядок роутов ──────────────────────────────────────────────────────────


async def test_refs_not_captured_by_task_id(client, env):
    """`refs` не должен уехать в /{task_id}: FastAPI матчит по порядку объявления.

    Без этого теста ошибка порядка проявилась бы 422 «task_id должен быть int»
    только в рантайме — существующий тест покрывает лишь путь /board.
    """
    resp = await client.get(f"{BASE}/refs/labels", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    resp = await client.get(f"{BASE}/refs/attributes", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text


async def test_bulk_not_captured_by_task_id(client, env):
    resp = await client.post(
        f"{BASE}/bulk/labels",
        json={"task_ids": [], "label_ids": [], "mode": "add"},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text


# ─── Метки ───────────────────────────────────────────────────────────────────


async def test_label_crud_by_lead(client, env):
    resp = await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)
    assert resp.status_code == 201, resp.text
    label = resp.json()
    assert label["name"] == "Срочно"
    assert label["color"] == "red"
    assert label["usage_count"] == 0

    resp = await client.put(
        f"{BASE}/refs/labels/{label['id']}",
        json={"name": "Очень срочно", "color": "amber", "sort_order": 5},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Очень срочно"

    resp = await client.get(f"{BASE}/refs/labels", headers=env.viewer.h)
    assert [lb["name"] for lb in resp.json()] == ["Очень срочно"]


async def test_label_write_is_lead_only(client, env):
    """editor не-lead читает справочник, но не пишет в него."""
    resp = await client.get(f"{BASE}/refs/labels", headers=env.author.h)
    assert resp.status_code == 200, resp.text

    resp = await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.author.h)
    assert resp.status_code == 403, resp.text
    assert _msg(resp) == "Справочник ведёт ведущий дизайнер"


async def test_label_duplicate_name(client, env):
    await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)
    resp = await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Такое название уже есть"


async def test_archived_label_frees_its_name(client, env):
    """Архивирование не ставит is_deleted — имя обязано освободиться.

    Индекс, условный только по is_deleted, держал бы имя занятым навсегда и
    отдавал 500 IntegrityError вместо контрактного 400.
    """
    created = (await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)).json()
    resp = await client.delete(f"{BASE}/refs/labels/{created['id']}", headers=env.lead.h)
    assert resp.status_code == 204, resp.text

    resp = await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] != created["id"]


async def test_repeated_delete_is_idempotent(client, env):
    created = (await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)).json()
    for _ in range(2):
        resp = await client.delete(f"{BASE}/refs/labels/{created['id']}", headers=env.lead.h)
        assert resp.status_code == 204, resp.text


async def test_include_archived(client, env):
    created = (await client.post(f"{BASE}/refs/labels", json=LABEL, headers=env.lead.h)).json()
    await client.delete(f"{BASE}/refs/labels/{created['id']}", headers=env.lead.h)

    resp = await client.get(f"{BASE}/refs/labels", headers=env.viewer.h)
    assert resp.json() == []

    resp = await client.get(
        f"{BASE}/refs/labels", params={"include_archived": "true"}, headers=env.viewer.h
    )
    assert [lb["is_archived"] for lb in resp.json()] == [True]


async def test_label_color_must_be_from_palette(client, env):
    resp = await client.post(
        f"{BASE}/refs/labels", json={"name": "Свой", "color": "#ff00aa"}, headers=env.lead.h
    )
    assert resp.status_code == 422, resp.text  # валидация схемы, произвольный hex запрещён (Р26)


async def test_label_not_found_is_404(client, env):
    resp = await client.put(
        f"{BASE}/refs/labels/99999999", json=LABEL, headers=env.lead.h
    )
    assert resp.status_code == 404, resp.text
    assert _msg(resp) == "Метка не найдена"


async def test_labels_are_project_scoped(client, env, auth_headers):
    """Метка чужого проекта не видна и не редактируется (Iron rule 1)."""
    other = await _create_project(client, auth_headers, "Design C чужой")
    alien = (
        await client.post(f"{BASE}/refs/labels", json=LABEL, headers=_h(auth_headers, other["id"]))
    ).json()

    resp = await client.get(f"{BASE}/refs/labels", headers=env.viewer.h)
    assert resp.json() == []

    resp = await client.put(f"{BASE}/refs/labels/{alien['id']}", json=LABEL, headers=env.lead.h)
    assert resp.status_code == 404, resp.text


# ─── Реквизиты ───────────────────────────────────────────────────────────────


async def test_attribute_with_values(client, env):
    attr = (
        await client.post(
            f"{BASE}/refs/attributes", json={"name": "Бренд", "is_multi": False}, headers=env.lead.h
        )
    ).json()
    assert attr["is_multi"] is False
    assert attr["values"] == []

    for value in ("Меллори", "Уютопия"):
        resp = await client.post(
            f"{BASE}/refs/attributes/{attr['id']}/values",
            json={"value": value},
            headers=env.lead.h,
        )
        assert resp.status_code == 201, resp.text

    resp = await client.get(f"{BASE}/refs/attributes", headers=env.viewer.h)
    body = resp.json()
    assert [a["name"] for a in body] == ["Бренд"]
    assert [v["value"] for v in body[0]["values"]] == ["Меллори", "Уютопия"]


async def test_duplicate_value(client, env):
    attr = (
        await client.post(f"{BASE}/refs/attributes", json={"name": "Бренд"}, headers=env.lead.h)
    ).json()
    await client.post(
        f"{BASE}/refs/attributes/{attr['id']}/values", json={"value": "Меллори"}, headers=env.lead.h
    )
    resp = await client.post(
        f"{BASE}/refs/attributes/{attr['id']}/values", json={"value": "Меллори"}, headers=env.lead.h
    )
    assert resp.status_code == 400, resp.text
    assert _msg(resp) == "Такое значение уже есть"


async def test_archiving_attribute_cascades_to_values(client, env):
    """Архивирование поля архивирует все его значения (Р30)."""
    attr = (
        await client.post(f"{BASE}/refs/attributes", json={"name": "Бренд"}, headers=env.lead.h)
    ).json()
    await client.post(
        f"{BASE}/refs/attributes/{attr['id']}/values", json={"value": "Меллори"}, headers=env.lead.h
    )

    resp = await client.delete(f"{BASE}/refs/attributes/{attr['id']}", headers=env.lead.h)
    assert resp.status_code == 204, resp.text

    resp = await client.get(
        f"{BASE}/refs/attributes", params={"include_archived": "true"}, headers=env.viewer.h
    )
    body = resp.json()
    assert body[0]["is_archived"] is True
    assert [v["is_archived"] for v in body[0]["values"]] == [True]

    # Без флага не видно ни поля, ни его значений.
    resp = await client.get(f"{BASE}/refs/attributes", headers=env.viewer.h)
    assert resp.json() == []


async def test_attribute_and_value_not_found_texts(client, env):
    resp = await client.put(
        f"{BASE}/refs/attributes/99999999", json={"name": "Нет"}, headers=env.lead.h
    )
    assert resp.status_code == 404
    assert _msg(resp) == "Поле не найдено"

    resp = await client.put(f"{BASE}/refs/values/99999999", json={"value": "Нет"}, headers=env.lead.h)
    assert resp.status_code == 404
    assert _msg(resp) == "Значение не найдено"


async def test_usage_count_shows_where_value_is_used(client, env):
    """usage_count поля — число ЗАДАЧ, а не сумма по значениям (Р30, предупреждение при архивировании)."""
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

    for _ in range(3):
        task = await _mk_task(client, env.author.h)
        resp = await client.put(
            f"{BASE}/{task['id']}/attributes", json={"value_ids": [value["id"]]}, headers=env.lead.h
        )
        assert resp.status_code == 200, resp.text

    body = (await client.get(f"{BASE}/refs/attributes", headers=env.viewer.h)).json()
    assert body[0]["usage_count"] == 3
    assert body[0]["values"][0]["usage_count"] == 3


# ─── Сид миграции (Р33) ──────────────────────────────────────────────────────


def _load_migration():
    """Миграция — не пакет, поэтому подгружаем по пути."""
    path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "dsn05_design_labels_attributes.py"
    spec = importlib.util.spec_from_file_location("dsn05_seed", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not os.environ.get("DATABASE_URL_SYNC"), reason="нужен sync-URL для сида")
async def test_seed_creates_two_fields_and_six_brands(client, env):
    """Сид заводит «Кабинет ВБ» и «Бренд» с шестью брендами и идемпотентен.

    Прогоняется отдельно от миграции: на момент `alembic upgrade` в БД может не
    быть ни одной задачи дизайна, и тогда сид отработает вхолостую — ошибка
    в нём осталась бы незамеченной.
    """
    import psycopg2

    await _mk_task(client, env.author.h)  # проект попадает в выборку сида
    module = _load_migration()

    conn = psycopg2.connect(os.environ["DATABASE_URL_SYNC"])
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            wrapper = _SyncConn(cur)
            module.seed_refs(wrapper)
            module.seed_refs(wrapper)  # второй прогон ничего не задваивает

            cur.execute(
                "SELECT name FROM design_attributes WHERE project_id = %s "
                "AND is_deleted = false AND is_archived = false ORDER BY sort_order",
                (env.pid,),
            )
            assert [r[0] for r in cur.fetchall()] == ["Кабинет ВБ", "Бренд"]

            cur.execute(
                "SELECT v.value FROM design_attribute_values v "
                "JOIN design_attributes a ON a.id = v.attribute_id "
                "WHERE v.project_id = %s AND a.name = 'Бренд' ORDER BY v.sort_order",
                (env.pid,),
            )
            assert [r[0] for r in cur.fetchall()] == [
                "АРТСПЕЙС", "Меллори", "НУ-НУ", "СамПоклей", "Уютопия", "Redmi",
            ]
    finally:
        conn.close()


class _SyncConn:
    """Мини-адаптер: сид зовёт conn.execute(text(sql), params) — как SQLAlchemy."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, statement, params=None):
        sql = str(statement)
        for key in sorted((params or {}), key=len, reverse=True):
            sql = sql.replace(f":{key}", f"%({key})s")
        self._cursor.execute(sql, params or {})
        return self

    def __iter__(self):
        return iter(self._cursor.fetchall())

    def scalar(self):
        row = self._cursor.fetchone() if self._cursor.description else None
        return row[0] if row else None
