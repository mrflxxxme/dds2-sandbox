# ruff: noqa: RUF001, RUF002, RUF003
"""Утренняя TG-сводка модуля «Дизайн карточек» + «срок завтра» (Ф4).

Раз в день (конфиг-константы DIGEST_HOUR_MSK / DIGEST_MINUTE_MSK, дефолт
09:00 МСК — STATUS «Открытые вопросы»):

- в каждый чат проекта с design_notify_enabled — HTML-сводка: в работе /
  на проверке / просрочено / принято вчера + deep-link на доску;
- исполнителям задач с due_date = завтра (активный статус) — личное
  напоминание (резолв через TelegramBotUser, нет привязки — молча скип).

Антиспам — Redis SET NX EX, ключи РАЗДЕЛЬНЫЕ по веткам (сбой одной не гасит
другую): design_digest:{project_id}:{date}:digest — сводка в чаты,
design_digest:{project_id}:{date}:due — личные «срок завтра». Ключ захватывается
ПОСЛЕ успешной сборки данных, непосредственно перед отправкой; повторный запуск
джобы в тот же день — no-op. Redis недоступен — шлём (best-effort, как
draft_staleness_watch: лучше редкий повтор, чем молчание). Сбой одного
проекта не роняет остальные; asyncio.CancelledError пробрасывается (§6.8).
Доставка — send_analytics_message (httpx, без aiogram-синглтона); вся отправка
идёт ВНЕ открытой DB-сессии (сессия закрывается до первого внешнего HTTP).
"""

import asyncio
import html
import logging
from datetime import date, datetime, time, timedelta

import pytz
from sqlalchemy import func as sqlfunc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import get_redis
from backend.database import AsyncSessionLocal
from backend.models.auth import Project, ProjectMember
from backend.models.design import DESIGN_ACTIVE_STATUSES, DesignTask, DesignTaskStatus
from backend.models.telegram import TelegramBotUser, TelegramChatBinding
from backend.services import telegram_service

logger = logging.getLogger("dds.scheduler")

MSK = pytz.timezone("Europe/Moscow")
_APP_BASE_URL = "https://app.vyatkin-wb.ru"

# Конфиг-константы времени сводки (читает регистрация в scheduler/__init__.py).
# Дефолт 09:00 МСК — до ответа менеджера по кабинетам (STATUS «Открытые вопросы»).
DIGEST_HOUR_MSK = 9
DIGEST_MINUTE_MSK = 0

# TTL антиспам-ключа: дата в ключе делает его одноразовым, TTL — только уборка.
_ANTISPAM_TTL_SECONDS = 48 * 3600
# Предохранители от простыни: больше строк не разбираем.
_DUE_SOON_CAP = 200
_PROJECTS_CAP = 1000

_ACTIVE_VALUES = [s.value for s in DESIGN_ACTIVE_STATUSES]


async def _antispam_acquire(key: str) -> bool:
    """True — слать можно (ключ захвачен). SET NX EX; Redis недоступен/сбоит —
    True (best-effort, повтор капнут суточным расписанием джобы)."""
    r = await get_redis()
    if r is None:
        return True
    try:
        return bool(await r.set(key, "1", ex=_ANTISPAM_TTL_SECONDS, nx=True))
    except Exception as e:
        logger.warning("Design digest: antispam redis error (%s) — sending anyway", e)
        return True


