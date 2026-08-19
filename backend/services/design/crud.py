# ruff: noqa: RUF002, RUF003
"""CRUD задач дизайна (write-side): нумерация DES-N, создание, PATCH, назначение.

Read-side (list_tasks / get_task / product_suggest / to_list_items) — в
queries.py, реэкспортирован здесь: состав API crud из спека F1 сохранён,
файлы держатся <500 строк. Кэша нет (Р10).

Нумерация (Р14): max+1 под advisory-xact-lock проекта, max считается ВКЛЮЧАЯ
soft-deleted строки — журнал append-only, номер не переиспользуется (partial-
unique игнорирует мёртвых, но бизнес-правило строже).
"""

import logging
import re
from typing import Any

from sqlalchemy import Integer, cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.auth import ProjectMember
from backend.models.design import (
    DesignTask,
    DesignTaskComment,
    DesignTaskEvent,
    DesignTaskStatus,
)
from backend.schemas.design import DesignTaskCreate, DesignTaskUpdate
from backend.services.design.common import actor_name, get_task_row, is_lead
from backend.services.design.notify import notify_assigned
from backend.services.design.queries import (  # noqa: F401  (реэкспорт read-side)
    get_task,
    list_tasks,
    product_suggest,
    to_list_items,
)
from backend.services.design.state import _commit_and_notify, apply_transition_locked
from backend.utils.time import utcnow

logger = logging.getLogger("dds.design")

_S = DesignTaskStatus
_TERMINAL_VALUES = (_S.ACCEPTED.value, _S.CANCELLED.value)
_SORT_STEP = 1000
# Класс advisory-lock нумерации модуля design (int4; ключ 2 — project_id).
_NUMBER_LOCK_CLASS = 0x00DE516  # 910614

# NOT NULL колонки DesignTask, для которых явный null в PATCH — ошибка (Р13).
_NOT_NULL_FIELDS = frozenset(
    {"title", "description", "sheet_url", "work_type", "complexity", "is_urgent", "is_outsourced"}
)
_LEAD_ONLY_FIELDS = frozenset({"complexity", "is_outsourced"})

# Р18: свой номер заявки. Разрешены латиница, кириллица, цифры, дефис, точка и
# подчёркивание — без пробелов и слешей. Регекс держим В ОДНОМ месте: фронт на
# него не полагается вовсе, а показывает текст 400 от бэка.
_NUMBER_MAX_LEN = 40
_NUMBER_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9._-]+$")


# ─── Нумерация (Р14) ─────────────────────────────────────────────────────────


