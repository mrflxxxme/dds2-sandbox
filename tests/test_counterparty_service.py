"""
Tests for CounterpartyService.
Uses real PG database (conftest_api session fixtures).
Tests: upsert_by_inn, upsert_by_contract, list, stats, create, update, soft_delete.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from backend.models.counterparty import Counterparty
from backend.schemas.counterparty import (
    CounterpartyCreate,
    CounterpartyFilter,
    CounterpartyUpdate,
)
from backend.services.counterparty_service import (
    CounterpartyConflictError,
    CounterpartyService,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _inn() -> str:
    """Generate unique fake INN (10 digits)."""
    return str(int(uuid.uuid4().hex[:9], 16) % 9000000000 + 1000000000)


async def _create_project(client, auth_headers) -> int:
    """Create test project and return project_id."""
    resp = await client.post(
        "/api/v1/projects",
        json={"name": f"test_{uuid.uuid4().hex[:6]}"},
        headers=auth_headers,
    )
    return resp.json()["id"]


# ─── upsert_by_inn ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_by_inn_creates_new(db_session, client, auth_headers):
    """upsert_by_inn creates new Counterparty with type=OTHER."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp = await service.upsert_by_inn(inn=inn, name="ИП Тест Новый", project_id=project_id)

    assert cp.id is not None
    assert cp.inn == inn
    assert cp.primary_type == "OTHER"
    assert cp.created_by_import is True
    assert cp.project_id == project_id


@pytest.mark.asyncio
async def test_upsert_by_inn_updates_longer_name(db_session, client, auth_headers):
    """upsert_by_inn updates name when new name is longer."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    # Create with short name
    cp1 = await service.upsert_by_inn(inn=inn, name="ИП Тест", project_id=project_id)
    # Upsert with longer name
    cp2 = await service.upsert_by_inn(inn=inn, name="ИП Тест Полное Название", project_id=project_id)

    assert cp1.id == cp2.id
    assert cp2.name == "ИП Тест Полное Название"


@pytest.mark.asyncio
async def test_upsert_by_inn_keeps_shorter_name(db_session, client, auth_headers):
    """upsert_by_inn does NOT update name when new name is shorter."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    # Create with long name
    await service.upsert_by_inn(inn=inn, name="ИП Тест Полное Длинное Название", project_id=project_id)
    # Upsert with shorter name — should NOT change
    cp = await service.upsert_by_inn(inn=inn, name="ИП Тест", project_id=project_id)

    assert cp.name == "ИП Тест Полное Длинное Название"


@pytest.mark.asyncio
async def test_upsert_by_inn_does_not_overwrite_primary_type(db_session, client, auth_headers):
    """upsert_by_inn does NOT overwrite primary_type if manually set."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    # Create with specific type
    cp = await service.upsert_by_inn(
        inn=inn, name="ООО Фулфилмент", project_id=project_id, defaults={"primary_type": "FULFILLMENT"}
    )
    assert cp.primary_type == "FULFILLMENT"

    # Upsert — should NOT overwrite type
    cp2 = await service.upsert_by_inn(
        inn=inn, name="ООО Фулфилмент Новое Название", project_id=project_id, defaults={"primary_type": "CARRIER"}
    )
    assert cp2.primary_type == "FULFILLMENT"  # unchanged


@pytest.mark.asyncio
async def test_upsert_by_inn_multitenancy(db_session, client, auth_headers):
    """Same INN in two projects creates separate Counterparty rows."""
    project1_id = await _create_project(client, auth_headers)
    project2_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp1 = await service.upsert_by_inn(inn=inn, name="ИП Тест", project_id=project1_id)
    cp2 = await service.upsert_by_inn(inn=inn, name="ИП Тест", project_id=project2_id)

    assert cp1.id != cp2.id
    assert cp1.project_id == project1_id
    assert cp2.project_id == project2_id


# ─── upsert_by_contract ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_by_contract_creates_chinese_supplier(db_session, client, auth_headers):
    """upsert_by_contract creates Counterparty without INN."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)

    cp = await service.upsert_by_contract(
        contract_number="20250707",
        project_id=project_id,
        defaults={"name": "Китайский поставщик текстиль", "primary_type": "SUPPLIER"},
    )

    assert cp.id is not None
    assert cp.contract_number == "20250707"
    assert cp.inn is None
    assert cp.primary_type == "SUPPLIER"


# ─── list ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_with_type_filter(db_session, client, auth_headers):
    """list() with type=FULFILLMENT returns only fulfillment counterparties."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)

    # Create different types
    inn_ff = _inn()
    inn_sup = _inn()
    await service.upsert_by_inn(
        inn=inn_ff, name="ФФ Компания", project_id=project_id, defaults={"primary_type": "FULFILLMENT"}
    )
    await service.upsert_by_inn(
        inn=inn_sup, name="Поставщик", project_id=project_id, defaults={"primary_type": "SUPPLIER"}
    )

    filters = CounterpartyFilter(type="FULFILLMENT", limit=100, offset=0)
    items, total = await service.list(project_id=project_id, filters=filters)

    types_in_result = {cp.primary_type for cp in items}
    assert "FULFILLMENT" in types_in_result
    assert "SUPPLIER" not in types_in_result


@pytest.mark.asyncio
async def test_list_search_ilike(db_session, client, auth_headers):
    """list() with q= parameter searches by ILIKE."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    await service.upsert_by_inn(inn=inn, name="Кузнецов Транспорт", project_id=project_id)

    filters = CounterpartyFilter(q="кузнец", limit=100, offset=0)
    items, total = await service.list(project_id=project_id, filters=filters)

    names = [cp.name for cp in items]
    assert any("Кузнецов" in n for n in names)


