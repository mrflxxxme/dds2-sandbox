# ruff: noqa: RUF001, RUF002, RUF003
"""API-тесты роутера /design-tasks (Ф2): изоляция, RBAC, гварды, контракт.

AC-1..AC-7 спека F2 + carry-over ревью Ф1 (полный набор permissions, заголовки
download-ручек, капы limit/offset, move с comment, submissions multipart,
АБ-мост Ф6 — гвард «только по принятой», комментарии editor-only).

MinIO в тестах не требуется: пути заливки/скачивания монекпатчатся
(fixture no_minio) — гоняется и в контейнере без объектного стора.
"""

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from backend.models.design import DesignTaskStatus
from backend.services.design import files as design_files
from tests.design_helpers import add_submission, make_task

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 24

TASK_PAYLOAD = {
    "title": "Инфографика ковёр 80x150",
    "description": "Главное фото + карусель, акцент на состав и размеры.",
    "sheet_url": "https://docs.google.com/spreadsheets/d/tz-design-f2",
}

BASE = "/api/v1/design-tasks"


# ─── Хелперы ─────────────────────────────────────────────────────────────────


async def _register_user(client, prefix: str):
    """Свежий пользователь → (headers, user_id)."""
    uid = uuid.uuid4().hex[:8]
    username = f"{prefix}_{uid}"
    await client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "designpass123",
            "email": f"{username}@test.com",
        },
    )
    resp = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "designpass123"}
    )
    data = resp.json()
    assert "access_token" in data, f"Login failed ({resp.status_code}): {data}"
    headers = {"Authorization": f"Bearer {data['access_token']}"}
    me = await client.get("/api/v1/auth/me", headers=headers)
    return headers, me.json()["id"]


async def _create_project(client, owner_headers, name: str):
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=owner_headers)
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    return resp.json()


async def _join(client, owner_headers, slug: str, member_headers, role: str, pages: list[str]):
    """Инвайт + accept (хелперы-доноры test_api_auth.py)."""
    import json as _json

    resp = await client.get(
        f"/api/v1/projects/{slug}/invite-link",
        params={"role": role, "pages": _json.dumps(pages)},
        headers=owner_headers,
    )
    assert resp.status_code == 200, f"invite-link failed: {resp.text}"
    token = resp.json()["invite_token"]
    resp = await client.post(f"/api/v1/projects/invite/accept/{token}", headers=member_headers)
    assert resp.status_code == 200, resp.text


def _h(headers: dict, project_id: int) -> dict:
    return {**headers, "X-Project-Id": str(project_id)}


def _msg(resp) -> str:
    """Текст ошибки из единого конверта {"error": {message}} (backend/exceptions.py);
    422-валидация FastAPI остаётся в формате {"detail": [...]}."""
    body = resp.json()
    if "error" in body:
        return body["error"]["message"]
    return str(body.get("detail"))


async def _mk_task(client, headers) -> dict:
    resp = await client.post(BASE, json=TASK_PAYLOAD, headers=headers)
    assert resp.status_code == 201, f"create task failed: {resp.text}"
    return resp.json()


@pytest_asyncio.fixture
async def env(client, auth_headers):
    """Проект + команда ролей PRD: owner(lead), author/designer(editor),
    viewer, editor без page-ключа design-tasks."""
    project = await _create_project(client, auth_headers, "Design F2")
    pid, slug = project["id"], project["slug"]

    author_h, author_id = await _register_user(client, "author")
    designer_h, designer_id = await _register_user(client, "designer")
    viewer_h, viewer_id = await _register_user(client, "viewer")
    nopage_h, nopage_id = await _register_user(client, "nopage")
    await _join(client, auth_headers, slug, author_h, "editor", ["design-tasks"])
    await _join(client, auth_headers, slug, designer_h, "editor", ["design-tasks"])
    await _join(client, auth_headers, slug, viewer_h, "viewer", ["design-tasks"])
    await _join(client, auth_headers, slug, nopage_h, "editor", ["reports"])

    me = await client.get("/api/v1/auth/me", headers=auth_headers)
    return SimpleNamespace(
        project=project,
        pid=pid,
        lead=SimpleNamespace(h=_h(auth_headers, pid), raw=auth_headers, id=me.json()["id"]),
        author=SimpleNamespace(h=_h(author_h, pid), raw=author_h, id=author_id),
        designer=SimpleNamespace(h=_h(designer_h, pid), raw=designer_h, id=designer_id),
        viewer=SimpleNamespace(h=_h(viewer_h, pid), raw=viewer_h, id=viewer_id),
        nopage=SimpleNamespace(h=_h(nopage_h, pid), raw=nopage_h, id=nopage_id),
    )