async def next_number(db: AsyncSession, project_id: int) -> str:
    """`DES-N`: max+1 под pg_advisory_xact_lock(проект) — гонки не дублируют номер.

    Замок живёт до конца транзакции (совместим с PgBouncer transaction-pooling).
    Фильтра is_deleted НЕТ намеренно: номер удалённой задачи не переиспользуется.

    ⚠️ Счётчик НЕ монотонный (Р18, волна B v2). Максимум берётся по строкам, всё
    ещё подходящим под `^DES-\\d+$`; если ведущий переименует САМУЮ СТАРШУЮ такую
    задачу в свой номер, она выпадет из максимума и следующая заявка получит
    освободившийся `DES-N` повторно. Уникальность не страдает — номер физически
    свободен. Монотонность потребовала бы персистентного счётчика на проект
    (миграция); закреплено тестом test_renaming_maximal_number_frees_it_for_reuse.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :pid)"),
        {"cls": _NUMBER_LOCK_CLASS, "pid": project_id % 2**31},
    )
    res = await db.execute(
        select(func.coalesce(func.max(cast(func.substr(DesignTask.number, 5), Integer)), 0)).where(
            DesignTask.project_id == project_id,
            DesignTask.number.op("~")(r"^DES-\d+$"),
        )
    )
    return f"DES-{int(res.scalar() or 0) + 1}"


async def _apply_number_change(
    db: AsyncSession, project_id: int, task: DesignTask, raw: str, lead: bool, user: Any
) -> None:
    """Смена номера заявки: гварды CONTRACT-V2 §2 в том же порядке и с теми же текстами.

    Проверка занятости и запись идут под ТЕМ ЖЕ advisory-локом, что и next_number:
    иначе две параллельные смены на одно значение обе проходят проверку и падают
    IntegrityError (500) вместо честного 400.
    """
    if not lead:
        raise PermissionError("Номер заявки меняет только ведущий дизайнер")
    if task.status in _TERMINAL_VALUES:
        raise PermissionError("Номер закрытой задачи не меняется")

    number = raw.strip()
    if not number:
        raise ValueError("Номер не может быть пустым")
    if len(number) > _NUMBER_MAX_LEN:
        raise ValueError("Номер длиннее 40 символов")
    if not _NUMBER_RE.match(number):
        raise ValueError("Номер: только буквы, цифры, дефис, точка и подчёркивание")
    if number == task.number:
        return  # тот же номер — не «занят», а тихий no-op: ни записи, ни события

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :pid)"),
        {"cls": _NUMBER_LOCK_CLASS, "pid": project_id % 2**31},
    )
    taken = await db.execute(
        select(DesignTask.id)
        .where(
            DesignTask.project_id == project_id,
            DesignTask.number == number,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.id != task.id,
        )
        .limit(1)
    )
    if taken.scalar_one_or_none() is not None:
        raise ValueError("Номер уже занят")

    old = task.number
    task.number = number
    db.add(
        DesignTaskEvent(
            project_id=project_id,
            task_id=task.id,
            # Паттерн «не-переход» (как смена исполнителя): статус не двигается,
            # но факт обязан остаться в журнале.
            old_status=task.status,
            new_status=task.status,
            changed_at=utcnow(),
            changed_by=actor_name(user),
            comment=f"Номер изменён: {old} → {number}",
        )
    )


# ─── Создание ────────────────────────────────────────────────────────────────


async def create_task(
    db: AsyncSession, project_id: int, payload: DesignTaskCreate, user: Any
) -> DesignTask:
    """Заявка менеджера: NEW, в конец колонки (Р3), событие None→NEW.

    Notify-хука нет намеренно — лид увидит заявку по красной метке (Р5).
    """
    number = await next_number(db, project_id)  # advisory-lock проекта до commit
    res = await db.execute(
        select(func.coalesce(func.max(DesignTask.sort_order), 0)).where(
            DesignTask.project_id == project_id,
            DesignTask.status == _S.NEW.value,
            DesignTask.is_deleted == False,  # noqa: E712
        )
    )
    max_sort = int(res.scalar() or 0)

    task = DesignTask(
        project_id=project_id,
        number=number,
        title=payload.title,
        description=payload.description,
        sheet_url=str(payload.sheet_url),
        nm_id=payload.nm_id,
        work_type=payload.work_type,
        is_urgent=payload.is_urgent,
        due_date=payload.due_date,
        author_user_id=user.id,
        status=_S.NEW.value,
        sort_order=max_sort + _SORT_STEP,
    )
    db.add(task)
    await db.flush()
    db.add(
        DesignTaskEvent(
            project_id=project_id,
            task_id=task.id,
            old_status=None,
            new_status=_S.NEW.value,
            changed_at=utcnow(),
            changed_by=actor_name(user),
        )
    )
    await db.commit()
    await db.refresh(task)
    return task


# ─── Просмотр лидом (Р5) ─────────────────────────────────────────────────────


async def mark_viewed(
    db: AsyncSession, project_id: int, task_id: int, user: Any, member_role: str
) -> DesignTask:
    """Идемпотентно снимает красную метку: ставит viewed_by_lead_at, только lead."""
    if not is_lead(member_role):
        raise PermissionError("Отметку просмотра ставит только ведущий дизайнер")
    task = await get_task_row(db, project_id, task_id)
    if task.viewed_by_lead_at is None:
        task.viewed_by_lead_at = utcnow()
        await db.commit()
        await db.refresh(task)
    return task


# ─── PATCH ───────────────────────────────────────────────────────────────────


async def update_task(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    payload: DesignTaskUpdate,
    user: Any,
    member_role: str,
) -> DesignTask:
    """PATCH-семантика через model_dump(exclude_unset=True).

    Права: lead — везде, кроме терминалов; автор — везде, кроме REVIEW и
    терминалов. complexity/is_outsourced меняет только lead. Явный null для
    NOT NULL полей (title/description/sheet_url/work_type/…) — ValueError.
    """
    task = await get_task_row(db, project_id, task_id, for_update=True)
    lead = is_lead(member_role)
    data = payload.model_dump(exclude_unset=True)

    # Номер идёт ПЕРЕД общими гвардами: у него своя лесенка с собственными
    # текстами (CONTRACT-V2 §2), и на закрытой задаче пользователь должен узнать
    # именно про номер, а не общее «задачи не редактируются». Явный null здесь
    # значит «не менять» (поле NOT NULL, очистить его нельзя).
    number_raw = data.pop("number", None)
    if number_raw is not None:
        await _apply_number_change(db, project_id, task, number_raw, lead, user)

    if task.status in _TERMINAL_VALUES:
        raise PermissionError("Принятые и отменённые задачи не редактируются")
    if not lead:
        if task.author_user_id != user.id:
            raise PermissionError("Заявку может править её автор или ведущий")
        if task.status == _S.REVIEW.value:
            raise PermissionError("На проверке заявку правит только ведущий")

    for field in _NOT_NULL_FIELDS & data.keys():
        if data[field] is None:
            raise ValueError(f"Поле {field} обязательное — его нельзя очистить")
    if not lead and (_LEAD_ONLY_FIELDS & data.keys()):
        raise PermissionError("Сложность и аутсорс меняет только ведущий дизайнер")

    for field, value in data.items():
        if field == "sheet_url":
            value = str(value)
        setattr(task, field, value)
    await db.commit()
    await db.refresh(task)
    return task


# ─── Назначение (только lead) ────────────────────────────────────────────────


async def assign(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    assignee_user_id: int | None,
    user: Any,
    member_role: str,
) -> DesignTask:
    """Назначение/снятие исполнителя. Исполнитель обязан быть активным участником
    проекта (ProjectMember, is_deleted=false). NEW+назначение → NEW→ASSIGNED;
    ASSIGNED+снятие → ASSIGNED→NEW; снятие вне {NEW, ASSIGNED, ON_HOLD} запрещено
    (в работе/на проверке задача без исполнителя ломает матрицу прав); иначе —
    смена без смены статуса (событие old=new с комментарием — журнал фиксирует
    и передачу задачи)."""
    if not is_lead(member_role):
        raise PermissionError("Назначать исполнителя может только ведущий дизайнер")
    task = await get_task_row(db, project_id, task_id, for_update=True)
    if task.status in _TERMINAL_VALUES:
        raise ValueError("Задача закрыта — исполнитель не меняется")

    if assignee_user_id is not None:
        member_res = await db.execute(
            select(ProjectMember.id)
            .where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == assignee_user_id,
                ProjectMember.is_deleted == False,  # noqa: E712
            )
            .limit(1)
        )
        if member_res.scalar_one_or_none() is None:
            raise ValueError("Пользователь не состоит в проекте")
        task.assignee_user_id = assignee_user_id
        if task.status == _S.NEW.value:
            await apply_transition_locked(db, task, _S.ASSIGNED, user, member_role)
        else:
            db.add(
                DesignTaskEvent(
                    project_id=project_id,
                    task_id=task.id,
                    old_status=task.status,
                    new_status=task.status,
                    changed_at=utcnow(),
                    changed_by=actor_name(user),
                    comment="Смена исполнителя",
                )
            )
    else:
        if task.status == _S.ASSIGNED.value:
            await apply_transition_locked(db, task, _S.NEW, user, member_role)  # чистит assignee
        elif task.status in (_S.NEW.value, _S.ON_HOLD.value):
            task.assignee_user_id = None
            db.add(
                DesignTaskEvent(
                    project_id=project_id,
                    task_id=task.id,
                    old_status=task.status,
                    new_status=task.status,
                    changed_at=utcnow(),
                    changed_by=actor_name(user),
                    comment="Исполнитель снят",
                )
            )
        else:
            raise ValueError("Сначала верните задачу в «Новые» или «Отложенные»")

    await _commit_and_notify(db)
    await db.refresh(task)
    try:
        await notify_assigned(task, assignee_user_id, actor=user)
    except Exception as e:  # best-effort (§6.8)
        logger.warning("design notify_assigned failed: %s", e)
    return task


# ─── Удаление (Р: author|lead, любой статус — решение lead) ──────────────────


async def delete_task(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    user: Any,
    member_role: str,
) -> None:
    """Мягкое удаление задачи (iron rule 3: soft_delete, не db.delete).

    Право — автор или lead, из любого статуса (решение lead). Журнал append-only
    получает событие «Задача удалена»; номер DES-N не переиспользуется (Р14).
    """
    task = await get_task_row(db, project_id, task_id, for_update=True)
    if not (is_lead(member_role) or task.author_user_id == user.id):
        raise PermissionError("Удалить задачу может её автор или ведущий")
    task.soft_delete()
    db.add(
        DesignTaskEvent(
            project_id=project_id,
            task_id=task.id,
            old_status=task.status,
            new_status=task.status,
            changed_at=utcnow(),
            changed_by=actor_name(user),
            comment="Задача удалена",
        )
    )
    await db.commit()


# ─── Комментарии ─────────────────────────────────────────────────────────────


async def add_comment(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    body: str,
    user: Any,
    *,
    file_data: bytes | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
) -> DesignTaskComment:
    """Комментарий (+опциональное вложение через files-хелпер).

    Перед заливкой вложения валидационная транзакция коммитится — БД-коннект
    не висит через внешний HTTP к MinIO (канон learnings, вариант А).
    """
    await get_task_row(db, project_id, task_id)
    if not body or not body.strip():
        raise ValueError("Комментарий пуст")

    minio_path: str | None = None
    original_filename: str | None = None
    if file_data is not None and filename:
        from backend.services.design import files as files_mod

        await db.commit()  # закрыть autobegin-транзакцию валидации до MinIO
        minio_path, original_filename = await files_mod.upload_comment_attachment(
            project_id=project_id,
            task_id=task_id,
            file_data=file_data,
            filename=filename,
            mime_type=mime_type,
        )

    comment = DesignTaskComment(
        project_id=project_id,
        task_id=task_id,
        author_user_id=user.id,
        body=body.strip(),
        minio_path=minio_path,
        original_filename=original_filename,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment
