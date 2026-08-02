# ruff: noqa: RUF002, RUF003
"""Notify-хуки модуля «Дизайн карточек» — no-op заглушки до Ф4.

Ф4 наполнит их личными TG-уведомлениями (Р6: user_id → TelegramBotUser,
send_analytics_message, best-effort после commit) НЕ меняя сигнатур ядра.
Вызываются из state/crud/files строго ПОСЛЕ commit, обёрнуты try/except —
сбой уведомления не роняет операцию (инвариант §6.8).
"""

from typing import Any

from backend.models.design import DesignTask


async def notify_status_changed(
    task: DesignTask,
    old_status: str,
    new_status: str,
    comment: str | None = None,
    actor: Any = None,
) -> None:
    """TODO(F4): личное TG-уведомление участникам о смене статуса."""
    return None


async def notify_assigned(task: DesignTask, assignee_user_id: int | None, actor: Any = None) -> None:
    """TODO(F4): личное TG-уведомление «вам назначена задача»."""
    return None
