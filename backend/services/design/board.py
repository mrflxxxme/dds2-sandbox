# ruff: noqa: RUF002, RUF003
"""Доска (Р3, Р4): 6 колонок одним ответом + move с midpoint-вставкой.

sort_order: шаг 1000, вставка в середину зазора; зазор <1 → перенумерация
колонки шагом 1000 В ТОЙ ЖЕ транзакции (Р3). Конфликты — last-write-wins под
FOR UPDATE (Р4). Кэша нет (Р10).
"""

import logging
from typing import Any

from sqlalchemy import case, func, null, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.models.design import (
    DESIGN_BOARD_STATUSES,
    DesignTask,
    DesignTaskStatus,
)
from backend.schemas.design import DesignBoardResponse, DesignTaskListItem
from backend.services.design.common import get_task_row, is_lead
from backend.services.design.queries import to_list_items
from backend.services.design.state import _commit_and_notify, apply_transition_locked

logger = logging.getLogger("dds.design")

_SORT_STEP = 1000
_COLUMN_LIMIT = 200
# Предохранитель перенумерации: колонка выбирается ЦЕЛИКОМ, но не более 2000 строк.
_RENUMBER_LIMIT = 2000
_BOARD_VALUES = [s.value for s in DESIGN_BOARD_STATUSES]
# Класс advisory-lock перетаскиваний доски (int4; ключ 2 — project_id).
_MOVE_LOCK_CLASS = 0x00DE517  # 911639, класс свободен (нумерация crud — 0xDE516)


async def get_board(
    db: AsyncSession, project_id: int, user: Any, member_role: str
) -> DesignBoardResponse:
    """Одна выборка по 6 колонкам + counts по всем статусам (вкл. ON_HOLD/
    CANCELLED — они вне доски, доступны фильтром).

    Пер-колоночный лимит — в SQL: row_number() OVER (PARTITION BY status) <= 200.
    Порядок внутри партиции: живые колонки — sort_order, id; ACCEPTED —
    accepted_at DESC NULLS LAST, id DESC (свежепринятые сверху). Партиция
    однородна по статусу, поэтому CASE-ключи безопасны.
    """
    _acc = DesignTask.status == DesignTaskStatus.ACCEPTED.value
    rn = (
        func.row_number()
        .over(
            partition_by=DesignTask.status,
            order_by=(
                case((_acc, null()), else_=DesignTask.sort_order).asc(),
                case((_acc, DesignTask.accepted_at)).desc().nulls_last(),
                # -id для ACCEPTED = id DESC при общем ASC.
                case((_acc, -DesignTask.id), else_=DesignTask.id).asc(),
            ),
        )
        .label("rn")
    )
    sub = (
        select(DesignTask, rn)
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.status.in_(_BOARD_VALUES),
        )
        .subquery()
    )
    dt = aliased(DesignTask, sub)
    res = await db.execute(
        select(dt)
        .where(sub.c.rn <= _COLUMN_LIMIT)
        .order_by(sub.c.status, sub.c.rn)
        .limit(_COLUMN_LIMIT * len(_BOARD_VALUES))
    )
    tasks = list(res.scalars().all())

    by_status: dict[str, list[DesignTask]] = {v: [] for v in _BOARD_VALUES}
    for t in tasks:
        by_status[t.status].append(t)

    flat = [t for column in by_status.values() for t in column]
    items = {i.id: i for i in await to_list_items(db, project_id, flat)}
    columns: dict[str, list[DesignTaskListItem]] = {
        status: [items[t.id] for t in column] for status, column in by_status.items()
    }

    counts_res = await db.execute(
        select(DesignTask.status, func.count())
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
        )
        .group_by(DesignTask.status)
    )
    counts = {s.value: 0 for s in DesignTaskStatus}
    counts.update({row[0]: row[1] for row in counts_res.all()})

    return DesignBoardResponse(columns=columns, counts=counts)


async def move_task(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    to_status: str,
    after_task_id: int | None,
    user: Any,
    member_role: str,
    comment: str | None = None,
) -> DesignTask:
    """Перетаскивание (Р4): под FOR UPDATE смена статуса по той же матрице/гвардам
    (drag&drop и деталка не расходятся) + позиционирование.

    Начало — pg_advisory_xact_lock(проект): два параллельных перетаскивания
    берут замок в одном порядке и не дедлочатся на row-lock'ах колонок.
    comment прокидывается в переход (dnd в «Правки» требует причину — фронт Ф3).
    Перестановка ВНУТРИ колонки (to_status == текущий) — только lead (Р9:
    порядок задаёт лид); позиционирование при легитимной смене колонки доступно
    тому, кому разрешён сам переход. Опорная задача (after_task_id) обязана быть
    того же проекта, живой и в целевой колонке — иначе единый текст «Опорная
    задача не найдена» (существование чужих задач не раскрывается).
    """
    if to_status not in _BOARD_VALUES:
        raise ValueError("Недопустимая колонка доски")

    await db.execute(
        text("SELECT pg_advisory_xact_lock(:cls, :pid)"),
        {"cls": _MOVE_LOCK_CLASS, "pid": project_id % 2**31},
    )
    task = await get_task_row(db, project_id, task_id, for_update=True)
    if to_status == task.status:
        if not is_lead(member_role):
            raise PermissionError("Перестановка карточек в колонке доступна только ведущему")
    else:
        await apply_transition_locked(db, task, to_status, user, member_role, comment)

    # Колонка под замком — позиционирование и возможная перенумерация атомарны.
    col_res = await db.execute(
        select(DesignTask)
        .where(
            DesignTask.project_id == project_id,
            DesignTask.status == to_status,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.id != task.id,
        )
        .order_by(DesignTask.sort_order, DesignTask.id)
        .with_for_update()
        .limit(_RENUMBER_LIMIT)
    )
    column = list(col_res.scalars().all())
    if len(column) >= _RENUMBER_LIMIT:
        logger.warning(
            "design move_task: колонка %s проекта %d упёрлась в предохранитель "
            "%d строк — перенумерация может не покрыть хвост",
            to_status, project_id, _RENUMBER_LIMIT,
        )

    anchor: DesignTask | None = None
    if after_task_id is not None:
        anchor = next((t for t in column if t.id == after_task_id), None)
        if anchor is None:
            raise ValueError("Опорная задача не найдена")

    insert_at = column.index(anchor) + 1 if anchor is not None else 0
    prev_order = anchor.sort_order if anchor is not None else 0
    next_order = (
        column[insert_at].sort_order if insert_at < len(column) else prev_order + 2 * _SORT_STEP
    )

    if next_order - prev_order > 1:
        task.sort_order = (prev_order + next_order) // 2
    else:
        # Зазор исчерпан — перенумерация колонки шагом 1000 в этой же транзакции (Р3).
        reordered = column[:insert_at] + [task] + column[insert_at:]
        for idx, row in enumerate(reordered):
            row.sort_order = (idx + 1) * _SORT_STEP

    await _commit_and_notify(db)
    await db.refresh(task)
    return task
