# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты ядра migfull-сервиса: опись, резолв склада, cookie-сессия.

Плюс регрессия основного пути «заявка на отгрузку ИЗ СБОРКИ» (портал замокан):
у сборки и перемещения общий контур ``_build_draft`` / ``_send``, и сборочная
сторона обязана остаться дословно прежней — шапка, опись, audit, связь.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.integrations.migfull_portal_client import MigfullPortalAuthError
from backend.models import (
    FulfillmentRequest,
    FulfillmentStock,
    IntegrationKey,
    MigfullShipmentOrder,
    MigfullShipmentStatus,
    Nomenclature,
    Warehouse,
)
from backend.models.assembly import AssemblyRequest, AssemblyRequestItem, AssemblyStatus
from backend.models.fulfillment import FfRequestKind
from backend.schemas.migfull_portal import MigfullSendRequest
from backend.services import migfull_portal_service
from backend.services.migfull_portal_service import (
    MigfullPortalServiceError,
    _destinations_from_mirror_rows,
    _with_portal_session,
    classify_opis_lines,
    resolve_destination,
)
from backend.utils.crypto import encrypt
from tests.test_migfull_portal_inbound import EAN, GUID, ITF
from tests.test_migfull_portal_transfer import FakeShipmentClient, _use_fake_shipment_client

# Справочник как из read-API: ВСЕ склады помечены delivery_type='direct' (по историческим
# отгрузкам), хотя портал отдаёт их и для pickup с тем же numeric id.
_DESTS = [
    {"id": 110, "name": "ВБ | Тула Алексин", "delivery_type": "direct", "marketplace_id": 2},
    {"id": 90, "name": "ВБ | МО Электросталь", "delivery_type": "direct", "marketplace_id": 2},
    {"id": 156, "name": "ВБ | Екатеринбург Перспективный", "delivery_type": "direct", "marketplace_id": 2},
    {"id": 114, "name": "ВБ | Краснодар", "delivery_type": "direct", "marketplace_id": 2},
]


def test_box_line_exact_multiple():
    lines, warnings = classify_opis_lines(
        {"2049985828273": 40},
        box_for_piece={"2049985828273": ("12049985828273", 5, "ELKA короб 5 шт")},
        name_for_barcode={"2049985828273": "ELKA"},
    )
    assert warnings == []
    assert len(lines) == 1
    line = lines[0]
    assert line.is_box is True
    assert line.barcode == "12049985828273"  # ШК короба (ITF14)
    assert line.quantity == 8  # 40 / 5 коробов
    assert line.units_per_box == 5
    assert line.pieces == 40


def test_non_divisible_falls_back_to_loose_with_warning():
    lines, warnings = classify_opis_lines(
        {"2049985828273": 42},  # 42 не кратно 5
        box_for_piece={"2049985828273": ("12049985828273", 5, "ELKA короб 5 шт")},
        name_for_barcode={"2049985828273": "ELKA"},
    )
    assert len(warnings) == 1
    assert "не кратно" in warnings[0]
    assert len(lines) == 1
    line = lines[0]
    assert line.is_box is False
    assert line.barcode == "2049985828273"  # россыпь EAN13
    assert line.quantity == 42  # штуки
    assert line.pieces == 42


def test_loose_line_when_no_box_mapping():
    lines, warnings = classify_opis_lines(
        {"2049985828273": 7},
        box_for_piece={},
        name_for_barcode={"2049985828273": "палас"},
    )
    assert warnings == []
    assert lines[0].is_box is False
    assert lines[0].quantity == 7
    assert lines[0].name == "палас"


def test_units_per_box_one_treated_as_loose():
    # короб «по 1 шт» (upb<=1) — не короб, отправляем россыпью без warning
    lines, warnings = classify_opis_lines(
        {"111": 3},
        box_for_piece={"111": ("999", 1, "x")},
        name_for_barcode={"111": "x"},
    )
    assert warnings == []
    assert lines[0].is_box is False
    assert lines[0].barcode == "111"


