"""
Tests for wb_supply_service — занос заявки-сборки в FBW-поставку WB (реплей портала).

WbPortalClient замокан: проверяем оркестрацию, персист статусов/id, изоляцию по
project_id и обработку истёкшей сессии — без реальных HTTP к WB.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.integrations.wb_portal_client import WbPortalError, WbSessionExpired
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.assembly_wb import WbSupplySyncStatus
from backend.services import integrations_service, wb_supply_service
from backend.services.wb_supply_service import WbSupplyError

PROJECT_ID = 0
OTHER_PROJECT_ID = 0
BC = "TEST_BC_WBSUP_1"
WH_NAME = "Волгоград"


class FakeClient:
    """Мок реплея портала. По флагам поднимает ошибки сессии/портала."""

    def __init__(
        self,
        *,
        expire=False,
        portal_error=False,
        supplies=None,
        box_types=None,
        goods_error=False,
        statuses=None,
        list_expire=False,
        supply_status=None,
        details_expire=False,
    ):
        self.expire = expire
        self.portal_error = portal_error
        self.supplies = supplies if supplies is not None else []
        self.box_types = box_types if box_types is not None else [2]
        self.goods_error = goods_error
        self.statuses = statuses if statuses is not None else []
        self.list_expire = list_expire  # WbSessionExpired на list_statuses (bulk-синк)
        # supplyDetails: авторитетный кабинетный статус (по умолчанию «Запланировано»).
        self.supply_status = supply_status if supply_status is not None else {"statusName": "Запланировано", "statusId": 1}
        self.details_expire = details_expire  # WbSessionExpired на supply_details
        self.details_calls = 0
        self.goods_sent = None
        self.bind_sent = None
        self.trn_saved = None

    async def get_warehouse_filter_items(self):
        return [{"warehouseID": 301983, "warehouseName": "Волгоград"}]

    async def draft_create(self):
        if self.expire:
            raise WbSessionExpired("expired")
        if self.portal_error:
            raise WbPortalError("bad draft")
        return "draft-uuid-1"

    async def draft_update_goods(self, draft_id, barcodes):
        self.goods_sent = barcodes

    async def validate_warehouse_goods(self, draft_id, warehouse_id):
        return {"items": [{"availableBoxTypes": self.box_types}]}

    async def supply_create(self, draft_id, warehouse_id, box_type_id, **kw):
        return 52670743

    async def edit_preorder_goods(self, preorder_id, barcodes):
        if self.goods_error:
            return [
                {
                    "barcode": barcodes[0]["barcode"],
                    "quantity": barcodes[0]["quantity"],
                    "errors": [{"field": "monopallet", "error": "мелкий товар"}],
                    "hasError": True,
                }
            ]
        return [
            {"barcode": b["barcode"], "quantity": b["quantity"], "errors": [], "hasError": False}
            for b in barcodes
        ]

    async def list_supplies(self, status_ids=None, limit=50, offset=0):
        # Одна страница: всё на offset 0, дальше пусто (терминатор пагинации).
        if self.list_expire:
            raise WbSessionExpired("expired")
        return self.supplies if offset == 0 else []

    async def list_statuses(self):
        if self.list_expire:
            raise WbSessionExpired("expired")
        return self.statuses

    async def supply_details(self, supply_id):
        self.details_calls += 1
        if self.details_expire:
            raise WbSessionExpired("expired")
        return self.supply_status

    async def create_box_barcodes(self, supply_id, count):
        return [f"WB_{i}" for i in range(count)]

    async def bind_barcodes(self, supply_id, bind):
        self.bind_sent = bind

    async def trn_details(self, supply_id):
        return {
            "details": {
                "trns": [
                    {
                        "barcode": {"barcodeId": 999, "barcodePrefix": "WB-GI-"},
                        "firstName": "Александр",
                        "lastName": "Рыболов",
                        "carModel": "газ",
                        "carNumber": "O253PY790",
                        "quantity": 7,
                        "phone": "",
                        "dateFrom": "2026-07-10T00:00:00+03:00",
                        "dateTo": "2026-07-12T00:00:00+03:00",
                    }
                ]
            }
        }

    async def set_trn(self, **kw):
        self.trn_saved = kw

    async def pass_history(self):
        return [{"firstName": "Иван", "lastName": "Иванов", "phone": "79001112233"}]

    async def list_boxes(self, supply_id):
        return [
            {
                "boxcode": "WB_111",
                "quantity": 1,
                "barcodes": [
                    {"barcode": "BC1", "quantity": 40, "imtName": "Товар А", "brand": "Бренд", "saNm": "ART-1"},
                ],
            },
            {
                "boxcode": "WB_222",
                "quantity": 1,
                "barcodes": [
                    {"barcode": "BC1", "quantity": 10},
                    {"barcode": "BC2", "quantity": 5},
                ],
            },
        ]


@pytest_asyncio.fixture(autouse=True)
async def setup(db_session, project, other_project, monkeypatch):
    global PROJECT_ID, OTHER_PROJECT_ID
    PROJECT_ID = project.id
    OTHER_PROJECT_ID = other_project.id

    # FULFILLMENT-склад + номенклатура + баркод
    await db_session.execute(
        text(
            "INSERT INTO warehouses (project_id, name, warehouse_type, sort_order, is_active, is_deleted, created_at, updated_at) "
            "VALUES (:pid, 'WBSUP WH', 'FULFILLMENT', 1, true, false, NOW(), NOW())"
        ),
        {"pid": PROJECT_ID},
    )
    wh_id = (
        await db_session.execute(
            text("SELECT id FROM warehouses WHERE project_id = :pid ORDER BY id DESC LIMIT 1"),
            {"pid": PROJECT_ID},
        )
    ).scalar()
    await db_session.execute(
        text("INSERT INTO nomenclature (project_id, barcode, updated_at) VALUES (:pid, :bc, NOW())"),
        {"pid": PROJECT_ID, "bc": BC},
    )
    nom_id = (
        await db_session.execute(
            text("SELECT id FROM nomenclature WHERE project_id = :pid AND barcode = :bc"),
            {"pid": PROJECT_ID, "bc": BC},
        )
    ).scalar()

    req = AssemblyRequest(
        project_id=PROJECT_ID,
        warehouse_id=wh_id,
        number="WBSUP-1",
        status=AssemblyStatus.IN_PROGRESS,
        pallets_count=2,
        pallet_weight_kg=Decimal("100.00"),
        wb_warehouse_name_manual=WH_NAME,
    )
    db_session.add(req)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=PROJECT_ID,
            assembly_request_id=req.id,
            nomenclature_id=nom_id,
            barcode=BC,
            quantity=10,
        )
    )
    await db_session.commit()

    global ASSEMBLY_ID
    ASSEMBLY_ID = req.id
    yield


async def _patch_client(monkeypatch, client):
    async def fake_get(db, project_id):
        return client

    monkeypatch.setattr(integrations_service, "get_wb_portal_client", fake_get)


@pytest.mark.asyncio
async def test_create_preorder_happy(db_session, monkeypatch):
    client = FakeClient()
    await _patch_client(monkeypatch, client)

    link = await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)

    assert link.sync_status == WbSupplySyncStatus.PREORDER.value
    assert link.warehouse_id_wb == 301983
    assert link.box_type_id == 2
    assert link.draft_id == "draft-uuid-1"
    assert link.preorder_id == 52670743
    assert client.goods_sent == [{"barcode": BC, "quantity": 10}]


@pytest.mark.asyncio
async def test_create_preorder_uses_assembly_package_type(db_session, monkeypatch):
    # заявка помечена MONOPALLET, WB предлагает тип 5 → boxType 5
    await db_session.execute(
        text("UPDATE assembly_requests SET package_type = 'MONOPALLET' WHERE id = :id"),
        {"id": ASSEMBLY_ID},
    )
    await db_session.commit()
    db_session.expire_all()  # сбросить кэш ORM, чтобы сервис прочитал свежий package_type
    await _patch_client(monkeypatch, FakeClient(box_types=[5]))
    link = await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.box_type_id == 5


@pytest.mark.asyncio
async def test_create_preorder_explicit_package_override(db_session, monkeypatch):
    # явный выбор SUPERSAFE в панели перекрывает тип заявки; WB предлагает 6 → boxType 6
    await _patch_client(monkeypatch, FakeClient(box_types=[6]))
    link = await wb_supply_service.create_preorder(
        db_session, PROJECT_ID, ASSEMBLY_ID, package_type="SUPERSAFE"
    )
    assert link.box_type_id == 6


@pytest.mark.asyncio
async def test_create_preorder_rejects_unavailable_package(db_session, monkeypatch):
    # заявка BOX (тип 2), а WB для этих товаров/склада предлагает только 5 → понятная ошибка
    await _patch_client(monkeypatch, FakeClient(box_types=[5]))
    with pytest.raises(WbSupplyError, match="не принимает упаковку"):
        await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)


@pytest.mark.asyncio
async def test_create_preorder_no_goods(db_session, monkeypatch):
    # Убрать позиции → ошибка «нет товаров»
    await db_session.execute(
        text("DELETE FROM assembly_request_items WHERE assembly_request_id = :id"),
        {"id": ASSEMBLY_ID},
    )
    await db_session.commit()
    await _patch_client(monkeypatch, FakeClient())
    with pytest.raises(WbSupplyError, match="нет товаров"):
        await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)


@pytest.mark.asyncio
async def test_update_preorder_goods_happy(db_session, monkeypatch):
    # Сначала создать преордер, затем дослать наполнение — без ошибок WB.
    client = FakeClient()
    await _patch_client(monkeypatch, client)
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)

    link = await wb_supply_service.update_preorder_goods(db_session, PROJECT_ID, ASSEMBLY_ID)

    assert link.sync_status != WbSupplySyncStatus.ERROR.value
    assert link.last_error is None


@pytest.mark.asyncio
async def test_update_preorder_goods_wb_rejects(db_session, monkeypatch):
    # WB отклоняет товар (мелкий в монопаллету) → WbSupplyError с текстом «отклонил».
    await _patch_client(monkeypatch, FakeClient())
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)

    await _patch_client(monkeypatch, FakeClient(goods_error=True))
    with pytest.raises(WbSupplyError, match="отклонил"):
        await wb_supply_service.update_preorder_goods(db_session, PROJECT_ID, ASSEMBLY_ID)


@pytest.mark.asyncio
async def test_session_expired_marks_and_raises(db_session, monkeypatch):
    client = FakeClient(expire=True)
    await _patch_client(monkeypatch, client)
    called = {}

    async def fake_mark(db, project_id):
        called["marked"] = project_id

    monkeypatch.setattr(integrations_service, "mark_wb_portal_expired", fake_mark)

    with pytest.raises(WbSupplyError, match="истекла"):
        await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert called.get("marked") == PROJECT_ID


@pytest.mark.asyncio
async def test_portal_error_sets_error_status(db_session, monkeypatch):
    await _patch_client(monkeypatch, FakeClient(portal_error=True))
    with pytest.raises(WbSupplyError, match="отклонил"):
        await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    link = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.sync_status == WbSupplySyncStatus.ERROR.value
    assert link.last_error


@pytest.mark.asyncio
async def test_project_isolation(db_session, monkeypatch):
    await _patch_client(monkeypatch, FakeClient())
    with pytest.raises(WbSupplyError, match="не найдена"):
        await wb_supply_service.create_preorder(db_session, OTHER_PROJECT_ID, ASSEMBLY_ID)


@pytest.mark.asyncio
async def test_sync_supply_matches_preorder(db_session, monkeypatch):
    client = FakeClient(supplies=[{"supplyId": 40699158, "preorders": [52670743]}])
    await _patch_client(monkeypatch, client)
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    link = await wb_supply_service.sync_supply_id(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.supply_id == 40699158
    assert link.sync_status == WbSupplySyncStatus.BOOKED.value


@pytest.mark.asyncio
async def test_push_boxes_and_pass(db_session, monkeypatch):
    client = FakeClient(supplies=[{"supplyId": 40699158, "preorders": [52670743]}])
    await _patch_client(monkeypatch, client)
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    await wb_supply_service.sync_supply_id(db_session, PROJECT_ID, ASSEMBLY_ID)

    # Короба
    await wb_supply_service.save_boxes(
        db_session, PROJECT_ID, ASSEMBLY_ID, [{"boxcode": None, "items": [{"barcode": BC, "quantity": 10}]}]
    )
    link = await wb_supply_service.push_boxes(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.sync_status == WbSupplySyncStatus.BOXED.value
    assert link.boxes[0]["boxcode"] == "WB_0"
    assert client.bind_sent[0]["barcodes"][0]["quantity"] == 10

    # Пропуск
    await wb_supply_service.save_pass(
        db_session,
        PROJECT_ID,
        ASSEMBLY_ID,
        {"driver_first": "Влад", "driver_last": "Вяткин", "car_number": "В815УН37", "car_model": "Газель", "driver_phone": "79001112233", "pallets": 2},
    )
    link = await wb_supply_service.push_pass(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.sync_status == WbSupplySyncStatus.PASSED.value
    assert link.barcode_id == 999
    assert client.trn_saved["first_name"] == "Влад"


@pytest.mark.asyncio
async def test_push_boxes_requires_supply(db_session, monkeypatch):
    await _patch_client(monkeypatch, FakeClient())
    await wb_supply_service.save_boxes(
        db_session, PROJECT_ID, ASSEMBLY_ID, [{"boxcode": None, "items": [{"barcode": BC, "quantity": 10}]}]
    )
    with pytest.raises(WbSupplyError, match="забронируйте"):
        await wb_supply_service.push_boxes(db_session, PROJECT_ID, ASSEMBLY_ID)


# ─── bulk-синк WB-состояний (sync_all_states) ────────────────────────────────


@pytest.mark.asyncio
async def test_sync_all_states_empty_no_wb_call(db_session, monkeypatch):
    # Нет поставок с supply_id и без FBO → в кабинет не ходим, {0,0,0}.
    client = FakeClient()
    await _patch_client(monkeypatch, client)
    res = await wb_supply_service.sync_all_states(db_session, PROJECT_ID)
    assert res == {"checked": 0, "updated": 0, "supplies_seen": 0}
    assert client.details_calls == 0


@pytest.mark.asyncio
async def test_sync_all_states_writes_cabinet_status(db_session, monkeypatch):
    # Связь с supply_id → listSupplies даёт АВТОРИТЕТНЫЙ кабинетный статус.
    client = FakeClient(
        supplies=[{"supplyId": 40699158, "preorders": [52670743], "statusId": 3, "statusName": "Отгрузка разрешена"}],
        supply_status={"statusName": "Отгрузка разрешена", "statusId": 3},
    )
    await _patch_client(monkeypatch, client)
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    await wb_supply_service.sync_supply_id(db_session, PROJECT_ID, ASSEMBLY_ID)

    res = await wb_supply_service.sync_all_states(db_session, PROJECT_ID)
    assert res["checked"] == 1
    assert res["updated"] == 1

    link = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.wb_supply_state == "Отгрузка разрешена"
    assert link.wb_supply_state_id == 3
    assert link.wb_state_synced_at is not None


@pytest.mark.asyncio
async def test_sync_all_states_creates_row_for_adopted_fbo(db_session, monkeypatch):
    # Заявка с забронированной FBO-поставкой без реплей-строки → sync создаёт
    # строку с кабинетным статусом (listSupplies матч по supplyId).
    await _attach_fbo(db_session, "40503730", "IN_PROGRESS")
    client = FakeClient(
        supplies=[{"supplyId": 40503730, "statusId": 1, "statusName": "Запланировано"}],
        supply_status={"statusName": "Запланировано", "statusId": 1},
    )
    await _patch_client(monkeypatch, client)

    res = await wb_supply_service.sync_all_states(db_session, PROJECT_ID)
    assert res["checked"] == 1
    assert res["updated"] == 1

    link = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.supply_id == 40503730
    assert link.wb_supply_state == "Запланировано"


@pytest.mark.asyncio
async def test_sync_all_states_no_change_updates_synced_at(db_session, monkeypatch):
    # Второй прогон с тем же статусом → updated=0, но synced_at обновлён.
    client = FakeClient(
        supplies=[{"supplyId": 40699158, "preorders": [52670743], "statusId": 1, "statusName": "Запланировано"}],
        supply_status={"statusName": "Запланировано", "statusId": 1},
    )
    await _patch_client(monkeypatch, client)
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    await wb_supply_service.sync_supply_id(db_session, PROJECT_ID, ASSEMBLY_ID)
    await wb_supply_service.sync_all_states(db_session, PROJECT_ID)

    first = (await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)).wb_state_synced_at

    res = await wb_supply_service.sync_all_states(db_session, PROJECT_ID)
    assert res["updated"] == 0
    assert res["checked"] == 1

    second = (await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)).wb_state_synced_at
    assert second is not None and second >= first


@pytest.mark.asyncio
async def test_sync_all_states_session_expired(db_session, monkeypatch):
    # WbSessionExpired на supplyDetails bulk-синка → WbSupplyError + пометка EXPIRED.
    client = FakeClient(supplies=[{"supplyId": 40699158, "preorders": [52670743]}])
    await _patch_client(monkeypatch, client)
    await wb_supply_service.create_preorder(db_session, PROJECT_ID, ASSEMBLY_ID)
    await wb_supply_service.sync_supply_id(db_session, PROJECT_ID, ASSEMBLY_ID)

    called = {}

    async def fake_mark(db, project_id):
        called["marked"] = project_id

    monkeypatch.setattr(integrations_service, "mark_wb_portal_expired", fake_mark)
    client.details_expire = True  # supplyDetails бросит WbSessionExpired

    with pytest.raises(WbSupplyError, match="истекла"):
        await wb_supply_service.sync_all_states(db_session, PROJECT_ID)
    assert called.get("marked") == PROJECT_ID


@pytest.mark.asyncio
async def test_get_state_mirrors_assembly_vehicle(db_session, monkeypatch):
    # get_state прокидывает зеркало машины/паллет заявки транзиентными атрибутами.
    await db_session.execute(
        text(
            "UPDATE assembly_requests SET vehicle_info = :vi, vehicle_brand = :vb, "
            "driver_phone = :dp WHERE id = :id"
        ),
        {"vi": "В815УН37", "vb": "Газель", "dp": "79001112233", "id": ASSEMBLY_ID},
    )
    await db_session.commit()
    db_session.expire_all()

    link = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.assembly_vehicle_info == "В815УН37"
    assert link.assembly_vehicle_brand == "Газель"
    assert link.assembly_driver_phone == "79001112233"
    assert link.assembly_pallets_count == 2


# ─── адопция забронированной поставки из FBO ──────────────────────────────────


async def _attach_fbo(db_session, wb_supply_id: str, wb_status: str) -> int:
    """Прицепить FBO-поставку к тест-заявке (сырым INSERT), вернуть её id."""
    await db_session.execute(
        text(
            "INSERT INTO wb_fbo_supplies "
            "(project_id, wb_supply_id, wb_status, name, warehouse_name, created_at_wb, "
            " total_qty, accepted_qty, created_at, updated_at) "
            "VALUES (:pid, :sid, :st, :nm, :wh, NOW(), 0, 0, NOW(), NOW())"
        ),
        {"pid": PROJECT_ID, "sid": wb_supply_id, "st": wb_status, "nm": f"FBW-{wb_supply_id}", "wh": WH_NAME},
    )
    fid = (
        await db_session.execute(
            text("SELECT id FROM wb_fbo_supplies WHERE project_id = :pid AND wb_supply_id = :sid ORDER BY id DESC LIMIT 1"),
            {"pid": PROJECT_ID, "sid": wb_supply_id},
        )
    ).scalar()
    await db_session.execute(
        text("UPDATE assembly_requests SET wb_fbo_supply_id = :fid WHERE id = :id"),
        {"fid": fid, "id": ASSEMBLY_ID},
    )
    await db_session.commit()
    db_session.expire_all()
    return fid


@pytest.mark.asyncio
async def test_get_state_adopts_booked_fbo(db_session, monkeypatch):
    # Реплей не заводили, но есть забронированная FBO-поставка → BOOKED + supply_id.
    # Клиент не патчим → кабинет недоступен → статус из FBO-фолбэка.
    await _attach_fbo(db_session, "40503730", "IN_PROGRESS")
    state = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert state.supply_id == 40503730
    assert state.sync_status == WbSupplySyncStatus.BOOKED.value
    assert state.wb_supply_state == "Разгрузка разрешена"


@pytest.mark.asyncio
async def test_get_state_uses_cabinet_status(db_session, monkeypatch):
    # Кабинет доступен → АВТОРИТЕТНЫЙ статус из supplyDetails (не FBO-метка).
    await _attach_fbo(db_session, "40503730", "ON_DELIVERY")  # FBO бы дал «В пути»
    await _patch_client(monkeypatch, FakeClient(supply_status={"statusName": "Запланировано", "statusId": 1}))
    state = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert state.supply_id == 40503730
    assert state.wb_supply_state == "Запланировано"  # кабинет, а не FBO «В пути»
    assert state.wb_supply_state_id == 1


@pytest.mark.asyncio
async def test_get_state_no_adopt_when_cancelled(db_session, monkeypatch):
    await _attach_fbo(db_session, "40503730", "CANCELLED")
    state = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert state.supply_id is None
    assert state.sync_status == WbSupplySyncStatus.NONE.value


@pytest.mark.asyncio
async def test_get_state_no_adopt_when_no_fbo(db_session, monkeypatch):
    # Без FBO-привязки — прежнее поведение NONE.
    state = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert state.supply_id is None
    assert state.sync_status == WbSupplySyncStatus.NONE.value


@pytest.mark.asyncio
async def test_push_pass_adopts_supply_id_from_fbo(db_session, monkeypatch):
    # Пропуск для «усыновлённой» поставки: строки нет, supply_id берётся из FBO.
    await _attach_fbo(db_session, "40503730", "IN_PROGRESS")
    client = FakeClient()
    await _patch_client(monkeypatch, client)
    await wb_supply_service.save_pass(
        db_session,
        PROJECT_ID,
        ASSEMBLY_ID,
        {"driver_first": "Антон", "driver_last": "Александров", "car_number": "B331YT69"},
    )
    res = await wb_supply_service.push_pass(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert res.supply_id == 40503730
    assert res.sync_status == WbSupplySyncStatus.PASSED.value


@pytest.mark.asyncio
async def test_get_cabinet_boxes_from_adopted_supply(db_session, monkeypatch):
    # Короба тянутся по supply_id, усыновлённому из FBO; сводка агрегируется.
    await _attach_fbo(db_session, "40503730", "IN_PROGRESS")
    await _patch_client(monkeypatch, FakeClient())
    res = await wb_supply_service.get_cabinet_boxes(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert res.total_boxes == 2
    assert res.total_barcodes == 2  # BC1, BC2 (BC1 в двух коробах — уникальных 2)
    assert res.total_units == 55  # 40 + 10 + 5
    assert res.boxes[0].boxcode == "WB_111"
    assert res.boxes[0].items[0].imt_name == "Товар А"


@pytest.mark.asyncio
async def test_get_cabinet_boxes_empty_without_supply(db_session, monkeypatch):
    # Без supply (нет ни реплея, ни FBO) — пустой ответ, в WB не ходим.
    await _patch_client(monkeypatch, FakeClient())
    res = await wb_supply_service.get_cabinet_boxes(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert res.total_boxes == 0
    assert res.boxes == []


@pytest.mark.asyncio
async def test_get_cabinet_pass_from_adopted_supply(db_session, monkeypatch):
    # Пропуск заведён в кабинете → тянем его из trn_details по supply_id из FBO.
    await _attach_fbo(db_session, "40566125", "IN_PROGRESS")
    await _patch_client(monkeypatch, FakeClient())
    p = await wb_supply_service.get_cabinet_pass(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert p.has_pass is True
    assert p.driver_first == "Александр"
    assert p.driver_last == "Рыболов"
    assert p.car_number == "O253PY790"
    assert p.pallets == 7
    assert p.barcode_id == 999
    assert p.barcode_prefix == "WB-GI-"


@pytest.mark.asyncio
async def test_get_cabinet_pass_empty_without_supply(db_session, monkeypatch):
    # Без supply — пустой пропуск, в WB не ходим.
    await _patch_client(monkeypatch, FakeClient())
    p = await wb_supply_service.get_cabinet_pass(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert p.has_pass is False


# ─── sync_pass_from_vehicle (F3: назначение машины → пропуск) ──────────────────


async def _reload_assembly(db):
    return await wb_supply_service._load_assembly(db, PROJECT_ID, ASSEMBLY_ID)


def test_extract_plate_and_name_parsing():
    # Кириллица-номер + ФИО (порядок кабинета: Фамилия Имя …) + телефон.
    s = "В874УА37 Крапива Дмитрий Васильевич 89158491778"
    plate = wb_supply_service._extract_plate(s)
    assert plate == "В874УА37"
    first, last = wb_supply_service._parse_driver_name(s, plate)
    assert last == "Крапива"
    assert first == "Дмитрий"


def test_extract_plate_none_when_absent():
    assert wb_supply_service._extract_plate("просто водитель без номера") is None


@pytest.mark.asyncio
async def test_sync_pass_from_vehicle_mirrors_fields(db_session):
    assembly = await _reload_assembly(db_session)
    assembly.vehicle_info = "В874УА37 Крапива Дмитрий 89158491778"
    assembly.vehicle_brand = "ГАЗ-330"
    assembly.driver_phone = "+7 915 849 17 78"
    await db_session.flush()

    await wb_supply_service.sync_pass_from_vehicle(db_session, PROJECT_ID, assembly)
    await db_session.commit()

    link = await wb_supply_service._get_or_create_link(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.pass_car_number == "В874УА37"
    assert link.pass_car_model == "ГАЗ-330"
    assert link.pass_driver_phone == "+7 915 849 17 78"
    assert link.pass_driver_last == "Крапива"
    assert link.pass_driver_first == "Дмитрий"
    # pallets_count заявки = 2 (фикстура) → проставился в пустой пропуск.
    assert link.pass_pallets == 2


@pytest.mark.asyncio
async def test_sync_pass_preserves_existing_name(db_session):
    # Уже заполненное ФИО не затираем best-effort парсингом.
    link = await wb_supply_service._get_or_create_link(db_session, PROJECT_ID, ASSEMBLY_ID)
    link.pass_driver_first = "Иван"
    link.pass_driver_last = "Иванов"
    link.pass_pallets = 5
    await db_session.commit()

    assembly = await _reload_assembly(db_session)
    assembly.vehicle_info = "О253РУ790 Петров Пётр"
    assembly.vehicle_brand = "КАМАЗ"
    await db_session.flush()
    await wb_supply_service.sync_pass_from_vehicle(db_session, PROJECT_ID, assembly)
    await db_session.commit()

    link = await wb_supply_service._get_or_create_link(db_session, PROJECT_ID, ASSEMBLY_ID)
    # ФИО и pallets сохранены, номер/марка — перезаписаны машиной.
    assert link.pass_driver_first == "Иван"
    assert link.pass_driver_last == "Иванов"
    assert link.pass_pallets == 5
    assert link.pass_car_number == "О253РУ790"
    assert link.pass_car_model == "КАМАЗ"


@pytest.mark.asyncio
async def test_sync_pass_noop_without_vehicle(db_session):
    assembly = await _reload_assembly(db_session)
    # Без данных машины — строку пропуска не трогаем/не плодим.
    await wb_supply_service.sync_pass_from_vehicle(db_session, PROJECT_ID, assembly)
    await db_session.commit()
    link = await wb_supply_service._get_or_create_link(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert link.pass_car_number is None
    assert link.pass_driver_phone is None


# ─── supplyDate / rejectReason из карточки поставки кабинета ───────────────────


def test_parse_wb_dt_keeps_calendar_date():
    # +03:00 не конвертируем в UTC — иначе слот 23 июля уехал бы на 22-е.
    dt = wb_supply_service._parse_wb_dt("2026-07-23T00:00:00+03:00")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour) == (2026, 7, 23, 0)
    assert dt.tzinfo is None


def test_parse_wb_dt_bad_input():
    assert wb_supply_service._parse_wb_dt(None) is None
    assert wb_supply_service._parse_wb_dt("") is None
    assert wb_supply_service._parse_wb_dt("не дата") is None


@pytest.mark.asyncio
async def test_get_state_exposes_supply_date_and_reject_reason(db_session, monkeypatch):
    await _attach_fbo(db_session, "40566125", "IN_PROGRESS")
    client = FakeClient(
        supply_status={
            "statusName": "Запланировано",
            "statusId": 1,
            "supplyDate": "2026-07-23T00:00:00+03:00",
            "rejectReason": "Не заполнены ШК коробов.\nНе заполнен пропуск.",
        }
    )
    await _patch_client(monkeypatch, client)

    st = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert st.supply_date is not None
    assert (st.supply_date.month, st.supply_date.day) == (7, 23)
    assert "Не заполнен пропуск" in (st.reject_reason or "")


@pytest.mark.asyncio
async def test_get_state_reject_reason_empty_string_is_none(db_session, monkeypatch):
    await _attach_fbo(db_session, "40566125", "IN_PROGRESS")
    client = FakeClient(supply_status={"statusName": "Принято", "statusId": 5, "rejectReason": "   "})
    await _patch_client(monkeypatch, client)
    st = await wb_supply_service.get_state(db_session, PROJECT_ID, ASSEMBLY_ID)
    assert st.reject_reason is None
    assert st.supply_date is None
