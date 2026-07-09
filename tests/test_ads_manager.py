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
    nm_meta_row = MagicMock(nm_id=111, brand="НУ-НУ", subject="Ковры")
    active.nm_ids = [111]
    db = _db_seq(
        _scalars_result([paused, active]),
        _rows_result([period_row]),
        _rows_result([today_row]),
        _rows_result([nm_meta_row]),
    )

    rows = await list_ad_campaigns(db, PROJECT_ID, date_from="2026-06-13", date_to="2026-06-19")

    assert [r["campaign_id"] for r in rows] == [202, 201]
    assert rows[0]["spend_today"] == 300.0
    assert rows[0]["spend_period"] == 2100.0
    assert rows[0]["clicks_period"] == 250
    assert rows[0]["ctr"] == 2.5  # 250/10000
    assert rows[0]["cpc"] == 8.4  # 2100/250
    assert rows[0]["brands"] == ["НУ-НУ"]
    assert rows[0]["subjects"] == ["Ковры"]
    assert rows[0]["status_label"] == "Активна"
    assert rows[1]["status_label"] == "Пауза"


def test_sanitize_autopay_clamps():
    """Настройки автопополнения нормализуются: час 0-23, порог 0-100, сумма ≥ 0."""
    from backend.services.funnel.ads_manager import _sanitize_autopay

    s = _sanitize_autopay({"enabled": True, "amount": -5, "hour": 25, "threshold_pct": 150})
    assert s == {"enabled": True, "amount": 0.0, "hour": 23, "threshold_pct": 100}
    d = _sanitize_autopay({})
    assert d == {"enabled": False, "amount": 0.0, "hour": 9, "threshold_pct": 50}