@pytest.fixture
def no_minio(monkeypatch):
    """MinIO-пути замоканы: заливка — no-op, скачивание отдаёт PNG-байты."""

    async def _fake_put(object_name, data, content_type):
        return None

    async def _fake_download(object_name):
        return PNG

    monkeypatch.setattr(design_files, "_put_object", _fake_put)
    monkeypatch.setattr(design_files, "download_file", _fake_download)


# ─── AC-1: изоляция проектов ─────────────────────────────────────────────────


async def test_isolation(client, env):
    """Задача проекта A недоступна с X-Project-Id проекта B; список/доска пусты."""
    project_b = await _create_project(client, env.lead.raw, "Design F2 B")
    hb = _h(env.lead.raw, project_b["id"])

    task = await _mk_task(client, env.author.h)

    resp = await client.get(f"{BASE}/{task['id']}", headers=hb)
    assert resp.status_code == 404, resp.text

    resp = await client.get(BASE, headers=hb)
    assert resp.status_code == 200
    assert resp.json() == []

    resp = await client.get(f"{BASE}/board", headers=hb)
    assert resp.status_code == 200
    body = resp.json()
    assert all(len(col) == 0 for col in body["columns"].values())
    assert all(cnt == 0 for cnt in body["counts"].values())


# ─── AC-2: /all-projects только по членству ──────────────────────────────────


async def test_all_projects_membership(client, env):
    """Пользователь видит в /all-projects только проекты членства; чужой проект
    не появляется даже при знании его id."""
    task_a = await _mk_task(client, env.author.h)

    stranger_h, _ = await _register_user(client, "stranger")
    project_b = await _create_project(client, stranger_h, "Foreign Design")
    hb = _h(stranger_h, project_b["id"])
    task_b = await _mk_task(client, hb)

    # Номера DES-N пер-проектные (совпадают между проектами) — сверяем по id.
    resp = await client.get(f"{BASE}/all-projects", headers=env.author.raw)
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert task_a["id"] in ids
    assert task_b["id"] not in ids
    assert all(t["project_name"] == "Design F2" for t in resp.json())

    # Знание чужого id не помогает: X-Project-Id чужого проекта игнорируется
    # сквозной ручкой (скоуп — членство), а обычный список отвечает 403.
    resp = await client.get(f"{BASE}/all-projects", headers=_h(env.author.raw, project_b["id"]))
    assert task_b["id"] not in [t["id"] for t in resp.json()]

    resp = await client.get(BASE, headers=_h(env.author.raw, project_b["id"]))
    assert resp.status_code == 403


# ─── AC-3: page-гейт RBAC (Р11) ──────────────────────────────────────────────


async def test_rbac_page_gate(client, env):
    """Editor без 'design-tasks' в pages → 403 на GET /design-tasks."""
    resp = await client.get(BASE, headers=env.nopage.h)
    assert resp.status_code == 403, resp.text
    assert "design-tasks" in _msg(resp)

    # Позитивный контроль: viewer с page-ключом читает.
    resp = await client.get(BASE, headers=env.viewer.h)
    assert resp.status_code == 200


# ─── AC-4: гварды и словарь переходов через HTTP ─────────────────────────────


