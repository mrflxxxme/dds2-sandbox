"""
Tests for backend/services/funnel/ads_manager.py — «Управление рекламой».

Covers compute_budget_gap (чистый расчёт доливки до полуночи) и
get_budget_gaps / list_ad_campaigns на мокнутой БД.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.services.funnel.ads_manager import compute_budget_gap

PROJECT_ID = 1


# ─── compute_budget_gap ──────────────────────────────────────────────────────


def test_gap_midday_stop():
    """Бюджет кончился в 12:00, потрачено 1200₽ → 100₽/ч, долить 1200₽ на 12 ч."""
    g = compute_budget_gap(spend_today=1200.0, ran_out_hour=12.0, now_hour=15.0)
    assert g["burn_rate"] == 100.0
    assert g["needed_till_midnight"] == 1200.0
    assert g["remaining_hours"] == 12.0


def test_gap_evening_stop_floored_to_min_topup():
    """Остановка 18:30: raw-нужда 275 ₽, но WB минимум 1000 ₽ → показываем 1000."""
    g = compute_budget_gap(spend_today=925.0, ran_out_hour=18.5, now_hour=20.0)
    assert g["burn_rate"] == 50.0
    assert g["raw_needed"] == 275.0
    assert g["needed_till_midnight"] == 1000.0  # нельзя пополнять меньше 1000 ₽
    assert g["min_topup"] == 1000.0


def test_gap_unknown_stop_uses_now_min_topup():
    """Час остановки неизвестен → считаем по текущему часу; raw 600 < 1000 → 1000."""
    g = compute_budget_gap(spend_today=600.0, ran_out_hour=None, now_hour=12.0)
    assert g["burn_rate"] == 50.0
    assert g["raw_needed"] == 600.0
    assert g["needed_till_midnight"] == 1000.0


def test_gap_needed_above_min_kept():
    """Если расчётная нужда > 1000 — оставляем её (округляем до рубля)."""
    g = compute_budget_gap(spend_today=1200.0, ran_out_hour=12.0, now_hour=15.0)
    assert g["raw_needed"] == 1200.0
    assert g["needed_till_midnight"] == 1200.0


def test_gap_zero_spend_no_division_error():
    g = compute_budget_gap(spend_today=0.0, ran_out_hour=0.0, now_hour=1.0)
    assert g["burn_rate"] == 0.0
    assert g["needed_till_midnight"] == 0.0


def test_gap_stop_near_midnight_no_negative():
    """Остановка «после 24:00» невозможна, но защита от отрицательного остатка есть."""
    g = compute_budget_gap(spend_today=2400.0, ran_out_hour=24.0, now_hour=23.9)
    assert g["remaining_hours"] == 0.0
    assert g["needed_till_midnight"] == 0.0


# ─── get_budget_gaps (мокнутая БД) ───────────────────────────────────────────


def _campaign(campaign_id: int, budget: float = 0, status: int = 9, nm_ids: list | None = None) -> MagicMock:
    c = MagicMock()
    c.campaign_id = campaign_id
    c.name = f"Кампания {campaign_id}"
    c.campaign_type = "unified"
    c.status = status
    c.budget = Decimal(str(budget))
    c.nm_ids = nm_ids or [111]
    return c


def _event(campaign_id: int, new_value: str, created_at: datetime) -> MagicMock:
    e = MagicMock()
    e.campaign_id = campaign_id
    e.event_type = "budget_change"
    e.new_value = new_value
    e.created_at = created_at
    return e


def _db_seq(*results) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(results))
    return db


def _scalars_result(rows: list) -> MagicMock:
    m = MagicMock()
    m.scalars.return_value.all.return_value = rows
    return m


def _rows_result(rows: list) -> MagicMock:
    m = MagicMock()
    m.all.return_value = rows
    return m


@pytest.mark.asyncio
async def test_budget_gaps_uses_last_zero_event():
    """Кампания с событием бюджет→0 попадает в список с часом остановки."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(101)
    spend_row = MagicMock(campaign_id=101, spend=Decimal("800"))
    # событие в 09:00 UTC = 12:00 МСК
    ev = _event(101, "0", datetime(2026, 7, 3, 9, 0, 0))
    nm_meta = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    db = _db_seq(_scalars_result([camp]), _rows_result([spend_row]), _scalars_result([ev]), _rows_result([nm_meta]))

    rows = await get_budget_gaps(db, PROJECT_ID)

    assert len(rows) == 1
    assert rows[0]["campaign_id"] == 101
    assert rows[0]["spend_today"] == 800.0
    assert rows[0]["ran_out_at"] is not None
    assert "needed_till_midnight" in rows[0]
    assert rows[0]["brands"] == ["НУ-НУ"]
    assert rows[0]["subjects"] == ["Ковры"]


