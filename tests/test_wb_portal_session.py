"""
Тесты статуса кабинетной сессии WB (get_wb_portal_status).

Контракт: NONE / ACTIVE / EXPIRED. Источник истины — `is_active`; `config.status` —
лишь информационный снимок последнего перехода, он может разойтись с флагом
(маскировка sync-prod гасит is_active, не трогая config) и не должен его перебивать.
"""

import pytest

from backend.models.integrations import IntegrationKey
from backend.services.integrations_service import (
    WB_PORTAL_LABEL,
    WB_PORTAL_SERVICE,
    get_wb_portal_status,
)

pytestmark = pytest.mark.asyncio


async def _seed_key(db, project_id, *, is_active, config, is_deleted=False):
    key = IntegrationKey(
        project_id=project_id,
        service=WB_PORTAL_SERVICE,
        label=WB_PORTAL_LABEL,
        encrypted_key="irrelevant-for-status",
        is_active=is_active,
        config=config,
        is_deleted=is_deleted,
    )
    db.add(key)
    await db.commit()
    return key


async def test_status_none_without_key(db_session, project):
    assert await get_wb_portal_status(db_session, project.id) == {"status": "NONE"}


async def test_status_active_when_key_active(db_session, project):
    await _seed_key(
        db_session, project.id,
        is_active=True,
        config={"status": "ACTIVE", "updated_at": "2026-07-09T07:30:15"},
    )
    state = await get_wb_portal_status(db_session, project.id)
    assert state["status"] == "ACTIVE"
    assert state["updated_at"] == "2026-07-09T07:30:15"


async def test_status_expired_when_key_inactive(db_session, project):
    await _seed_key(
        db_session, project.id,
        is_active=False,
        config={"status": "EXPIRED", "expired_at": "2026-07-15T10:00:00"},
    )
    state = await get_wb_portal_status(db_session, project.id)
    assert state["status"] == "EXPIRED"


async def test_status_follows_is_active_not_stale_config(db_session, project):
    """is_active=False + config.status='ACTIVE' (след маскировки sync-prod) → EXPIRED.

    Ключ выключен, клиент кабинета по нему не соберётся — UI обязан показать EXPIRED,
    а не зелёный статус из протухшего config.
    """
    await _seed_key(
        db_session, project.id,
        is_active=False,
        config={"status": "ACTIVE", "updated_at": "2026-07-09T07:30:15"},
    )
    state = await get_wb_portal_status(db_session, project.id)
    assert state["status"] == "EXPIRED"


async def test_status_none_for_soft_deleted_key(db_session, project):
    await _seed_key(
        db_session, project.id,
        is_active=True,
        config={"status": "ACTIVE"},
        is_deleted=True,
    )
    assert await get_wb_portal_status(db_session, project.id) == {"status": "NONE"}


async def test_status_is_project_scoped(db_session, project, other_project):
    await _seed_key(
        db_session, project.id, is_active=True, config={"status": "ACTIVE"},
    )
    assert (await get_wb_portal_status(db_session, other_project.id))["status"] == "NONE"
