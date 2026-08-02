# ruff: noqa: RUF002, RUF003
"""Личные TG-уведомления модуля «Дизайн карточек» (Ф4, Р6).

4 события: назначили → исполнителю; вернули на доработку (REVIEW→REVISION) →
исполнителю с причиной; приняли (→ACCEPTED) → исполнителю; сдали версию
(→REVIEW) → автору. Резолв user_id → TelegramBotUser.telegram_id (deep-link
привязка через /start уже работает); нет привязки — молча скип. Доставка —
send_analytics_message (httpx, работает вне aiogram-синглтона, в т.ч. в
web-процессе). Вызываются из сервисов Ф1 строго ПОСЛЕ commit; весь блок
best-effort (§6.8): сбой TG не роняет операцию. Сигнатуры хуков зафиксированы
в Ф1 — НЕ менять (их зовёт диспатч _commit_and_notify всех путей переходов).
"""

import html
import logging
from typing import Any

from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.models.auth import Project, ProjectMember
from backend.models.design import DesignSubmission, DesignTask, DesignTaskStatus
from backend.models.telegram import TelegramBotUser
from backend.services import telegram_service

logger = logging.getLogger("dds.design")

# Прод-домен приложения: уведомления реально уходят только с прода (локалка —
# без токена бота, send_analytics_message = no-op). Как в draft_staleness_watch.
_APP_BASE_URL = "https://app.vyatkin-wb.ru"

_S = DesignTaskStatus


def _task_link(slug: str | None, task_id: int) -> str | None:
    """Deep-link на деталку задачи; None — slug не найден (ссылку опускаем)."""
    if not slug:
        return None
    return f"{_APP_BASE_URL}/p/{html.escape(slug)}/design-tasks/{task_id}"


def _with_link(text: str, url: str | None) -> str:
    if url:
        return f'{text}\n🔗 <a href="{url}">Открыть задачу</a>'
    return text


async def _resolve_chat_id(db: AsyncSession, user_id: int | None, project_id: int) -> int | None:
    """user_id → telegram_id (личный чат с ботом). None — привязки нет (скип, Р6).

    Резолв только при АКТИВНОМ членстве в проекте (ProjectMember без is_deleted):
    удалённый из проекта пользователь уведомления не получает.
    """
    if user_id is None:
        return None
    res = await db.execute(
        select(TelegramBotUser.telegram_id)
        .join(ProjectMember, ProjectMember.user_id == TelegramBotUser.user_id)
        .where(
            TelegramBotUser.user_id == user_id,
            ProjectMember.project_id == project_id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
        .limit(1)
    )
    return res.scalar_one_or_none()


async def _project_slug(db: AsyncSession, project_id: int) -> str | None:
    res = await db.execute(
        select(Project.slug).where(
            Project.id == project_id,
            Project.is_deleted == False,  # noqa: E712
        )
    )
    return res.scalar_one_or_none()


async def _latest_version_no(db: AsyncSession, task: DesignTask) -> int:
    """Номер только что сданной версии = max(version_no) задачи (сдача уже в БД —
    хук зовётся после commit)."""
    res = await db.execute(
        select(sqlfunc.max(DesignSubmission.version_no)).where(
            DesignSubmission.project_id == task.project_id,
            DesignSubmission.task_id == task.id,
        )
    )
    return int(res.scalar() or 1)


async def notify_status_changed(
    task: DesignTask,
    old_status: str,
    new_status: str,
    comment: str | None = None,
    actor: Any = None,
) -> None:
    """Личное TG-уведомление по переходу (Р6, тексты — спек F4):

    - →REVIEW («сдали версию») — автору: «DES-N ждёт проверки, версия N»;
    - REVIEW→REVISION («вернули») — исполнителю: «DES-N вернули: <причина>»;
    - →ACCEPTED («приняли») — исполнителю: «DES-N принята».

    Прочие переходы — no-op. Best-effort: любое исключение глотается (§6.8).
    """
    try:
        esc = html.escape
        number = esc(str(task.number))
        recipient: int | None
        async with AsyncSessionLocal() as db:
            if new_status == _S.REVIEW.value:
                recipient = task.author_user_id
                version_no = await _latest_version_no(db, task)
                text = f"📬 <b>{number}</b> ждёт проверки, версия {version_no}"
            elif new_status == _S.REVISION.value and old_status == _S.REVIEW.value:
                # ON_HOLD→REVISION — возврат из отложенных, не «вернули на доработку».
                recipient = task.assignee_user_id
                reason = (comment or "").strip() or "—"
                text = f"✏️ <b>{number}</b> вернули: {esc(reason)}"
            elif new_status == _S.ACCEPTED.value:
                recipient = task.assignee_user_id
                text = f"✅ <b>{number}</b> принята"
            else:
                return
            chat_id = await _resolve_chat_id(db, recipient, task.project_id)
            if chat_id is None:
                return  # нет привязки — молча скип (Р6)
            slug = await _project_slug(db, task.project_id)
        await telegram_service.send_analytics_message(
            chat_id, _with_link(text, _task_link(slug, task.id))
        )
    except Exception as e:  # best-effort (§6.8): сбой уведомления не роняет операцию
        logger.warning("design notify_status_changed failed (task=%s): %s", task.id, e)


async def notify_assigned(task: DesignTask, assignee_user_id: int | None, actor: Any = None) -> None:
    """«Вам назначена задача» — исполнителю: «DES-N · <title> · срок <дата>».

    Снятие исполнителя (None) не уведомляет. Best-effort (§6.8).
    """
    try:
        if assignee_user_id is None:
            return
        esc = html.escape
        async with AsyncSessionLocal() as db:
            chat_id = await _resolve_chat_id(db, assignee_user_id, task.project_id)
            if chat_id is None:
                return  # нет привязки — молча скип (Р6)
            slug = await _project_slug(db, task.project_id)
        due = task.due_date.strftime("%d.%m.%Y") if task.due_date else "—"
        text = f"🎨 <b>{esc(str(task.number))}</b> · {esc(str(task.title))} · срок {due}"
        await telegram_service.send_analytics_message(
            chat_id, _with_link(text, _task_link(slug, task.id))
        )
    except Exception as e:  # best-effort (§6.8)
        logger.warning("design notify_assigned failed (task=%s): %s", task.id, e)