@pytest.mark.asyncio
async def test_budget_gaps_topup_after_zero_excludes():
    """Если после нуля было пополнение (>0), кампания не считается остановленной."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(102)
    spend_row = MagicMock(campaign_id=102, spend=Decimal("500"))
    events = [
        _event(102, "0", datetime(2026, 7, 3, 6, 0, 0)),
        _event(102, "1000", datetime(2026, 7, 3, 7, 0, 0)),
    ]
    nm_meta = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    db = _db_seq(_scalars_result([camp]), _rows_result([spend_row]), _scalars_result(events), _rows_result([nm_meta]))

    rows = await get_budget_gaps(db, PROJECT_ID)

    # бюджет сейчас 0 (модель), но события говорят «пополнена после нуля» → час неизвестен (None), строка остаётся
    assert len(rows) == 1
    assert rows[0]["ran_out_at"] is None


@pytest.mark.asyncio
async def test_budget_gaps_no_spend_today_excluded():
    """Кампания без расхода сегодня — не «нехватка бюджета» (она вообще не крутилась)."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(103)
    nm_meta = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    db = _db_seq(_scalars_result([camp]), _rows_result([]), _scalars_result([]), _rows_result([nm_meta]))

    rows = await get_budget_gaps(db, PROJECT_ID)
    assert rows == []


@pytest.mark.asyncio
async def test_list_campaigns_sorted_active_first():
    """Список кампаний: активные с расходом — первыми, метрики за период посчитаны."""
    from backend.services.funnel.ads_manager import list_ad_campaigns

    paused = _campaign(201, budget=100, status=11)
    active = _campaign(202, budget=500, status=9)
    period_row = MagicMock(campaign_id=202, spend=Decimal("2100"), views=10000, clicks=250)
    today_row = MagicMock(campaign_id=202, today=Decimal("300"))
    yest_row = MagicMock(nm_id=111, rev=Decimal("100000"))  # выручка вчера по товару 111
    nm_meta_row = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    active.nm_ids = [111]
    # Обе кампании: у active budget=500 (>0), у paused status=11 → недобор не считается,
    # _budget_gap_today_map не ходит в БД, лишнего db.execute нет.
    db = _db_seq(
        _scalars_result([paused, active]),
        _rows_result([period_row]),
        _rows_result([today_row]),
        _rows_result([yest_row]),
        _rows_result([]),  # расход кампаний вчера (yest_spend) — для ДРР за вчера
        _rows_result([nm_meta_row]),
    )

    rows = await list_ad_campaigns(db, PROJECT_ID, date_from="2026-06-13", date_to="2026-06-19")

    assert [r["campaign_id"] for r in rows] == [202, 201]
    assert rows[0]["spend_today"] == 300.0
    assert rows[0]["spend_period"] == 2100.0
    assert rows[0]["clicks_period"] == 250
    assert rows[0]["ctr"] == 2.5  # 250/10000
    assert rows[0]["cpc"] == 8.4  # 2100/250
    assert rows[0]["spend_per_hour"] == 12.5  # 2100 / (7 дней × 24 ч)
    assert rows[0]["brands"] == ["НУ-НУ"]
    assert rows[0]["subjects"] == ["Ковры"]
    assert rows[0]["status_label"] == "Активна"
    assert rows[1]["status_label"] == "Пауза"
    # Новые поля: выручка вчера (для «ДРР план») и недобор бюджета (0 — бюджет не исчерпан)
    assert rows[0]["rev_yesterday"] == 100000.0
    assert rows[0]["budget_gap"] == 0.0


