# ruff: noqa: RUF002, RUF003
"""Разрезы аналитики и раскладка дашборда (волна D v2, CONTRACT-V2 §4).

Семантику существующего `GET /stats` не трогаем: он живёт в stats.py, здесь —
только НОВЫЕ разрезы поверх тех же правил.

Общее с v1, чтобы разрезы сходились с метриками:
- `None` = «нет данных» (пустой знаменатель), не ноль;
- окно режет по `accepted_at` для метрик приёмки и по `created_at` для входных;
- «активные статусы» — константа `DESIGN_ACTIVE_STATUSES`, своего списка нет;
- просрочка = `due_date < сегодня` И активный статус; задача без срока не просрочена;
- архивные метки и значения из выдачи НЕ исключаются (Р30) — иначе история сломается.

Кэша нет (Р10): окно периода делает ключ кэша бесполезным.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.auth import User
from backend.models.design import (
    DESIGN_ACTIVE_STATUSES,
    DESIGN_BOARD_STATUSES,
    DesignAttribute,
    DesignAttributeValue,
    DesignDashboardLayout,
    DesignLabel,
    DesignSubmission,
    DesignTask,
    DesignTaskAttributeValue,
    DesignTaskEvent,
    DesignTaskLabel,
)
from backend.schemas.design import (
    DASHBOARD_WIDGET_IDS,
    DesignDashboardLayoutIn,
    DesignDashboardLayoutOut,
    DesignDashboardWidget,
    DesignStatsAssigneeRow,
    DesignStatsAttributeGroup,
    DesignStatsByAssigneeOut,
    DesignStatsByAttributeOut,
    DesignStatsFunnelOut,
    DesignStatsFunnelRow,
    DesignStatsLabelRow,
    DesignStatsValueRow,
)
from backend.utils.time import msk_today

# Cap'ы выдачи (CONTRACT-V2 §4). Усечение всегда отдаётся флагом truncated.
MAX_ASSIGNEE_ROWS = 200
MAX_ATTRIBUTE_FIELDS = 50
MAX_VALUES_PER_FIELD = 200
MAX_LABEL_ROWS = 100

MAX_WINDOW_DAYS = 400
DEFAULT_WINDOW_DAYS = 30

_NO_VALUE = "Без значения"


def resolve_stats_window(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Окно периода — единое правило для ВСЕХ ручек статистики (CONTRACT-V2 §4).

    Мягче календарного намеренно: одиночный `date_from` — существующий рабочий
    вызов (панель метрик шлёт именно его), и запрет сломал бы FROZEN-контракт v1.
    Тексты ошибок те же, что у `GET /calendar`: один набор на модуль.
    """
    if date_from is None and date_to is None:
        today = msk_today()
        return today - timedelta(days=DEFAULT_WINDOW_DAYS - 1), today
    if date_from is None:
        raise ValueError("Укажите обе границы диапазона")
    if date_to is None:
        date_to = msk_today()  # поведение v1 сохраняется дословно
    if date_from > date_to:
        raise ValueError("Начало диапазона позже конца")
    if (date_to - date_from).days + 1 > MAX_WINDOW_DAYS:
        raise ValueError("Диапазон не больше 400 дней")
    return date_from, date_to


def stats_window(column: Any, date_from: date, date_to: date) -> list[Any]:
    """Условие «столбец внутри окна». Публичная: ею пользуется и выгрузка.

    Верхняя граница клампится: `date.max + 1 день` бросает OverflowError, а он
    НЕ ValueError — маппер ошибок роутера его не поймает и пользователь получит
    500 вместо 400 на вполне достижимом `date_to=9999-12-31`.
    """
    upper = (
        datetime.max
        if date_to >= date.max
        else datetime.combine(date_to + timedelta(days=1), time.min)
    )
    return [column >= datetime.combine(date_from, time.min), column < upper]


