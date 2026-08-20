# ruff: noqa: RUF002, RUF003
"""Read-side задач дизайна: список, деталка, автоподсказка товара, обогащение.

Вынесен из crud.py по правилу «файл <500 строк»; API остаётся доступным через
crud (реэкспорт) — состав модулей спека F1 не меняется. Кэша нет (Р10).
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy import func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.auth import Project, ProjectMember, User
from backend.models.cost import Nomenclature
from backend.rbac import get_effective_pages
from backend.models.design import (
    DESIGN_ACTIVE_STATUSES,
    DesignAttribute,
    DesignAttributeValue,
    DesignLabel,
    DesignMaterial,
    DesignSubmission,
    DesignSubmissionFile,
    DesignTask,
    DesignTaskAttributeValue,
    DesignTaskComment,
    DesignTaskEvent,
    DesignTaskLabel,
    DesignTaskStatus,
)
from backend.schemas.design import (
    DesignAttributeRef,
    DesignCommentOut,
    DesignEventOut,
    DesignLabelHistoryRow,
    DesignLabelRef,
    DesignMaterialOut,
    DesignProductSuggestion,
    DesignSubmissionFileOut,
    DesignSubmissionOut,
    DesignTaskDetail,
    DesignTaskListItem,
    DesignTaskPermissions,
)
from backend.services.design.common import get_task_row
from backend.services.design.permissions import compute_permissions
from backend.services.design.state import can_user_transition
from backend.utils.time import msk_today, utcnow

_S = DesignTaskStatus

# Потолки на задачу для .limit() батч-выборок разметки: без них выборка
# формально безлимитная (правило backend.md), а лишних строк там и не бывает.
_MAX_LABELS_PER_TASK = 100
_MAX_ATTRS_PER_TASK = 200


def escape_like(value: str) -> str:
    """Экранировать %/_ пользовательского ввода для ILIKE (правило backend.md)."""
    return value.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


async def _user_names(db: AsyncSession, user_ids: set[int]) -> dict[int, str]:
    """Только нужные колонки — не тащить весь User (password_hash и пр.)."""
    if not user_ids:
        return {}
    res = await db.execute(
        select(User.id, User.first_name, User.last_name, User.username)
        .where(User.id.in_(user_ids))
        .limit(len(user_ids))
    )
    return {
        row.id: (f"{row.first_name or ''} {row.last_name or ''}".strip() or row.username)
        for row in res.all()
    }


async def load_labels_by_task(
    db: AsyncSession, project_id: int, task_ids: list[int]
) -> dict[int, list[DesignLabelRef]]:
    """Текущие метки задач ОДНИМ запросом на страницу (инвариант §6.10, без N+1)."""
    if not task_ids:
        return {}
    res = await db.execute(
        select(
            DesignTaskLabel.task_id,
            DesignLabel.id,
            DesignLabel.name,
            DesignLabel.color,
        )
        .join(DesignLabel, DesignLabel.id == DesignTaskLabel.label_id)
        .where(
            DesignTaskLabel.project_id == project_id,
            DesignTaskLabel.task_id.in_(task_ids),
            DesignTaskLabel.removed_at.is_(None),
            DesignLabel.is_deleted == False,  # noqa: E712
        )
        # Архивные метки из выдачи НЕ исключаются (Р30): история не должна ломаться.
        .order_by(DesignLabel.sort_order, DesignLabel.id)
        .limit(len(task_ids) * _MAX_LABELS_PER_TASK)
    )
    out: dict[int, list[DesignLabelRef]] = {}
    for task_id, label_id, name, color in res.all():
        out.setdefault(task_id, []).append(DesignLabelRef(id=label_id, name=name, color=color))
    return out


async def load_attributes_by_task(
    db: AsyncSession, project_id: int, task_ids: list[int]
) -> dict[int, list[DesignAttributeRef]]:
    """Выбранные реквизиты задач ОДНИМ запросом на страницу."""
    if not task_ids:
        return {}
    res = await db.execute(
        select(
            DesignTaskAttributeValue.task_id,
            DesignAttribute.id,
            DesignAttribute.name,
            DesignAttributeValue.id,
            DesignAttributeValue.value,
        )
        .join(
            DesignAttributeValue,
            DesignAttributeValue.id == DesignTaskAttributeValue.value_id,
        )
        .join(DesignAttribute, DesignAttribute.id == DesignAttributeValue.attribute_id)
        .where(
            DesignTaskAttributeValue.project_id == project_id,
            DesignTaskAttributeValue.task_id.in_(task_ids),
            DesignAttributeValue.is_deleted == False,  # noqa: E712
            DesignAttribute.is_deleted == False,  # noqa: E712
        )
        .order_by(DesignAttribute.sort_order, DesignAttribute.id, DesignAttributeValue.sort_order)
        .limit(len(task_ids) * _MAX_ATTRS_PER_TASK)
    )
    out: dict[int, list[DesignAttributeRef]] = {}
    for task_id, attribute_id, attribute_name, value_id, value in res.all():
        out.setdefault(task_id, []).append(
            DesignAttributeRef(
                attribute_id=attribute_id,
                attribute_name=attribute_name,
                value_id=value_id,
                value=value,
            )
        )
    return out


async def to_list_items(
    db: AsyncSession, project_id: int, tasks: list[DesignTask]
) -> list[DesignTaskListItem]:
    """Батч-обогащение имён, счётчиков версий и разметки — без N+1."""
    if not tasks:
        return []
    task_ids = [t.id for t in tasks]
    labels_by_task = await load_labels_by_task(db, project_id, task_ids)
    attrs_by_task = await load_attributes_by_task(db, project_id, task_ids)
    counts_res = await db.execute(
        select(DesignSubmission.task_id, sa_func.count())
        .where(
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id.in_(task_ids),
        )
        .group_by(DesignSubmission.task_id)
    )
    versions: dict[int, int] = {row[0]: row[1] for row in counts_res.all()}

    user_ids = {t.author_user_id for t in tasks} | {
        t.assignee_user_id for t in tasks if t.assignee_user_id is not None
    }
    names = await _user_names(db, user_ids)

    today = utcnow().date()
    active_values = {s.value for s in DESIGN_ACTIVE_STATUSES}
    items: list[DesignTaskListItem] = []
    for t in tasks:
        items.append(
            DesignTaskListItem(
                id=t.id,
                number=t.number,
                title=t.title,
                status=t.status,
                nm_id=t.nm_id,
                work_type=t.work_type,
                complexity=t.complexity,
                is_urgent=t.is_urgent,
                due_date=t.due_date,
                is_overdue=bool(
                    t.due_date is not None and t.due_date < today and t.status in active_values
                ),
                is_outsourced=t.is_outsourced,
                unviewed=bool(t.status == _S.NEW.value and t.viewed_by_lead_at is None),
                assignee_name=names.get(t.assignee_user_id) if t.assignee_user_id else None,
                author_name=names.get(t.author_user_id),
                versions_count=versions.get(t.id, 0),
                sort_order=t.sort_order,
                labels=labels_by_task.get(t.id, []),
                attributes=attrs_by_task.get(t.id, []),
            )
        )
    return items


async def list_tasks(
    db: AsyncSession,
    project_id: int,
    *,
    status: list[str] | None = None,
    assignee_user_id: int | None = None,
    author_user_id: int | None = None,
    work_type: str | None = None,
    is_urgent: bool | None = None,
    overdue: bool | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DesignTaskListItem]:
    """Список (второй вид). Без фильтра статуса видны и ON_HOLD/CANCELLED.

    Сортировка: due_date ASC NULLS LAST, is_urgent DESC, id DESC — срочные выше
    при равном сроке; на ДОСКЕ порядок только sort_order (Р9).
    """
    limit = min(limit, 200)  # кап сервиса; роутер Ф2 добавит свой
    conds: list[Any] = [
        DesignTask.project_id == project_id,
        DesignTask.is_deleted == False,  # noqa: E712
    ]
    if status:
        conds.append(DesignTask.status.in_(status))
    if assignee_user_id is not None:
        conds.append(DesignTask.assignee_user_id == assignee_user_id)
    if author_user_id is not None:
        conds.append(DesignTask.author_user_id == author_user_id)
    if work_type:
        conds.append(DesignTask.work_type == work_type)
    if is_urgent is not None:
        conds.append(DesignTask.is_urgent == is_urgent)
    if overdue:
        conds.append(DesignTask.due_date < utcnow().date())
        conds.append(DesignTask.status.in_([s.value for s in DESIGN_ACTIVE_STATUSES]))
    if q and q.strip():
        pattern = f"%{escape_like(q.strip())}%"
        conds.append(
            or_(
                DesignTask.number.ilike(pattern, escape="\\"),
                DesignTask.title.ilike(pattern, escape="\\"),
                cast(DesignTask.nm_id, String).like(pattern, escape="\\"),
            )
        )

    res = await db.execute(
        select(DesignTask)
        .where(*conds)
        .order_by(
            DesignTask.due_date.asc().nulls_last(),
            DesignTask.is_urgent.desc(),
            DesignTask.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return await to_list_items(db, project_id, list(res.scalars().all()))


async def list_tasks_all_projects(
    db: AsyncSession,
    user_id: int,
    *,
    status: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DesignTaskListItem]:
    """Сквозной список по брендам: ТОЛЬКО проекты членства ProjectMember (§6.1)
    И только те, где участнику доступна страница design-tasks (page-гейт Р11:
    owner/admin — всегда, editor/viewer — по ключу в pages; решение fix-цикла Ф2).

    Единственный read-путь без project_id снаружи — скоуп задают членства
    (is_deleted = false у членства И проекта). project_name заполняется.
    """
    limit = min(limit, 200)
    member_res = await db.execute(
        select(
            ProjectMember.project_id,
            ProjectMember.role,
            ProjectMember.pages,
            ProjectMember.pages_updated_at,
        )
        .where(
            ProjectMember.user_id == user_id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
        .limit(1000)
    )
    # pages_updated_at обязателен: без него get_effective_pages выключает
    # наследование разделов, и участник, получивший design-tasks по наследству
    # (раздел появился в каталоге позже настройки его доступов), проходит
    # require_role на обычных ручках, но молча теряет бренд в сквозном списке.
    allowed_ids = [
        row.project_id
        for row in member_res.all()
        if "design-tasks" in get_effective_pages(row.role, row.pages, row.pages_updated_at)
    ]
    if not allowed_ids:
        return []
    conds: list[Any] = [
        DesignTask.is_deleted == False,  # noqa: E712
        DesignTask.project_id.in_(allowed_ids),
        Project.is_deleted == False,  # noqa: E712
    ]
    if status:
        conds.append(DesignTask.status.in_(status))

    res = await db.execute(
        select(DesignTask, Project.name)
        .join(Project, Project.id == DesignTask.project_id)
        .where(*conds)
        .order_by(
            DesignTask.due_date.asc().nulls_last(),
            DesignTask.is_urgent.desc(),
            DesignTask.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    rows = res.all()

    # to_list_items скоупится project_id → обогащаем по-проектно, порядок выдачи сохраняем.
    project_names: dict[int, str] = {}
    by_project: dict[int, list[DesignTask]] = {}
    order: dict[int, int] = {}
    for idx, (task, project_name) in enumerate(rows):
        project_names[task.project_id] = project_name
        by_project.setdefault(task.project_id, []).append(task)
        order[task.id] = idx

    items: list[DesignTaskListItem] = []
    for pid, tasks in by_project.items():
        enriched = await to_list_items(db, pid, tasks)
        for item in enriched:
            item.project_name = project_names[pid]
        items.extend(enriched)
    items.sort(key=lambda i: order[i.id])
    return items


# Видимая сетка месяца (Р7): CSS grid 7×6 захватывает хвосты соседних месяцев.
_CALENDAR_PAD_DAYS = 6
_CALENDAR_LIMIT = 500  # режим month — унаследован из v1, не меняется
_CALENDAR_RANGE_LIMIT = 2000  # режим произвольного диапазона (CONTRACT-V2 §1)
_CALENDAR_MAX_RANGE_DAYS = 400


def _pad(day: date, days: int) -> date:
    """Расширение границы окна с клампом у краёв календаря.

    `date.max + 1 день` бросает OverflowError — не ValueError, поэтому `_svc`
    роутера его не поймает и пользователь получит 500 вместо 400. Запрос вида
    `date_to=9999-12-31` вполне достижим ручным вводом в пикере.
    """
    try:
        return day + timedelta(days=days)
    except OverflowError:
        return date.max if days > 0 else date.min


def resolve_calendar_window(
    month: str | None, date_from: date | None, date_to: date | None
) -> tuple[str, date, date, int]:
    """Разбор параметров GET /calendar → (month, окно_от, окно_до, cap).

    Границы возвращаются уже расширенными на ±6 дней: календарь дорисовывает
    недели соседних месяцев, и фронт полагается на это (v1-семантика полей
    date_from/date_to ответа сохраняется дословно).

    Тексты ошибок — часть контракта (CONTRACT-V2 §1), тесты сверяют их дословно.
    """
    has_range = date_from is not None or date_to is not None
    if month is not None and has_range:
        raise ValueError("Укажите либо month, либо диапазон дат")

    if has_range:
        if date_from is None or date_to is None:
            raise ValueError("Укажите обе границы диапазона")
        if date_from > date_to:
            raise ValueError("Начало диапазона позже конца")
        if (date_to - date_from).days + 1 > _CALENDAR_MAX_RANGE_DAYS:
            raise ValueError("Диапазон не больше 400 дней")
        return (
            date_from.strftime("%Y-%m"),
            _pad(date_from, -_CALENDAR_PAD_DAYS),
            _pad(date_to, _CALENDAR_PAD_DAYS),
            _CALENDAR_RANGE_LIMIT,
        )

    # Дефолт — текущий месяц по МСК: календарь пользовательский, и в 00:00–02:59
    # МСК UTC-дата отдала бы прошлый месяц.
    if month is None:
        today = msk_today()
        month_first = date(today.year, today.month, 1)
        month = f"{today.year:04d}-{today.month:02d}"
    else:
        try:
            month_first = date.fromisoformat(f"{month}-01")
        except ValueError as e:
            raise ValueError("month должен быть в формате YYYY-MM") from e

    if month_first.month == 12:
        next_month = date(month_first.year + 1, 1, 1)
    else:
        next_month = date(month_first.year, month_first.month + 1, 1)
    return (
        month,
        _pad(month_first, -_CALENDAR_PAD_DAYS),
        _pad(next_month, _CALENDAR_PAD_DAYS - 1),
        _CALENDAR_LIMIT,
    )


async def list_calendar(
    db: AsyncSession,
    project_id: int,
    *,
    month: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[str, date, date, list[DesignTaskListItem], bool]:
    """Задачи месяца или произвольного диапазона по due_date (спек F2 + CONTRACT-V2 §1).

    Возвращает (month, окно_от, окно_до, items, truncated) — роутер собирает
    DesignCalendarOut. Усечение по cap отдаётся явным флагом: тихие cap'ы запрещены.
    """
    month_out, win_from, win_to, limit = resolve_calendar_window(month, date_from, date_to)

    res = await db.execute(
        select(DesignTask)
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.due_date >= win_from,
            DesignTask.due_date <= win_to,
        )
        .order_by(DesignTask.due_date, DesignTask.id)
        .limit(limit + 1)
    )
    rows = list(res.scalars().all())
    truncated = len(rows) > limit
    items = await to_list_items(db, project_id, rows[:limit])
    return month_out, win_from, win_to, items, truncated


async def get_task(
    db: AsyncSession, project_id: int, task_id: int, user: Any, member_role: str
) -> DesignTaskDetail:
    """Деталка со связями + permissions (флаги считает только бэк, §6.9)."""
    task = await get_task_row(db, project_id, task_id)

    mat_res = await db.execute(
        select(DesignMaterial)
        .where(
            DesignMaterial.project_id == project_id,
            DesignMaterial.task_id == task_id,
        )
        .order_by(DesignMaterial.id)
        .limit(200)
    )
    materials = list(mat_res.scalars().all())

    sub_res = await db.execute(
        select(DesignSubmission)
        .where(
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id == task_id,
        )
        .order_by(DesignSubmission.version_no)
        .limit(100)
    )
    submissions = list(sub_res.scalars().all())

    files_by_sub: dict[int, list[Any]] = {}
    if submissions:
        f_res = await db.execute(
            select(DesignSubmissionFile)
            .where(
                DesignSubmissionFile.project_id == project_id,
                DesignSubmissionFile.submission_id.in_([s.id for s in submissions]),
            )
            .order_by(DesignSubmissionFile.id)
            .limit(500)
        )
        for f in f_res.scalars().all():
            files_by_sub.setdefault(f.submission_id, []).append(f)

    com_res = await db.execute(
        select(DesignTaskComment)
        .where(
            DesignTaskComment.project_id == project_id,
            DesignTaskComment.task_id == task_id,
            DesignTaskComment.is_deleted == False,  # noqa: E712
        )
        .order_by(DesignTaskComment.id)
        .limit(500)
    )
    comments = list(com_res.scalars().all())

    ev_res = await db.execute(
        select(DesignTaskEvent)
        .where(
            DesignTaskEvent.project_id == project_id,
            DesignTaskEvent.task_id == task_id,
        )
        .order_by(DesignTaskEvent.id)
        .limit(500)
    )
    events = list(ev_res.scalars().all())

    user_ids = {task.author_user_id} | {c.author_user_id for c in comments}
    if task.assignee_user_id is not None:
        user_ids.add(task.assignee_user_id)
    names = await _user_names(db, user_ids)

    labels = (await load_labels_by_task(db, project_id, [task_id])).get(task_id, [])
    attributes = (await load_attributes_by_task(db, project_id, [task_id])).get(task_id, [])
    label_history = await _label_history(db, project_id, task_id)

    # Полный набор флагов без фильтрации-пересечения (инвариант §6.9): схема
    # DesignTaskPermissions зеркалит ключи compute_permissions один-в-один.
    permissions = DesignTaskPermissions(**compute_permissions(task, user, member_role))

    # amendment M3 (2026-08-02, аддитивно): целевые статусы, куда ТЕКУЩИЙ
    # пользователь реально может перевести задачу. Та же матрица прав, что и сам
    # переход — can_user_transition (ребро вне словаря → False), логика не дублируется.
    # Порядок детерминирован объявлением enum'а DesignTaskStatus.
    allowed_transitions = [
        s.value
        for s in DesignTaskStatus
        if can_user_transition(task, s.value, user.id, member_role)
    ]

    return DesignTaskDetail(
        id=task.id,
        number=task.number,
        title=task.title,
        description=task.description,
        sheet_url=task.sheet_url,
        nm_id=task.nm_id,
        work_type=task.work_type,
        complexity=task.complexity,
        is_urgent=task.is_urgent,
        status=task.status,
        sort_order=task.sort_order,
        due_date=task.due_date,
        author_user_id=task.author_user_id,
        author_name=names.get(task.author_user_id),
        assignee_user_id=task.assignee_user_id,
        assignee_name=names.get(task.assignee_user_id) if task.assignee_user_id else None,
        is_outsourced=task.is_outsourced,
        viewed_by_lead_at=task.viewed_by_lead_at,
        held_from_status=task.held_from_status,
        started_at=task.started_at,
        accepted_at=task.accepted_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        materials=[DesignMaterialOut.model_validate(m) for m in materials],
        submissions=[
            DesignSubmissionOut(
                id=s.id,
                version_no=s.version_no,
                submitted_by_user_id=s.submitted_by_user_id,
                submitted_at=s.submitted_at,
                comment=s.comment,
                verdict=s.verdict,
                verdict_comment=s.verdict_comment,
                verdict_by_user_id=s.verdict_by_user_id,
                verdict_at=s.verdict_at,
                files=[
                    DesignSubmissionFileOut.model_validate(f) for f in files_by_sub.get(s.id, [])
                ],
            )
            for s in submissions
        ],
        comments=[
            DesignCommentOut(
                id=c.id,
                author_user_id=c.author_user_id,
                author_name=names.get(c.author_user_id),
                body=c.body,
                original_filename=c.original_filename,
                created_at=c.created_at,
            )
            for c in comments
        ],
        events=[DesignEventOut.model_validate(e) for e in events],
        labels=labels,
        attributes=attributes,
        label_history=label_history,
        permissions=permissions,
        allowed_transitions=allowed_transitions,
    )


async def _label_history(
    db: AsyncSession, project_id: int, task_id: int
) -> list[DesignLabelHistoryRow]:
    """Р20: «была с меткой N раз» — COUNT по строкам связи, включая снятые.

    Считается по label_id, а НЕ парсингом DesignTaskEvent.comment: там свободный
    текст, и счёт сломался бы при первом переименовании метки.
    """
    res = await db.execute(
        select(
            DesignTaskLabel.label_id,
            DesignLabel.name,
            DesignLabel.color,
            sa_func.count(),
        )
        .join(DesignLabel, DesignLabel.id == DesignTaskLabel.label_id)
        .where(
            DesignTaskLabel.project_id == project_id,
            DesignTaskLabel.task_id == task_id,
            DesignLabel.is_deleted == False,  # noqa: E712
        )
        .group_by(DesignTaskLabel.label_id, DesignLabel.name, DesignLabel.color, DesignLabel.sort_order)
        .order_by(DesignLabel.sort_order, DesignTaskLabel.label_id)
        .limit(_MAX_LABELS_PER_TASK)
    )
    return [
        DesignLabelHistoryRow(label_id=label_id, name=name, color=color, times=times)
        for label_id, name, color, times in res.all()
    ]


async def product_suggest(
    db: AsyncSession, project_id: int, q: str, limit: int = 10
) -> list[DesignProductSuggestion]:
    """Подсказка по Nomenclature: seller/brand/subject ILIKE + article_wb::text.

    nm_id = article_wb (НЕ vendor_code); distinct по article_wb; только project_id.
    """
    query = (q or "").strip()
    if not query:
        return []
    pattern = f"%{escape_like(query)}%"
    res = await db.execute(
        select(
            Nomenclature.article_wb,
            Nomenclature.article_seller,
            Nomenclature.brand,
            Nomenclature.subject,
        )
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.isnot(None),
            or_(
                Nomenclature.article_seller.ilike(pattern, escape="\\"),
                Nomenclature.brand.ilike(pattern, escape="\\"),
                Nomenclature.subject.ilike(pattern, escape="\\"),
                cast(Nomenclature.article_wb, String).like(pattern, escape="\\"),
            ),
        )
        .distinct(Nomenclature.article_wb)
        .order_by(Nomenclature.article_wb)
        .limit(limit)
    )
    return [
        DesignProductSuggestion(
            nm_id=row[0], article_seller=row[1], brand=row[2], subject=row[3]
        )
        for row in res.all()
    ]