@pytest.mark.asyncio
async def test_budget_gap_today_map_only_exhausted():
    """Недобор считаем только активным кампаниям с исчерпанным бюджетом и расходом сегодня."""
    from backend.services.funnel.ads_manager import _budget_gap_today_map

    exhausted = _campaign(301, budget=0, status=9)      # исчерпан, крутился
    has_budget = _campaign(302, budget=500, status=9)   # бюджет есть → не считаем
    paused = _campaign(303, budget=0, status=11)        # не активна → не считаем
    ev = _event(301, "0", datetime(2026, 7, 3, 9, 0, 0))  # 09:00 UTC = 12:00 МСК
    db = _db_seq(_scalars_result([ev]))
    today_map = {301: 1200.0, 302: 400.0, 303: 100.0}

    gaps = await _budget_gap_today_map(db, PROJECT_ID, [exhausted, has_budget, paused], today_map)

    assert set(gaps) == {301}          # только исчерпанная активная с расходом
    # стоп в 12:00: burn=1200/12=100 ₽/ч, до полуночи 12 ч → 1200 ₽
    assert gaps[301] == 1200.0


@pytest.mark.asyncio
async def test_budget_gap_today_map_none_qualify_no_db():
    """Нет исчерпанных кампаний — пустая карта, в БД не ходим."""
    from backend.services.funnel.ads_manager import _budget_gap_today_map

    db = AsyncMock()
    gaps = await _budget_gap_today_map(db, PROJECT_ID, [_campaign(401, budget=500, status=9)], {401: 100.0})

    assert gaps == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_hourly_spend_reconstructs_from_budget_deltas():
    """Расход по часам = убывание остатка бюджета между снимками; пополнение (рост) пропускаем."""
    from backend.services.funnel.ads_manager import get_hourly_spend

    def ev(old: str, new: str, dt: datetime) -> MagicMock:
        e = MagicMock()
        e.event_type = "budget_change"; e.old_value = old; e.new_value = new; e.created_at = dt
        return e

    camp = _campaign(501)
    events = [
        ev("10000", "9500", datetime(2026, 7, 3, 9, 0, 0)),    # 12:00 МСК, −500
        ev("9500", "9000", datetime(2026, 7, 3, 9, 30, 0)),    # 12:30 МСК, −500
        ev("9000", "8700", datetime(2026, 7, 3, 10, 0, 0)),    # 13:00 МСК, −300
        ev("8700", "10000", datetime(2026, 7, 3, 10, 30, 0)),  # пополнение (+1300) — пропускаем
    ]
    db = _db_seq(_scalars_result([camp]), _scalars_result(events))

    res = await get_hourly_spend(db, PROJECT_ID, 501, date="2026-07-03")

    by_hour = {r["hour"]: r["spend"] for r in res["hours"]}
    assert by_hour[12] == 1000.0  # 500 + 500 в 12:00 и 12:30 МСК
    assert by_hour[13] == 300.0
    assert res["total"] == 1300.0  # пополнение не учтено
    assert len(res["hours"]) == 24 and res["date"] == "2026-07-03"


def test_sanitize_autopay_clamps():
    """Настройки автопополнения нормализуются: час 0-23, порог 0-100, сумма ≥ 0."""
    from backend.services.funnel.ads_manager import _sanitize_autopay

    s = _sanitize_autopay({"enabled": True, "amount": -5, "hour": 25, "threshold_pct": 150})
    assert s == {"enabled": True, "mode": "to_target", "amount": 0.0, "hour": 23, "threshold_pct": 100,
                 "low_balance_threshold": 1000.0, "topup_amount": 1000.0, "daily_cap": 1}
    d = _sanitize_autopay({})
    assert d == {"enabled": False, "mode": "to_target", "amount": 0.0, "hour": 9, "threshold_pct": 50,
                 "low_balance_threshold": 1000.0, "topup_amount": 1000.0, "daily_cap": 1}
    # Неизвестный режим откатывается к to_target; low_balance-поля нормализуются
    lb = _sanitize_autopay({"enabled": True, "mode": "wat", "low_balance_threshold": -1, "topup_amount": 1200, "daily_cap": -3})
    assert lb["mode"] == "to_target" and lb["low_balance_threshold"] == 0.0 and lb["topup_amount"] == 1200.0
    assert lb["daily_cap"] == 0  # отрицательный клампится к 0 (без ограничения)


