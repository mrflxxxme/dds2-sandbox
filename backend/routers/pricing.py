# ruff: noqa: RUF001, RUF002, RUF003
"""Ценообразование: наценка по артикулам (текущая цена ВБ + себестоимость + расходы ВБ)."""

import logging

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.models import Project, WbSppProbe
from backend.project_context import get_current_project
from backend.schemas.pricing import SppMapResponse
from backend.services.pricing import ai_advisor
from backend.services.pricing import markup as markup_service
from backend.services.pricing import spp_map as spp_map_service
from backend.services.pricing import spp_points as spp_points_service
from backend.services.pricing import spp_probe as spp_probe_service
from backend.services.pricing import spp_scan as spp_scan_service
from backend.services.pricing.sync import sync_card_spp, sync_wb_prices
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.pricing")

router = APIRouter(prefix="/pricing")


@router.get("/markup")
async def get_markup(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    brand: str | None = Query(None),
    category: str | None = Query(None),
    search: str | None = Query(None),
    min_orders: int = Query(0, ge=0),
    only_in_stock: bool = Query(False),
    anomaly_only: bool = Query(False),
    group_by: str = Query("category"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Наценка по артикулам. group_by: category | sku | size | imt (склейка) | anomaly."""
    gb = group_by if group_by in ("sku", "anomaly", "category", "size", "imt") else "category"
    return await markup_service.get_markup_analytics(
        db,
        project.id,
        date_from=date_from,
        date_to=date_to,
        brand=brand,
        category=category,
        search=search,
        min_orders=min_orders,
        only_in_stock=only_in_stock,
        anomaly_only=anomaly_only,
        group_by=gb,
    )


@router.post("/ai-recommendations")
async def ai_recommendations(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    only_in_stock: bool = Query(True),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """AI-рекомендации по ценам/рекламе/остаткам (Claude по сводке метрик)."""
    return await ai_advisor.get_ai_recommendations(
        db, project.id, date_from=date_from, date_to=date_to, only_in_stock=only_in_stock
    )


@router.post("/sync")
async def sync_prices(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Ручной синк текущих цен витрины ВБ (API «Цены и скидки»)."""
    sync_log = await sync_wb_prices(db, project.id)
    await invalidate_cache(f"reports:pricing_markup:project_id={project.id}")
    return {
        "status": sync_log.status,
        "rows": sync_log.rows_inserted,
        "synced_at": sync_log.finished_at.isoformat() if sync_log.finished_at else None,
        "message": sync_log.error_msg,
    }


@router.get("/spp-map", response_model=SppMapResponse)
async def spp_map(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    step: int = Query(100, ge=1, le=1000),  # 1 ₽ = без корзин, каждая цена сама себе уровень
    source: str = Query("card"),
    category: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Карта «категория × цена → СПП»: лесенка уровней и обрывы, где СПП падает."""
    src = source if source in ("card", "orders") else "card"
    return await spp_map_service.get_spp_map(
        db, project.id, date_from=date_from, date_to=date_to, step=step, source=src, category=category
    )


@router.get("/spp-scan")
async def spp_scan(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    top: int = Query(40, ge=1, le=200),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """План прогонов: какие цены пробовать, чтобы закрыть белые пятна и пороги.

    Ничего не ставит и не пишет — только считает очередь по накопленным точкам.
    """
    return await spp_scan_service.get_scan_plan(
        db, project.id, date_from=date_from, date_to=date_to, top=top
    )


@router.post("/spp-scan/run")
async def spp_scan_run(
    top: int = Query(10, ge=1, le=40, description="сколько первых целей плана поставить"),
    max_wait_sec: int = Query(1200, ge=60, le=3600),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Прогон пачкой: поставить цены плана, дождаться реакции витрины, вернуть назад.

    ПИШЕТ ЦЕНЫ В ВБ. Рамки (−300/+1000 ₽, шаг 35 %, копейки, возврат) —
    в `spp_probe`; здесь только очередь и ожидание.
    """
    plan = await spp_scan_service.get_scan_plan(db, project.id, top=top)
    targets = [{"nm_id": r["donor"]["nm_id"], "price": r["price"]} for r in plan["plan"]]
    return await spp_scan_service.run_scan_batch(
        db, project.id, targets, max_wait_sec=max_wait_sec
    )


@router.get("/spp-history")
async def spp_history(
    nm_ids: str = Query(..., description="nm_id через запятую"),
    days: int = Query(30, ge=1, le=180),
    source: str = Query("card"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История «цена → СПП» по дням для набора артикулов."""
    ids = [int(x) for x in nm_ids.split(",") if x.strip().isdigit()][:500]
    src = source if source in ("card", "orders") else "card"
    return {
        "rows": await spp_map_service.get_level_history(db, project.id, ids, days=days, source=src)
    }


@router.post("/spp-probe")
async def spp_probe(
    nm_id: int = Query(..., description="артикул-донор"),
    target_price: float = Query(..., gt=0, description="целевая цена витрины, ₽"),
    hold_sec: int = Query(spp_probe_service.HOLD_SEC, ge=300, le=spp_probe_service.MAX_HOLD_SEC),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Проба цены: поставить, дождаться реакции витрины, вернуть назад.

    ЕДИНСТВЕННЫЙ эндпоинт, который пишет цену в ВБ. Возврат гарантирован даже
    при ошибке; результат остаётся в журнале проб.
    """
    try:
        probe = await spp_probe_service.start_probe(
            db, project.id, nm_id, target_price, hold_sec=hold_sec
        )
    except spp_probe_service.ProbeRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    asyncio.create_task(_run_probe_bg(probe.id, project.id, hold_sec))  # noqa: RUF006
    return {"probe_id": probe.id, "status": probe.status, "hold_sec": hold_sec}


async def _run_probe_bg(probe_id: int, project_id: int, hold_sec: int) -> None:
    """Фон: проба живёт часами, HTTP-запрос столько ждать не может."""
    from backend.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        probe = await db.get(WbSppProbe, probe_id)
        if probe and probe.project_id == project_id:
            await spp_probe_service.run_probe(db, probe, hold_sec=hold_sec)


@router.get("/spp-probes")
async def spp_probes(
    limit: int = Query(50, ge=1, le=500),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Журнал проб: что ставили, что увидели, вернули ли цену."""
    rows = (
        await db.execute(
            select(WbSppProbe)
            .where(WbSppProbe.project_id == project.id)
            .order_by(WbSppProbe.started_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "rows": [
            {
                "id": p.id, "nm_id": p.nm_id, "status": p.status,
                "price_before": float(p.seller_price_before), "target_price": float(p.target_price),
                "buyer_before": float(p.buyer_price_before) if p.buyer_price_before else None,
                "buyer_after": float(p.buyer_price_after) if p.buyer_price_after else None,
                "spp_before": float(p.spp_before) if p.spp_before else None,
                "spp_after": float(p.spp_after) if p.spp_after else None,
                "reacted_after_sec": p.reacted_after_sec, "polls": p.polls,
                "reverted": p.reverted, "error": p.error,
                "started_at": p.started_at.isoformat(),
                "finished_at": p.finished_at.isoformat() if p.finished_at else None,
            }
            for p in rows
        ]
    }


@router.post("/spp-observe")
async def spp_observe(
    backfill_days: int = Query(0, ge=0, le=180),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Снять точку СПП сейчас (card-API); `backfill_days>0` — ещё и ретро из заказов.

    Сначала синкаем ЦЕНЫ, потом витрину. Иначе срез считается от нашей вчерашней
    цены, а точки товаров, которым цену меняли, молча улетают в `stale`: снимок
    сверяет нашу базу с `basic` витрины и расходящиеся пропускает. Кнопка «Снять
    срез» обязана давать свежий срез целиком, а не наполовину.
    """
    fresh = await spp_points_service.snapshot_now(db, project.id)
    await invalidate_cache(f"reports:pricing_markup:project_id={project.id}")
    back = (
        await spp_points_service.backfill_from_orders(db, project.id, days=backfill_days)
        if backfill_days
        else {"written": 0, "days": 0}
    )
    return {**fresh, "backfill": back}


@router.post("/sync-spp")
async def sync_spp(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(rate_limit_write),
):
    """Синк реальной цены покупателя с СПП из публичного card-API (без ключа)."""
    res = await sync_card_spp(db, project.id)
    await invalidate_cache(f"reports:pricing_markup:project_id={project.id}")
    return res
