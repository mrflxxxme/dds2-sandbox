"""
Service tests — сводный обзор заявок ФФ по всем складам проекта (get_overview).

Зеркальные FulfillmentRequest создаются напрямую в БД (синк здесь не предмет
теста); HTTP-клиенты провайдеров мокаются только для connect().
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from starlette.routing import Match

from backend.integrations.migfull_client import MigfullClient
from backend.integrations.skladbot_client import SkladbotClient
from backend.integrations.wmscelicom_client import WmsCelicomClient
from backend.models import FulfillmentRequest, Nomenclature, Warehouse
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.services import fulfillment_service

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _uid() -> str:
    return uuid.uuid4().hex[:8]


FAKE_SKLADBOT_TOKEN = "x" * 32  # фейковый токен
FAKE_WMS_TOKEN = "0" * 32  # фейковый токен
FAKE_MIG_TOKEN = "1" * 32  # фейковый токен
FAKE_MIG_GUID = "11111111-2222-3333-4444-555555555555"


async def _connect_skladbot(db_session, project_id, warehouse_id, monkeypatch):
    async def fake_test_connection(self):
        return {"id": 6282, "name": "ООО ТЕСТ ФФ"}

    monkeypatch.setattr(SkladbotClient, "test_connection", fake_test_connection)
    await fulfillment_service.connect(db_session, project_id, warehouse_id, "skladbot", FAKE_SKLADBOT_TOKEN)


async def _connect_wms(db_session, project_id, warehouse_id, monkeypatch):
    async def fake_test_connection(self):
        return True

    monkeypatch.setattr(WmsCelicomClient, "test_connection", fake_test_connection)
    await fulfillment_service.connect(
        db_session, project_id, warehouse_id, "wmscelicom", FAKE_WMS_TOKEN, base_url="test-client.wmscelicom.ru"
    )


async def _connect_migfull(db_session, project_id, warehouse_id, monkeypatch):
    async def fake_test_connection(self):
        return True

    monkeypatch.setattr(MigfullClient, "test_connection", fake_test_connection)
    await fulfillment_service.connect(
        db_session, project_id, warehouse_id, "migfull", FAKE_MIG_TOKEN, tenant_guid=FAKE_MIG_GUID
    )


def _mirror(
    project_id,
    warehouse_id,
    provider="skladbot",
    *,
    kind="assembly",
    created=None,
    archived=False,
    completed=False,
    assembly_request_id=None,
    inbound_receipt_id=None,
    raw=None,
    number=None,
):
    """Зеркальная ФФ-заявка для прямой вставки в БД."""
    return FulfillmentRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        provider=provider,
        external_id=_uid(),
        number=number,
        kind=kind,
        status="Новая",
        archived=archived,
        is_completed=completed,
        external_created_at=created,
        assembly_request_id=assembly_request_id,
        inbound_receipt_id=inbound_receipt_id,
        raw=raw,
    )


def _wms_raw(items):
    """raw отгрузки FBO wmscelicom: состав в packages → items [(barcode, count)]."""
    return {
        "shipment_fbo_id": 1,
        "status": "Новая",
        "packages": {
            "1": {
                "number": 1,
                "barcode": "WB_1",
                "items": [{"name": "Товар", "barcode": bc, "count": qty} for bc, qty in items],
            }
        },
    }


async def _make_warehouse(db_session, project_id):
    wh = Warehouse(project_id=project_id, name=f"FF-{_uid()}", warehouse_type="FULFILLMENT")
    db_session.add(wh)
    await db_session.commit()
    await db_session.refresh(wh)
    return wh


async def _make_nomenclature(db_session, project_id, barcode):
    nom = Nomenclature(project_id=project_id, barcode=barcode, article_seller=f"ART-{_uid()[:6]}")
    db_session.add(nom)
    await db_session.commit()
    await db_session.refresh(nom)
    return nom


async def _make_assembly(
    db_session,
    project_id,
    warehouse_id,
    *,
    status=AssemblyStatus.IN_PROGRESS.value,
    created_at=None,
    items=(),
):
    """Кандидат-заявка на сборку; items — [(barcode, nomenclature_id, qty)]."""
    doc = AssemblyRequest(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=f"A-{_uid()[:6]}",
        status=status,
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    if created_at is not None:
        doc.created_at = created_at
    db_session.add(doc)
    await db_session.commit()
    await db_session.refresh(doc)
    if items:
        db_session.add_all(
            [
                AssemblyRequestItem(
                    project_id=project_id,
                    assembly_request_id=doc.id,
                    nomenclature_id=nom_id,
                    barcode=barcode,
                    quantity=qty,
                )
                for barcode, nom_id, qty in items
            ]
        )
        await db_session.commit()
    return doc


# ─── Сцена: два интегрированных склада + один без интеграции ────────────────


@pytest_asyncio.fixture
async def scene(db_session, project, monkeypatch):
    """wh_skl (skladbot) + wh_wms (wmscelicom) + wh_free (без интеграции) и зеркало заявок."""
    wh_skl = await _make_warehouse(db_session, project.id)
    wh_wms = await _make_warehouse(db_session, project.id)
    wh_free = await _make_warehouse(db_session, project.id)
    await _connect_skladbot(db_session, project.id, wh_skl.id, monkeypatch)
    await _connect_wms(db_session, project.id, wh_wms.id, monkeypatch)

    linked_doc = await _make_assembly(db_session, project.id, wh_skl.id, status=AssemblyStatus.PENDING.value)

    rows = {
        "unlinked1": _mirror(project.id, wh_skl.id, created=date(2026, 6, 10)),
        "unlinked2": _mirror(project.id, wh_skl.id, created=date(2026, 6, 9)),
        "linked": _mirror(project.id, wh_skl.id, created=date(2026, 6, 8), assembly_request_id=linked_doc.id),
        "archived": _mirror(project.id, wh_skl.id, created=date(2026, 6, 7), archived=True),
        "completed": _mirror(project.id, wh_skl.id, created=date(2026, 6, 6), completed=True),
        "inbound": _mirror(project.id, wh_skl.id, kind="inbound", created=date(2026, 6, 5)),
        "wms_row": _mirror(project.id, wh_wms.id, provider="wmscelicom", created=date(2026, 6, 4)),
        # Склад без активной интеграции — его зеркало в сводку не входит
        "free_row": _mirror(project.id, wh_free.id, created=date(2026, 6, 3)),
    }
    db_session.add_all(rows.values())
    await db_session.commit()
    for r in rows.values():
        await db_session.refresh(r)

    return {
        "wh_skl": wh_skl,
        "wh_wms": wh_wms,
        "wh_free": wh_free,
        "linked_doc": linked_doc,
        "rows": rows,
    }


# ─── Склады с интеграцией + каунты ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_overview_warehouses_and_counts(db_session, project, scene):
    data = await fulfillment_service.get_overview(db_session, project.id)

    by_wh = {w["warehouse_id"]: w for w in data["warehouses"]}
    assert set(by_wh) == {scene["wh_skl"].id, scene["wh_wms"].id}  # wh_free без интеграции

    skl = by_wh[scene["wh_skl"].id]
    assert skl["provider"] == "skladbot"
    assert skl["provider_label"] == "skladbot.ru"
    assert skl["warehouse_name"] == scene["wh_skl"].name
    # активные assembly: unlinked1, unlinked2, linked (archived/completed/inbound — мимо)
    assert skl["requests_total"] == 3
    assert skl["requests_unlinked"] == 2

    wms = by_wh[scene["wh_wms"].id]
    assert wms["provider"] == "wmscelicom"
    assert wms["provider_label"] == "WMS Celicom"
    assert wms["requests_total"] == 1
    assert wms["requests_unlinked"] == 1


@pytest.mark.asyncio
async def test_overview_requests_default_kind_assembly(db_session, project, scene):
    data = await fulfillment_service.get_overview(db_session, project.id)
    rows = data["requests"]

    # kind=assembly по умолчанию: inbound и заявки неинтегрированного склада — мимо
    ids = [r["id"] for r in rows]
    expected = [
        scene["rows"]["unlinked1"].id,
        scene["rows"]["unlinked2"].id,
        scene["rows"]["linked"].id,
        scene["rows"]["archived"].id,
        scene["rows"]["completed"].id,
        scene["rows"]["wms_row"].id,
    ]
    assert ids == expected  # сортировка external_created_at desc

    by_id = {r["id"]: r for r in rows}
    linked = by_id[scene["rows"]["linked"].id]
    assert linked["linked_number"] == scene["linked_doc"].number
    assert linked["linked_status"] == scene["linked_doc"].status

    # Обогащение склада/провайдера на каждой строке
    assert linked["warehouse_id"] == scene["wh_skl"].id
    assert linked["warehouse_name"] == scene["wh_skl"].name
    assert linked["provider"] == "skladbot"
    wms_row = by_id[scene["rows"]["wms_row"].id]
    assert wms_row["warehouse_name"] == scene["wh_wms"].name
    assert wms_row["provider"] == "wmscelicom"


@pytest.mark.asyncio
async def test_overview_filters(db_session, project, scene):
    # kind=inbound
    data = await fulfillment_service.get_overview(db_session, project.id, kind="inbound")
    assert [r["id"] for r in data["requests"]] == [scene["rows"]["inbound"].id]
    assert data["requests"][0]["suggestions"] == []  # suggestions только для assembly
    assert len(data["warehouses"]) == 2  # склады — независимо от фильтров

    # warehouse_id
    data = await fulfillment_service.get_overview(db_session, project.id, warehouse_id=scene["wh_wms"].id)
    assert [r["id"] for r in data["requests"]] == [scene["rows"]["wms_row"].id]
    assert len(data["warehouses"]) == 2

    # only_unlinked: linked-строка исчезает
    data = await fulfillment_service.get_overview(db_session, project.id, only_unlinked=True)
    ids = {r["id"] for r in data["requests"]}
    assert scene["rows"]["linked"].id not in ids
    assert scene["rows"]["unlinked1"].id in ids


# ─── Suggestions: date_score ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def skl_warehouse(db_session, project, monkeypatch):
    wh = await _make_warehouse(db_session, project.id)
    await _connect_skladbot(db_session, project.id, wh.id, monkeypatch)
    return wh


@pytest.mark.asyncio
async def test_suggestions_date_score_and_top3(db_session, project, skl_warehouse, monkeypatch):
    """date_score 0/1/2 дн = 70/55/40; >2 дн, чужой статус, связанные, удалённые, чужой склад — мимо."""
    wh = skl_warehouse
    ff = _mirror(project.id, wh.id, created=date(2026, 6, 8))
    db_session.add(ff)
    await db_session.commit()
    await db_session.refresh(ff)

    mk = _make_assembly
    c0 = await mk(db_session, project.id, wh.id, created_at=datetime(2026, 6, 8, 12, 0))
    c1 = await mk(
        db_session, project.id, wh.id, status=AssemblyStatus.READY.value, created_at=datetime(2026, 6, 9, 9, 0)
    )
    c8 = await mk(
        db_session,
        project.id,
        wh.id,
        status=AssemblyStatus.VEHICLE_ASSIGNED.value,
        created_at=datetime(2026, 6, 7, 9, 0),
    )
    c2 = await mk(db_session, project.id, wh.id, created_at=datetime(2026, 6, 6, 9, 0))  # ±2 дн = 40
    # Отбрасываемые кандидаты:
    await mk(db_session, project.id, wh.id, created_at=datetime(2026, 6, 11, 9, 0))  # ±3 дн
    await mk(db_session, project.id, wh.id, status=AssemblyStatus.PENDING.value, created_at=datetime(2026, 6, 8, 9, 0))
    c_linked = await mk(db_session, project.id, wh.id, created_at=datetime(2026, 6, 8, 9, 0))
    db_session.add(_mirror(project.id, wh.id, created=date(2026, 6, 8), assembly_request_id=c_linked.id))
    c_deleted = await mk(db_session, project.id, wh.id, created_at=datetime(2026, 6, 8, 9, 0))
    c_deleted.soft_delete()
    other_wh = await _make_warehouse(db_session, project.id)
    await mk(db_session, project.id, other_wh.id, created_at=datetime(2026, 6, 8, 9, 0))  # чужой склад
    await db_session.commit()

    data = await fulfillment_service.get_overview(db_session, project.id, warehouse_id=wh.id)
    row = next(r for r in data["requests"] if r["id"] == ff.id)
    sugg = row["suggestions"]

    # top-3 по score: c0 (70) > c1/c8 (55) > c2 (40 — вытеснен из топ-3)
    assert [s["score"] for s in sugg] == [70, 55, 55]
    assert sugg[0]["assembly_request_id"] == c0.id
    assert {sugg[1]["assembly_request_id"], sugg[2]["assembly_request_id"]} == {c1.id, c8.id}
    assert c2.id not in {s["assembly_request_id"] for s in sugg}

    assert sugg[0]["reason"] == "дата совпадает"
    assert sugg[0]["number"] == c0.number
    assert sugg[0]["status"] == AssemblyStatus.IN_PROGRESS.value
    assert sugg[0]["total_qty"] == 0  # позиции не заводили
    assert "дата ±1 дн" in {sugg[1]["reason"], sugg[2]["reason"]}


# ─── Suggestions: barcode-пересечение из raw + qty-бонус ─────────────────────


@pytest_asyncio.fixture
async def wms_warehouse(db_session, project, monkeypatch):
    wh = await _make_warehouse(db_session, project.id)
    await _connect_wms(db_session, project.id, wh.id, monkeypatch)
    return wh


@pytest.mark.asyncio
async def test_suggestions_barcode_overlap_and_qty_bonus(db_session, project, wms_warehouse):
    """wmscelicom: состав из raw.packages; score = date + ШК-пересечение×30 + qty-бонус."""
    wh = wms_warehouse
    bc_a, bc_b, bc_c = f"20{_uid()}", f"21{_uid()}", f"22{_uid()}"
    nom_a = await _make_nomenclature(db_session, project.id, bc_a)
    nom_b = await _make_nomenclature(db_session, project.id, bc_b)
    nom_c = await _make_nomenclature(db_session, project.id, bc_c)

    ff = _mirror(
        project.id,
        wh.id,
        provider="wmscelicom",
        created=date(2026, 6, 8),
        raw=_wms_raw([(bc_a, 5), (bc_b, 5)]),  # итого 10 шт
    )
    db_session.add(ff)
    await db_session.commit()
    await db_session.refresh(ff)

    # Полное совпадение: 70 + 30 + 10 → cap 100
    cand_full = await _make_assembly(
        db_session,
        project.id,
        wh.id,
        created_at=datetime(2026, 6, 8, 10, 0),
        items=[(bc_a, nom_a.id, 5), (bc_b, nom_b.id, 5)],
    )
    # Половина ШК, qty мимо: 40 + 15 = 55
    cand_half = await _make_assembly(
        db_session,
        project.id,
        wh.id,
        created_at=datetime(2026, 6, 6, 10, 0),
        items=[(bc_a, nom_a.id, 20)],
    )
    # Без пересечения ШК, но qty в ±10%: 55 + 10 = 65
    cand_qty = await _make_assembly(
        db_session,
        project.id,
        wh.id,
        created_at=datetime(2026, 6, 9, 10, 0),
        items=[(bc_c, nom_c.id, 9)],
    )

    data = await fulfillment_service.get_overview(db_session, project.id, warehouse_id=wh.id)
    row = next(r for r in data["requests"] if r["id"] == ff.id)
    sugg = row["suggestions"]

    assert [s["assembly_request_id"] for s in sugg] == [cand_full.id, cand_qty.id, cand_half.id]
    assert [s["score"] for s in sugg] == [100, 65, 55]

    assert sugg[0]["reason"] == "дата совпадает, ШК 100%, кол-во ±10%"
    assert sugg[0]["total_qty"] == 10
    assert sugg[1]["reason"] == "дата ±1 дн, кол-во ±10%"
    assert sugg[1]["total_qty"] == 9
    assert sugg[2]["reason"] == "дата ±2 дн, ШК 50%"
    assert sugg[2]["total_qty"] == 20


@pytest.mark.asyncio
async def test_suggestions_only_for_unlinked_active(db_session, project, skl_warehouse):
    """Связанные / завершённые / архивные / без external_created_at — suggestions пустые."""
    wh = skl_warehouse
    # Живой кандидат под боком — но строки не подпадают под условия
    await _make_assembly(db_session, project.id, wh.id, created_at=datetime(2026, 6, 8, 10, 0))
    linked_doc = await _make_assembly(db_session, project.id, wh.id, created_at=datetime(2026, 6, 8, 11, 0))

    ff_linked = _mirror(project.id, wh.id, created=date(2026, 6, 8), assembly_request_id=linked_doc.id)
    ff_completed = _mirror(project.id, wh.id, created=date(2026, 6, 8), completed=True)
    ff_archived = _mirror(project.id, wh.id, created=date(2026, 6, 8), archived=True)
    ff_no_date = _mirror(project.id, wh.id, created=None)
    ff_ok = _mirror(project.id, wh.id, created=date(2026, 6, 8))
    db_session.add_all([ff_linked, ff_completed, ff_archived, ff_no_date, ff_ok])
    await db_session.commit()
    for r in (ff_linked, ff_completed, ff_archived, ff_no_date, ff_ok):
        await db_session.refresh(r)

    data = await fulfillment_service.get_overview(db_session, project.id, warehouse_id=wh.id)
    by_id = {r["id"]: r for r in data["requests"]}
    assert by_id[ff_linked.id]["suggestions"] == []
    assert by_id[ff_completed.id]["suggestions"] == []
    assert by_id[ff_archived.id]["suggestions"] == []
    assert by_id[ff_no_date.id]["suggestions"] == []
    assert len(by_id[ff_ok.id]["suggestions"]) == 1  # незанятый кандидат предложен


# ─── Смена провайдера: строки старого ключа не в каунтах/списке ──────────────


@pytest.mark.asyncio
async def test_overview_provider_change_excludes_stale_rows(db_session, project, monkeypatch):
    """После смены ключа (skladbot → wmscelicom) зеркальные строки старого
    провайдера не синкаются и не должны инфлировать каунты и список заявок."""
    wh = await _make_warehouse(db_session, project.id)
    await _connect_skladbot(db_session, project.id, wh.id, monkeypatch)
    stale1 = _mirror(project.id, wh.id, provider="skladbot", created=date(2026, 6, 9))
    stale2 = _mirror(project.id, wh.id, provider="skladbot", created=date(2026, 6, 8))
    db_session.add_all([stale1, stale2])
    await db_session.commit()
    for r in (stale1, stale2):
        await db_session.refresh(r)

    # Смена ключа: отключаем skladbot, подключаем wmscelicom
    await fulfillment_service.disconnect(db_session, project.id, wh.id)
    await _connect_wms(db_session, project.id, wh.id, monkeypatch)
    fresh = _mirror(project.id, wh.id, provider="wmscelicom", created=date(2026, 6, 10))
    db_session.add(fresh)
    await db_session.commit()
    await db_session.refresh(fresh)

    data = await fulfillment_service.get_overview(db_session, project.id)

    by_wh = {w["warehouse_id"]: w for w in data["warehouses"]}
    row = by_wh[wh.id]
    assert row["provider"] == "wmscelicom"
    assert row["requests_total"] == 1  # только fresh; stale1/stale2 — мимо
    assert row["requests_unlinked"] == 1

    ids = [r["id"] for r in data["requests"]]
    assert fresh.id in ids
    assert stale1.id not in ids
    assert stale2.id not in ids


# ─── Изоляция проектов ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overview_project_isolation(db_session, project, other_project, monkeypatch):
    my_wh = await _make_warehouse(db_session, project.id)
    await _connect_skladbot(db_session, project.id, my_wh.id, monkeypatch)
    other_wh = await _make_warehouse(db_session, other_project.id)
    await _connect_skladbot(db_session, other_project.id, other_wh.id, monkeypatch)

    db_session.add(_mirror(other_project.id, other_wh.id, created=date(2026, 6, 8)))
    await db_session.commit()

    data = await fulfillment_service.get_overview(db_session, project.id)
    assert [w["warehouse_id"] for w in data["warehouses"]] == [my_wh.id]
    assert data["requests"] == []

    other_data = await fulfillment_service.get_overview(db_session, other_project.id)
    assert [w["warehouse_id"] for w in other_data["warehouses"]] == [other_wh.id]
    assert len(other_data["requests"]) == 1


# ─── Роутинг: /warehouse/fulfillment/overview не конфликтует с /{warehouse_id} ──


def _resolve(app, method: str, path: str):
    """Первый FULL-матч в порядке регистрации роутов (как делает Starlette)."""
    scope = {"type": "http", "method": method, "path": path, "root_path": "", "headers": []}
    for route in app.router.routes:
        match, child_scope = route.matches(scope)
        if match == Match.FULL:
            return route, child_scope
    return None, None


def test_overview_route_resolves_without_warehouse_id():
    from backend.main import app

    route, child_scope = _resolve(app, "GET", "/api/v1/warehouse/fulfillment/overview")
    assert route is not None, "GET /warehouse/fulfillment/overview не зарезолвился"
    assert route.name == "fulfillment_overview"
    # «fulfillment» не проглочен как warehouse_id параметризованным роутом
    assert child_scope["path_params"] == {}


def test_parametrized_fulfillment_routes_still_resolve():
    from backend.main import app

    route, child_scope = _resolve(app, "GET", "/api/v1/warehouse/123/fulfillment/status")
    assert route is not None
    assert route.name == "get_status"
    assert child_scope["path_params"] == {"warehouse_id": "123"}

    route, child_scope = _resolve(app, "GET", "/api/v1/warehouse/123/fulfillment/requests")
    assert route is not None
    assert route.name == "list_requests"
    assert child_scope["path_params"] == {"warehouse_id": "123"}


@pytest.mark.asyncio
async def test_overview_migfull_provider_label(db_session, project, monkeypatch):
    """Третий провайдер в сводке: label «Натали (migfull.app)», каунты считаются."""
    wh = await _make_warehouse(db_session, project.id)
    await _connect_migfull(db_session, project.id, wh.id, monkeypatch)
    db_session.add(_mirror(project.id, wh.id, provider="migfull"))
    await db_session.commit()

    data = await fulfillment_service.get_overview(db_session, project.id)
    row = next(w for w in data["warehouses"] if w["warehouse_id"] == wh.id)
    assert row["provider"] == "migfull"
    assert row["provider_label"] == "Натали (migfull.app)"
    assert row["requests_total"] == 1
    assert row["requests_unlinked"] == 1