# ─── compute_autopay_decision ────────────────────────────────────────────────


def _decision(**overrides):
    from backend.services.funnel.ads_manager import compute_autopay_decision

    kwargs = {
        "setting": {"enabled": True, "amount": 3000.0, "hour": 9, "threshold_pct": 50},
        "budget": 500.0,
        "spend_day": 2000.0,
        "now_hour_msk": 9,
        "already_topped_today": False,
        "pending_unknown": False,
    }
    kwargs.update(overrides)
    return compute_autopay_decision(**kwargs)


def test_autopay_deposit_tops_up_to_x():
    """Бюджет 500, X=3000 → пополнить 2500 (кратно 50)."""
    d = _decision()
    assert d == {"action": "deposit", "sum": 2500, "reason": "ok"}


def test_autopay_min_1000_and_round50():
    """Недобор 300 → минимум 1000; недобор 1230 → округление вверх до 1250."""
    d = _decision(budget=2700.0)
    assert d["action"] == "deposit" and d["sum"] == 1000
    d = _decision(budget=1770.0)
    assert d["action"] == "deposit" and d["sum"] == 1250


def test_autopay_skips():
    """Скипы: выключено, не тот час, уже пополняли, неизвестный исход, бюджет полон."""
    assert _decision(setting={"enabled": False, "amount": 3000, "hour": 9, "threshold_pct": 0})["reason"] == "disabled"
    assert _decision(now_hour_msk=10)["reason"] == "not_time"
    assert _decision(already_topped_today=True)["reason"] == "already_today"
    assert _decision(pending_unknown=True)["reason"] == "pending_unknown"
    assert _decision(budget=3000.0)["reason"] == "budget_full"
    assert _decision(budget=5000.0)["reason"] == "budget_full"


def test_autopay_threshold():
    """Порог 50% от X=3000 → 1500: открут 1499 — скип, 1500 — пополняем. Порог 0 — всегда."""
    assert _decision(spend_day=1499.0)["reason"] == "below_threshold"
    assert _decision(spend_day=1500.0)["action"] == "deposit"
    d = _decision(spend_day=0.0, setting={"enabled": True, "amount": 3000, "hour": 9, "threshold_pct": 0})
    assert d["action"] == "deposit"


def test_autopay_zero_amount_disabled():
    d = _decision(setting={"enabled": True, "amount": 0, "hour": 9, "threshold_pct": 0})
    assert d["reason"] == "disabled"


# ─── Режим low_balance («как на ВБ»): долив по остатку, любой час, повторяемо ──


def _lb(**setting):
    base = {"enabled": True, "mode": "low_balance", "low_balance_threshold": 1000, "topup_amount": 1000}
    base.update(setting)
    return base


def test_autopay_low_balance_tops_up_when_below():
    """Остаток < порога → долить topup; час/«уже пополняли сегодня» игнорируются в этом режиме."""
    d = _decision(setting=_lb(), budget=800.0, now_hour_msk=15, already_topped_today=True)
    assert d == {"action": "deposit", "sum": 1000, "reason": "ok"}


def test_autopay_low_balance_skips_when_above():
    d = _decision(setting=_lb(), budget=1200.0)
    assert d["reason"] == "above_threshold"


def test_autopay_low_balance_pending_unknown_skips():
    """Неизвестный исход прошлой попытки — не рискуем двойным списанием."""
    d = _decision(setting=_lb(), budget=100.0, pending_unknown=True)
    assert d["reason"] == "pending_unknown"


