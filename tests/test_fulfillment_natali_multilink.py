# ruff: noqa: RUF001, RUF002, RUF003
"""
Натали (provider=migfull) — несколько ФФ-заявок на одну заявку ДДС (N:1).

У склада «Натали» одной нашей заявке на сборку может соответствовать 2+ заявки
в их системе. Поэтому для migfull:
- link_request разрешает привязать вторую (и далее) ФФ-заявку к той же сборке;
- кандидаты модалки «Связать» НЕ исключают уже связанную сборку (+ linked_ff_count);
- get_ff_link_for_assembly отдаёт ВСЕ привязки (ff_links);
- авто-READY срабатывает, только когда ВСЕ привязанные заявки готовы.
Для skladbot/wmscelicom 1:1 сохраняется.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio

from backend.models import FulfillmentRequest, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyStatus
from backend.services import fulfillment_service


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def warehouse(db_session, project):
    wh = Warehouse(project_id=project.id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


async def _add(db_session, obj):
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


async def _make_assembly(db_session, project_id, warehouse_id, *, status=AssemblyStatus.IN_PROGRESS.value):
    return await _add(
        db_session,
        AssemblyRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            number=f"A-{_uid()[:6]}",
            status=status,
            pallets_count=1,
            pallet_weight_kg=Decimal("10.00"),
        ),
    )


async def _make_ff(
    db_session,
    project_id,
    warehouse_id,
    *,
    provider="migfull",
    stage_code=None,
    assembly_request_id=None,
    number=None,
):
    return await _add(
        db_session,
        FulfillmentRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            external_id=_uid(),
            number=number or f"PVB-{_uid()[:6]}",
            kind="assembly",
            status="Новый",
            stage_code=stage_code,
            assembly_request_id=assembly_request_id,
        ),
    )


# ─── link_request: migfull допускает N:1, прочие провайдеры — нет ────────────


@pytest.mark.asyncio
async def test_migfull_allows_second_ff_link_to_same_assembly(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff1 = await _make_ff(db_session, project.id, warehouse.id)
    ff2 = await _make_ff(db_session, project.id, warehouse.id)

    r1 = await fulfillment_service.link_request(
        db_session, project.id, ff1.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
    )
    r2 = await fulfillment_service.link_request(
        db_session, project.id, ff2.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
    )
    assert r1 is not None and r2 is not None
    await db_session.refresh(ff1)
    await db_session.refresh(ff2)
    assert ff1.assembly_request_id == doc.id
    assert ff2.assembly_request_id == doc.id


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["skladbot", "wmscelicom"])
async def test_non_migfull_second_link_still_conflicts(db_session, project, warehouse, provider):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff1 = await _make_ff(db_session, project.id, warehouse.id, provider=provider)
    ff2 = await _make_ff(db_session, project.id, warehouse.id, provider=provider)

    await fulfillment_service.link_request(
        db_session, project.id, ff1.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
    )
    with pytest.raises(ValueError, match="уже связана"):
        await fulfillment_service.link_request(
            db_session, project.id, ff2.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
        )


# ─── get_link_candidates: migfull показывает связанную сборку + linked_ff_count ─


@pytest.mark.asyncio
async def test_migfull_candidates_include_already_linked_with_count(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff1 = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id)
    ff2 = await _make_ff(db_session, project.id, warehouse.id)

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff2.id)
    assert data is not None
    cand = {c["doc_id"]: c for c in data["candidates"]}
    assert doc.id in cand, "связанная сборка должна оставаться кандидатом для migfull"
    assert cand[doc.id]["linked_ff_count"] == 1
    # сама ff1 не учитывается в счётчике для запроса по ff2 (id != ff_request_id)
    _ = ff1


@pytest.mark.asyncio
async def test_wmscelicom_candidates_exclude_already_linked(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    await _make_ff(db_session, project.id, warehouse.id, provider="wmscelicom", assembly_request_id=doc.id)
    ff2 = await _make_ff(db_session, project.id, warehouse.id, provider="wmscelicom")

    data = await fulfillment_service.get_link_candidates(db_session, project.id, warehouse.id, ff2.id)
    assert data is not None
    doc_ids = {c["doc_id"] for c in data["candidates"]}
    assert doc.id not in doc_ids, "связанная сборка исключается из кандидатов для wmscelicom"


# ─── get_ff_link_for_assembly: все привязки ─────────────────────────────────


@pytest.mark.asyncio
async def test_get_ff_link_for_assembly_returns_all_links(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff1 = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id, number="PVB-001")
    ff2 = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id, number="PVB-002")

    link = await fulfillment_service.get_ff_link_for_assembly(db_session, project.id, doc.id)
    assert link is not None
    assert "ff_links" in link and len(link["ff_links"]) == 2
    ids = {x["ff_request_id"] for x in link["ff_links"]}
    assert ids == {ff1.id, ff2.id}
    # первая привязка дублируется в плоских полях (обратная совместимость)
    assert link["ff_request_id"] == link["ff_links"][0]["ff_request_id"] == ff1.id


@pytest.mark.asyncio
async def test_get_ff_link_for_assembly_none_when_unlinked(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    assert await fulfillment_service.get_ff_link_for_assembly(db_session, project.id, doc.id) is None


# ─── авто-READY: только когда ВСЕ привязанные заявки готовы ──────────────────


@pytest.mark.asyncio
async def test_mark_ready_requires_all_linked_ff_ready(db_session, project, warehouse):
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff_ready = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id, stage_code="ready")
    ff_wip = await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id, stage_code="uploaded")

    # одна готова, вторая ещё собирается → сборка НЕ переходит в READY
    transitioned = await fulfillment_service._mark_linked_assemblies_ready(db_session, project.id, [ff_ready, ff_wip])
    await db_session.commit()
    await db_session.refresh(doc)
    assert transitioned == []
    assert doc.status == AssemblyStatus.IN_PROGRESS.value

    # вторая стала «Собран» → теперь все готовы → READY
    ff_wip.stage_code = "ready"
    await db_session.commit()
    transitioned = await fulfillment_service._mark_linked_assemblies_ready(db_session, project.id, [ff_ready, ff_wip])
    await db_session.commit()
    await db_session.refresh(doc)
    assert len(transitioned) == 1
    assert doc.status == AssemblyStatus.READY.value


@pytest.mark.asyncio
async def test_link_does_not_flip_ready_while_sibling_unready(db_session, project, warehouse):
    """Привязка готовой ФФ-заявки не переводит сборку в READY, пока сосед собирается."""
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    # FF#1 ещё собирается, уже привязана
    await _make_ff(db_session, project.id, warehouse.id, assembly_request_id=doc.id, stage_code="uploaded")
    # FF#2 готова, привязываем сейчас
    ff2 = await _make_ff(db_session, project.id, warehouse.id, stage_code="ready")

    await fulfillment_service.link_request(
        db_session, project.id, ff2.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
    )
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.IN_PROGRESS.value


@pytest.mark.asyncio
async def test_link_flips_ready_when_single_ff_ready(db_session, project, warehouse):
    """Единственная привязанная готовая ФФ-заявка → авто-READY (как и раньше)."""
    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff = await _make_ff(db_session, project.id, warehouse.id, stage_code="ready")

    await fulfillment_service.link_request(
        db_session, project.id, ff.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
    )
    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value


@pytest.mark.asyncio
async def test_link_ready_sends_telegram_notify(db_session, project, warehouse):
    """Авто-READY на привязке шлёт Telegram-уведомление «✅ Сборка готова».

    Регресс c09e87d: notify_ff_events жил только в sync-пути, а link_request
    переводил сборку в READY молча → уведомление терялось навсегда (синк уже-READY
    не подхватывает). Теперь привязка шлёт его best-effort, как синк.
    """
    from unittest.mock import AsyncMock, patch

    doc = await _make_assembly(db_session, project.id, warehouse.id)
    ff = await _make_ff(db_session, project.id, warehouse.id, stage_code="ready")

    with patch("backend.services.fulfillment_notify.notify_ff_events", new=AsyncMock()) as mock_notify:
        await fulfillment_service.link_request(
            db_session, project.id, ff.id, assembly_request_id=doc.id, warehouse_id=warehouse.id
        )

    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value  # авто-READY сработал
    assert mock_notify.called, "уведомление о готовности должно уйти и при привязке"
    # notify_ff_events(db, project_id, [item]) — item про нашу сборку
    items = mock_notify.call_args.args[2]
    assert len(items) == 1
    assert str(doc.number) in str(items[0]["text"])