def _f(value: Any) -> float | None:
    return float(value) if value is not None else None


# ─── Разрез по исполнителям ──────────────────────────────────────────────────


async def get_by_assignee(
    db: AsyncSession, project_id: int, date_from: date | None, date_to: date | None
) -> DesignStatsByAssigneeOut:
    """По исполнителю: активные сейчас + принятое за окно.

    `active` — снимок на момент запроса (окно на него не влияет), именно по нему
    разрез сходится с `GET /workload`.
    """
    win_from, win_to = resolve_stats_window(date_from, date_to)

    active_res = await db.execute(
        select(DesignTask.assignee_user_id, func.count())
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.assignee_user_id.isnot(None),
            DesignTask.status.in_([s.value for s in DESIGN_ACTIVE_STATUSES]),
        )
        .group_by(DesignTask.assignee_user_id)
    )
    active = {row[0]: row[1] for row in active_res.all()}

    versions_sq = (
        select(DesignSubmission.task_id.label("task_id"), func.count().label("cnt"))
        .where(DesignSubmission.project_id == project_id)
        .group_by(DesignSubmission.task_id)
        .subquery()
    )
    accepted_res = await db.execute(
        select(
            DesignTask.assignee_user_id,
            func.count(),
            func.avg(
                case(
                    (
                        and_(
                            DesignTask.due_date.isnot(None),
                            cast(DesignTask.accepted_at, Date) <= DesignTask.due_date,
                        ),
                        1.0,
                    ),
                    (DesignTask.due_date.isnot(None), 0.0),
                    else_=None,
                )
            ),
            func.avg(
                func.extract("epoch", DesignTask.accepted_at - DesignTask.created_at) / 86400.0
            ),
            func.avg(func.coalesce(versions_sq.c.cnt, 0)),
        )
        .select_from(DesignTask)
        .outerjoin(versions_sq, versions_sq.c.task_id == DesignTask.id)
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.assignee_user_id.isnot(None),
            DesignTask.accepted_at.isnot(None),
            *stats_window(DesignTask.accepted_at, win_from, win_to),
        )
        .group_by(DesignTask.assignee_user_id)
    )
    accepted = {row[0]: row for row in accepted_res.all()}

    user_ids = set(active) | set(accepted)
    if not user_ids:
        return DesignStatsByAssigneeOut()

    # ORDER BY до LIMIT: усечение детерминировано — остаются самые загруженные.
    ordered = sorted(user_ids, key=lambda uid: (-(active.get(uid, 0)), uid))
    truncated = len(ordered) > MAX_ASSIGNEE_ROWS
    ordered = ordered[:MAX_ASSIGNEE_ROWS]

    names_res = await db.execute(
        select(User.id, User.first_name, User.last_name, User.username)
        .where(User.id.in_(ordered))
        .limit(len(ordered))
    )
    names = {
        row.id: (f"{row.first_name or ''} {row.last_name or ''}".strip() or row.username)
        for row in names_res.all()
    }

    rows = [
        DesignStatsAssigneeRow(
            user_id=uid,
            name=names.get(uid, f"user:{uid}"),
            active=active.get(uid, 0),
            accepted=int(accepted[uid][1]) if uid in accepted else 0,
            on_time_share=_f(accepted[uid][2]) if uid in accepted else None,
            avg_cycle_days=_f(accepted[uid][3]) if uid in accepted else None,
            avg_versions=_f(accepted[uid][4]) if uid in accepted else None,
        )
        for uid in ordered
    ]
    rows.sort(key=lambda r: r.name.lower())
    return DesignStatsByAssigneeOut(rows=rows, truncated=truncated)


# ─── Воронка по статусам доски ───────────────────────────────────────────────