def test_autopay_low_balance_min_topup_and_round50():
    d = _decision(setting=_lb(topup_amount=300), budget=100.0)
    assert d["sum"] == 1000  # минимум 1000
    d = _decision(setting=_lb(topup_amount=1230), budget=100.0)
    assert d["sum"] == 1250  # округление вверх до 50


def test_autopay_low_balance_zero_topup_disabled():
    d = _decision(setting=_lb(topup_amount=0), budget=100.0)
    assert d["reason"] == "disabled"


def test_autopay_low_balance_daily_cap():
    """«Не чаще N раз в день»: при достижении cap — скип; 0 = без ограничения."""
    # cap=1: одно пополнение уже было сегодня → скип
    d = _decision(setting=_lb(daily_cap=1), budget=100.0, topped_today_count=1)
    assert d["reason"] == "cap_reached"
    # cap=2: одно было — ещё можно
    d = _decision(setting=_lb(daily_cap=2), budget=100.0, topped_today_count=1)
    assert d["action"] == "deposit"
    # cap=0 (без ограничения): сколько бы ни было — доливаем
    d = _decision(setting=_lb(daily_cap=0), budget=100.0, topped_today_count=5)
    assert d["action"] == "deposit"


# ─── Журнал автопополнений ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_autopay_log_append_and_cap(monkeypatch):
    """append кладёт запись в начало и режет журнал до AUTOPAY_LOG_CAP."""
    import json as _json

    from backend.services.funnel import ads_manager as am

    store: dict[str, str] = {}

    async def fake_get(db, project_id, key):
        return store.get(key)

    async def fake_set(db, project_id, key, value):
        store[key] = value

    monkeypatch.setattr("backend.services.settings_service.get_setting", fake_get)
    monkeypatch.setattr("backend.services.settings_service.set_setting", fake_set)

    db = AsyncMock()
    for i in range(am.AUTOPAY_LOG_CAP + 5):
        await am.append_autopay_log(db, PROJECT_ID, {"campaign_id": i, "ts": f"2026-07-08T0{i % 10}:00:00+00:00", "status": "ok"})

    log = _json.loads(store[am.AUTOPAY_LOG_KEY])
    assert len(log) == am.AUTOPAY_LOG_CAP
    assert log[0]["campaign_id"] == am.AUTOPAY_LOG_CAP + 4  # новые — первыми


@pytest.mark.asyncio
async def test_autopay_log_bad_json_returns_empty(monkeypatch):
    from backend.services.funnel import ads_manager as am

    async def fake_get(db, project_id, key):
        return "{broken json"

    monkeypatch.setattr("backend.services.settings_service.get_setting", fake_get)
    assert await am.get_autopay_log(AsyncMock(), PROJECT_ID) == []


# ─── Пауза / запуск кампании ─────────────────────────────────────────────────


def _scalar_one_result(obj) -> MagicMock:
    m = MagicMock()
    m.scalar_one_or_none.return_value = obj
    return m


@pytest.mark.asyncio
async def test_set_campaign_active_starts_and_updates_status(monkeypatch):
    """active=True → WB start; при успехе локальный статус кампании → 9."""
    from backend.services.funnel import ads_manager as am

    calls: dict = {}

    async def fake_key(db, pid):
        return "tok"

    async def fake_state(api_key, cid, action):
        calls["action"] = action
        return {"ok": True, "error": None}

    monkeypatch.setattr(am, "_get_advert_api_key", fake_key)
    monkeypatch.setattr("backend.services.funnel.wb_advertising_api.set_campaign_state", fake_state)

    camp = MagicMock()
    camp.status = 11
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one_result(camp))

    res = await am.set_campaign_active(db, PROJECT_ID, 555, True)

    assert calls["action"] == "start"
    assert res == {"ok": True, "status": 9, "error": None}
    assert camp.status == 9
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_set_campaign_active_pause(monkeypatch):
    """active=False → WB pause; локальный статус → 11."""
    from backend.services.funnel import ads_manager as am

    calls: dict = {}

    async def fake_key(db, pid):
        return "tok"

    async def fake_state(api_key, cid, action):
        calls["action"] = action
        return {"ok": True, "error": None}

    monkeypatch.setattr(am, "_get_advert_api_key", fake_key)
    monkeypatch.setattr("backend.services.funnel.wb_advertising_api.set_campaign_state", fake_state)

    camp = MagicMock()
    camp.status = 9
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one_result(camp))

    res = await am.set_campaign_active(db, PROJECT_ID, 555, False)
    assert calls["action"] == "pause"
    assert res["status"] == 11
    assert camp.status == 11


