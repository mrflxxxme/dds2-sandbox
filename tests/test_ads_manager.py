"""
Tests for backend/services/funnel/ads_manager.py — «Управление рекламой».

Covers compute_budget_gap (чистый расчёт доливки до полуночи) и
get_budget_gaps / list_ad_campaigns на мокнутой БД.
"""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytz

from backend.services.funnel.ads_manager import (
    _campaign_potential,
    _chronic_stats,
    _extrapolate_full_day,
    _median,
    _runout_by_day,
    compute_budget_gap,
)

PROJECT_ID = 1
MSK = pytz.timezone("Europe/Moscow")


def _utc_from_msk(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    """naive-UTC момент по времени МСК (как хранит WbAdCampaignEvent.created_at)."""
    return MSK.localize(datetime(y, mo, d, h, mi)).astimezone(pytz.UTC).replace(tzinfo=None)


def _event(campaign_id: int, new_value: str | None, created_at: datetime) -> MagicMock:
    ev = MagicMock()
    ev.campaign_id = campaign_id
    ev.new_value = new_value
    ev.created_at = created_at
    return ev


# ─── экстраполяция полного дня / потенциал / медиана ─────────────────────────


def test_extrapolate_full_day_runout_scales_to_midnight():
    """Остановка в 08:00 при расходе 2000 → скорость 250 ₽/ч → полный день 6000, недобор 4000."""
    pot, short = _extrapolate_full_day(2000.0, 8.0)
    assert pot == 6000.0
    assert short == 4000.0


def test_extrapolate_full_day_no_runout_is_full():
    """День без остановки — потенциал = факт, недобора нет."""
    assert _extrapolate_full_day(1500.0, None) == (1500.0, 0.0)
    assert _extrapolate_full_day(0.0, 8.0) == (0.0, 0.0)


def test_median_even_and_odd():
    assert _median([3.0, 1.0, 2.0]) == 2.0
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5
    assert _median([]) is None


def test_campaign_potential_median_of_runout_days():
    """Потенциал = медиана экстраполированных дней-остановок (устойчива к раннему выбросу)."""
    spend = {date(2026, 7, 1): 1000.0, date(2026, 7, 2): 1200.0, date(2026, 7, 3): 2000.0}
    stops = {date(2026, 7, 1): 10.0, date(2026, 7, 2): 12.0, date(2026, 7, 3): 4.0}
    # потенциалы: 2400, 2400, 12000 → медиана 2400 (выброс 12000 не перетягивает)
    pot, n = _campaign_potential(spend, stops, recent_days=7)
    assert pot == 2400.0
    assert n == 3


def test_campaign_potential_fallback_to_full_days():
    """Если дней-остановок нет — потенциал = среднее расхода по полным дням."""
    spend = {date(2026, 7, 1): 1000.0, date(2026, 7, 2): 1200.0}
    pot, n = _campaign_potential(spend, {}, recent_days=7)
    assert pot == 1100.0
    assert n == 2


def test_runout_by_day_last_zero_wins_per_day():
    """В пределах дня последний переход бюджета →0 выигрывает; разные дни независимы."""
    evs = [
        _event(10, "0", _utc_from_msk(2026, 7, 1, 12)),
        _event(10, "0", _utc_from_msk(2026, 7, 1, 15)),  # позже — этот час остановки
        _event(10, "0", _utc_from_msk(2026, 7, 2, 9)),
    ]
    out = _runout_by_day(evs)
    assert out[10][date(2026, 7, 1)] == _utc_from_msk(2026, 7, 1, 15)
    assert out[10][date(2026, 7, 2)] == _utc_from_msk(2026, 7, 2, 9)


def test_runout_by_day_topup_after_zero_clears_day():
    """Пополнение (>0) после →0 в тот же день снимает день из остановленных."""
    evs = [
        _event(10, "0", _utc_from_msk(2026, 7, 1, 12)),
        _event(10, "3000", _utc_from_msk(2026, 7, 1, 13)),  # долили — день не «кончился»
    ]
    out = _runout_by_day(evs)
    assert date(2026, 7, 1) not in out.get(10, {})


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


def _win_ev_row(campaign_id: int, msk_day: date, new_value: str | None, created_at: datetime) -> MagicMock:
    """Строка «последнее событие бюджета за МСК-день» (DISTINCT ON из _budget_window_history)."""
    r = MagicMock()
    r.campaign_id = campaign_id
    r.msk_day = msk_day
    r.new_value = new_value
    r.created_at = created_at
    return r


@pytest.mark.asyncio
async def test_budget_gaps_uses_last_zero_event():
    """Кампания с событием бюджет→0 попадает в список с часом остановки."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(101)
    spend_row = MagicMock(campaign_id=101, spend=Decimal("800"))
    # событие в 09:00 UTC = 12:00 МСК
    ev = _event(101, "0", datetime(2026, 7, 3, 9, 0, 0))
    # окно полных дней (потенциал) — два дня по 1000/1200 ₽, событий-остановок нет
    win_daily = [
        MagicMock(campaign_id=101, date=date(2026, 7, 1), spend=Decimal("1000")),
        MagicMock(campaign_id=101, date=date(2026, 7, 2), spend=Decimal("1200")),
    ]
    nm_meta = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    db = _db_seq(
        _scalars_result([camp]), _rows_result([spend_row]), _scalars_result([ev]),
        _rows_result(win_daily), _rows_result([]), _rows_result([nm_meta]),
    )

    rows = await get_budget_gaps(db, PROJECT_ID)

    assert len(rows) == 1
    assert rows[0]["campaign_id"] == 101
    assert rows[0]["spend_today"] == 800.0
    assert rows[0]["ran_out_at"] is not None
    assert "needed_till_midnight" in rows[0]
    assert rows[0]["brands"] == ["НУ-НУ"]
    assert rows[0]["subjects"] == ["Ковры"]
    # потенциал полного дня = (1000+1200)/2 = 1100; недобор = 1100 − 800 = 300
    assert rows[0]["potential_daily"] == 1100.0
    assert rows[0]["full_days"] == 2
    assert rows[0]["needed_potential"] == 1000.0  # raw 300 < минимума WB 1000 → 1000
    assert rows[0]["raw_needed_potential"] == 300.0


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
    db = _db_seq(
        _scalars_result([camp]), _rows_result([spend_row]), _scalars_result(events),
        _rows_result([]), _rows_result([]), _rows_result([nm_meta]),
    )

    rows = await get_budget_gaps(db, PROJECT_ID)

    # бюджет сейчас 0 (модель), но события говорят «пополнена после нуля» → час неизвестен (None), строка остаётся
    assert len(rows) == 1
    assert rows[0]["ran_out_at"] is None
    # окно пустое → потенциал неизвестен, недобор падает на линейный фолбэк
    assert rows[0]["potential_daily"] is None
    assert rows[0]["full_days"] == 0


@pytest.mark.asyncio
async def test_budget_gaps_no_spend_today_excluded():
    """Кампания без расхода сегодня — не «нехватка бюджета» (она вообще не крутилась)."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(103)
    nm_meta = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    db = _db_seq(_scalars_result([camp]), _rows_result([]), _scalars_result([]), _rows_result([nm_meta]))

    rows = await get_budget_gaps(db, PROJECT_ID)
    assert rows == []


def test_chronic_stats_counts_active_and_median_hour():
    """Дни-с-расходом vs дни-остановки; типичный час — медиана, дни без расхода отброшены."""
    day_spend = {date(2026, 7, 1): 900.0, date(2026, 7, 2): 800.0, date(2026, 7, 3): 0.0, date(2026, 7, 4): 700.0}
    # 7/3 есть событие остановки, но расхода нет → в счёт не идёт
    stop_hours = {date(2026, 7, 1): 15.0, date(2026, 7, 2): 13.0, date(2026, 7, 3): 10.0}
    runout_days, active_days, typical = _chronic_stats(day_spend, stop_hours)
    assert active_days == 3       # 7/1, 7/2, 7/4 (7/3 spend=0)
    assert runout_days == 2       # 7/1, 7/2 (день без расхода исключён)
    assert typical == 14.0        # median([15, 13])


@pytest.mark.asyncio
async def test_budget_gaps_chronic_predicted_when_budget_alive():
    """Живая кампания (бюджет>0), что регулярно кончалась за окно → строка-прогноз."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(701, budget=500, status=9)  # бюджет есть → факта нет, только прогноз
    spend_row = MagicMock(campaign_id=701, spend=Decimal("300"))  # частичный расход сегодня
    days = [date(2026, 7, d) for d in (1, 2, 3, 4, 5)]
    win_daily = [MagicMock(campaign_id=701, date=d, spend=Decimal("900")) for d in days]
    # последнее событие дня → 0 в 15:00 МСК на 4 из 5 дней
    win_ev = [_win_ev_row(701, date(2026, 7, d), "0", _utc_from_msk(2026, 7, d, 15, 0)) for d in (1, 2, 3, 4)]
    nm_meta = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    # ran_ids пуст (бюджет>0) → запрос сегодняшних событий не идёт
    db = _db_seq(
        _scalars_result([camp]), _rows_result([spend_row]),
        _rows_result(win_daily), _rows_result(win_ev), _rows_result([nm_meta]),
    )

    rows = await get_budget_gaps(db, PROJECT_ID)

    assert len(rows) == 1
    r = rows[0]
    assert r["campaign_id"] == 701
    assert r["predicted"] is True
    assert r["ran_out_at"] is None
    assert r["typical_stop_hour"] == 15.0
    assert r["runout_days"] == 4 and r["active_days"] == 5
    assert r["potential_daily"] == 1440.0   # 900/15×24, медиана 4 дней
    assert r["needed_potential"] == 1140     # 1440 − 300 потрачено сегодня
    assert r["brands"] == ["НУ-НУ"]


@pytest.mark.asyncio
async def test_budget_gaps_alive_not_chronic_excluded():
    """Живая кампания с одиночной остановкой (< порога дней) — не хроник, в список не идёт."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    camp = _campaign(702, budget=500, status=9)
    spend_row = MagicMock(campaign_id=702, spend=Decimal("300"))
    days = [date(2026, 7, d) for d in (1, 2, 3, 4, 5)]
    win_daily = [MagicMock(campaign_id=702, date=d, spend=Decimal("900")) for d in days]
    win_ev = [_win_ev_row(702, date(2026, 7, 1), "0", _utc_from_msk(2026, 7, 1, 15, 0))]  # всего 1 день-остановка < 3
    # result пуст → запрос nm_meta не идёт
    db = _db_seq(
        _scalars_result([camp]), _rows_result([spend_row]),
        _rows_result(win_daily), _rows_result(win_ev),
    )

    rows = await get_budget_gaps(db, PROJECT_ID)
    assert rows == []


@pytest.mark.asyncio
async def test_budget_gaps_fact_before_prediction():
    """Факт (кончился сегодня) идёт впереди прогноза (хроник с живым бюджетом)."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    fact = _campaign(801, budget=0, status=9)     # кончился сегодня
    pred = _campaign(802, budget=500, status=9)   # живой, но хроник
    fact.nm_ids = [111]
    pred.nm_ids = [222]
    spend_rows = [MagicMock(campaign_id=801, spend=Decimal("1000")), MagicMock(campaign_id=802, spend=Decimal("200"))]
    today_ev = [_event(801, "0", _utc_from_msk(2026, 7, 16, 14, 0))]  # факт остановки сегодня
    days = [date(2026, 7, d) for d in (1, 2, 3, 4, 5)]
    win_daily = [MagicMock(campaign_id=802, date=d, spend=Decimal("900")) for d in days]
    win_ev = [_win_ev_row(802, date(2026, 7, d), "0", _utc_from_msk(2026, 7, d, 15, 0)) for d in (1, 2, 3, 4)]
    nm_meta = [MagicMock(nm_id=111, brand="A", subject="X"), MagicMock(nm_id=222, brand="B", subject="Y")]
    db = _db_seq(
        _scalars_result([fact, pred]), _rows_result(spend_rows), _scalars_result(today_ev),
        _rows_result(win_daily), _rows_result(win_ev), _rows_result(nm_meta),
    )

    rows = await get_budget_gaps(db, PROJECT_ID)

    assert [r["campaign_id"] for r in rows] == [801, 802]  # факт → прогноз
    assert rows[0]["predicted"] is False and rows[0]["ran_out_at"] is not None
    assert rows[1]["predicted"] is True


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


def test_sanitize_schedule_clamps():
    """Настройки расписания нормализуются: часы МСК в 0-23, дефолт 00→09."""
    from backend.services.funnel.ads_manager import _sanitize_schedule

    s = _sanitize_schedule({"enabled": True, "pause_hour": -1, "resume_hour": 25})
    assert s == {"enabled": True, "pause_hour": 0, "resume_hour": 23}
    d = _sanitize_schedule({})
    assert d == {"enabled": False, "pause_hour": 0, "resume_hour": 9}


def test_wb_side_topups_detects_increases_only():
    """Долив ВБ = рост бюджета ≥ 50₽; падения и дрожание <50₽ — не доливы; последний долив выигрывает."""
    from backend.services.funnel.ads_manager import _wb_side_topups

    events = [
        _event(101, None, datetime(2026, 7, 16, 21, 2)),          # мусор без числа — пропуск
        _event(101, "3000.0", datetime(2026, 7, 15, 21, 2)),      # 0→3000: долив
        _event(101, "10.0", datetime(2026, 7, 16, 12, 0)),        # 3000→10: списание, не долив
        _event(101, "40.0", datetime(2026, 7, 16, 13, 0)),        # +30: дрожание < 50
        _event(101, "5000.0", datetime(2026, 7, 16, 21, 2)),      # 40→5000: долив (последний)
        _event(202, "999.0", datetime(2026, 7, 16, 21, 3)),       # чужая кампания без роста
    ]
    # old_value проставляем цепочкой руками (у _event его нет)
    olds = [None, "0.0", "3000.0", "10.0", "40.0", "999.0"]
    for ev, old in zip(events, olds):
        ev.old_value = old

    res = _wb_side_topups(events, manual_marks=[])
    assert set(res) == {101}
    assert res[101]["count"] == 2
    assert res[101]["last"] == datetime(2026, 7, 16, 21, 2) and res[101]["last_amount"] == 4960.0


def test_wb_side_topups_excludes_our_manual_deposits():
    """Рост бюджета в ±15 мин от нашего ручного пополнения через ДДС — не долив ВБ."""
    from backend.services.funnel.ads_manager import _wb_side_topups

    ev = _event(101, "2000.0", datetime(2026, 7, 16, 12, 0))
    ev.old_value = "500.0"
    # наш депозит через ДДС в 12:05 — событие роста через 5 минут = это он
    res = _wb_side_topups([ev], manual_marks=[(101, datetime(2026, 7, 16, 12, 5))])
    assert res == {}
    # депозит по ДРУГОЙ кампании не гасит долив этой
    res = _wb_side_topups([ev], manual_marks=[(202, datetime(2026, 7, 16, 12, 5))])
    assert set(res) == {101}


def test_schedule_active_requires_nonzero_window():
    """Выключенная или с пустым окном (pause==resume) настройка не работает."""
    from backend.services.funnel.ads_manager import _schedule_active, _sanitize_schedule

    assert _schedule_active(_sanitize_schedule({"enabled": True, "pause_hour": 0, "resume_hour": 9}))
    assert not _schedule_active(_sanitize_schedule({"enabled": False, "pause_hour": 0, "resume_hour": 9}))
    assert not _schedule_active(_sanitize_schedule({"enabled": True, "pause_hour": 5, "resume_hour": 5}))


# ─── compute_schedule_action ─────────────────────────────────────────────────

ACTIVE, PAUSED = 9, 11


def _action(**overrides):
    from backend.services.funnel.ads_manager import compute_schedule_action

    kwargs = {
        "setting": {"enabled": True, "pause_hour": 0, "resume_hour": 9},
        "status": ACTIVE,
        "now_msk": datetime(2026, 7, 17, 0, 30),  # внутри окна 00–09
        "journal": [],
    }
    kwargs.update(overrides)
    return compute_schedule_action(**kwargs)


def _je(kind, status="ok", window_id="2026-07-17", campaign_id=101):
    """Запись журнала расписания (новые первыми — порядок задаёт вызывающий)."""
    return {"campaign_id": campaign_id, "kind": kind, "status": status, "window_id": window_id}


def test_schedule_pauses_active_in_window():
    """Активная кампания внутри окна, журнал пуст → пауза (окно = МСК-дата старта)."""
    d = _action()
    assert d == {"action": "pause", "window_id": "2026-07-17", "reason": "ok"}


def test_schedule_skips_not_active_in_window():
    """Внутри окна не активна (пауза руками/ещё не запускалась/нет в БД) → не трогаем."""
    assert _action(status=PAUSED)["reason"] == "not_active"
    assert _action(status=4)["reason"] == "not_active"
    assert _action(status=None)["reason"] == "not_active"


def test_schedule_no_repause_after_manual_start():
    """Уже глушили в это окно (есть успешная пауза), кампания снова активна —
    значит юзер поднял её руками ночью. Повторно не глушим."""
    d = _action(status=ACTIVE, journal=[_je("pause")])
    assert d["reason"] == "already_paused"


def test_schedule_retries_failed_pause_up_to_cap():
    """Ошибка WB ретраится следующим тиком, но не более SCHEDULE_MAX_ATTEMPTS на окно."""
    from backend.services.funnel.ads_manager import SCHEDULE_MAX_ATTEMPTS

    d = _action(journal=[_je("pause", status="error")])
    assert d["action"] == "pause"  # одна неудача — пробуем ещё
    exhausted = [_je("pause", status="error")] * SCHEDULE_MAX_ATTEMPTS
    assert _action(journal=exhausted)["reason"] == "attempts_exhausted"


def test_schedule_starts_our_pause_after_window():
    """Окно кончилось, кампания на паузе, последняя успешная операция — наша пауза → запуск."""
    d = _action(status=PAUSED, now_msk=datetime(2026, 7, 17, 9, 1), journal=[_je("pause")])
    assert d == {"action": "start", "window_id": "2026-07-17", "reason": "ok"}


def test_schedule_never_starts_foreign_pause():
    """Паузу, которую ставили НЕ мы, не трогаем: пустой журнал или последняя операция — запуск."""
    after = datetime(2026, 7, 17, 9, 1)
    assert _action(status=PAUSED, now_msk=after)["reason"] == "not_ours"
    assert _action(status=PAUSED, now_msk=after, journal=[_je("start"), _je("pause")])["reason"] == "not_ours"


def test_schedule_skips_active_after_window():
    """Вне окна активная кампания — делать нечего."""
    assert _action(now_msk=datetime(2026, 7, 17, 12, 0))["reason"] == "not_paused"


def test_schedule_missed_window_self_heals():
    """Тик проспал окно (бэкенд лежал) → застрявшая на паузе кампания поднимается позже."""
    d = _action(
        status=PAUSED,
        now_msk=datetime(2026, 7, 18, 14, 0),  # следующий день, давно вне окна
        journal=[_je("pause", window_id="2026-07-17")],
    )
    assert d["action"] == "start" and d["window_id"] == "2026-07-17"


def test_schedule_cross_midnight_window():
    """Окно через полночь (22→08): вечер и утро — одно окно с датой старта."""
    setting = {"enabled": True, "pause_hour": 22, "resume_hour": 8}
    # вечер 23:30 — окно стартовало сегодня
    d = _action(setting=setting, now_msk=datetime(2026, 7, 17, 23, 30))
    assert d == {"action": "pause", "window_id": "2026-07-17", "reason": "ok"}
    # утро 03:00 следующего дня — то же окно (стартовало вчера)
    d = _action(setting=setting, now_msk=datetime(2026, 7, 18, 3, 0))
    assert d["action"] == "pause" and d["window_id"] == "2026-07-17"
    # 03:00, но в это окно уже глушили → скип
    d = _action(setting=setting, now_msk=datetime(2026, 7, 18, 3, 0), journal=[_je("pause")])
    assert d["reason"] == "already_paused"
    # 08:00 — окно кончилось, поднимаем свою паузу
    d = _action(setting=setting, status=PAUSED, now_msk=datetime(2026, 7, 18, 8, 1), journal=[_je("pause")])
    assert d == {"action": "start", "window_id": "2026-07-17", "reason": "ok"}


def test_schedule_disabled_or_empty_window_skips():
    assert _action(setting={"enabled": False, "pause_hour": 0, "resume_hour": 9})["reason"] == "disabled"
    assert _action(setting={"enabled": True, "pause_hour": 9, "resume_hour": 9})["reason"] == "disabled"


# ─── Журнал пополнений (ручные) ──────────────────────────────────────────────


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


def _settings_store(monkeypatch) -> dict[str, str]:
    """project_settings в памяти: настройки и журнал расписания живут в JSON."""
    store: dict[str, str] = {}

    async def fake_get(db, project_id, key):
        return store.get(key)

    async def fake_set(db, project_id, key, value):
        store[key] = value

    monkeypatch.setattr("backend.services.settings_service.get_setting", fake_get)
    monkeypatch.setattr("backend.services.settings_service.set_setting", fake_set)
    return store


@pytest.mark.asyncio
async def test_schedule_settings_merge_and_drop_disabled(monkeypatch):
    """set мержит по кампании; выключенные записи вычищаются из JSON."""
    from backend.services.funnel import ads_manager as am

    _settings_store(monkeypatch)
    db = AsyncMock()
    await am.set_schedule_setting(db, PROJECT_ID, 101, {"enabled": True, "pause_hour": 0, "resume_hour": 9})
    settings = await am.set_schedule_setting(db, PROJECT_ID, 202, {"enabled": True, "pause_hour": 22, "resume_hour": 8})
    assert set(settings) == {"101", "202"}

    settings = await am.set_schedule_setting(db, PROJECT_ID, 101, {"enabled": False})
    assert set(settings) == {"202"}  # выключенная запись не хранится
    assert (await am.get_schedule_settings(db, PROJECT_ID)) == settings


def _campaigns_db(*camps) -> AsyncMock:
    """db, чей execute отдаёт список кампаний (scalars().all())."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(camps)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_run_schedule_tick_pause_then_resume(monkeypatch):
    """Полный цикл тика: 00:30 — пауза + журнал, повторный тик — скип, 09:01 — запуск.

    Сейв настройки кампанию не трогает — только тик (урок автопея: сейв менял
    статус кампании и это стреляло). Статус в нашей БД обновляется синхронно.
    """
    import json as _json

    from backend.services.funnel import ads_manager as am

    store = _settings_store(monkeypatch)
    calls: list[tuple[int, str]] = []

    async def fake_state(api_key, campaign_id, action):
        calls.append((campaign_id, action))
        return {"ok": True, "error": None}

    monkeypatch.setattr("backend.services.funnel.wb_advertising_api.set_campaign_state", fake_state)

    db = AsyncMock()
    await am.set_schedule_setting(db, PROJECT_ID, 101, {"enabled": True, "pause_hour": 0, "resume_hour": 9})
    assert calls == []  # сейв не дёргает WB

    camp = MagicMock(campaign_id=101, status=am.CAMPAIGN_STATUS_ACTIVE)

    # 00:30 МСК — внутри окна → пауза
    monkeypatch.setattr(am, "msk_now", lambda: datetime(2026, 7, 17, 0, 30))
    res = await am.run_ads_schedule_tick(_campaigns_db(camp), PROJECT_ID, "key")
    assert res == {"paused": 1, "started": 0, "checked": 1}
    assert calls == [(101, "pause")]
    assert camp.status == am.CAMPAIGN_STATUS_PAUSED  # наш статус обновлён сразу
    log = _json.loads(store[am.SCHEDULE_LOG_KEY])
    assert log[0]["kind"] == "pause" and log[0]["status"] == "ok" and log[0]["window_id"] == "2026-07-17"

    # 00:46 — то же окно, уже глушили → ничего не делаем
    res = await am.run_ads_schedule_tick(_campaigns_db(camp), PROJECT_ID, "key")
    assert res == {"paused": 0, "started": 0, "checked": 1}
    assert len(calls) == 1

    # 09:01 — окно кончилось → запускаем свою паузу
    monkeypatch.setattr(am, "msk_now", lambda: datetime(2026, 7, 17, 9, 1))
    res = await am.run_ads_schedule_tick(_campaigns_db(camp), PROJECT_ID, "key")
    assert res == {"paused": 0, "started": 1, "checked": 1}
    assert calls[-1] == (101, "start")
    assert camp.status == am.CAMPAIGN_STATUS_ACTIVE
    log = _json.loads(store[am.SCHEDULE_LOG_KEY])
    assert log[0]["kind"] == "start" and log[0]["status"] == "ok"

    # 09:16 — кампания активна, журнал закрыт → тишина
    res = await am.run_ads_schedule_tick(_campaigns_db(camp), PROJECT_ID, "key")
    assert res == {"paused": 0, "started": 0, "checked": 1}
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_run_schedule_tick_wb_error_logged_not_counted(monkeypatch):
    """Ошибка WB: статус в БД не меняем, в журнал пишем error (ретрай следующим тиком)."""
    import json as _json

    from backend.services.funnel import ads_manager as am

    store = _settings_store(monkeypatch)

    async def fake_state(api_key, campaign_id, action):
        return {"ok": False, "error": "HTTP 429: limited"}

    monkeypatch.setattr("backend.services.funnel.wb_advertising_api.set_campaign_state", fake_state)

    await am.set_schedule_setting(AsyncMock(), PROJECT_ID, 101, {"enabled": True, "pause_hour": 0, "resume_hour": 9})
    camp = MagicMock(campaign_id=101, status=am.CAMPAIGN_STATUS_ACTIVE)
    monkeypatch.setattr(am, "msk_now", lambda: datetime(2026, 7, 17, 0, 30))

    res = await am.run_ads_schedule_tick(_campaigns_db(camp), PROJECT_ID, "key")
    assert res == {"paused": 0, "started": 0, "checked": 1}
    assert camp.status == am.CAMPAIGN_STATUS_ACTIVE
    log = _json.loads(store[am.SCHEDULE_LOG_KEY])
    assert log[0]["status"] == "error" and "429" in log[0]["reason"]


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


# ─── msk_today: мёртвая МСК-ветка utcnow().tzinfo ────────────────────────────


def test_msk_today_is_msk_calendar_day(monkeypatch):
    """23:30 UTC = 02:30 МСК следующего дня → msk_today() обязан отдать следующий день.

    Регресс: «utcnow().astimezone(MSK).date() if utcnow().tzinfo else utcnow().date()» —
    tzinfo у naive utcnow() всегда None → всегда бралась сырая UTC-дата (вчера по МСК).
    """
    from datetime import date, datetime

    from backend.utils import time as time_utils

    monkeypatch.setattr(time_utils, "utcnow", lambda: datetime(2026, 7, 14, 23, 30, 0))
    assert time_utils.msk_today() == date(2026, 7, 15)

    monkeypatch.setattr(time_utils, "utcnow", lambda: datetime(2026, 7, 14, 12, 0, 0))
    assert time_utils.msk_today() == date(2026, 7, 14)