async def get_funnel(
    db: AsyncSession, project_id: int, date_from: date | None, date_to: date | None
) -> DesignStatsFunnelOut:
    """Шесть колонок доски: сколько задач сейчас и сколько в среднем в них сидят.

    Среднее время берётся из append-only журнала `design_task_events`, а НЕ из
    `updated_at`: тот затирается любой правкой задачи.
    """
    win_from, win_to = resolve_stats_window(date_from, date_to)
    board_values = [s.value for s in DESIGN_BOARD_STATUSES]

    count_res = await db.execute(
        select(DesignTask.status, func.count())
        .where(
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            DesignTask.status.in_(board_values),
        )
        .group_by(DesignTask.status)
    )
    counts = {row[0]: row[1] for row in count_res.all()}

    # Длительность пребывания = разница между событием входа в статус и следующим
    # событием той же задачи. LEAD оконной функцией — один проход по журналу.
    nxt = func.lead(DesignTaskEvent.changed_at).over(
        partition_by=DesignTaskEvent.task_id, order_by=DesignTaskEvent.changed_at
    )
    spans = (
        select(
            DesignTaskEvent.new_status.label("status"),
            (func.extract("epoch", nxt - DesignTaskEvent.changed_at) / 86400.0).label("days"),
        )
        .select_from(DesignTaskEvent)
        # JOIN с задачей: журнал append-only и хранит события мягко удалённых
        # задач тоже — без фильтра они тянули бы среднее время в статусе.
        .join(DesignTask, DesignTask.id == DesignTaskEvent.task_id)
        .where(
            DesignTaskEvent.project_id == project_id,
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
            # В журнал пишутся и события-«не-переходы» (old == new): смена
            # исполнителя, смена номера, правка меток и реквизитов. Без этого
            # фильтра каждая такая запись резала бы интервал пребывания в
            # статусе на куски и систематически занижала среднее — тем сильнее,
            # чем активнее команда пользуется метками.
            or_(
                DesignTaskEvent.old_status.is_(None),  # создание задачи — вход в NEW
                DesignTaskEvent.old_status != DesignTaskEvent.new_status,
            ),
            *stats_window(DesignTaskEvent.changed_at, win_from, win_to),
        )
        .subquery()
    )
    avg_res = await db.execute(
        select(spans.c.status, func.avg(spans.c.days))
        .where(spans.c.days.isnot(None), spans.c.status.in_(board_values))
        .group_by(spans.c.status)
    )
    avg_days = {row[0]: _f(row[1]) for row in avg_res.all()}

    return DesignStatsFunnelOut(
        rows=[
            DesignStatsFunnelRow(
                status=value,
                count=counts.get(value, 0),
                avg_days_in_status=avg_days.get(value),
            )
            for value in board_values
        ]
    )


# ─── Разрез по реквизитам и меткам ───────────────────────────────────────────