@pytest.mark.asyncio
async def test_set_campaign_active_no_key(monkeypatch):
    """Нет рекламного ключа → ошибка, без вызова WB и без коммита."""
    from backend.services.funnel import ads_manager as am

    async def fake_key(db, pid):
        return None

    monkeypatch.setattr(am, "_get_advert_api_key", fake_key)
    db = AsyncMock()
    res = await am.set_campaign_active(db, PROJECT_ID, 1, True)
    assert res["ok"] is False and res["status"] is None
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_campaign_active_wb_error_keeps_status(monkeypatch):
    """Отказ WB → ошибка пробрасывается, локальный статус НЕ трогаем."""
    from backend.services.funnel import ads_manager as am

    async def fake_key(db, pid):
        return "tok"

    async def fake_state(api_key, cid, action):
        return {"ok": False, "error": "read-only токен"}

    monkeypatch.setattr(am, "_get_advert_api_key", fake_key)
    monkeypatch.setattr("backend.services.funnel.wb_advertising_api.set_campaign_state", fake_state)

    db = AsyncMock()
    res = await am.set_campaign_active(db, PROJECT_ID, 1, True)
    assert res == {"ok": False, "status": None, "error": "read-only токен"}
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_autopay_activates_paused_campaign(monkeypatch):
    """Включили автопополнение на кампании НА ПАУЗЕ (11) → активируется."""
    from backend.services.funnel import ads_manager as am

    activated: dict = {}

    async def fake_save(db, pid, cid, entry):
        return {str(cid): entry}

    async def fake_active(db, pid, cid, active):
        activated["cid"] = cid
        activated["active"] = active
        return {"ok": True, "status": 9, "error": None}

    monkeypatch.setattr(am, "set_autopay_setting", fake_save)
    monkeypatch.setattr(am, "set_campaign_active", fake_active)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one_result(11))  # текущий статус — пауза
    res = await am.save_autopay_and_maybe_activate(db, PROJECT_ID, 42, {"enabled": True, "amount": 1000})
    assert activated == {"cid": 42, "active": True}
    assert res["activation"]["ok"] is True


@pytest.mark.asyncio
async def test_save_autopay_skips_activate_when_already_active(monkeypatch):
    """Кампания уже активна (9) → повторный start НЕ дёргаем (нет ложного баннера)."""
    from backend.services.funnel import ads_manager as am

    called = {"n": 0}

    async def fake_save(db, pid, cid, entry):
        return {str(cid): entry}

    async def fake_active(db, pid, cid, active):
        called["n"] += 1
        return {"ok": True, "status": 9, "error": None}

    monkeypatch.setattr(am, "set_autopay_setting", fake_save)
    monkeypatch.setattr(am, "set_campaign_active", fake_active)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one_result(9))  # уже активна
    res = await am.save_autopay_and_maybe_activate(db, PROJECT_ID, 42, {"enabled": True, "amount": 1000})
    assert called["n"] == 0
    assert res["activation"] is None


@pytest.mark.asyncio
async def test_save_autopay_no_activate_when_disabled(monkeypatch):
    """Выключенное автопополнение НЕ трогает статус кампании."""
    from backend.services.funnel import ads_manager as am

    called = {"n": 0}

    async def fake_save(db, pid, cid, entry):
        return {}

    async def fake_active(db, pid, cid, active):
        called["n"] += 1
        return {}

    monkeypatch.setattr(am, "set_autopay_setting", fake_save)
    monkeypatch.setattr(am, "set_campaign_active", fake_active)

    res = await am.save_autopay_and_maybe_activate(AsyncMock(), PROJECT_ID, 42, {"enabled": False, "amount": 0})
    assert called["n"] == 0
    assert res["activation"] is None


