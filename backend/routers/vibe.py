# ruff: noqa: RUF001, RUF002
"""Router: /api/v1/vibe/* — вкладка «Вайбкодинг».

Тонкий HTTP-слой, логика — в `services/vibe_service.py`.

Доступ НЕ по проектной роли: `ProjectMember.role` проектная, и клиент-селлер —
`owner` своего проекта, то есть прошёл бы любую проверку по роли. Список доступа —
таблица `vibe_authors`: нет строки → 403 (см. докстринг `backend/models/vibe.py`).

Ингеста тут нет намеренно: CI ходит по SSH и зовёт management-команду напрямую,
HTTP-эндпоинт означал бы ещё один write-путь в прод и токен к нему.

Эндпоинты:
  GET /vibe/me            — статистика текущего пользователя за период
  GET /vibe/is-vibecoder  — дешёвый флаг для сайдбара
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import User
from backend.schemas.vibe import VibeStats
from backend.services import vibe_service
from backend.utils.time import utcnow

router = APIRouter(prefix="/vibe")

DEFAULT_PERIOD_DAYS = 30
# Верхняя граница периода: выборка поставок и `by_day` линейны по длине периода,
# без потолка запрос `?since=2000-01-01` строит 9000 дней ради пустого графика.
MAX_PERIOD_DAYS = 366


@router.get("/me", response_model=VibeStats)
async def get_my_vibe_stats(
    since: date | None = Query(None, description="Начало периода (по умолчанию — 30 дней назад)"),
    until: date | None = Query(None, description="Конец периода (по умолчанию — сегодня)"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VibeStats:
    """Статистика вайбкодинга текущего пользователя за период.

    403 — пользователь не вайбкодер (нет строки в `vibe_authors`).
    """
    until = until or utcnow().date()
    # 30 дней ВКЛЮЧИТЕЛЬНО: since=until-29 даёт ровно 30 точек в by_day.
    since = since or until - timedelta(days=DEFAULT_PERIOD_DAYS - 1)

    if since > until:
        raise HTTPException(400, "Начало периода позже конца")
    if (until - since).days + 1 > MAX_PERIOD_DAYS:
        raise HTTPException(400, f"Период больше {MAX_PERIOD_DAYS} дней")

    stats = await vibe_service.get_stats(db, user.id, since, until)
    if stats is None:
        raise HTTPException(403, "Раздел доступен только вайбкодерам")
    return stats


@router.get("/is-vibecoder")
async def check_is_vibecoder(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    """Флаг для сайдбара: показывать ли пункт «Вайбкодинг».

    Отдельно от `/me`, потому что сайдбар зовёт это на каждой странице, а `/me`
    тянет все поставки за период. Здесь — один индексированный SELECT.
    403 не отдаём: «нет» — это нормальный ответ, а не отказ.
    """
    return {"is_vibecoder": await vibe_service.is_vibecoder(db, user.id)}
