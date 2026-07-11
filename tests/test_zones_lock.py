"""
Tests for _zones_lock (backend/services/funnel/ads_manager.py).

WB (PUT /adv/v0/auction/placements) принимает переключение зон показов только для
CPM с ручной ставкой в статусах 4/9/11. Всё остальное должно блокироваться У НАС,
до похода в WB: у CPC зона одна по определению, у CPM-единой зоны всегда обе.
"""

import pytest

from backend.services.funnel.ads_manager import ZONE_EDIT_STATUSES, _zones_lock


@pytest.mark.parametrize("status", ZONE_EDIT_STATUSES)
def test_cpm_manual_unlocked_in_editable_statuses(status):
    locked, reason = _zones_lock("cpm", "manual", status)
    assert locked is False
    assert reason is None


@pytest.mark.parametrize("status", [7, 8, 10])
def test_cpm_manual_locked_in_other_statuses(status):
    """Завершённая/архивная кампания — WB вернул бы 400."""
    locked, reason = _zones_lock("cpm", "manual", status)
    assert locked is True
    assert "готовой, активной или приостановленной" in reason


def test_cpc_always_locked():
    """У CPC показы только в поиске — выключать нечего."""
    locked, reason = _zones_lock("cpc", "manual", 9)
    assert locked is True
    assert "CPC" in reason


def test_cpm_unified_locked():
    """Единая ставка: обе зоны крутятся одновременно, WB не даёт разделить."""
    locked, reason = _zones_lock("cpm", "unified", 9)
    assert locked is True
    assert "единая ставка" in reason


def test_cpm_unknown_bid_mode_locked():
    """bid_mode ещё не синкнулся (None) — не рискуем и блокируем."""
    locked, reason = _zones_lock("cpm", None, 9)
    assert locked is True
    assert reason is not None


def test_cpc_beats_status_check():
    """У CPC причина — про CPC, а не про статус (порядок проверок важен для текста)."""
    locked, reason = _zones_lock("cpc", "manual", 7)
    assert locked is True
    assert "CPC" in reason