@pytest.mark.asyncio
async def test_set_campaign_state_unknown_action():
    """Неизвестное действие отбивается без сетевого вызова."""
    from backend.services.funnel.wb_advertising_api import set_campaign_state

    res = await set_campaign_state("tok", 1, "delete")
    assert res["ok"] is False and "unknown action" in res["error"]


@pytest.mark.asyncio
async def test_set_campaign_state_uses_adv_v0(monkeypatch):
    """Статус кампании управляется через adv/v0 (v1 отдаёт 404 — прод 2026-07-09)."""
    from backend.services.funnel import wb_advertising_api as wa

    captured: dict = {}

    class _Resp:
        status_code = 200
        text = ""

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            captured["url"] = url
            return _Resp()

    monkeypatch.setattr(wa.httpx, "AsyncClient", _Client)

    res = await wa.set_campaign_state("tok", 555, "start")
    assert res["ok"] is True
    assert "/adv/v0/start?id=555" in captured["url"]
    assert "/adv/v1/" not in captured["url"]


@pytest.mark.asyncio
async def test_campaign_metrics_customer_price_applies_spp():
    """«Цена Клиенту» = avg_price × (1 − СПП per день) из bdr_rates_map."""
    from datetime import date

    from backend.services.funnel.ads_manager import get_campaign_metrics
    from backend.services.funnel.bdr_rates import BdrRates, BdrRatesLookup

    d = date(2026, 7, 10)
    camp = MagicMock(nm_ids=[111], campaign_type="cpm", name="C")
    camp_res = MagicMock()
    camp_res.scalar_one_or_none.return_value = camp
    ad_row = MagicMock(date=d, views=1000, clicks=50, spend=Decimal("200"))
    f_row = MagicMock(date=d, opens=100, carts=10, orders=5, orders_sum=Decimal("5000"), avg_price=Decimal("1000"))
    db = _db_seq(camp_res, _rows_result([ad_row]), _rows_result([f_row]))

    bdr = BdrRatesLookup({(111, d): BdrRates(to_pay_rate=0.6, spp_rate=0.2, buyout_pct=0.9)}, {})
    res = await get_campaign_metrics(db, PROJECT_ID, 999, date_from="2026-07-10", date_to="2026-07-10", bdr_rates_map=bdr)

    row = res["rows"][0]
    assert row["avg_price"] == 1000.0
    assert row["customer_price"] == 800.0        # 1000 × (1 − 0.2)
    assert res["totals"]["customer_price"] == 800.0


@pytest.mark.asyncio
async def test_campaign_metrics_customer_price_zero_without_spp_map():
    """Без карты СПП «Цена Клиенту» = сама цена (СПП 0)."""
    from datetime import date

    from backend.services.funnel.ads_manager import get_campaign_metrics

    d = date(2026, 7, 10)
    camp = MagicMock(nm_ids=[111], campaign_type="cpm", name="C")
    camp_res = MagicMock()
    camp_res.scalar_one_or_none.return_value = camp
    ad_row = MagicMock(date=d, views=1000, clicks=50, spend=Decimal("200"))
    f_row = MagicMock(date=d, opens=100, carts=10, orders=5, orders_sum=Decimal("5000"), avg_price=Decimal("1000"))
    db = _db_seq(camp_res, _rows_result([ad_row]), _rows_result([f_row]))

    res = await get_campaign_metrics(db, PROJECT_ID, 999, date_from="2026-07-10", date_to="2026-07-10")
    assert res["rows"][0]["customer_price"] == 1000.0  # СПП нет → цена как есть


# ─── _autopay_journal_day_stats: naive-UTC ts vs день МСК ────────────────────


