"""
Tests for _manage_guard (backend/services/funnel/ads_manager.py).

WB разрешает операции управления только в определённых статусах. Отбиваем нарушения
у себя ДО похода в WB — иначе WB вернёт 400, а удаление вообще доступно только «готовой».
"""

import pytest

from backend.services.funnel.ads_manager import MANAGE_ALLOWED_STATUSES, _manage_guard


@pytest.mark.parametrize("status", [4, 9, 11])
def test_stop_allowed_in_running_statuses(status):
    assert _manage_guard("stop", status) is None


@pytest.mark.parametrize("status", [7, -1])
def test_stop_blocked_in_terminal_statuses(status):
    reason = _manage_guard("stop", status)
    assert reason is not None
    assert "Завершить" in reason


def test_delete_only_ready():
    assert _manage_guard("delete", 4) is None
    for status in (9, 11, 7):
        reason = _manage_guard("delete", status)
        assert reason is not None
        assert "Готова" in reason


@pytest.mark.parametrize("action", ["bids", "nms"])
@pytest.mark.parametrize("status", [4, 9, 11])
def test_bids_nms_allowed(action, status):
    assert _manage_guard(action, status) is None


@pytest.mark.parametrize("action", ["bids", "nms"])
def test_bids_nms_blocked_when_completed(action):
    assert _manage_guard(action, 7) is not None


def test_rename_allowed_anywhere():
    for status in (4, 7, 9, 11, -1):
        assert _manage_guard("rename", status) is None


def test_unknown_action():
    assert _manage_guard("teleport", 9) is not None


def test_allowed_map_covers_all_service_actions():
    """Каждая операция объявлена в карте статусов — иначе guard вернёт 'неизвестная'."""
    assert set(MANAGE_ALLOWED_STATUSES) == {"stop", "delete", "bids", "nms", "rename"}
