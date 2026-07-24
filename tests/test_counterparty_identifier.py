# ruff: noqa: RUF001
"""
Tests: CRUD идентификаторов контрагента (CounterpartyService.{add,list,delete}_identifier),
включая restore-путь (SoftDelete + partial-unique: повторное добавление того же
(project, kind, value) восстанавливает строку, а не падает IntegrityError).
"""

import uuid

import pytest

from backend.schemas.counterparty import CounterpartyCreate
from backend.services.counterparty_service import CounterpartyService


async def _create_project(client, auth_headers) -> int:
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"ci_test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_identifier_add_list_delete_restore(db_session, client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    svc = CounterpartyService(db_session)
    cp = await svc.create(CounterpartyCreate(name="TiAmo", primary_type="SUPPLIER"), project_id=project_id)

    i1 = await svc.add_identifier(cp.id, project_id=project_id, kind="CONTRACT", value="20250707", currency="CNY")
    items = await svc.list_identifiers(cp.id, project_id=project_id)
    assert [(i.kind, i.value) for i in items] == [("CONTRACT", "20250707")]

    assert await svc.delete_identifier(cp.id, i1.id, project_id=project_id) is True
    assert await svc.list_identifiers(cp.id, project_id=project_id) == []

    # Re-add the SAME (project, kind, value) → must restore i1, not a fresh INSERT.
    i2 = await svc.add_identifier(cp.id, project_id=project_id, kind="CONTRACT", value="20250707")
    assert i2.id == i1.id
    assert len(await svc.list_identifiers(cp.id, project_id=project_id)) == 1


@pytest.mark.asyncio
async def test_identifier_value_trimmed(db_session, client, auth_headers):
    project_id = await _create_project(client, auth_headers)
    svc = CounterpartyService(db_session)
    cp = await svc.create(CounterpartyCreate(name="Панели", primary_type="SUPPLIER"), project_id=project_id)
    ident = await svc.add_identifier(cp.id, project_id=project_id, kind="CONTRACT", value="  20250801  ")
    assert ident.value == "20250801"
