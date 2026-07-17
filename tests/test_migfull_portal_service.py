# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты ядра migfull-сервиса: опись, резолв склада, cookie-сессия (без БД/сети)."""

import pytest

from backend.integrations.migfull_portal_client import MigfullPortalAuthError
from backend.services import migfull_portal_service
from backend.services.migfull_portal_service import (
    _destinations_from_mirror_rows,
    _with_portal_session,
    classify_opis_lines,
    resolve_destination,
)

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
