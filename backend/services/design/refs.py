# ruff: noqa: RUF002, RUF003
"""Справочники модуля «Дизайн карточек» (волна C v2): метки и реквизиты.

Ведёт справочники ТОЛЬКО ведущий дизайнер; читает любой с page-ключом.
Пользовательское «Удалить» — архивирование (Р30): запись пропадает из выбора
для новых задач, но остаётся видимой на старых и в аналитике. Поэтому все
выборки «активных» фильтруют И is_deleted, И is_archived — ровно так же, как
условлены partial-unique индексы. Расхождение условия индекса и гварда даёт
500 (IntegrityError) вместо контрактного 400.

Метки на задаче хранят историю: снятие проставляет removed_at, строку не
удаляет — из этого считается «была с меткой N раз» (Р20). Реквизиты истории
не ведут: снятие значения удаляет строку связи.

Тексты гвардов — часть контракта (CONTRACT-V2 §3), тесты сверяют их дословно.
"""

import logging
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.design import (
    DesignAttribute,
    DesignAttributeValue,
    DesignLabel,
    DesignTask,
    DesignTaskAttributeValue,
    DesignTaskEvent,
    DesignTaskLabel,
    DesignTaskStatus,
)
from backend.schemas.design import (
    DesignAttributeIn,
    DesignAttributeOut,
    DesignAttributeValueIn,
    DesignAttributeValueOut,
    DesignBulkErrorRow,
    DesignBulkResultOut,
    DesignLabelIn,
    DesignLabelOut,
)
from backend.services.design.common import actor_name, get_task_row, is_lead
from backend.utils.time import utcnow

logger = logging.getLogger("dds.design")

_S = DesignTaskStatus
_TERMINAL_VALUES = (_S.ACCEPTED.value, _S.CANCELLED.value)

# Cap'ы справочника (CONTRACT-V2 §3). Один текст на все три — предел один по смыслу.
MAX_LABELS = 100
MAX_ATTRIBUTES = 200
MAX_VALUES_PER_ATTRIBUTE = 500
MAX_BULK_TASKS = 500

_LEAD_ONLY = "Справочник ведёт ведущий дизайнер"
_LIMIT_REACHED = "Достигнут предел справочника"
_LABEL_NOT_FOUND = "Метка не найдена"
_ATTRIBUTE_NOT_FOUND = "Поле не найдено"
_VALUE_NOT_FOUND = "Значение не найдено"


def _require_lead(member_role: str) -> None:
    if not is_lead(member_role):
        raise PermissionError(_LEAD_ONLY)


def _active(model: Any) -> tuple[Any, Any]:
    """Условие «запись активна» — зеркало partial-unique индексов."""
    return (model.is_deleted == False, model.is_archived == False)  # noqa: E712


# ─── Метки ───────────────────────────────────────────────────────────────────


async def list_labels(
    db: AsyncSession, project_id: int, *, include_archived: bool = False
) -> list[DesignLabelOut]:
    """Список меток с usage_count одним GROUP BY (не per-row — инвариант §6.10)."""
    conds = [DesignLabel.project_id == project_id, DesignLabel.is_deleted == False]  # noqa: E712
    if not include_archived:
        conds.append(DesignLabel.is_archived == False)  # noqa: E712
    res = await db.execute(
        select(DesignLabel)
        .where(*conds)
        .order_by(DesignLabel.sort_order, DesignLabel.id)
        .limit(MAX_LABELS)
    )
    labels = list(res.scalars().all())
    if not labels:
        return []

    usage_res = await db.execute(
        select(DesignTaskLabel.label_id, func.count())
        .where(
            DesignTaskLabel.project_id == project_id,
            DesignTaskLabel.label_id.in_([lb.id for lb in labels]),
            DesignTaskLabel.removed_at.is_(None),
        )
        .group_by(DesignTaskLabel.label_id)
    )
    usage = {row[0]: row[1] for row in usage_res.all()}
    return [
        DesignLabelOut(
            id=lb.id,
            name=lb.name,
            color=lb.color,
            sort_order=lb.sort_order,
            is_archived=lb.is_archived,
            usage_count=usage.get(lb.id, 0),
        )
        for lb in labels
    ]