async def get_digest_counts(
    db: AsyncSession, project_id: int, *, now_msk: datetime
) -> dict[str, int]:
    """Счётчики сводки проекта: в работе / на проверке / просрочено / принято вчера.

    Просрочка: due_date < сегодня (МСК) и активный статус; задача БЕЗ срока
    просроченной НЕ считается (STATUS «Открытые вопросы»). «Принято вчера» —
    accepted_at внутри вчерашних календарных суток МСК (в БД naive-UTC).
    """
    today = now_msk.date()
    rows = await db.execute(
        select(DesignTask.status, sqlfunc.count())
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.status.in_(
                [DesignTaskStatus.IN_PROGRESS.value, DesignTaskStatus.REVIEW.value]
            ),
        )
        .group_by(DesignTask.status)
    )
    by_status: dict[str, int] = {status: cnt for status, cnt in rows.all()}

    overdue = (
        await db.execute(
            select(sqlfunc.count())
            .select_from(DesignTask)
            .where(
                DesignTask.project_id == project_id,
                DesignTask.is_deleted == False,  # noqa: E712
                DesignTask.due_date.is_not(None),
                DesignTask.due_date < today,
                DesignTask.status.in_(_ACTIVE_VALUES),
            )
        )
    ).scalar() or 0

    # Границы вчерашних суток МСК → naive-UTC (accepted_at пишется utcnow()).
    day_start_msk = MSK.localize(datetime.combine(today, time.min))
    y_start_utc = (day_start_msk - timedelta(days=1)).astimezone(pytz.utc).replace(tzinfo=None)
    y_end_utc = day_start_msk.astimezone(pytz.utc).replace(tzinfo=None)
    accepted_yesterday = (
        await db.execute(
            select(sqlfunc.count())
            .select_from(DesignTask)
            .where(
                DesignTask.project_id == project_id,
                DesignTask.is_deleted == False,  # noqa: E712
                DesignTask.status == DesignTaskStatus.ACCEPTED.value,
                DesignTask.accepted_at >= y_start_utc,
                DesignTask.accepted_at < y_end_utc,
            )
        )
    ).scalar() or 0

    return {
        "in_progress": by_status.get(DesignTaskStatus.IN_PROGRESS.value, 0),
        "review": by_status.get(DesignTaskStatus.REVIEW.value, 0),
        "overdue": int(overdue),
        "accepted_yesterday": int(accepted_yesterday),
    }


def build_digest_message(slug: str | None, counts: dict[str, int]) -> str:
    """HTML утренней сводки для чата (+deep-link на доску, если slug известен)."""
    lines = [
        "🎨 <b>Дизайн-задачи</b> — утренняя сводка",
        "",
        f"🔧 В работе: <b>{counts['in_progress']}</b>",
        f"👀 На проверке: <b>{counts['review']}</b>",
        f"⚠ Просрочено: <b>{counts['overdue']}</b>",
        f"✅ Принято вчера: <b>{counts['accepted_yesterday']}</b>",
    ]
    if slug:
        url = f"{_APP_BASE_URL}/p/{html.escape(slug)}/design-tasks"
        lines.append(f'\n🔗 <a href="{url}">Открыть доску</a>')
    return "\n".join(lines)


def build_due_tomorrow_message(slug: str | None, task_id: int, number: str, title: str) -> str:
    """HTML личного напоминания исполнителю «срок завтра»."""
    esc = html.escape
    text = f"⏰ <b>{esc(str(number))}</b> · {esc(str(title))} — срок завтра"
    if slug:
        url = f"{_APP_BASE_URL}/p/{esc(slug)}/design-tasks/{task_id}"
        text += f'\n🔗 <a href="{url}">Открыть задачу</a>'
    return text


async def get_due_tomorrow_tasks(
    db: AsyncSession, project_id: int, *, tomorrow: date
) -> list[tuple[int, str, str, int]]:
    """Активные задачи проекта с исполнителем и due_date = завтра:
    [(task_id, number, title, assignee_user_id)]."""
    rows = await db.execute(
        select(
            DesignTask.id,
            DesignTask.number,
            DesignTask.title,
            DesignTask.assignee_user_id,
        )
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.due_date == tomorrow,
            DesignTask.assignee_user_id.is_not(None),
            DesignTask.status.in_(_ACTIVE_VALUES),
        )
        .order_by(DesignTask.id)
        .limit(_DUE_SOON_CAP)
    )
    return [
        (int(tid), str(num), str(title), int(assignee))
        for tid, num, title, assignee in rows.all()
    ]


async def _get_target_project_ids(tomorrow: date) -> list[int]:
    """Проекты для обхода: с включёнными design-чатами ∪ с задачами «срок завтра».

    Обе ветки — JOIN на Project с фильтром is_deleted: по soft-deleted проекту
    ни сводка, ни личные напоминания не уходят (iron rule 2).
    """
    async with AsyncSessionLocal() as db:
        enabled = (
            await db.execute(
                select(TelegramChatBinding.project_id)
                .join(Project, Project.id == TelegramChatBinding.project_id)
                .where(
                    TelegramChatBinding.design_notify_enabled == True,  # noqa: E712
                    Project.is_deleted == False,  # noqa: E712
                )
                .distinct()
                .limit(_PROJECTS_CAP)
            )
        ).scalars().all()
        due = (
            await db.execute(
                select(DesignTask.project_id)
                .join(Project, Project.id == DesignTask.project_id)
                .where(
                    DesignTask.is_deleted == False,  # noqa: E712
                    DesignTask.due_date == tomorrow,
                    DesignTask.assignee_user_id.is_not(None),
                    DesignTask.status.in_(_ACTIVE_VALUES),
                    Project.is_deleted == False,  # noqa: E712
                )
                .distinct()
                .limit(_PROJECTS_CAP)
            )
        ).scalars().all()
    return sorted(set(enabled) | set(due))