def test_zero_and_empty_skipped():
    lines, warnings = classify_opis_lines(
        {"111": 0},
        box_for_piece={},
        name_for_barcode={"111": "x"},
    )
    assert lines == []
    assert warnings == []


def test_lines_sorted_by_name():
    lines, _ = classify_opis_lines(
        {"b": 1, "a": 1},
        box_for_piece={},
        name_for_barcode={"b": "Zebra", "a": "Alpha"},
    )
    assert [line.name for line in lines] == ["Alpha", "Zebra"]


# ─── Резолв склада назначения (наш WB-склад → migfull numeric id) ─────────────


def test_resolve_by_name_returns_numeric_id():
    m = resolve_destination("Тула", None, _DESTS)
    assert m is not None and m["id"] == 110


def test_resolve_delivery_type_none_ignores_index_type():
    # Заявка pickup, но индекс типизирован 'direct' — при delivery_type=None матч работает.
    assert resolve_destination("Электросталь", None, _DESTS)["id"] == 90


def test_resolve_pickup_filter_would_drop_direct_index():
    # Демонстрация БАГА, ради которого резолвим по имени: явный pickup отсекает 'direct'-склады.
    assert resolve_destination("Тула", "pickup", _DESTS) is None


def test_resolve_no_match_returns_none():
    assert resolve_destination("Владивосток", None, _DESTS) is None


def test_resolve_disambiguates_unique_top():
    assert resolve_destination("Екатеринбург Перспективная 14", None, _DESTS)["id"] == 156


def test_resolve_empty_name_returns_none():
    assert resolve_destination(None, None, _DESTS) is None


# ─── Индекс складов назначения из зеркала БД (raw отгрузок) ───────────────────


def test_mirror_rows_dedup_by_id_first_wins_and_bad_rows_skipped():
    rows = [
        (156, "ВБ | Екатеринбург Перспективный", "direct", 2),
        (156, "ВБ | Екатеринбург Перспективный (старое имя)", "direct", 2),  # дубль id: свежая строка первая
        (None, "без numeric id", "direct", 2),
        (110, None, "direct", 2),  # без имени
        (90, "ВБ | МО Электросталь", None, None),
    ]
    out = _destinations_from_mirror_rows(rows)
    assert {d["id"] for d in out} == {156, 90}
    ekb = next(d for d in out if d["id"] == 156)
    assert ekb == {"id": 156, "name": "ВБ | Екатеринбург Перспективный", "delivery_type": "direct", "marketplace_id": 2}


def test_mirror_index_resolves_warehouse_without_read_api():
    # Прод-кейс ASM-804: read-API индекс не собрался (таймаут /shipments → негативный
    # кэш) → «склад не распознан». Теперь индекс приходит из зеркала БД и резолвит.
    dests = _destinations_from_mirror_rows([(156, "ВБ | Екатеринбург Перспективный", "direct", 2)])
    assert resolve_destination("Екатеринбург - Перспективная 14", None, dests)["id"] == 156


# ─── Cookie-сессия портала: один логин на батч, перелогин при протухании ──────


class _StubPortalClient:
    """Минимальный интерфейс MigfullPortalClient для _with_portal_session (без сети)."""

    def __init__(self):
        self.project_id, self.host, self.login = 1, "https://plusvb.migfull.app", "a@b.ru"
        self.logins = 0
        self.restored: dict | None = None

    async def authenticate(self):
        self.logins += 1

    def restore_session(self, state):
        self.restored = state
        return True

    def export_session(self):
        return {"cookies": {"migfull_session": f"s{self.logins}"}}


@pytest.fixture
def _fresh_session_cache(monkeypatch):
    monkeypatch.setattr(migfull_portal_service, "_portal_sessions", {})


