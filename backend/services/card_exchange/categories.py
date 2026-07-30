"""Справочник корневых категорий биржи карточек (статический, из xlsx Дениса).

Источник — `backend/data/card_exchange_categories.json` вида {категория: [имя предмета]}.
Дерево строил Денис из категорий WB; каждый предмет ровно в одной категории
(96 категорий → 7424 предмета на 2026-07-30). Обновление базы = заменить JSON + деплой.

Зачем: WB отдаёт предметы биржи ПЛОСКО (`showcase_subjects`, поле `category` пустое),
корневой группировки у WB нет. Этот справочник добавляет каскад
«корневая категория → предметы» поверх витрины биржи. Матчим предмет по ИМЕНИ:
имя из JSON → `id` из живого `showcase_subjects` (см. resolve_subject_ids).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# backend/services/card_exchange/categories.py → parents[2] == backend/
_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "card_exchange_categories.json"


@lru_cache(maxsize=1)
def _mapping() -> dict[str, list[str]]:
    """{категория: [имя предмета]} — читается один раз, кешируется на процесс."""
    with _DATA_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    return {str(k): [str(x) for x in v] for k, v in data.items()}


@lru_cache(maxsize=1)
def _reverse_index() -> dict[str, str]:
    """{имя предмета: корневая категория} — обратный индекс справочника.

    Дерево чистое (каждый предмет ровно в одной категории), поэтому обратный индекс
    однозначен. Нужен, чтобы по предмету объявления биржи (meta.subjectName из
    showcase/ads/{id}/details) определить корневую категорию Дениса.
    """
    return {subject: category for category, subjects in _mapping().items() for subject in subjects}


def root_category_for_subject(subject: str) -> str | None:
    """Корневая категория предмета (None, если предмета нет в справочнике)."""
    return _reverse_index().get((subject or "").strip())


def root_categories_for_subjects(subjects: list[str]) -> list[str]:
    """Уникальные корневые категории набора предметов, в порядке справочника."""
    found = {root_category_for_subject(s) for s in subjects}
    found.discard(None)
    order = list(_mapping())
    return sorted((c for c in found if c), key=order.index)


def list_root_categories() -> list[dict]:
    """Корневые категории для селектора фильтра: [{"category", "subject_count"}] по имени.

    Ключи — snake_case: роутер собирает из них RootCategory напрямую (`RootCategory(**c)`).
    """
    m = _mapping()
    return [{"category": c, "subject_count": len(m[c])} for c in m]


def subjects_for_category(category: str) -> list[str]:
    """Имена предметов одной корневой категории (пустой список, если категории нет)."""
    return list(_mapping().get(category, []))


def resolve_subject_ids(
    categories: list[str], name_to_id: dict[str, int]
) -> tuple[list[int], list[str]]:
    """Имена предметов выбранных категорий → subjectIDs через карту WB `name → id`.

    name_to_id строится из `WbPortalClient.showcase_subjects()`. Возвращает
    (отсортированные уникальные ids, отсортированные уникальные несматченные имена).
    Несматченные — диагностика рассинхрона нашего справочника и предметов WB;
    в фильтр они просто не попадают.
    """
    ids: set[int] = set()
    unmatched: set[str] = set()
    for cat in categories:
        for name in subjects_for_category(cat):
            sid = name_to_id.get(name)
            if sid is None:
                unmatched.add(name)
            else:
                ids.add(sid)
    return sorted(ids), sorted(unmatched)
