# ruff: noqa: RUF002
"""Router: /localization — отчёт «Индекс локализации» (ИЛ + ИРП).

См. backend/services/localization_index_service.py для расчётной логики
и backend/services/localization_tariff.py для таблиц КТР/КРП.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.localization import (
    LocalizationByPeriod,
    LocalizationDailyPoint,
    LocalizationSkuRow,
    LocalizationSummary,
)
from backend.services import localization_index_service
from backend.services.wb_orders_sync_service import sync_wb_orders
from backend.utils.rate_limit import rate_limit_write

router = APIRouter()


@router.get("/localization/available-dates", response_model=list[str])
async def get_localization_available_dates(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """Список ISO-дат с данными локализации (для UI date-picker'а)."""
    return await localization_index_service.get_available_dates(db, project.id)


@router.get("/localization/summary", response_model=LocalizationSummary)
async def get_localization_summary(
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Top-block отчёта локализации: ИЛ, ИРП, заказы, артикулы."""
    return await localization_index_service.get_summary(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )


@router.get("/localization/skus", response_model=list[LocalizationSkuRow])
async def get_localization_by_sku(
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Таблица per-SKU: nm_id, total/local/non_local, КТР/КРП, статус."""
    return await localization_index_service.get_by_sku(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )


@router.get("/localization/daily", response_model=list[LocalizationDailyPoint])
async def get_localization_daily(
    date_from: date = Query(...),
    date_to: date = Query(...),
    subject: str | None = Query(None),
    brand: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """Динамика ИЛ/ИРП по дням за период с фильтрами по subject/brand."""
    return await localization_index_service.get_daily(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
        subject=subject,
        brand=brand,
    )


@router.get("/localization", response_model=LocalizationByPeriod)
async def get_localization_full(
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Полный ответ: top-block + таблица per-SKU одним запросом."""
    summary = await localization_index_service.get_summary(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )
    rows = await localization_index_service.get_by_sku(
        db,
        project.id,
        date_from.isoformat(),
        date_to.isoformat(),
    )
    return {"summary": summary, "rows": rows}


@router.post("/localization/sync", dependencies=[Depends(rate_limit_write)])
async def sync_localization_now(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Manual trigger: синк WB orders + funnel за указанный период.

    - wb_orders: rolling 30 дней (для разбивки по округам)
    - wb_funnel_daily: за указанный date_from..date_to (для localization_percent)

    Если даты не переданы — синкаются последние 30 дней. Под rate_limit_write —
    защищает от бомбёжки WB API (rate-limit ~1 req/min на ключ).
    """
    # 1) wb_orders за последние 30 дней — синхронно (быстро, ~5-10s)
    orders_stats = await sync_wb_orders(db, project.id, days_back=30)
    # 2) localization_percent — в фоне. WB /history эндпоинт отказывает за
    #    период старше ~30 дней (HTTP 400 "excess limit on days"), поэтому
    #    обрезаем диапазон до min(date_from, today-29). Для более старых дней
    #    `localization_percent` останется NULL — это ограничение WB API.
    from datetime import date as _date, timedelta as _td

    funnel_status = "skipped"
    if date_from and date_to:
        d_to = date_to
        d_from_capped = max(date_from, _date.today() - _td(days=29))
        if d_from_capped <= d_to:
            import asyncio as _asyncio

            # — fire-and-forget background sync, refers нет смысла
            # хранить (нет cancellation/awaiting). Под endpoint scope.
            _bg_task = _asyncio.create_task(
                localization_index_service.sync_localization_only(
                    project.id, d_from_capped.isoformat(), d_to.isoformat()
                )
            )
            del _bg_task  # silences F841
            funnel_status = "scheduled"
        else:
            funnel_status = "skipped_too_old"
    return {
        "ok": True,
        "orders": orders_stats,
        "funnel_status": funnel_status,
        "funnel_window_days": 30,
        "total_fetched": orders_stats.get("total_fetched", 0),
        "updated": orders_stats.get("updated", 0),
    }