async def test_batch_of_sends_logs_in_once(_fresh_session_cache):
    # Батч из 9 заявок: раньше 9 логинов → Filament-троттлинг (~5/мин) валил 6-ю+
    # («вход не подтверждён»). Теперь логин один, дальше — кэш cookie-сессии.
    results = []
    for _ in range(9):
        client = _StubPortalClient()  # каждый send создаёт нового клиента
        cached = migfull_portal_service._portal_sessions.copy()

        async def op():
            return "ok"

        results.append(await _with_portal_session(client, op))
        if cached:  # со 2-й отправки сессия должна прийти из кэша, без логина
            assert client.logins == 0 and client.restored is not None
    assert results == ["ok"] * 9
    assert len(migfull_portal_service._portal_sessions) == 1


async def test_expired_session_relogins_once_and_retries(_fresh_session_cache):
    key = (1, "https://plusvb.migfull.app", "a@b.ru")
    migfull_portal_service._portal_sessions[key] = (
        migfull_portal_service.utcnow().timestamp(),
        {"cookies": {"migfull_session": "stale"}},
    )
    client = _StubPortalClient()
    attempts = {"n": 0}

    async def op():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise MigfullPortalAuthError()  # сессия из кэша протухла на портале
        return "created"

    assert await _with_portal_session(client, op) == "created"
    assert client.logins == 1  # ровно один перелогин
    assert attempts["n"] == 2
    # Свежая сессия перезаписала протухшую в кэше
    assert migfull_portal_service._portal_sessions[key][1] == {"cookies": {"migfull_session": "s1"}}


async def test_stale_cache_ttl_forces_login(_fresh_session_cache):
    key = (1, "https://plusvb.migfull.app", "a@b.ru")
    old_ts = migfull_portal_service.utcnow().timestamp() - migfull_portal_service._SESSION_TTL_SEC - 1
    migfull_portal_service._portal_sessions[key] = (old_ts, {"cookies": {"migfull_session": "old"}})
    client = _StubPortalClient()

    async def op():
        return "ok"

    assert await _with_portal_session(client, op) == "ok"
    assert client.logins == 1 and client.restored is None  # TTL истёк — кэш не использован


# ─── Регрессия сборочной стороны (общий контур с перемещением) ────────────────


@pytest_asyncio.fixture
async def asm_env(db_session, project):
    """Склад «Натали» + портальный ключ + готовая сборка на WB-склад (40 шт, короб 5)."""
    natali = Warehouse(project_id=project.id, name="натали", warehouse_type="FULFILLMENT")
    db_session.add(natali)
    await db_session.flush()

    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service="migfull_portal",
            encrypted_key=encrypt("portal-pass"),
            config={"login": "test@example.com"},
            warehouse_id=natali.id,
            is_active=True,
        )
    )
    nom = Nomenclature(project_id=project.id, barcode=EAN, article_seller="ELKA")
    db_session.add(nom)
    await db_session.flush()

    assembly = AssemblyRequest(
        project_id=project.id,
        warehouse_id=natali.id,
        number=f"ASM-{uuid.uuid4().hex[:5]}",
        status=AssemblyStatus.READY.value,
        wb_warehouse_name_manual="Екатеринбург Перспективный",
        delivery_date=date(2026, 8, 20),
        pallets_count=1,
        pallet_weight_kg=Decimal("10.00"),
    )
    db_session.add(assembly)
    await db_session.flush()
    db_session.add(
        AssemblyRequestItem(
            project_id=project.id,
            assembly_request_id=assembly.id,
            nomenclature_id=nom.id,
            barcode=EAN,
            quantity=40,
        )
    )
    db_session.add(
        FulfillmentStock(
            project_id=project.id,
            warehouse_id=natali.id,
            provider="migfull",
            barcode=ITF,
            name="ELKA короб 5 шт.",
            base_barcode=EAN,
            units_per_box=5,
        )
    )
    await db_session.commit()

    from types import SimpleNamespace

    return SimpleNamespace(project_id=project.id, warehouse=natali, assembly=assembly)