async def _resolve_telegram_ids(
    db: AsyncSession, project_id: int, user_ids: set[int]
) -> dict[int, int]:
    """Батч-резолв user_id → telegram_id одним SELECT (снимает N+1, HIGH-3).

    Только пользователи с АКТИВНЫМ членством в проекте (ProjectMember без
    is_deleted): удалённый из проекта исполнитель напоминание не получает.
    """
    if not user_ids:
        return {}
    rows = await db.execute(
        select(TelegramBotUser.user_id, TelegramBotUser.telegram_id)
        .join(ProjectMember, ProjectMember.user_id == TelegramBotUser.user_id)
        .where(
            TelegramBotUser.user_id.in_(user_ids),
            ProjectMember.project_id == project_id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
    )
    return {int(uid): int(tg_id) for uid, tg_id in rows.all()}


async def send_design_digests() -> None:
    """Обойти проекты: сводка в design-чаты + «срок завтра» исполнителям.

    Порядок в проекте: (1) собрать ВСЕ данные в короткой DB-сессии (slug,
    chat_ids, счётчики, задачи «срок завтра» с батч-резолвом telegram_id) и
    закрыть её; (2) захватить антиспам-ключ ветки; (3) слать gather'ом. Ни
    одного внешнего HTTP внутри открытой сессии (иначе коннект pgbouncer висит
    idle-in-transaction на время TG-запросов). Ключи раздельные —
    …:digest и …:due — сбой одной ветки не гасит другую.
    """
    now_msk = datetime.now(MSK)
    today = now_msk.date()
    tomorrow = today + timedelta(days=1)
    project_ids = await _get_target_project_ids(tomorrow)
    if not project_ids:
        return

    sent = 0
    for project_id in project_ids:
        try:
            # ── Фаза 1: сборка данных (сессия закрывается до отправок) ──────
            async with AsyncSessionLocal() as db:
                slug = (
                    await db.execute(
                        select(Project.slug).where(
                            Project.id == project_id,
                            Project.is_deleted == False,  # noqa: E712
                        )
                    )
                ).scalar_one_or_none()

                chat_ids = [
                    c.chat_id
                    for c in await telegram_service.list_design_notify_chats(db, project_id)
                ]
                counts = (
                    await get_digest_counts(db, project_id, now_msk=now_msk)
                    if chat_ids
                    else None
                )

                due_tasks = await get_due_tomorrow_tasks(db, project_id, tomorrow=tomorrow)
                tg_by_user = await _resolve_telegram_ids(
                    db, project_id, {assignee for _, _, _, assignee in due_tasks}
                )

            # ── Фаза 2: отправка (вне сессии), антиспам — после сборки ──────
            if chat_ids and counts is not None:
                key = f"design_digest:{project_id}:{today.isoformat()}:digest"
                if await _antispam_acquire(key):
                    text = build_digest_message(slug, counts)
                    results = await asyncio.gather(
                        *[
                            telegram_service.send_analytics_message(chat_id, text)
                            for chat_id in chat_ids
                        ],
                        return_exceptions=True,
                    )
                    sent += sum(1 for ok in results if ok is True)

            # «Срок завтра» — личные напоминания исполнителям (независимо от чатов).
            # Нет привязки / не член проекта — молча скип (Р6).
            due_messages = [
                (tg_by_user[assignee], build_due_tomorrow_message(slug, task_id, number, title))
                for task_id, number, title, assignee in due_tasks
                if assignee in tg_by_user
            ]
            if due_messages:
                key = f"design_digest:{project_id}:{today.isoformat()}:due"
                if await _antispam_acquire(key):
                    results = await asyncio.gather(
                        *[
                            telegram_service.send_analytics_message(chat_id, text)
                            for chat_id, text in due_messages
                        ],
                        return_exceptions=True,
                    )
                    sent += sum(1 for ok in results if ok is True)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # best-effort: один проект не должен ронять джобу
            logger.error("Design digest: project %d failed — %s", project_id, e, exc_info=True)
    if sent:
        logger.info("Design digest: sent %d message(s)", sent)