def test_autopay_journal_night_window_counts_as_today_msk():
    """Запись 00:30 МСК = 21:30 UTC вчера (naive) обязана считаться «сегодня» по МСК.

    Регресс: naive-UTC ts сравнивался по ts.date() (UTC-дата) с датой МСК —
    в 00:00–02:59 МСК идемпотентность и защита unknown молча отключались.
    """
    from datetime import date

    from backend.services.funnel.ads_manager import _autopay_journal_day_stats

    today_msk = date(2026, 7, 15)
    log = [
        # 00:30 МСК 15.07 = 21:30 UTC 14.07 — journal пишет utcnow() (naive UTC)
        {"ts": "2026-07-14T21:30:00", "campaign_id": 111, "status": "ok"},
        {"ts": "2026-07-14T21:40:00", "campaign_id": 222, "status": "unknown"},
        # 23:50 UTC 14.07 = 02:50 МСК 15.07 — тоже «сегодня»
        {"ts": "2026-07-14T23:50:00", "campaign_id": 111, "status": "ok"},
        # 20:59 UTC 14.07 = 23:59 МСК 14.07 — «вчера», не считается
        {"ts": "2026-07-14T20:59:00", "campaign_id": 111, "status": "ok"},
    ]
    topped, unknown = _autopay_journal_day_stats(log, today_msk)
    assert topped == {111: 2}
    assert unknown == {222}


def test_autopay_journal_aware_ts_and_garbage():
    """Aware-метки конвертируются честно; мусорные ts пропускаются молча."""
    from datetime import date

    from backend.services.funnel.ads_manager import _autopay_journal_day_stats

    today_msk = date(2026, 7, 15)
    log = [
        {"ts": "2026-07-15T09:00:00+03:00", "campaign_id": 5, "status": "ok"},
        {"ts": "not-a-date", "campaign_id": 6, "status": "ok"},
        {"ts": "", "campaign_id": 7, "status": "unknown"},
    ]
    topped, unknown = _autopay_journal_day_stats(log, today_msk)
    assert topped == {5: 1}
    assert unknown == set()


# ─── create_campaign: точечный догруз вместо полного синка ───────────────────


async def test_create_campaign_uses_targeted_refresh_not_full_sync(monkeypatch):
    """После save-ad подтягивается ОДНА кампания, а не весь кабинет.

    Регресс: полный sync_ad_campaigns внутри HTTP-запроса отвечал 613 секунд
    (1300+ budget-запросов к WB с 429-паузами) — таймаут прокси и дубли кампаний.
    """
    from unittest.mock import AsyncMock

    from backend.services.funnel import ads_manager as m

    monkeypatch.setattr(m, "_get_advert_api_key", AsyncMock(return_value="key"))
    monkeypatch.setattr(
        "backend.services.funnel.wb_advertising_api.create_campaign",
        AsyncMock(return_value={"ok": True, "campaign_id": 777, "error": None}),
    )
    refresh = AsyncMock(return_value={"ok": True, "error": None})
    full_sync = AsyncMock()
    monkeypatch.setattr(
        "backend.services.funnel.ad_campaigns_service.refresh_one_campaign", refresh
    )
    monkeypatch.setattr(
        "backend.services.funnel.ad_campaigns_service.sync_ad_campaigns", full_sync
    )

    db = AsyncMock()
    res = await m.create_campaign(db, 1, "тест", [111], "unified", "cpm", None)

    assert res["ok"] is True and res["campaign_id"] == 777
    refresh.assert_awaited_once_with(db, 1, 777)
    full_sync.assert_not_awaited()


async def test_create_campaign_refresh_failure_not_fatal(monkeypatch):
    """Провал точечного догруза не роняет ответ — кампания уже создана в WB."""
    from unittest.mock import AsyncMock

    from backend.services.funnel import ads_manager as m

    monkeypatch.setattr(m, "_get_advert_api_key", AsyncMock(return_value="key"))
    monkeypatch.setattr(
        "backend.services.funnel.wb_advertising_api.create_campaign",
        AsyncMock(return_value={"ok": True, "campaign_id": 778, "error": None}),
    )
    monkeypatch.setattr(
        "backend.services.funnel.ad_campaigns_service.refresh_one_campaign",
        AsyncMock(side_effect=RuntimeError("WB недоступен")),
    )

    db = AsyncMock()
    res = await m.create_campaign(db, 1, "тест", [111], "unified", "cpm", None)
    assert res["ok"] is True and res["campaign_id"] == 778
