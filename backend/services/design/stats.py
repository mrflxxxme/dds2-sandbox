# ruff: noqa: RUF002, RUF003
"""Метрики PRD §10 для панели (потребитель — Ф6).

Решения (STATUS «Открытые вопросы» + owned):
- задачи без due_date НЕ считаются просроченными и в on_time_share не входят;
- медиана цикла accepted_at − created_at — КАЛЕНДАРНЫЕ дни (приближение
  рабочих, допустимо по спеку);
- окно (from/to) режет по accepted_at для метрик приёмки и по created_at для
  tracked_share (доля заявок с опознанным товаром — метрика входа);
- unassigned_over_2d — текущий снимок (NEW/ASSIGNED без исполнителя > 2 дней),
  окно на него не влияет.
"""

from datetime import date, datetime, time, timedelta
from typing import Any

from sqlalchemy import Date, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.design import DesignSubmission, DesignTask, DesignTaskStatus
from backend.schemas.design import DesignStatsOut
from backend.utils.time import utcnow

_S = DesignTaskStatus


def _window(column: Any, date_from: date | None, date_to: date | None) -> list[Any]:
    conds = []
    if date_from is not None:
        conds.append(column >= datetime.combine(date_from, time.min))
    if date_to is not None:
        conds.append(column < datetime.combine(date_to + timedelta(days=1), time.min))
    return conds


async def get_stats(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> DesignStatsOut:
    """Агрегаты одним-двумя запросами; None = «нет данных» (пустой знаменатель).

    Окно разбирается ОБЩИМ правилом модуля (CONTRACT-V2 §4), тем же, что у трёх
    новых разрезов: иначе `/stats` без параметров считал бы за всё время, а
    соседние ручки — за 30 дней, и цифры дашборда не сходились бы между виджетами.
    Поведение одиночного `date_from` (то, что шлёт панель метрик) сохраняется дословно.
    """
    from backend.services.design.analytics import resolve_stats_window

    date_from, date_to = resolve_stats_window(date_from, date_to)
    accepted_conds = [
        DesignTask.project_id == project_id,
        DesignTask.is_deleted == False,  # noqa: E712
        DesignTask.status == _S.ACCEPTED.value,
        DesignTask.accepted_at.isnot(None),
        *_window(DesignTask.accepted_at, date_from, date_to),
    ]

    versions_sq = (
        select(
            DesignSubmission.task_id.label("task_id"),
            func.count().label("cnt"),
        )
        .where(DesignSubmission.project_id == project_id)
        .group_by(DesignSubmission.task_id)
        .subquery()
    )

    accepted_row = (
        await db.execute(
            select(
                func.count(),
                # Доля принятых в срок — только среди задач с due_date.
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
                func.avg(func.coalesce(versions_sq.c.cnt, 0)),
                func.percentile_cont(0.5).within_group(
                    func.extract("epoch", DesignTask.accepted_at - DesignTask.created_at)
                    / 86400.0
                ),
                func.avg(case((DesignTask.is_outsourced == True, 1.0), else_=0.0)),  # noqa: E712
            )
            .select_from(DesignTask)
            .outerjoin(versions_sq, versions_sq.c.task_id == DesignTask.id)
            .where(*accepted_conds)
        )
    ).one()
    accepted_count = int(accepted_row[0] or 0)

    unassigned_row = (
        await db.execute(
            select(func.count()).where(
                DesignTask.project_id == project_id,
                DesignTask.is_deleted == False,  # noqa: E712
                DesignTask.status.in_((_S.NEW.value, _S.ASSIGNED.value)),
                DesignTask.assignee_user_id.is_(None),
                DesignTask.created_at < utcnow() - timedelta(days=2),
            )
        )
    ).scalar()

    tracked_row = (
        await db.execute(
            select(func.avg(case((DesignTask.nm_id.isnot(None), 1.0), else_=0.0))).where(
                DesignTask.project_id == project_id,
                DesignTask.is_deleted == False,  # noqa: E712
                *_window(DesignTask.created_at, date_from, date_to),
            )
        )
    ).scalar()

    def _f(value: Any) -> float | None:
        return float(value) if value is not None else None

    return DesignStatsOut(
        on_time_share=_f(accepted_row[1]) if accepted_count else None,
        avg_versions_to_accept=_f(accepted_row[2]) if accepted_count else None,
        median_cycle_days=_f(accepted_row[3]) if accepted_count else None,
        unassigned_over_2d=int(unassigned_row or 0),
        outsourced_share=_f(accepted_row[4]) if accepted_count else None,
        tracked_share=_f(tracked_row),
    )