async def _label_row(db: AsyncSession, project_id: int, label_id: int) -> DesignLabel:
    res = await db.execute(
        select(DesignLabel)
        .where(
            DesignLabel.id == label_id,
            DesignLabel.project_id == project_id,
            DesignLabel.is_deleted == False,  # noqa: E712
        )
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise ValueError(_LABEL_NOT_FOUND)
    return row


async def _label_name_taken(
    db: AsyncSession, project_id: int, name: str, *, exclude_id: int | None = None
) -> bool:
    conds = [DesignLabel.project_id == project_id, DesignLabel.name == name, *_active(DesignLabel)]
    if exclude_id is not None:
        conds.append(DesignLabel.id != exclude_id)
    res = await db.execute(select(DesignLabel.id).where(*conds).limit(1))
    return res.scalar_one_or_none() is not None


async def create_label(
    db: AsyncSession, project_id: int, payload: DesignLabelIn, member_role: str
) -> DesignLabelOut:
    _require_lead(member_role)
    count_res = await db.execute(
        select(func.count()).select_from(DesignLabel).where(
            DesignLabel.project_id == project_id, *_active(DesignLabel)
        )
    )
    if int(count_res.scalar() or 0) >= MAX_LABELS:
        raise ValueError(_LIMIT_REACHED)
    if await _label_name_taken(db, project_id, payload.name):
        raise ValueError("Такое название уже есть")

    label = DesignLabel(
        project_id=project_id,
        name=payload.name,
        color=payload.color,
        sort_order=payload.sort_order,
    )
    db.add(label)
    await db.commit()
    await db.refresh(label)
    return DesignLabelOut.model_validate(label)


async def update_label(
    db: AsyncSession, project_id: int, label_id: int, payload: DesignLabelIn, member_role: str
) -> DesignLabelOut:
    _require_lead(member_role)
    label = await _label_row(db, project_id, label_id)
    if await _label_name_taken(db, project_id, payload.name, exclude_id=label_id):
        raise ValueError("Такое название уже есть")
    label.name = payload.name
    label.color = payload.color
    label.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(label)
    return DesignLabelOut.model_validate(label)


async def archive_label(
    db: AsyncSession, project_id: int, label_id: int, member_role: str
) -> None:
    """«Удалить» = архивировать (Р30). Повторный вызов идемпотентен — 204 и тишина."""
    _require_lead(member_role)
    label = await _label_row(db, project_id, label_id)
    if label.is_archived:
        return
    label.is_archived = True
    await db.commit()


# ─── Реквизиты ───────────────────────────────────────────────────────────────


async def list_attributes(
    db: AsyncSession, project_id: int, *, include_archived: bool = False
) -> list[DesignAttributeOut]:
    """Поля со вложенными значениями. include_archived действует и на значения."""
    attr_conds = [
        DesignAttribute.project_id == project_id,
        DesignAttribute.is_deleted == False,  # noqa: E712
    ]
    if not include_archived:
        attr_conds.append(DesignAttribute.is_archived == False)  # noqa: E712
    res = await db.execute(
        select(DesignAttribute)
        .where(*attr_conds)
        .order_by(DesignAttribute.sort_order, DesignAttribute.id)
        .limit(MAX_ATTRIBUTES)
    )
    attributes = list(res.scalars().all())
    if not attributes:
        return []
    attr_ids = [a.id for a in attributes]

    value_conds = [
        DesignAttributeValue.project_id == project_id,
        DesignAttributeValue.attribute_id.in_(attr_ids),
        DesignAttributeValue.is_deleted == False,  # noqa: E712
    ]
    if not include_archived:
        value_conds.append(DesignAttributeValue.is_archived == False)  # noqa: E712
    values_res = await db.execute(
        select(DesignAttributeValue)
        .where(*value_conds)
        .order_by(DesignAttributeValue.sort_order, DesignAttributeValue.id)
        .limit(MAX_ATTRIBUTES * MAX_VALUES_PER_ATTRIBUTE)
    )
    values = list(values_res.scalars().all())

    # usage_count значений — один GROUP BY на всю выдачу.
    usage: dict[int, int] = {}
    if values:
        usage_res = await db.execute(
            select(DesignTaskAttributeValue.value_id, func.count())
            .where(
                DesignTaskAttributeValue.project_id == project_id,
                DesignTaskAttributeValue.value_id.in_([v.id for v in values]),
            )
            .group_by(DesignTaskAttributeValue.value_id)
        )
        usage = {row[0]: row[1] for row in usage_res.all()}

    # usage_count поля — число ЗАДАЧ, где выбрано любое его значение (не сумма
    # по значениям: у is_multi-поля одна задача могла бы посчитаться дважды).
    attr_usage: dict[int, int] = {}
    if values:
        attr_usage_res = await db.execute(
            select(DesignAttributeValue.attribute_id, func.count(func.distinct(DesignTaskAttributeValue.task_id)))
            .join(
                DesignTaskAttributeValue,
                DesignTaskAttributeValue.value_id == DesignAttributeValue.id,
            )
            .where(
                DesignAttributeValue.project_id == project_id,
                DesignAttributeValue.attribute_id.in_(attr_ids),
                DesignTaskAttributeValue.project_id == project_id,
            )
            .group_by(DesignAttributeValue.attribute_id)
        )
        attr_usage = {row[0]: row[1] for row in attr_usage_res.all()}

    by_attribute: dict[int, list[DesignAttributeValueOut]] = {}
    for v in values:
        by_attribute.setdefault(v.attribute_id, []).append(
            DesignAttributeValueOut(
                id=v.id,
                attribute_id=v.attribute_id,
                value=v.value,
                sort_order=v.sort_order,
                is_archived=v.is_archived,
                usage_count=usage.get(v.id, 0),
            )
        )

    return [
        DesignAttributeOut(
            id=a.id,
            name=a.name,
            is_multi=a.is_multi,
            sort_order=a.sort_order,
            is_archived=a.is_archived,
            usage_count=attr_usage.get(a.id, 0),
            values=by_attribute.get(a.id, []),
        )
        for a in attributes
    ]


async def _attribute_row(db: AsyncSession, project_id: int, attribute_id: int) -> DesignAttribute:
    res = await db.execute(
        select(DesignAttribute)
        .where(
            DesignAttribute.id == attribute_id,
            DesignAttribute.project_id == project_id,
            DesignAttribute.is_deleted == False,  # noqa: E712
        )
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise ValueError(_ATTRIBUTE_NOT_FOUND)
    return row


async def _value_row(db: AsyncSession, project_id: int, value_id: int) -> DesignAttributeValue:
    res = await db.execute(
        select(DesignAttributeValue)
        .where(
            DesignAttributeValue.id == value_id,
            DesignAttributeValue.project_id == project_id,
            DesignAttributeValue.is_deleted == False,  # noqa: E712
        )
        .limit(1)
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise ValueError(_VALUE_NOT_FOUND)
    return row


async def create_attribute(
    db: AsyncSession, project_id: int, payload: DesignAttributeIn, member_role: str
) -> DesignAttributeOut:
    _require_lead(member_role)
    count_res = await db.execute(
        select(func.count()).select_from(DesignAttribute).where(
            DesignAttribute.project_id == project_id, *_active(DesignAttribute)
        )
    )
    if int(count_res.scalar() or 0) >= MAX_ATTRIBUTES:
        raise ValueError(_LIMIT_REACHED)
    taken = await db.execute(
        select(DesignAttribute.id)
        .where(
            DesignAttribute.project_id == project_id,
            DesignAttribute.name == payload.name,
            *_active(DesignAttribute),
        )
        .limit(1)
    )
    if taken.scalar_one_or_none() is not None:
        raise ValueError("Такое название уже есть")

    attribute = DesignAttribute(
        project_id=project_id,
        name=payload.name,
        is_multi=payload.is_multi,
        sort_order=payload.sort_order,
    )
    db.add(attribute)
    await db.commit()
    await db.refresh(attribute)
    return DesignAttributeOut.model_validate(attribute)


async def update_attribute(
    db: AsyncSession,
    project_id: int,
    attribute_id: int,
    payload: DesignAttributeIn,
    member_role: str,
) -> DesignAttributeOut:
    _require_lead(member_role)
    attribute = await _attribute_row(db, project_id, attribute_id)
    taken = await db.execute(
        select(DesignAttribute.id)
        .where(
            DesignAttribute.project_id == project_id,
            DesignAttribute.name == payload.name,
            DesignAttribute.id != attribute_id,
            *_active(DesignAttribute),
        )
        .limit(1)
    )
    if taken.scalar_one_or_none() is not None:
        raise ValueError("Такое название уже есть")
    attribute.name = payload.name
    attribute.is_multi = payload.is_multi
    attribute.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(attribute)
    return DesignAttributeOut.model_validate(attribute)


async def archive_attribute(
    db: AsyncSession, project_id: int, attribute_id: int, member_role: str
) -> None:
    """Архивирование поля каскадом архивирует ВСЕ его значения (Р30)."""
    _require_lead(member_role)
    attribute = await _attribute_row(db, project_id, attribute_id)
    if attribute.is_archived:
        return
    attribute.is_archived = True
    await db.execute(
        update(DesignAttributeValue)
        .where(
            DesignAttributeValue.project_id == project_id,
            DesignAttributeValue.attribute_id == attribute_id,
            DesignAttributeValue.is_archived == False,  # noqa: E712
        )
        .values(is_archived=True)
    )
    await db.commit()


async def create_value(
    db: AsyncSession,
    project_id: int,
    attribute_id: int,
    payload: DesignAttributeValueIn,
    member_role: str,
) -> DesignAttributeValueOut:
    _require_lead(member_role)
    await _attribute_row(db, project_id, attribute_id)
    count_res = await db.execute(
        select(func.count()).select_from(DesignAttributeValue).where(
            DesignAttributeValue.project_id == project_id,
            DesignAttributeValue.attribute_id == attribute_id,
            *_active(DesignAttributeValue),
        )
    )
    if int(count_res.scalar() or 0) >= MAX_VALUES_PER_ATTRIBUTE:
        raise ValueError(_LIMIT_REACHED)
    taken = await db.execute(
        select(DesignAttributeValue.id)
        .where(
            DesignAttributeValue.project_id == project_id,
            DesignAttributeValue.attribute_id == attribute_id,
            DesignAttributeValue.value == payload.value,
            *_active(DesignAttributeValue),
        )
        .limit(1)
    )
    if taken.scalar_one_or_none() is not None:
        raise ValueError("Такое значение уже есть")

    value = DesignAttributeValue(
        project_id=project_id,
        attribute_id=attribute_id,
        value=payload.value,
        sort_order=payload.sort_order,
    )
    db.add(value)
    await db.commit()
    await db.refresh(value)
    return DesignAttributeValueOut.model_validate(value)


async def update_value(
    db: AsyncSession,
    project_id: int,
    value_id: int,
    payload: DesignAttributeValueIn,
    member_role: str,
) -> DesignAttributeValueOut:
    _require_lead(member_role)
    value = await _value_row(db, project_id, value_id)
    taken = await db.execute(
        select(DesignAttributeValue.id)
        .where(
            DesignAttributeValue.project_id == project_id,
            DesignAttributeValue.attribute_id == value.attribute_id,
            DesignAttributeValue.value == payload.value,
            DesignAttributeValue.id != value_id,
            *_active(DesignAttributeValue),
        )
        .limit(1)
    )
    if taken.scalar_one_or_none() is not None:
        raise ValueError("Такое значение уже есть")
    value.value = payload.value
    value.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(value)
    return DesignAttributeValueOut.model_validate(value)


async def archive_value(
    db: AsyncSession, project_id: int, value_id: int, member_role: str
) -> None:
    _require_lead(member_role)
    value = await _value_row(db, project_id, value_id)
    if value.is_archived:
        return
    value.is_archived = True
    await db.commit()


# ─── Назначение на задачу ────────────────────────────────────────────────────


def _can_touch(task: DesignTask, user: Any, member_role: str) -> bool:
    """Р29: разметку задачи ставят ведущий, автор и исполнитель."""
    uid = int(user.id)
    return is_lead(member_role) or task.author_user_id == uid or task.assignee_user_id == uid


async def _current_label_ids(db: AsyncSession, project_id: int, task_id: int) -> set[int]:
    res = await db.execute(
        select(DesignTaskLabel.label_id).where(
            DesignTaskLabel.project_id == project_id,
            DesignTaskLabel.task_id == task_id,
            DesignTaskLabel.removed_at.is_(None),
        )
    )
    return {row[0] for row in res.all()}


async def set_task_labels(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    label_ids: list[int],
    user: Any,
    member_role: str,
) -> None:
    """REPLACE набора меток. Метки меняются и в терминальных статусах (Р31).

    Снятие — removed_at, не удаление строки: из этого считается история (Р20).
    Набор не изменился → ни строк, ни события (идемпотентность).
    """
    task = await get_task_row(db, project_id, task_id)
    if not _can_touch(task, user, member_role):
        raise PermissionError("Метки ставит ведущий, автор или исполнитель")

    wanted = set(label_ids)
    current = await _current_label_ids(db, project_id, task_id)
    to_add, to_remove = wanted - current, current - wanted
    if not to_add and not to_remove:
        return

    if to_add:
        res = await db.execute(
            select(DesignLabel)
            .where(
                DesignLabel.id.in_(to_add),
                DesignLabel.project_id == project_id,
                DesignLabel.is_deleted == False,  # noqa: E712
            )
            .limit(len(to_add))
        )
        found = {lb.id: lb for lb in res.scalars().all()}
        missing = to_add - found.keys()
        if missing:
            raise ValueError(_LABEL_NOT_FOUND)
        # Архивную метку нельзя ПОВЕСИТЬ заново; уже висящую — можно оставить.
        if any(found[i].is_archived for i in to_add):
            raise ValueError("Метка в архиве")

    now = utcnow()
    actor = actor_name(user)
    for label_id in sorted(to_add):
        db.add(
            DesignTaskLabel(
                project_id=project_id,
                task_id=task_id,
                label_id=label_id,
                attached_at=now,
                attached_by=actor,
            )
        )
    if to_remove:
        await db.execute(
            update(DesignTaskLabel)
            .where(
                DesignTaskLabel.project_id == project_id,
                DesignTaskLabel.task_id == task_id,
                DesignTaskLabel.label_id.in_(to_remove),
                DesignTaskLabel.removed_at.is_(None),
            )
            .values(removed_at=now, removed_by=actor)
        )

    names: list[str] = []
    if wanted:
        names_res = await db.execute(
            select(DesignLabel.name)
            .where(DesignLabel.project_id == project_id, DesignLabel.id.in_(wanted))
            .order_by(DesignLabel.sort_order, DesignLabel.id)
            .limit(MAX_LABELS)
        )
        names = [row[0] for row in names_res.all()]
    db.add(
        DesignTaskEvent(
            project_id=project_id,
            task_id=task_id,
            old_status=task.status,
            new_status=task.status,
            changed_at=now,
            changed_by=actor,
            comment=f"Метки: {', '.join(names) if names else '—'}",
        )
    )
    await db.commit()


async def set_task_attribute_values(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    value_ids: list[int],
    user: Any,
    member_role: str,
) -> None:
    """REPLACE набора значений реквизитов. В терминальных статусах запрещено (Р31)."""
    task = await get_task_row(db, project_id, task_id)
    if not _can_touch(task, user, member_role):
        raise PermissionError("Реквизиты ставит ведущий, автор или исполнитель")
    if task.status in _TERMINAL_VALUES:
        raise ValueError("Реквизиты закрытой задачи не меняются")

    wanted = set(value_ids)
    cur_res = await db.execute(
        select(DesignTaskAttributeValue.value_id).where(
            DesignTaskAttributeValue.project_id == project_id,
            DesignTaskAttributeValue.task_id == task_id,
        )
    )
    current = {row[0] for row in cur_res.all()}
    to_add, to_remove = wanted - current, current - wanted
    if not to_add and not to_remove:
        return

    rows: list[DesignAttributeValue] = []
    if wanted:
        res = await db.execute(
            select(DesignAttributeValue)
            .where(
                DesignAttributeValue.id.in_(wanted),
                DesignAttributeValue.project_id == project_id,
                DesignAttributeValue.is_deleted == False,  # noqa: E712
            )
            .limit(len(wanted))
        )
        rows = list(res.scalars().all())
        if len(rows) != len(wanted):
            raise ValueError(_VALUE_NOT_FOUND)
        if any(v.is_archived for v in rows if v.id in to_add):
            raise ValueError("Значение в архиве")
        await _guard_single_value(db, project_id, rows)

    now = utcnow()
    actor = actor_name(user)
    for value_id in sorted(to_add):
        db.add(
            DesignTaskAttributeValue(
                project_id=project_id,
                task_id=task_id,
                value_id=value_id,
                created_at=now,
                created_by=actor,
            )
        )
    if to_remove:
        await db.execute(
            delete(DesignTaskAttributeValue).where(
                DesignTaskAttributeValue.project_id == project_id,
                DesignTaskAttributeValue.task_id == task_id,
                DesignTaskAttributeValue.value_id.in_(to_remove),
            )
        )

    db.add(
        DesignTaskEvent(
            project_id=project_id,
            task_id=task_id,
            old_status=task.status,
            new_status=task.status,
            changed_at=now,
            changed_by=actor,
            comment=f"Реквизиты: {await _describe_values(db, project_id, rows)}",
        )
    )
    await db.commit()


async def _guard_single_value(
    db: AsyncSession, project_id: int, rows: list[DesignAttributeValue]
) -> None:
    """У поля с is_multi = false на задаче может быть только одно значение."""
    by_attribute: dict[int, int] = {}
    for v in rows:
        by_attribute[v.attribute_id] = by_attribute.get(v.attribute_id, 0) + 1
    multi_candidates = [aid for aid, n in by_attribute.items() if n > 1]
    if not multi_candidates:
        return
    res = await db.execute(
        select(DesignAttribute.name)
        .where(
            DesignAttribute.id.in_(multi_candidates),
            DesignAttribute.project_id == project_id,
            DesignAttribute.is_multi == False,  # noqa: E712
        )
        .order_by(DesignAttribute.sort_order, DesignAttribute.id)
        .limit(1)
    )
    name = res.scalar_one_or_none()
    if name is not None:
        raise ValueError(f"Поле «{name}» допускает одно значение")


async def _describe_values(
    db: AsyncSession, project_id: int, rows: list[DesignAttributeValue]
) -> str:
    """Человекочитаемый след в журнал: «Бренд — Меллори, Кабинет ВБ — Основной»."""
    if not rows:
        return "—"
    res = await db.execute(
        select(DesignAttribute.id, DesignAttribute.name).where(
            DesignAttribute.project_id == project_id,
            DesignAttribute.id.in_({v.attribute_id for v in rows}),
        )
    )
    names = {row[0]: row[1] for row in res.all()}
    return ", ".join(
        f"{names.get(v.attribute_id, '?')} — {v.value}"
        for v in sorted(rows, key=lambda r: (r.attribute_id, r.sort_order, r.id))
    )


# ─── Массовое проставление (Р33) ─────────────────────────────────────────────


async def bulk_set_labels(
    db: AsyncSession,
    project_id: int,
    task_ids: list[int],
    label_ids: list[int],
    mode: str,
    user: Any,
    member_role: str,
) -> DesignBulkResultOut:
    """add/remove по каждой задаче. Нет прав → skipped; остальное → errors."""
    if len(task_ids) > MAX_BULK_TASKS:
        raise ValueError("Не больше 500 задач за раз")
    result = DesignBulkResultOut()
    for task_id in task_ids:
        try:
            task = await get_task_row(db, project_id, task_id)
        except ValueError:
            result.errors.append(DesignBulkErrorRow(task_id=task_id, message="Задача не найдена"))
            continue
        if not _can_touch(task, user, member_role):
            result.skipped += 1
            continue
        current = await _current_label_ids(db, project_id, task_id)
        wanted = (current | set(label_ids)) if mode == "add" else (current - set(label_ids))
        try:
            await set_task_labels(db, project_id, task_id, sorted(wanted), user, member_role)
            result.updated += 1
        except (ValueError, PermissionError) as e:
            result.errors.append(DesignBulkErrorRow(task_id=task_id, message=str(e)))
    return result


async def bulk_set_attribute_values(
    db: AsyncSession,
    project_id: int,
    task_ids: list[int],
    value_ids: list[int],
    mode: str,
    user: Any,
    member_role: str,
) -> DesignBulkResultOut:
    if len(task_ids) > MAX_BULK_TASKS:
        raise ValueError("Не больше 500 задач за раз")
    result = DesignBulkResultOut()
    for task_id in task_ids:
        try:
            task = await get_task_row(db, project_id, task_id)
        except ValueError:
            result.errors.append(DesignBulkErrorRow(task_id=task_id, message="Задача не найдена"))
            continue
        if not _can_touch(task, user, member_role):
            result.skipped += 1
            continue
        cur_res = await db.execute(
            select(DesignTaskAttributeValue.value_id).where(
                DesignTaskAttributeValue.project_id == project_id,
                DesignTaskAttributeValue.task_id == task_id,
            )
        )
        current = {row[0] for row in cur_res.all()}
        wanted = (current | set(value_ids)) if mode == "add" else (current - set(value_ids))
        try:
            await set_task_attribute_values(
                db, project_id, task_id, sorted(wanted), user, member_role
            )
            result.updated += 1
        except (ValueError, PermissionError) as e:
            result.errors.append(DesignBulkErrorRow(task_id=task_id, message=str(e)))
    return result