async def test_assembly_draft_prefill_and_opis(db_session, asm_env):
    draft = await migfull_portal_service.build_draft(db_session, asm_env.project_id, asm_env.assembly.id)

    assert draft.eligible is True
    assert draft.already_sent is False
    assert draft.prefill.assembly_number == asm_env.assembly.number
    assert draft.prefill.transfer_number is None  # источник — сборка, не переезд
    assert draft.prefill.shipment_date == date(2026, 8, 20)
    assert draft.prefill.filter_delivery_type == "pickup"
    assert draft.prefill.wb_warehouse_name == "Екатеринбург Перспективный"
    assert asm_env.assembly.number in (draft.prefill.notes or "")
    # 40 шт коробом по 5 → 8 коробов ITF14
    assert [(ln.barcode, ln.quantity, ln.is_box) for ln in draft.opis_lines] == [(ITF, 8, True)]
    assert draft.total_boxes == 8 and draft.total_pieces == 40
    # WB-склад в справочнике Натали не нашёлся (зеркала отгрузок нет) — предупреждаем.
    assert any("не распознан" in w for w in draft.warnings)


async def test_assembly_send_header_audit_and_link(db_session, asm_env, monkeypatch):
    fake = FakeShipmentClient()
    _use_fake_shipment_client(monkeypatch, fake)

    res = await migfull_portal_service.send_shipment(
        db_session, asm_env.project_id, asm_env.assembly.id, MigfullSendRequest()
    )
    assert res.ok is True
    header = fake.created_headers[0]
    assert header["marketplace_id"] == "2"
    assert header["shipment_type"] == "fbo"
    assert header["shipment_date"] == "2026-08-20"
    assert header["filter_delivery_type"] == "pickup"
    assert fake.uploads[0][1] == f"opis_{asm_env.assembly.number}.xlsx"

    order = (
        await db_session.execute(
            select(MigfullShipmentOrder).where(MigfullShipmentOrder.project_id == asm_env.project_id)
        )
    ).scalar_one()
    assert order.status == MigfullShipmentStatus.SENT
    assert order.assembly_request_id == asm_env.assembly.id
    assert order.stock_transfer_id is None

    ff = (
        await db_session.execute(
            select(FulfillmentRequest).where(
                FulfillmentRequest.project_id == asm_env.project_id,
                FulfillmentRequest.external_id == GUID,
            )
        )
    ).scalar_one()
    assert ff.assembly_request_id == asm_env.assembly.id
    assert ff.stock_transfer_id is None
    assert ff.kind == FfRequestKind.ASSEMBLY.value
    assert ff.warehouse_id == asm_env.warehouse.id
    assert ff.dest_warehouse == "Екатеринбург Перспективный"
    assert ff.total_qty == 40


async def test_assembly_resend_blocked_and_cancelled_rejected(db_session, asm_env, monkeypatch):
    fake = FakeShipmentClient()
    _use_fake_shipment_client(monkeypatch, fake)
    await migfull_portal_service.send_shipment(
        db_session, asm_env.project_id, asm_env.assembly.id, MigfullSendRequest()
    )
    with pytest.raises(MigfullPortalServiceError) as exc:
        await migfull_portal_service.send_shipment(
            db_session, asm_env.project_id, asm_env.assembly.id, MigfullSendRequest()
        )
    assert exc.value.status_code == 409
    assert "сборки" in str(exc.value)
    assert len(fake.created_headers) == 1

    asm_env.assembly.status = AssemblyStatus.CANCELLED.value
    await db_session.commit()
    with pytest.raises(MigfullPortalServiceError) as exc:
        await migfull_portal_service.send_shipment(
            db_session, asm_env.project_id, asm_env.assembly.id, MigfullSendRequest(force_resend=True)
        )
    assert exc.value.status_code == 400
    assert "отменённую сборку" in str(exc.value)


async def test_assembly_missing_is_404(db_session, asm_env):
    with pytest.raises(MigfullPortalServiceError) as exc:
        await migfull_portal_service.build_draft(db_session, asm_env.project_id, 987654321)
    assert exc.value.status_code == 404