async def test_guards_over_http(client, env):
    task = await _mk_task(client, env.author.h)
    tid = task["id"]

    # Запрещённое словарём ребро NEW→IN_PROGRESS → 400 с текстом Ф1.
    resp = await client.post(
        f"{BASE}/{tid}/status", json={"to_status": "IN_PROGRESS"}, headers=env.lead.h
    )
    assert resp.status_code == 400
    assert "Недопустимый переход" in _msg(resp)

    # NEW→ASSIGNED без исполнителя → гвард.
    resp = await client.post(
        f"{BASE}/{tid}/status", json={"to_status": "ASSIGNED"}, headers=env.lead.h
    )
    assert resp.status_code == 400
    assert _msg(resp) == "Назначьте исполнителя"

    # Назначение — только lead: editor-автор получает 403.
    resp = await client.post(
        f"{BASE}/{tid}/assign", json={"assignee_user_id": env.designer.id}, headers=env.author.h
    )
    assert resp.status_code == 403

    resp = await client.post(
        f"{BASE}/{tid}/assign", json={"assignee_user_id": env.designer.id}, headers=env.lead.h
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ASSIGNED"

    # Исполнитель берёт в работу.
    resp = await client.post(
        f"{BASE}/{tid}/status", json={"to_status": "IN_PROGRESS"}, headers=env.designer.h
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "IN_PROGRESS"

    # →REVIEW без сданной версии → гвард.
    resp = await client.post(
        f"{BASE}/{tid}/status", json={"to_status": "REVIEW"}, headers=env.designer.h
    )
    assert resp.status_code == 400
    assert _msg(resp) == "Сдайте версию с файлами"

    # Отмена без причины → гвард.
    resp = await client.post(
        f"{BASE}/{tid}/status", json={"to_status": "CANCELLED"}, headers=env.lead.h
    )
    assert resp.status_code == 400
    assert _msg(resp) == "Укажите причину отмены"


# ─── AC-5: reorder внутри колонки — только lead (Р9) ─────────────────────────


async def test_move_permissions(client, env):
    t1 = await _mk_task(client, env.author.h)
    t2 = await _mk_task(client, env.author.h)

    # Editor-автор: перестановка в своей колонке запрещена.
    resp = await client.post(
        f"{BASE}/{t2['id']}/move",
        json={"to_status": "NEW", "after_task_id": None},
        headers=env.author.h,
    )
    assert resp.status_code == 403, resp.text

    # Lead: t2 в начало колонки NEW → порядок на доске [t2, t1].
    resp = await client.post(
        f"{BASE}/{t2['id']}/move",
        json={"to_status": "NEW", "after_task_id": None},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text

    resp = await client.get(f"{BASE}/board", headers=env.lead.h)
    new_ids = [t["id"] for t in resp.json()["columns"]["NEW"]]
    assert new_ids[:2] == [t2["id"], t1["id"]]

    # Опорная задача чужая/несуществующая → 400 единым текстом.
    resp = await client.post(
        f"{BASE}/{t1['id']}/move",
        json={"to_status": "NEW", "after_task_id": 99999999},
        headers=env.lead.h,
    )
    assert resp.status_code == 400
    assert _msg(resp) == "Опорная задача не найдена"


# ─── AC-6: openapi полон, board не перехвачен /{task_id} ─────────────────────


async def test_openapi_contract(client):
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]

    expected = {
        f"{BASE}": {"get", "post"},
        f"{BASE}/board": {"get"},
        f"{BASE}/all-projects": {"get"},
        f"{BASE}/workload": {"get"},
        f"{BASE}/calendar": {"get"},
        f"{BASE}/stats": {"get"},
        f"{BASE}/product-suggest": {"get"},
        f"{BASE}/{{task_id}}": {"get", "put", "delete"},
        f"{BASE}/{{task_id}}/status": {"post"},
        f"{BASE}/{{task_id}}/move": {"post"},
        f"{BASE}/{{task_id}}/assign": {"post"},
        f"{BASE}/{{task_id}}/viewed": {"post"},
        f"{BASE}/{{task_id}}/materials": {"post"},
        f"{BASE}/{{task_id}}/materials/file": {"post"},
        f"{BASE}/{{task_id}}/materials/{{mat_id}}/file": {"get"},
        f"{BASE}/{{task_id}}/materials/{{mat_id}}": {"delete"},
        f"{BASE}/{{task_id}}/submissions": {"post"},
        f"{BASE}/{{task_id}}/submissions/{{sub_id}}/files/{{file_id}}": {"get"},
        f"{BASE}/{{task_id}}/submissions/{{sub_id}}/verdict": {"post"},
        f"{BASE}/{{task_id}}/comments": {"post"},
        f"{BASE}/{{task_id}}/ab-test": {"post"},
    }
    for path, methods in expected.items():
        assert path in paths, f"нет пути в openapi: {path}"
        assert methods <= set(paths[path]), f"{path}: {methods} ⊄ {set(paths[path])}"


async def test_board_not_captured_by_task_id(client, env):
    """Регресс на порядок роутов: /board отвечает доской, а не 422 от /{task_id}."""
    resp = await client.get(f"{BASE}/board", headers=env.viewer.h)
    assert resp.status_code == 200
    assert set(resp.json().keys()) == {"columns", "counts"}


# ─── AC-7: каждая мутация под rate_limit_write ───────────────────────────────


def test_all_mutations_have_rate_limit_write():
    src = (
        Path(__file__).resolve().parents[1] / "backend" / "routers" / "design_tasks.py"
    ).read_text(encoding="utf-8")
    chunks = re.split(r"@router\.", src)[1:]
    assert chunks, "роутер пуст?"
    checked = 0
    for chunk in chunks:
        method = chunk.split("(", 1)[0].strip()
        decorator = chunk.split("async def", 1)[0]
        if method in ("post", "put", "delete"):
            checked += 1
            assert "rate_limit_write" in decorator, f"мутация без rate_limit_write: {chunk[:100]}"
    assert checked >= 13  # 12 мутаций таблицы спека + запас на будущее


# ─── Carry-over 1: ПОЛНЫЙ набор флагов permissions (§6.9) ────────────────────


def test_permissions_schema_matches_service():
    """Схема DesignTaskPermissions зеркалит ключи compute_permissions один-в-один."""
    from backend.schemas.design import DesignTaskPermissions
    from backend.services.design.permissions import compute_permissions

    task = SimpleNamespace(status="NEW", author_user_id=1, assignee_user_id=None, nm_id=None)
    perms = compute_permissions(task, SimpleNamespace(id=1), "admin")
    assert set(perms) == set(DesignTaskPermissions.model_fields)


async def test_detail_returns_full_permissions(client, env):
    from backend.schemas.design import DesignTaskPermissions

    task = await _mk_task(client, env.author.h)
    resp = await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)
    assert resp.status_code == 200
    perms = resp.json()["permissions"]
    assert set(perms.keys()) == set(DesignTaskPermissions.model_fields)
    # lead на NEW: can_assign/can_reorder есть, can_take без исполнителя нет.
    assert perms["can_assign"] and perms["can_reorder"] and perms["can_set_complexity"]
    assert not perms["can_take"] and not perms["can_create_ab_test"]


# ─── Carry-over 2: заголовки download-ручек ──────────────────────────────────


async def test_download_headers(client, env, db_session, no_minio):
    """Content-Disposition attachment (RFC 5987) + X-Content-Type-Options: nosniff
    на ОБЕИХ download-ручках (материал и файл версии)."""
    from urllib.parse import quote

    from backend.models.design import DesignMaterial, DesignSubmissionFile

    task = await _mk_task(client, env.author.h)
    filename = "отчёт дизайна.png"

    mat = DesignMaterial(
        project_id=env.pid,
        task_id=task["id"],
        kind="FILE",
        minio_path=f"design/{env.pid}/{task['id']}/materials/x.png",
        original_filename=filename,
        mime_type="image/png",
        file_size=len(PNG),
        created_by_user_id=env.author.id,
    )
    db_session.add(mat)
    await db_session.commit()

    resp = await client.get(f"{BASE}/{task['id']}/materials/{mat.id}/file", headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == f"attachment; filename*=UTF-8''{quote(filename)}"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.content == PNG

    # Файл версии сдачи.
    task_row = await make_task(
        db_session, env.pid, env.author.id, assignee_id=env.designer.id, commit=False
    )
    sub = await add_submission(db_session, task_row, env.designer.id, with_file=True, commit=True)
    from sqlalchemy import select

    file_res = await db_session.execute(
        select(DesignSubmissionFile).where(DesignSubmissionFile.submission_id == sub.id)
    )
    file_row = file_res.scalar_one()

    resp = await client.get(
        f"{BASE}/{task_row.id}/submissions/{sub.id}/files/{file_row.id}", headers=env.viewer.h
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"].startswith("attachment; filename*=UTF-8''")
    assert resp.headers["x-content-type-options"] == "nosniff"

    # Чужой sub_id в URL → 404 (файл принадлежит именно своей версии).
    resp = await client.get(
        f"{BASE}/{task_row.id}/submissions/{sub.id + 1000}/files/{file_row.id}",
        headers=env.viewer.h,
    )
    assert resp.status_code == 404


# ─── Carry-over 3: капы limit/offset ─────────────────────────────────────────


async def test_limit_offset_validation(client, env):
    for params in ({"limit": 500}, {"limit": 0}, {"offset": -1}):
        resp = await client.get(BASE, params=params, headers=env.viewer.h)
        assert resp.status_code == 422, f"{params}: {resp.status_code}"
        resp = await client.get(f"{BASE}/all-projects", params=params, headers=env.viewer.raw)
        assert resp.status_code == 422, f"all-projects {params}: {resp.status_code}"

    resp = await client.get(BASE, params={"limit": 200, "offset": 0}, headers=env.viewer.h)
    assert resp.status_code == 200


# ─── Carry-over 4: DELETE через сервисный soft_delete (author|lead) ──────────


async def test_delete_task(client, env):
    task = await _mk_task(client, env.author.h)
    tid = task["id"]

    resp = await client.delete(f"{BASE}/{tid}", headers=env.viewer.h)
    assert resp.status_code == 403  # viewer < editor

    resp = await client.delete(f"{BASE}/{tid}", headers=env.designer.h)
    assert resp.status_code == 403  # editor не-автор

    resp = await client.delete(f"{BASE}/{tid}", headers=env.author.h)
    assert resp.status_code == 204

    resp = await client.get(f"{BASE}/{tid}", headers=env.author.h)
    assert resp.status_code == 404  # soft-deleted невидима

    # Повторное удаление — 404 (строка уже мёртвая).
    resp = await client.delete(f"{BASE}/{tid}", headers=env.author.h)
    assert resp.status_code == 404


# ─── Carry-over 5: move с comment (dnd в «Правки» требует причину) ───────────


async def test_move_to_revision_requires_comment(client, env, db_session):
    task = await make_task(
        db_session,
        env.pid,
        env.author.id,
        status=DesignTaskStatus.REVIEW,
        assignee_id=env.designer.id,
        commit=False,
    )
    await add_submission(db_session, task, env.designer.id, with_file=True, commit=True)

    resp = await client.post(
        f"{BASE}/{task.id}/move", json={"to_status": "REVISION"}, headers=env.lead.h
    )
    assert resp.status_code == 400
    assert _msg(resp) == "Напишите, что исправить"

    resp = await client.post(
        f"{BASE}/{task.id}/move",
        json={"to_status": "REVISION", "comment": "Поправить фон и шрифт"},
        headers=env.lead.h,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "REVISION"


# ─── Carry-over 6: submissions multipart, капы роутера ───────────────────────


async def test_submission_multipart_flow(client, env, db_session, no_minio):
    task = await make_task(
        db_session,
        env.pid,
        env.author.id,
        status=DesignTaskStatus.IN_PROGRESS,
        assignee_id=env.designer.id,
    )

    files = [
        ("files", ("v1_main.png", PNG, "image/png")),
        ("files", ("v1_extra.png", PNG, "image/png")),
    ]
    resp = await client.post(
        f"{BASE}/{task.id}/submissions",
        files=files,
        data={"comment": "Первая версия"},
        headers=env.designer.h,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "REVIEW"
    assert len(body["submissions"]) == 1
    assert body["submissions"][0]["version_no"] == 1
    assert len(body["submissions"][0]["files"]) == 2

    # 11 файлов → 400 на входе роутера.
    too_many = [("files", (f"f{i}.png", PNG, "image/png")) for i in range(11)]
    resp = await client.post(
        f"{BASE}/{task.id}/submissions", files=too_many, headers=env.designer.h
    )
    assert resp.status_code == 400
    assert "Не больше 10 файлов" in _msg(resp)

    # Недопустимый тип отсекается ДО чтения тела (порядок донора pr:710).
    resp = await client.post(
        f"{BASE}/{task.id}/submissions",
        files=[("files", ("virus.exe", b"MZ", "application/octet-stream"))],
        headers=env.designer.h,
    )
    assert resp.status_code == 400


# ─── Carry-over 7: АБ-мост (Ф6 реализован; полный контракт — test_design_ab_bridge) ─


async def test_ab_test_requires_accepted_task(client, env):
    task = await _mk_task(client, env.author.h)  # NEW — гвард моста отбивает 400
    resp = await client.post(f"{BASE}/{task['id']}/ab-test", headers=env.author.h)
    assert resp.status_code == 400
    assert _msg(resp) == "Тест создаётся только по принятой задаче"


# ─── Carry-over 8: комментарии — editor-only, cap 2000 ───────────────────────


async def test_comments_editor_only_and_cap(client, env):
    task = await _mk_task(client, env.author.h)
    tid = task["id"]

    resp = await client.post(
        f"{BASE}/{tid}/comments", json={"body": "viewer пишет"}, headers=env.viewer.h
    )
    assert resp.status_code == 403  # viewer read-only

    resp = await client.post(
        f"{BASE}/{tid}/comments", json={"body": "Уточните размер шрифта"}, headers=env.designer.h
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"] == "Уточните размер шрифта"
    assert resp.json()["author_user_id"] == env.designer.id

    resp = await client.post(
        f"{BASE}/{tid}/comments", json={"body": "x" * 2001}, headers=env.designer.h
    )
    assert resp.status_code == 422  # cap 2000 в схеме

    resp = await client.post(f"{BASE}/{tid}/comments", json={"body": ""}, headers=env.designer.h)
    assert resp.status_code == 422  # min 1

    resp = await client.get(f"{BASE}/{tid}", headers=env.viewer.h)
    assert len(resp.json()["comments"]) == 1


# ─── Дополнительно: viewed (Р5), календарь, материалы LINK/NM ────────────────


async def test_viewed_lead_only(client, env):
    task = await _mk_task(client, env.author.h)
    assert task["viewed_by_lead_at"] is None
    assert task["number"].startswith("DES-")

    resp = await client.post(f"{BASE}/{task['id']}/viewed", headers=env.author.h)
    assert resp.status_code == 403

    resp = await client.post(f"{BASE}/{task['id']}/viewed", headers=env.lead.h)
    assert resp.status_code == 200
    assert resp.json()["viewed_by_lead_at"] is not None


async def test_calendar_month(client, env, db_session):
    from datetime import date, timedelta

    task = await make_task(db_session, env.pid, env.author.id, due_date=date(2026, 8, 15))

    resp = await client.get(f"{BASE}/calendar", params={"month": "2026-08"}, headers=env.viewer.h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["month"] == "2026-08"
    assert date.fromisoformat(body["date_from"]) == date(2026, 8, 1) - timedelta(days=6)
    assert date.fromisoformat(body["date_to"]) == date(2026, 9, 1) + timedelta(days=5)
    assert task.id in [t["id"] for t in body["tasks"]]

    resp = await client.get(f"{BASE}/calendar", params={"month": "2026-13"}, headers=env.viewer.h)
    assert resp.status_code == 400

    resp = await client.get(f"{BASE}/calendar", params={"month": "август"}, headers=env.viewer.h)
    assert resp.status_code == 422  # pattern-валидация Query


async def test_materials_link_and_nm(client, env):
    task = await _mk_task(client, env.author.h)
    tid = task["id"]

    resp = await client.post(
        f"{BASE}/{tid}/materials",
        json={"kind": "LINK", "url": "https://example.com/ref", "caption": "референс"},
        headers=env.author.h,
    )
    assert resp.status_code == 201, resp.text
    mat_id = resp.json()["id"]
    assert resp.json()["kind"] == "LINK"

    resp = await client.post(
        f"{BASE}/{tid}/materials", json={"kind": "NM", "ref_nm_id": 123456}, headers=env.author.h
    )
    assert resp.status_code == 201
    assert resp.json()["ref_nm_id"] == 123456

    # kind=FILE этим payload'ом запрещён (422 схемой).
    resp = await client.post(
        f"{BASE}/{tid}/materials", json={"kind": "FILE"}, headers=env.author.h
    )
    assert resp.status_code == 422

    # Удаление: чужой editor → 403; автор → 204.
    resp = await client.delete(f"{BASE}/{tid}/materials/{mat_id}", headers=env.designer.h)
    assert resp.status_code == 403
    resp = await client.delete(f"{BASE}/{tid}/materials/{mat_id}", headers=env.author.h)
    assert resp.status_code == 204
    resp = await client.delete(f"{BASE}/{tid}/materials/{mat_id}", headers=env.author.h)
    assert resp.status_code == 404


# ─── Fix-цикл Ф2 п.1: /all-projects скоупится page-гейтом ────────────────────


async def test_all_projects_page_gate(client, env):
    """Участник проекта БЕЗ page-ключа design-tasks не видит его задач в сквозном
    списке (owner/admin проходят всегда — env.lead owner своего проекта)."""
    task = await _mk_task(client, env.author.h)

    # nopage — editor проекта с pages=["reports"]: членство есть, page-ключа нет.
    resp = await client.get(f"{BASE}/all-projects", headers=env.nopage.raw)
    assert resp.status_code == 200
    assert task["id"] not in [t["id"] for t in resp.json()]

    # Позитивные контроли: editor с ключом и owner видят задачу.
    resp = await client.get(f"{BASE}/all-projects", headers=env.author.raw)
    assert task["id"] in [t["id"] for t in resp.json()]
    resp = await client.get(f"{BASE}/all-projects", headers=env.lead.raw)
    assert task["id"] in [t["id"] for t in resp.json()]


# ─── Fix-цикл Ф2 п.2+п.3: 503 сквозной; пустая PENDING переиспользуется ───────


async def test_submission_minio_down_503_then_retry_same_version(
    client, env, db_session, monkeypatch
):
    """MinIO-down при заливке → 503 (не глотается в 400); повторная сдача после
    восстановления успешна ТЕМ ЖЕ version_no (пустая PENDING переиспользована)."""
    from fastapi import HTTPException

    task = await make_task(
        db_session,
        env.pid,
        env.author.id,
        status=DesignTaskStatus.IN_PROGRESS,
        assignee_id=env.designer.id,
    )

    async def _put_down(object_name, data, content_type):
        raise HTTPException(503, "Файловое хранилище (MinIO) недоступно")

    monkeypatch.setattr(design_files, "_put_object", _put_down)
    resp = await client.post(
        f"{BASE}/{task.id}/submissions",
        files=[("files", ("v1.png", PNG, "image/png"))],
        headers=env.designer.h,
    )
    assert resp.status_code == 503, resp.text

    # MinIO «ожил» — повтор успешен той же версией, дубля PENDING нет.
    async def _put_ok(object_name, data, content_type):
        return None

    monkeypatch.setattr(design_files, "_put_object", _put_ok)
    resp = await client.post(
        f"{BASE}/{task.id}/submissions",
        files=[("files", ("v1_retry.png", PNG, "image/png"))],
        data={"comment": "повтор"},
        headers=env.designer.h,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "REVIEW"
    assert len(body["submissions"]) == 1
    assert body["submissions"][0]["version_no"] == 1
    assert len(body["submissions"][0]["files"]) == 1


# ─── Fix-цикл Ф2 п.7/п.12: 413 на файл сверх лимита ──────────────────────────


async def test_file_too_large_413(client, env, monkeypatch):
    """Файл больше MAX_FILE_MB → 413 (единый источник: роутер и сервис)."""
    task = await _mk_task(client, env.author.h)
    monkeypatch.setattr(design_files, "MAX_FILE_MB", 0)  # любой непустой файл «слишком большой»

    resp = await client.post(
        f"{BASE}/{task['id']}/materials/file",
        files={"file": ("big.png", PNG, "image/png")},
        headers=env.author.h,
    )
    assert resp.status_code == 413, resp.text

    resp = await client.post(
        f"{BASE}/{task['id']}/submissions",
        files=[("files", ("big.png", PNG, "image/png"))],
        headers=env.author.h,
    )
    assert resp.status_code == 413, resp.text


# ─── Fix-цикл Ф2 п.8: невалидный enum в Query → 422 ──────────────────────────


async def test_list_enum_query_validation(client, env):
    resp = await client.get(BASE, params={"status": "BOGUS"}, headers=env.viewer.h)
    assert resp.status_code == 422
    resp = await client.get(BASE, params={"work_type": "BOGUS"}, headers=env.viewer.h)
    assert resp.status_code == 422
    resp = await client.get(f"{BASE}/all-projects", params={"status": "BOGUS"}, headers=env.viewer.raw)
    assert resp.status_code == 422

    resp = await client.get(
        BASE, params={"status": ["NEW", "REVIEW"], "work_type": "FULL_SET"}, headers=env.viewer.h
    )
    assert resp.status_code == 200


# ─── Fix-цикл Ф2 п.6: явный блок активных MIME (svg/html/xml) ────────────────


async def test_svg_mime_blocked(client, env):
    """SVG режется и расширением, и MIME: «нейтральное» имя с клиентским
    Content-Type image/svg+xml не проходит allowlist image/*."""
    task = await _mk_task(client, env.author.h)

    resp = await client.post(
        f"{BASE}/{task['id']}/materials/file",
        files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")},
        headers=env.author.h,
    )
    assert resp.status_code == 400

    resp = await client.post(
        f"{BASE}/{task['id']}/materials/file",
        files={"file": ("logo.img", b"<svg/>", "image/svg+xml")},
        headers=env.author.h,
    )
    assert resp.status_code == 400


# ─── Fix-цикл Ф3 (M3): allowed_transitions в деталке ─────────────────────────


async def test_allowed_transitions_in_detail(client, env, db_session):
    """allowed_transitions = DESIGN_TASK_TRANSITIONS[status] × матрица прав
    can_user_transition для ТЕКУЩЕГО пользователя (amendment M3, 2026-08-02).

    Автор в NEW видит только CANCELLED: ON_HOLD — только assignee/lead
    (*→ON_HOLD: assignee свою, lead — спек F1), ASSIGNED назначает lead.
    """
    from backend.models.design import DESIGN_TASK_TRANSITIONS

    task = await _mk_task(client, env.author.h)

    # Автор (editor, не assignee) в NEW: только отмена своей заявки.
    resp = await client.get(f"{BASE}/{task['id']}", headers=env.author.h)
    assert resp.status_code == 200
    assert resp.json()["allowed_transitions"] == ["CANCELLED"]

    # Lead в NEW: всё по словарю переходов.
    resp = await client.get(f"{BASE}/{task['id']}", headers=env.lead.h)
    assert set(resp.json()["allowed_transitions"]) == {
        s.value for s in DESIGN_TASK_TRANSITIONS[DesignTaskStatus.NEW]
    }

    # Viewer: переходов нет.
    resp = await client.get(f"{BASE}/{task['id']}", headers=env.viewer.h)
    assert resp.json()["allowed_transitions"] == []

    # Исполнитель в ASSIGNED: IN_PROGRESS + ON_HOLD (NEW — lead-only,
    # CANCELLED — автор|lead; исполнитель не автор).
    t2 = await make_task(
        db_session,
        env.pid,
        env.author.id,
        status=DesignTaskStatus.ASSIGNED,
        assignee_id=env.designer.id,
    )
    resp = await client.get(f"{BASE}/{t2.id}", headers=env.designer.h)
    assert set(resp.json()["allowed_transitions"]) == {"IN_PROGRESS", "ON_HOLD"}

    # Lead в ASSIGNED: всё по словарю (IN_PROGRESS, NEW, ON_HOLD, CANCELLED).
    resp = await client.get(f"{BASE}/{t2.id}", headers=env.lead.h)
    assert set(resp.json()["allowed_transitions"]) == {
        s.value for s in DESIGN_TASK_TRANSITIONS[DesignTaskStatus.ASSIGNED]
    }