@pytest.mark.asyncio
async def test_list_search_ilike_percent_escaped(db_session, client, auth_headers):
    """list() with % in search query does not cause SQL errors."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)

    # This should not raise an error (percent escaped properly)
    filters = CounterpartyFilter(q="%injection_attempt", limit=100, offset=0)
    items, total = await service.list(project_id=project_id, filters=filters)
    # No crash — safe result (zero or more items)
    assert isinstance(items, list)
    assert isinstance(total, int)


@pytest.mark.asyncio
async def test_list_active_only(db_session, client, auth_headers):
    """list(active_only=True) excludes soft-deleted counterparties."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp = await service.upsert_by_inn(inn=inn, name="Удалённый КА", project_id=project_id)
    await service.soft_delete(cp.id, project_id=project_id)

    filters = CounterpartyFilter(active_only=True, limit=100, offset=0)
    items, total = await service.list(project_id=project_id, filters=filters)

    assert all(not getattr(item, "is_deleted", False) for item in items)
    ids = [item.id for item in items]
    assert cp.id not in ids


# ─── stats ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_no_transactions(db_session, client, auth_headers):
    """stats() returns zero values when no transactions exist."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp = await service.upsert_by_inn(inn=inn, name="КА без транзакций", project_id=project_id)

    stats = await service.stats(
        counterparty_id=cp.id,
        project_id=project_id,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
        currency="RUB",
    )

    assert stats.in_sum == Decimal("0")
    assert stats.out_sum == Decimal("0")
    assert stats.tx_count == 0


# ─── create / get ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_counterparty(db_session, client, auth_headers):
    """create() creates a new Counterparty and returns full detail."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    data = CounterpartyCreate(
        inn=inn,
        name="ООО Тест Создание",
        primary_type="CARRIER",
    )
    cp = await service.create(data=data, project_id=project_id)

    assert cp.id is not None
    assert cp.inn == inn
    assert cp.primary_type == "CARRIER"


@pytest.mark.asyncio
async def test_create_duplicate_inn_raises_conflict(db_session, client, auth_headers):
    """create() raises CounterpartyConflictError for duplicate INN."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    data = CounterpartyCreate(inn=inn, name="КА Первый", primary_type="OTHER")
    await service.create(data=data, project_id=project_id)

    with pytest.raises(CounterpartyConflictError):
        data2 = CounterpartyCreate(inn=inn, name="КА Второй", primary_type="OTHER")
        await service.create(data=data2, project_id=project_id)


@pytest.mark.asyncio
async def test_create_same_inn_different_project(db_session, client, auth_headers):
    """create() allows same INN in different projects (multi-tenancy)."""
    project1 = await _create_project(client, auth_headers)
    project2 = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    data = CounterpartyCreate(inn=inn, name="КА Тест", primary_type="OTHER")
    cp1 = await service.create(data=data, project_id=project1)

    data2 = CounterpartyCreate(inn=inn, name="КА Тест", primary_type="OTHER")
    cp2 = await service.create(data=data2, project_id=project2)

    assert cp1.id != cp2.id


@pytest.mark.asyncio
async def test_get_wrong_project_raises_not_found(db_session, client, auth_headers):
    """get() raises 404 if project_id doesn't match."""
    project1 = await _create_project(client, auth_headers)
    project2 = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp = await service.create(
        CounterpartyCreate(inn=inn, name="КА", primary_type="OTHER"),
        project_id=project1,
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await service.get(
            counterparty_id=cp.id,
            project_id=project2,
            date_from=date(2026, 1, 1),
            date_to=date(2026, 4, 30),
        )
    assert exc_info.value.status_code == 404


# ─── update ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_counterparty(db_session, client, auth_headers):
    """update() applies PATCH changes."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp = await service.create(
        CounterpartyCreate(inn=inn, name="Старое Название", primary_type="OTHER"),
        project_id=project_id,
    )

    updated = await service.update(
        counterparty_id=cp.id,
        data=CounterpartyUpdate(name="Новое Название"),
        project_id=project_id,
    )

    assert updated.name == "Новое Название"
    assert updated.inn == inn  # unchanged


# ─── soft_delete ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_soft_delete(db_session, client, auth_headers):
    """soft_delete() marks row as deleted without removing from DB."""
    project_id = await _create_project(client, auth_headers)
    service = CounterpartyService(db_session)
    inn = _inn()

    cp = await service.create(
        CounterpartyCreate(inn=inn, name="КА для удаления", primary_type="OTHER"),
        project_id=project_id,
    )

    await service.soft_delete(cp.id, project_id=project_id)

    # Reload from DB directly
    from sqlalchemy import select

    result = await db_session.execute(select(Counterparty).where(Counterparty.id == cp.id))
    row = result.scalar_one()
    assert row.is_deleted is True
    assert row.deleted_at is not None
