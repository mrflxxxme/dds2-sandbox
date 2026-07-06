"""_inbound_accept_signal: сигнал «ФФ принял приёмку на остатки».

Регресс (склад «Газпром», FR 202523 / IN-186): skladbot довёл приёмку до
терминальной стадии «Завершение» (stage_code='completion'), но is_completed=0 —
приёмка навсегда висела EXPECTED. Терминальная стадия skladbot = приём."""

from backend.services.fulfillment_service import _inbound_accept_signal


def test_is_completed_true_always_accepts():
    assert _inbound_accept_signal("skladbot", None, None, True) is True
    assert _inbound_accept_signal("wmscelicom", None, None, True) is True
    assert _inbound_accept_signal("migfull", None, None, True) is True


def test_skladbot_completion_stage_accepts_without_is_completed():
    # Живой кейс FR 202523: stage=completion / «Завершение», is_completed=0.
    assert _inbound_accept_signal("skladbot", "completion", "Завершение", False) is True
    assert _inbound_accept_signal("skladbot", "COMPLETION", None, False) is True
    assert _inbound_accept_signal("skladbot", None, "завершение", False) is True


def test_skladbot_wip_stage_does_not_accept():
    # Незавершённая приёмка (какая-то промежуточная стадия) не должна авто-приниматься.
    assert _inbound_accept_signal("skladbot", "acceptance", "Приемка", False) is False
    assert _inbound_accept_signal("skladbot", None, None, False) is False


def test_other_providers_need_is_completed():
    # У wmscelicom/migfull стадия НЕ является сигналом — только is_completed.
    assert _inbound_accept_signal("wmscelicom", "completion", "Завершение", False) is False
    assert _inbound_accept_signal("migfull", "completion", "Завершение", False) is False