async def get_by_attribute(
    db: AsyncSession, project_id: int, date_from: date | None, date_to: date | None
) -> DesignStatsByAttributeOut:
    """Распределение задач окна по значениям реквизитов и по меткам.

    Архивные метки и значения из выдачи НЕ исключаются (Р30): иначе история
    ломалась бы каждый раз, когда команда убирает что-то из справочника.
    """
    win_from, win_to = resolve_stats_window(date_from, date_to)
    task_window = [
        DesignTask.project_id == project_id,
        DesignTask.is_deleted == False,  # noqa: E712
        *stats_window(DesignTask.created_at, win_from, win_to),
    ]

    total_res = await db.execute(select(func.count()).select_from(DesignTask).where(*task_window))
    total_tasks = int(total_res.scalar() or 0)

    attrs_res = await db.execute(
        select(DesignAttribute.id, DesignAttribute.name)
        .where(
            DesignAttribute.project_id == project_id,
            DesignAttribute.is_deleted == False,  # noqa: E712
        )
        .order_by(DesignAttribute.sort_order, DesignAttribute.id)
        .limit(MAX_ATTRIBUTE_FIELDS + 1)
    )
    attrs = attrs_res.all()
    truncated = len(attrs) > MAX_ATTRIBUTE_FIELDS
    attrs = attrs[:MAX_ATTRIBUTE_FIELDS]

    groups: list[DesignStatsAttributeGroup] = []
    if attrs:
        attr_ids = [a[0] for a in attrs]
        dist_res = await db.execute(
            select(
                DesignAttributeValue.attribute_id,
                DesignAttributeValue.id,
                DesignAttributeValue.value,
                func.count(func.distinct(DesignTask.id)),
            )
            .select_from(DesignTask)
            .join(DesignTaskAttributeValue, DesignTaskAttributeValue.task_id == DesignTask.id)
            .join(
                DesignAttributeValue,
                DesignAttributeValue.id == DesignTaskAttributeValue.value_id,
            )
            .where(
                *task_window,
                DesignTaskAttributeValue.project_id == project_id,
                DesignAttributeValue.attribute_id.in_(attr_ids),
            )
            .group_by(
                DesignAttributeValue.attribute_id,
                DesignAttributeValue.id,
                DesignAttributeValue.value,
                DesignAttributeValue.sort_order,
            )
            .order_by(DesignAttributeValue.attribute_id, DesignAttributeValue.sort_order)
            .limit(MAX_ATTRIBUTE_FIELDS * MAX_VALUES_PER_FIELD)
        )
        by_attr: dict[int, list[DesignStatsValueRow]] = {}
        for attribute_id, value_id, value, count in dist_res.all():
            by_attr.setdefault(attribute_id, []).append(
                DesignStatsValueRow(value_id=value_id, value=value, count=count)
            )

        #  Сколько РАЗЛИЧНЫХ задач имеют хоть одно значение поля. Сумма счётчиков
        #  по значениям для этого не годится: у поля с is_multi задача с двумя
        #  значениями посчиталась бы дважды, «Без значения» ушло бы в минус и
        #  строка молча исчезла бы — разрез перестал бы сходиться с числом задач.
        filled_res = await db.execute(
            select(
                DesignAttributeValue.attribute_id,
                func.count(func.distinct(DesignTask.id)),
            )
            .select_from(DesignTask)
            .join(DesignTaskAttributeValue, DesignTaskAttributeValue.task_id == DesignTask.id)
            .join(
                DesignAttributeValue,
                DesignAttributeValue.id == DesignTaskAttributeValue.value_id,
            )
            .where(
                *task_window,
                DesignTaskAttributeValue.project_id == project_id,
                DesignAttributeValue.attribute_id.in_(attr_ids),
            )
            .group_by(DesignAttributeValue.attribute_id)
        )
        filled = {row[0]: row[1] for row in filled_res.all()}

        for attribute_id, name in attrs:
            all_rows = by_attr.get(attribute_id, [])
            if len(all_rows) > MAX_VALUES_PER_FIELD:
                truncated = True
            rows = all_rows[:MAX_VALUES_PER_FIELD]
            #  Задачи без выбранного значения — отдельной строкой (Р33): иначе
            #  сумма по разрезу молча не сходилась бы с числом задач периода.
            empty = total_tasks - filled.get(attribute_id, 0)
            if empty > 0:
                rows = [*rows, DesignStatsValueRow(value_id=None, value=_NO_VALUE, count=empty)]
            groups.append(
                DesignStatsAttributeGroup(attribute_id=attribute_id, attribute_name=name, rows=rows)
            )

    labels_res = await db.execute(
        select(
            DesignLabel.id,
            DesignLabel.name,
            DesignLabel.color,
            func.count(func.distinct(DesignTask.id)),
        )
        .select_from(DesignTask)
        .join(DesignTaskLabel, DesignTaskLabel.task_id == DesignTask.id)
        .join(DesignLabel, DesignLabel.id == DesignTaskLabel.label_id)
        .where(
            *task_window,
            DesignTaskLabel.project_id == project_id,
            DesignTaskLabel.removed_at.is_(None),
            DesignLabel.is_deleted == False,  # noqa: E712
        )
        .group_by(DesignLabel.id, DesignLabel.name, DesignLabel.color, DesignLabel.sort_order)
        .order_by(DesignLabel.sort_order, DesignLabel.id)
        .limit(MAX_LABEL_ROWS + 1)
    )
    label_rows = labels_res.all()
    if len(label_rows) > MAX_LABEL_ROWS:
        truncated = True
        label_rows = label_rows[:MAX_LABEL_ROWS]

    return DesignStatsByAttributeOut(
        attributes=groups,
        labels=[
            DesignStatsLabelRow(label_id=lid, name=name, color=color, count=count)
            for lid, name, color, count in label_rows
        ],
        truncated=truncated,
    )


