# ruff: noqa: RUF001, RUF002, RUF003
"""
Связь учётного зеркала FBS (`AssemblyRequest.kind=fbs`) с заявкой ФФ.

До 2026-08-02 связь была запрещена каноном: `_assembly_candidates` и
`list_unlinked_assemblies` выкидывали kind=fbs из кандидатов. Запрет снят —
FBS-сборку физически ведёт тот же ФФ-склад, и его заявка это тот же документ.

ЧТО ОБЯЗАНО ОСТАТЬСЯ ПРАВДОЙ ПОСЛЕ СНЯТИЯ ЗАПРЕТА:
  • связь ЧИСТО учётная — привязка НЕ двигает статус зеркала (его ведёт джоб
    `wb_fbs.assembly_mirror`, и READY в его цепочке нет вообще);
  • скоуп проекта и склада — как у обычной сборки (чужое не пускаем);
  • 1:1 для не-migfull провайдеров: вторая ФФ-заявка на то же зеркало → отказ.
"""

import uuid
from datetime import date, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import FulfillmentRequest, Nomenclature, Warehouse
from backend.models.assembly import (
    AssemblyKind,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.services import fulfillment_service


def _uid() -> str:
    return uuid.uuid4().hex[:8]


# ─── Фикстуры и хелперы ──────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def wh(db_session: AsyncSession, project):
    wh = Warehouse(project_id=project.id, name=f"FBSLINK-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


async def _add(db_session, obj):
    db_session.add(obj)
    await db_session.commit()
    await db_session.refresh(obj)
    return obj


async def _mirror_assembly(
    db_session,
    project_id,
    warehouse_id,
    *,
    status=AssemblyStatus.DELIVERED.value,
    kind=AssemblyKind.FBS.value,
    created_at=None,
):
    """Учётное зеркало FBS (как его заводит джоб) либо обычная сборка при kind=fbo."""
    doc = AssemblyRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"ASM-{_uid()[:6]}",
        status=status,
        kind=kind,
        fbs_supply_id=f"WB-GI-{_uid()}" if kind == AssemblyKind.FBS.value else None,
        # NOT NULL в схеме; джоб зеркала кладёт нули — паллет у FBS-сборки нет.
        pallets_count=0,
        pallet_weight_kg=0,
    )
    if created_at is not None:
        doc.created_at = created_at
    return await _add(db_session, doc)


async def _ff_request(
    db_session,
    project_id,
    warehouse_id,
    *,
    provider="wmscelicom",
    kind="assembly",
    stage_title=None,
    is_completed=False,
    local_archived=False,
    assembly_request_id=None,
    created=None,
):
    """Зеркальная ФФ-заявка (строка синка) — прямой вставкой, без HTTP."""
    return await _add(
        db_session,
        FulfillmentRequest(
            project_id=project_id,
            warehouse_id=warehouse_id,
            provider=provider,
            external_id=_uid(),
            number=f"PVB-{_uid()[:5]}",
            kind=kind,
            status="Новая",
            stage_title=stage_title,
            is_completed=is_completed,
            local_archived=local_archived,
            assembly_request_id=assembly_request_id,
            external_created_at=created or date(2026, 8, 1),
        ),
    )


# ─── get_assembly_ff_candidates: новая ручка от карточки сборки ──────────────


@pytest.mark.asyncio
async def test_assembly_ff_candidates_for_fbs_mirror(db_session, project, wh):
    """Зеркало FBS видит свободные заявки ФФ своего склада."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)
    free = await _ff_request(db_session, project.id, wh.id)

    rows = await fulfillment_service.get_assembly_ff_candidates(db_session, project.id, mirror.id)

    assert [r["id"] for r in rows] == [free.id]
    row = rows[0]
    # Строка обязана нести склад: ручки link/unlink скоуплены складом.
    assert row["warehouse_id"] == wh.id
    assert row["number"] == free.number
    assert row["kind"] == "assembly"
    # `side` — атрибут ПЕРЕЕЗДА (две стороны), у сборки его быть не должно.
    assert "side" not in row


@pytest.mark.asyncio
async def test_assembly_ff_candidates_excludes_busy_and_foreign(db_session, project, wh, other_project):
    """Занятые, архивные, чужой склад/проект и не-assembly в кандидаты не попадают."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)
    good = await _ff_request(db_session, project.id, wh.id)

    other_wh = await _add(
        db_session,
        Warehouse(project_id=project.id, name=f"OTHER-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    busy_asm = await _mirror_assembly(db_session, project.id, wh.id, kind=AssemblyKind.FBO.value)
    await _ff_request(db_session, project.id, wh.id, assembly_request_id=busy_asm.id)  # занята
    await _ff_request(db_session, project.id, wh.id, local_archived=True)  # локальный архив
    await _ff_request(db_session, project.id, wh.id, kind="inbound")  # приёмка — не наша сторона
    await _ff_request(db_session, project.id, other_wh.id)  # другой склад
    foreign_wh = await _add(
        db_session,
        Warehouse(project_id=other_project.id, name=f"FRGN-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    await _ff_request(db_session, other_project.id, foreign_wh.id)  # другой проект

    rows = await fulfillment_service.get_assembly_ff_candidates(db_session, project.id, mirror.id)

    assert [r["id"] for r in rows] == [good.id]


@pytest.mark.asyncio
async def test_assembly_ff_candidates_foreign_project_raises(db_session, project, wh, other_project):
    """Сборка чужого проекта не находится → ValueError (роутер отдаст 404)."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)

    with pytest.raises(ValueError, match="не найдена"):
        await fulfillment_service.get_assembly_ff_candidates(db_session, other_project.id, mirror.id)


@pytest.mark.asyncio
async def test_assembly_ff_candidates_endpoint(client, auth_headers, db_session):
    """API: GET /warehouse/assembly/{id}/ff-candidates отдаёт строки и 404 на чужое."""
    resp = await client.post("/api/v1/projects", json={"name": "FBS FF link"}, headers=auth_headers)
    project_id = resp.json()["id"]
    headers = {**auth_headers, "X-Project-Id": str(project_id)}
    resp = await client.post(
        "/api/v1/warehouse",
        json={"name": f"FBSAPI-{_uid()}", "warehouse_type": "FULFILLMENT"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    wh_id = resp.json()["id"]

    mirror = await _mirror_assembly(db_session, project_id, wh_id)
    free = await _ff_request(db_session, project_id, wh_id)

    resp = await client.get(f"/api/v1/warehouse/assembly/{mirror.id}/ff-candidates", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [r["id"] for r in data] == [free.id]
    assert data[0]["warehouse_id"] == wh_id

    # Несуществующая сборка → 404, а не 422: путь `/{id}/ff-candidates` не съеден
    # соседним `/{id}` (одинаковое число сегментов + статический суффикс).
    resp = await client.get(f"/api/v1/warehouse/assembly/{mirror.id + 10**6}/ff-candidates", headers=headers)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_fbs_mirror_shows_ff_link_in_list_and_card(client, auth_headers, db_session):
    """Связанная ФФ-заявка ВИДНА и в списке `kind=fbs`, и в карточке зеркала.

    Обогащение (`_enrich_ff_links` для списка, `get_ff_link_for_assembly` для
    карточки) по `kind` не фильтрует — тест держит это свойство: без него связь
    сохранилась бы в БД, а экран остался бы пустым.
    """
    resp = await client.post("/api/v1/projects", json={"name": "FBS FF show"}, headers=auth_headers)
    project_id = resp.json()["id"]
    headers = {**auth_headers, "X-Project-Id": str(project_id)}
    resp = await client.post(
        "/api/v1/warehouse",
        json={"name": f"FBSSHOW-{_uid()}", "warehouse_type": "FULFILLMENT"},
        headers=headers,
    )
    wh_id = resp.json()["id"]

    mirror = await _mirror_assembly(db_session, project_id, wh_id)
    ff = await _ff_request(
        db_session, project_id, wh_id, stage_title="Отгружен", assembly_request_id=mirror.id
    )

    resp = await client.get("/api/v1/warehouse/assembly?kind=fbs&view=archived", headers=headers)
    assert resp.status_code == 200, resp.text
    row = next(r for r in resp.json()["items"] if r["id"] == mirror.id)
    assert row["ff_request_number"] == ff.number
    assert [link["ff_request_id"] for link in row["ff_links"]] == [ff.id]

    resp = await client.get(f"/api/v1/warehouse/assembly/{mirror.id}", headers=headers)
    assert resp.status_code == 200, resp.text
    card = resp.json()
    assert card["ff_request_number"] == ff.number
    assert [link["ff_request_id"] for link in card["ff_links"]] == [ff.id]
    assert card["ff_stage_title"] == "Отгружен"


# ─── link_request: привязка/отвязка зеркала FBS ──────────────────────────────


@pytest.mark.asyncio
async def test_link_fbs_mirror_allowed(db_session, project, wh):
    """Канон снят: ФФ-заявка привязывается к зеркалу FBS."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)
    ff = await _ff_request(db_session, project.id, wh.id)

    row = await fulfillment_service.link_request(
        db_session, project.id, ff.id, assembly_request_id=mirror.id, warehouse_id=wh.id
    )

    assert row is not None
    await db_session.refresh(ff)
    assert ff.assembly_request_id == mirror.id


@pytest.mark.asyncio
async def test_link_fbs_mirror_does_not_move_status(db_session, project, wh):
    """🔴 Инвариант: привязка НЕ двигает статус зеркала (READY нет в его цепочке).

    Обычная сборка в IN_PROGRESS при завершённой ФФ-заявке уходит в READY
    авто-переходом — зеркало обязано остаться IN_PROGRESS и без записи истории.
    """
    mirror = await _mirror_assembly(
        db_session, project.id, wh.id, status=AssemblyStatus.IN_PROGRESS.value
    )
    # Сигнал готовности ФФ: тот же, что переводит обычную сборку в READY.
    ff = await _ff_request(db_session, project.id, wh.id, is_completed=True, stage_title="Собран")

    await fulfillment_service.link_request(
        db_session, project.id, ff.id, assembly_request_id=mirror.id, warehouse_id=wh.id
    )

    await db_session.refresh(mirror)
    assert mirror.status == AssemblyStatus.IN_PROGRESS.value
    assert mirror.actual_ready_date is None
    history = (
        await db_session.execute(
            select(AssemblyStatusHistory).where(
                AssemblyStatusHistory.assembly_request_id == mirror.id,
                AssemblyStatusHistory.project_id == project.id,
            )
        )
    ).scalars().all()
    assert history == []


@pytest.mark.asyncio
async def test_link_fbo_assembly_still_auto_ready(db_session, project, wh):
    """Контроль: у ОБЫЧНОЙ сборки авто-READY при привязке не сломан гейтом kind."""
    doc = await _mirror_assembly(
        db_session,
        project.id,
        wh.id,
        status=AssemblyStatus.IN_PROGRESS.value,
        kind=AssemblyKind.FBO.value,
    )
    ff = await _ff_request(db_session, project.id, wh.id, is_completed=True, stage_title="Собран")

    await fulfillment_service.link_request(
        db_session, project.id, ff.id, assembly_request_id=doc.id, warehouse_id=wh.id
    )

    await db_session.refresh(doc)
    assert doc.status == AssemblyStatus.READY.value


@pytest.mark.asyncio
async def test_link_fbs_mirror_wrong_warehouse(db_session, project, wh):
    """Зеркало другого склада не привязывается (скоуп склада)."""
    other_wh = await _add(
        db_session,
        Warehouse(project_id=project.id, name=f"WH2-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    mirror = await _mirror_assembly(db_session, project.id, other_wh.id)
    ff = await _ff_request(db_session, project.id, wh.id)

    with pytest.raises(ValueError, match="другому складу"):
        await fulfillment_service.link_request(
            db_session, project.id, ff.id, assembly_request_id=mirror.id, warehouse_id=wh.id
        )


@pytest.mark.asyncio
async def test_link_fbs_mirror_foreign_project(db_session, project, wh, other_project):
    """Зеркало чужого проекта не привязывается (скоуп проекта)."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)
    foreign_wh = await _add(
        db_session,
        Warehouse(project_id=other_project.id, name=f"FRGN2-{_uid()}", warehouse_type="FULFILLMENT"),
    )
    ff = await _ff_request(db_session, other_project.id, foreign_wh.id)

    with pytest.raises(ValueError, match="не найдена в проекте"):
        await fulfillment_service.link_request(
            db_session,
            other_project.id,
            ff.id,
            assembly_request_id=mirror.id,
            warehouse_id=foreign_wh.id,
        )


@pytest.mark.asyncio
async def test_link_fbs_mirror_double_link_rejected(db_session, project, wh):
    """1:1 для не-migfull: вторая ФФ-заявка на то же зеркало отбивается."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)
    first = await _ff_request(db_session, project.id, wh.id)
    second = await _ff_request(db_session, project.id, wh.id)

    await fulfillment_service.link_request(
        db_session, project.id, first.id, assembly_request_id=mirror.id, warehouse_id=wh.id
    )

    with pytest.raises(ValueError, match="уже связана"):
        await fulfillment_service.link_request(
            db_session, project.id, second.id, assembly_request_id=mirror.id, warehouse_id=wh.id
        )


@pytest.mark.asyncio
async def test_unlink_fbs_mirror(db_session, project, wh):
    """Отвязка снимает связь и не трогает статус зеркала."""
    mirror = await _mirror_assembly(db_session, project.id, wh.id)
    ff = await _ff_request(db_session, project.id, wh.id, assembly_request_id=mirror.id)

    row = await fulfillment_service.unlink_request(
        db_session, project.id, ff.id, warehouse_id=wh.id
    )

    assert row is not None
    await db_session.refresh(ff)
    await db_session.refresh(mirror)
    assert ff.assembly_request_id is None
    assert mirror.status == AssemblyStatus.DELIVERED.value


# ─── Обратное направление: зеркало в кандидатах модалки «Связать» ────────────


@pytest.mark.asyncio
async def test_fbs_mirror_in_link_candidates(db_session, project, wh):
    """Модалка «Связать» со стороны ФФ-заявки показывает зеркало FBS."""
    mirror = await _mirror_assembly(
        db_session, project.id, wh.id, created_at=datetime(2026, 8, 1, 10, 0)
    )
    barcode = f"BC{_uid()}"
    nom = await _add(
        db_session,
        Nomenclature(project_id=project.id, barcode=barcode, article_seller=f"ART-{_uid()[:6]}"),
    )
    db_session.add(
        AssemblyRequestItem(
            project_id=project.id,
            assembly_request_id=mirror.id,
            nomenclature_id=nom.id,
            barcode=barcode,
            quantity=3,
        )
    )
    await db_session.commit()
    ff = await _ff_request(db_session, project.id, wh.id, created=date(2026, 8, 1))

    data = await fulfillment_service.get_link_candidates(db_session, project.id, wh.id, ff.id)

    assert data is not None
    assert mirror.id in [c["doc_id"] for c in data["candidates"] if c["doc_kind"] == "assembly"]


@pytest.mark.asyncio
async def test_fbs_mirror_in_unlinked_assemblies(db_session, project, wh):
    """Обратный список «сборки без ФФ» тоже показывает зеркало (IN_PROGRESS)."""
    mirror = await _mirror_assembly(
        db_session, project.id, wh.id, status=AssemblyStatus.IN_PROGRESS.value
    )

    rows = await fulfillment_service.list_unlinked_assemblies(db_session, project.id, wh.id)

    assert mirror.id in [r["id"] for r in rows]


@pytest.mark.asyncio
async def test_fbs_mirror_not_in_match_suggestions(db_session, project, wh):
    """Зеркало НЕ подсказывается авто-матчером — сознательная асимметрия.

    Связать можно (кандидаты выше), но фолбэк-скоринг по дате дал бы зеркалу
    70 баллов у каждой дневной FBO-заявки склада — это шум, а не подсказка.
    """
    await _mirror_assembly(
        db_session,
        project.id,
        wh.id,
        status=AssemblyStatus.IN_PROGRESS.value,
        created_at=datetime(2026, 8, 1, 10, 0),
    )
    ff = await _ff_request(db_session, project.id, wh.id, created=date(2026, 8, 1))

    out = await fulfillment_service._load_match_suggestions(db_session, project.id, [ff])

    assert out.get(ff.id, []) == []
