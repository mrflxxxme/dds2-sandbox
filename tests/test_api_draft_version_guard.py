"""
PUT /assembly/drafts/{id} — обязательный CAS-токен для фоновых писателей.

Прод 2026-07-23 («5-й возврат» целых паллет в предброни): промоция консолидации
записывалась и в ту же секунду затиралась соседним фоновым PUT — nginx показывал
два PUT 200 в одну секунду и ни одного 409, состояние возвращалось байт-в-байт.

Контракт HTTP-границы: PUT с `distribution` обязан нести `base_updated_at`,
КРОМЕ явных действий юзера (event MATRIX_EDIT / PREBOOK_TOPUP / MATRIX_WRITE —
канон «последняя воля юзера побеждает»). Фоновые записи (eventless-автосейвы,
AUTO_SYNC, ACCEPTANCE_SYNC) без токена получают 409 вместо молчаливого затирания.
Detail содержит подстроку DRAFT_VERSION_CONFLICT, чтобы уже задеплоенные клиенты
(isVersionConflict → перечитка) деградировали в refetch, а не в error-тост.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _project_headers(client: AsyncClient, auth_headers: dict) -> dict:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Draft CAS Test"},
        headers=auth_headers,
    )
    project = resp.json()
    return {**auth_headers, "X-Project-Id": str(project["id"])}


async def _create_draft(client: AsyncClient, headers: dict) -> dict:
    resp = await client.post(
        "/api/v1/assembly/drafts",
        json={
            "name": "CAS guard test",
            "distribution": {
                "rows": [{"nm_id": 1, "barcode": "b1", "src": {"1": 5}, "tgt": {"Тула": 5}}],
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


_DIST = {"rows": [{"nm_id": 1, "barcode": "b1", "src": {"1": 3}, "tgt": {"Тула": 3}}]}


def _err_message(resp) -> str:
    """Единый формат ошибок приложения: {"error": {"message": ...}} (backend/exceptions.py)."""
    return resp.json()["error"]["message"]


async def test_background_eventless_put_requires_version(client, auth_headers):
    """Eventless PUT с distribution без base_updated_at → 409 (главный затиратель)."""
    headers = await _project_headers(client, auth_headers)
    draft = await _create_draft(client, headers)
    resp = await client.put(
        f"/api/v1/assembly/drafts/{draft['id']}",
        json={"distribution": _DIST},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    message = _err_message(resp)
    assert "DRAFT_VERSION_REQUIRED" in message
    # Совместимость со старым клиентом: он матчит подстроку DRAFT_VERSION_CONFLICT.
    assert "DRAFT_VERSION_CONFLICT" in message


async def test_sync_evented_put_requires_version(client, auth_headers):
    """Фоновые evented-писатели (AUTO_SYNC/ACCEPTANCE_SYNC) тоже обязаны нести токен."""
    headers = await _project_headers(client, auth_headers)
    draft = await _create_draft(client, headers)
    for event_type in ("AUTO_SYNC", "ACCEPTANCE_SYNC"):
        resp = await client.put(
            f"/api/v1/assembly/drafts/{draft['id']}",
            json={"distribution": _DIST, "event": {"event_type": event_type, "summary": "t"}},
            headers=headers,
        )
        assert resp.status_code == 409, f"{event_type}: {resp.text}"
        assert "DRAFT_VERSION_REQUIRED" in _err_message(resp)


async def test_user_evented_put_allowed_without_version(client, auth_headers):
    """Явное действие юзера (MATRIX_EDIT/PREBOOK_TOPUP/MATRIX_WRITE) — «последняя воля»,
    токен не обязателен (канон схемы сохраняется)."""
    headers = await _project_headers(client, auth_headers)
    draft = await _create_draft(client, headers)
    for event_type in ("MATRIX_EDIT", "PREBOOK_TOPUP", "MATRIX_WRITE"):
        resp = await client.put(
            f"/api/v1/assembly/drafts/{draft['id']}",
            json={"distribution": _DIST, "event": {"event_type": event_type, "summary": "t"}},
            headers=headers,
        )
        assert resp.status_code == 200, f"{event_type}: {resp.text}"


async def test_eventless_put_with_fresh_version_ok(client, auth_headers):
    """Фоновый PUT со свежим base_updated_at проходит."""
    headers = await _project_headers(client, auth_headers)
    draft = await _create_draft(client, headers)
    resp = await client.put(
        f"/api/v1/assembly/drafts/{draft['id']}",
        json={"distribution": _DIST, "base_updated_at": draft["updated_at"]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def test_stale_version_put_conflicts(client, auth_headers):
    """Протухший токен → 409 DRAFT_VERSION_CONFLICT (регресс-гард HTTP-уровня)."""
    headers = await _project_headers(client, auth_headers)
    draft = await _create_draft(client, headers)
    stale = draft["updated_at"]
    fresh = await client.put(
        f"/api/v1/assembly/drafts/{draft['id']}",
        json={"distribution": _DIST, "base_updated_at": stale},
        headers=headers,
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["updated_at"] != stale, "updated_at обязан сдвинуться — иначе CAS слеп"
    resp = await client.put(
        f"/api/v1/assembly/drafts/{draft['id']}",
        json={"distribution": _DIST, "base_updated_at": stale},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "DRAFT_VERSION_CONFLICT" in _err_message(resp)


async def test_name_only_put_without_version_ok(client, auth_headers):
    """PUT без distribution (переименование) токена не требует."""
    headers = await _project_headers(client, auth_headers)
    draft = await _create_draft(client, headers)
    resp = await client.put(
        f"/api/v1/assembly/drafts/{draft['id']}",
        json={"name": "renamed"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "renamed"