# ─── Раскладка дашборда (Р23) ────────────────────────────────────────────────


def validate_widget_set(widgets: list[DesignDashboardWidget]) -> None:
    """Набор виджетов обязан быть ПОЛНЫМ и точным (CONTRACT-V2 §4).

    Проверка живёт в сервисе, а не в схеме, ради кода ответа: из схемы это было
    бы 422 в конверте FastAPI, а контракт обещает 400 в конверте модуля с
    дословным текстом.

    Полный набор требуется намеренно: иначе появление нового виджета в следующей
    версии молча оставляло бы его скрытым у всех, кто хоть раз сохранял раскладку.
    """
    seen: set[str] = set()
    for w in widgets:
        if w.id not in DASHBOARD_WIDGET_IDS:
            raise ValueError(f"Неизвестный виджет: {w.id}")
        if w.id in seen:
            raise ValueError(f"Виджет повторяется: {w.id}")
        seen.add(w.id)
    for known in DASHBOARD_WIDGET_IDS:
        if known not in seen:
            raise ValueError(f"Не хватает виджета: {known}")


def _default_layout() -> list[DesignDashboardWidget]:
    return [
        DesignDashboardWidget(id=wid, visible=True, order=i)
        for i, wid in enumerate(DASHBOARD_WIDGET_IDS)
    ]


async def get_layout(db: AsyncSession, project_id: int, user_id: int) -> DesignDashboardLayoutOut:
    """Раскладка пользователя; без сохранённой — дефолт с is_default = true."""
    res = await db.execute(
        select(DesignDashboardLayout)
        .where(
            DesignDashboardLayout.project_id == project_id,
            DesignDashboardLayout.user_id == user_id,
        )
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        return DesignDashboardLayoutOut(widgets=_default_layout(), is_default=True)
    widgets = [DesignDashboardWidget(**w) for w in row.widgets]
    widgets.sort(key=lambda w: w.order)
    return DesignDashboardLayoutOut(widgets=widgets, is_default=False)


async def save_layout(
    db: AsyncSession, project_id: int, user_id: int, payload: DesignDashboardLayoutIn
) -> DesignDashboardLayoutOut:
    """Сохранение раскладки. `user_id` — ИЗ СЕССИИ, никогда из тела запроса."""
    validate_widget_set(payload.widgets)
    #  order нормализуется к 0..N-1: наружу важен только относительный порядок.
    ordered = sorted(payload.widgets, key=lambda w: w.order)
    widgets = [
        DesignDashboardWidget(id=w.id, visible=w.visible, order=i) for i, w in enumerate(ordered)
    ]
    stored = [w.model_dump() for w in widgets]

    res = await db.execute(
        select(DesignDashboardLayout)
        .where(
            DesignDashboardLayout.project_id == project_id,
            DesignDashboardLayout.user_id == user_id,
        )
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        db.add(DesignDashboardLayout(project_id=project_id, user_id=user_id, widgets=stored))
    else:
        row.widgets = stored
    await db.commit()
    return DesignDashboardLayoutOut(widgets=widgets, is_default=False)
