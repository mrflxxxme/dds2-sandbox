"""API-тесты RBAC на домене АБ-тестов фото (/api/v1/ab-tests).

Домен меняет ЖИВОЕ главное фото карточки WB (start/apply-winner публикуют фото в
витрину), поэтому гейт обязан стоять на бэке, а не только в PageGuard фронта:
до фикса роутер был подключён лишь через `Depends(get_current_user)`, и любой
участник проекта — включая viewer и editor без ключа `ab-tests` — мог дёрнуть
API напрямую.

Проверяем матрицу: роль × наличие ключа `ab-tests` в pages × чтение/мутация.
"""

import uuid

import pytest

READ_URL = "/api/v1/ab-tests"
# Мутация на заведомо несуществующем тесте: гейт отдаёт 403, а сервис — 400
# («Тест не найден»). Поэтому 400 = «гейт пройден», без похода в WB.
WRITE_URL = "/api/v1/ab-tests/999999999/start"


async def _register_user(client):
    """Зарегистрировать свежего пользователя; вернуть заголовки авторизации."""
    username = f"abrbac_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "abrbacpass123", "email": f"{username}@test.com"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "abrbacpass123"},
    )
    data = resp.json()
    assert "access_token" in data, f"Login failed ({resp.status_code}): {data}"
    return {"Authorization": f"Bearer {data['access_token']}"}


async def _make_invite_token(client, owner_headers, slug, role, pages=None):
    """Владелец генерирует инвайт-ссылку; вернуть её токен (донор — test_api_auth.py)."""
    params = {"role": role}
    if pages is not None:
        import json as _json

        params["pages"] = _json.dumps(pages)
    resp = await client.get(
        f"/api/v1/projects/{slug}/invite-link",
        params=params,
        headers=owner_headers,
    )
    assert resp.status_code == 200, f"invite-link failed: {resp.text}"
    return resp.json()["invite_token"]


async def _member(client, owner_headers, project, role, pages=None):
    """Создать участника проекта с заданной ролью/страницами; вернуть его заголовки."""
    member_headers = await _register_user(client)
    token = await _make_invite_token(client, owner_headers, project["slug"], role=role, pages=pages)
    resp = await client.post(f"/api/v1/projects/invite/accept/{token}", headers=member_headers)
    assert resp.status_code == 200, f"accept invite failed: {resp.text}"
    return {**member_headers, "X-Project-Id": str(project["id"])}


async def _owner_with_project(client, name):
    owner_headers = await _register_user(client)
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=owner_headers)
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    project = resp.json()
    return owner_headers, project, {**owner_headers, "X-Project-Id": str(project["id"])}


@pytest.mark.asyncio
async def test_viewer_with_page_reads_but_cannot_mutate(client):
    """viewer с ключом ab-tests: чтение 200, мутация 403 (нужна роль editor)."""
    owner_headers, project, _ = await _owner_with_project(client, "AB RBAC viewer")
    headers = await _member(client, owner_headers, project, "viewer", pages=["ab-tests"])

    resp = await client.get(READ_URL, headers=headers)
    assert resp.status_code == 200, resp.text
    assert "tests" in resp.json()

    resp = await client.post(WRITE_URL, headers=headers)
    assert resp.status_code == 403, f"viewer не должен запускать АБ-тест: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_editor_without_page_blocked_on_read_and_write(client):
    """editor БЕЗ ключа ab-tests: 403 и на чтении, и на мутации.

    Ключевой сценарий фикса: раньше такой участник ходил в API напрямую мимо
    PageGuard и мог поменять живое фото карточки.
    """
    owner_headers, project, _ = await _owner_with_project(client, "AB RBAC editor no page")
    headers = await _member(client, owner_headers, project, "editor", pages=["reports"])

    resp = await client.get(READ_URL, headers=headers)
    assert resp.status_code == 403, f"чтение без ключа ab-tests: {resp.status_code} {resp.text}"

    resp = await client.post(WRITE_URL, headers=headers)
    assert resp.status_code == 403, f"мутация без ключа ab-tests: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_editor_with_page_passes_gate(client):
    """editor С ключом ab-tests: чтение 200, мутация проходит гейт (400 от сервиса)."""
    owner_headers, project, _ = await _owner_with_project(client, "AB RBAC editor with page")
    headers = await _member(client, owner_headers, project, "editor", pages=["ab-tests"])

    resp = await client.get(READ_URL, headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post(WRITE_URL, headers=headers)
    assert resp.status_code == 400, f"гейт не должен блокировать editor с ключом: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_owner_and_admin_pass_everywhere(client, role):
    """owner/admin получают все страницы каталога — гейт их не трогает."""
    owner_headers, project, owner_ctx = await _owner_with_project(client, f"AB RBAC {role}")
    headers = owner_ctx if role == "owner" else await _member(client, owner_headers, project, "admin")

    resp = await client.get(READ_URL, headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post(WRITE_URL, headers=headers)
    assert resp.status_code == 400, f"{role} не должен получать 403: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_every_ab_tests_route_is_gated(client):
    """Гард против регресса: ни одна ручка /ab-tests не должна остаться без page-гейта.

    Ловит новый эндпоинт, добавленный без `require_role(..., page="ab-tests")` —
    ровно тот класс дефекта, который чинит этот коммит.
    """
    from backend.main import app
    from backend.rbac import require_role

    # require_role возвращает замыкание `dependency` с qualname "require_role.<locals>.dependency"
    marker = require_role().__qualname__

    ungated = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/ab-tests"):
            continue
        deps = [getattr(d, "dependency", None) for d in getattr(route, "dependencies", [])]
        if not any(getattr(d, "__qualname__", "") == marker for d in deps):
            ungated.append(f"{sorted(route.methods)} {path}")
    assert ungated == [], f"ручки /ab-tests без RBAC page-гейта: {ungated}"
