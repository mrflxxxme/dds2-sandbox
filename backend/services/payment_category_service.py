# ruff: noqa: RUF001, RUF002, RUF003
"""Справочник «Назначение оплаты» (PaymentCategory) — CRUD редактируемых категорий.

Категории глобальные (project_id NULL = общие для всех брендов). `code` стабилен и
хранится в `payment_requests.category`; `label` редактируется. Системные (сид pay08)
переименовываются, но не удаляются (LOGISTICS/OTHER — дефолты сервиса заявок). Удаление
категории «в работе» (есть заявки) запрещено — иначе строки осиротеют по лейблу.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.payment_request import PaymentCategory, PaymentRequest
from backend.schemas.payment_request import ALLOWED_CATEGORIES

# Системные коды (7 дефолтов) всегда валидны, даже на свежей/тестовой БД без сида.
SYSTEM_CODES = frozenset(ALLOWED_CATEGORIES)


class PaymentCategoryError(ValueError):
    """Доменная ошибка справочника категорий (роутер → 400)."""


def _scope(project_id: int | None) -> Any:
    """Видимость: глобальные (NULL) + категории текущего проекта (None → только глобальные)."""
    return or_(PaymentCategory.project_id.is_(None), PaymentCategory.project_id == project_id)


async def list_categories(db: AsyncSession, project_id: int) -> list[PaymentCategory]:
    res = await db.execute(
        select(PaymentCategory)
        .where(_scope(project_id), PaymentCategory.is_deleted == False)  # noqa: E712
        .order_by(PaymentCategory.sort_order, PaymentCategory.label)
    )
    return list(res.scalars().all())


async def active_codes(db: AsyncSession, project_id: int | None) -> set[str]:
    res = await db.execute(
        select(PaymentCategory.code).where(_scope(project_id), PaymentCategory.is_deleted == False)  # noqa: E712
    )
    return set(res.scalars().all())


async def is_valid_code(db: AsyncSession, project_id: int | None, code: str) -> bool:
    """Код валиден, если он системный (один из 7) ИЛИ есть активная строка справочника."""
    if code in SYSTEM_CODES:
        return True
    return code in await active_codes(db, project_id)


def _slugify(label: str) -> str:
    """label → код [A-Z0-9_]≤30. Кириллица/пусто → '' (вызывающий даст уникальный фолбэк)."""
    s = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()
    return s[:24]  # запас до 30 под суффикс уникальности


async def _gen_unique_code(db: AsyncSession, project_id: int, label: str) -> str:
    base = _slugify(label) or f"C_{uuid.uuid4().hex[:8].upper()}"
    existing = await active_codes(db, project_id)
    # системные тоже занимают пространство кодов
    existing |= SYSTEM_CODES
    if base not in existing:
        return base
    for i in range(2, 1000):
        cand = f"{base[:26]}_{i}"
        if cand not in existing:
            return cand
    return f"C_{uuid.uuid4().hex[:8].upper()}"  # крайне маловероятно


async def create_category(db: AsyncSession, project_id: int, label: str, sort_order: int | None) -> PaymentCategory:
    label = label.strip()
    if not label:
        raise PaymentCategoryError("Укажите название категории")
    # дубль по имени (без учёта регистра) среди активных — не плодим визуальные дубли
    dup = await db.execute(
        select(PaymentCategory.id).where(
            _scope(project_id),
            PaymentCategory.is_deleted == False,  # noqa: E712
            func.lower(PaymentCategory.label) == label.lower(),
        )
    )
    if dup.first() is not None:
        raise PaymentCategoryError("Категория с таким названием уже есть")
    if sort_order is None:
        mx = await db.execute(
            select(func.max(PaymentCategory.sort_order)).where(_scope(project_id), PaymentCategory.is_deleted == False)  # noqa: E712
        )
        sort_order = (mx.scalar() or 0) + 10
    code = await _gen_unique_code(db, project_id, label)
    # Справочник намеренно ГЛОБАЛЬНЫЙ: новые категории всегда project_id=None (общие для всех
    # брендов). project_id-аргумент используется лишь для скоупа видимости при дедупе/нумерации
    # (см. _scope) — проектных категорий пока нет by design.
    cat = PaymentCategory(project_id=None, code=code, label=label, sort_order=sort_order, is_system=False)
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def _get(db: AsyncSession, project_id: int, cat_id: int) -> PaymentCategory | None:
    res = await db.execute(
        select(PaymentCategory).where(
            PaymentCategory.id == cat_id, _scope(project_id), PaymentCategory.is_deleted == False  # noqa: E712
        )
    )
    return res.scalar_one_or_none()


async def update_category(
    db: AsyncSession, project_id: int, cat_id: int, label: str | None, sort_order: int | None
) -> PaymentCategory:
    cat = await _get(db, project_id, cat_id)
    if cat is None:
        raise PaymentCategoryError("Категория не найдена")
    if label is not None:
        new_label = label.strip()
        if not new_label:
            raise PaymentCategoryError("Название не может быть пустым")
        dup = await db.execute(
            select(PaymentCategory.id).where(
                _scope(project_id),
                PaymentCategory.is_deleted == False,  # noqa: E712
                PaymentCategory.id != cat_id,
                func.lower(PaymentCategory.label) == new_label.lower(),
            )
        )
        if dup.first() is not None:
            raise PaymentCategoryError("Категория с таким названием уже есть")
        cat.label = new_label
    if sort_order is not None:
        cat.sort_order = sort_order
    await db.commit()
    await db.refresh(cat)
    return cat


async def delete_category(db: AsyncSession, project_id: int, cat_id: int) -> None:
    cat = await _get(db, project_id, cat_id)
    if cat is None:
        raise PaymentCategoryError("Категория не найдена")
    if cat.is_system:
        raise PaymentCategoryError("Системную категорию удалить нельзя — можно переименовать")
    # запрет удаления, если на коде висят заявки (любого проекта — категория глобальная)
    in_use = await db.execute(
        select(PaymentRequest.id).where(
            PaymentRequest.category == cat.code, PaymentRequest.is_deleted == False  # noqa: E712
        ).limit(1)
    )
    if in_use.first() is not None:
        raise PaymentCategoryError("Категория используется в заявках — удалить нельзя")
    cat.soft_delete()
    await db.commit()
