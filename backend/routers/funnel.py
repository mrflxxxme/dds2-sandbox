# ruff: noqa: RUF002
"""
Router: /funnel — WB Sales funnel analytics (воронка продаж).
Thin HTTP layer — all business logic is in services/funnel/ package.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas.tariff import WbTariffSchema, WbTariffUploadResult
from backend.services import funnel as funnel_service
from backend.services.tariff_service import delete_tariff, list_tariffs, upload_tariffs
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.funnel")

router = APIRouter(prefix="/funnel")


# ─── Schemas ─────────────────────────────────────────────────────────────────


class SyncRequest(BaseModel):
    date_from: str  # YYYY-MM-DD
    date_to: str  # YYYY-MM-DD


class CostOverrideRequest(BaseModel):
    nm_id: int
    cost_price: float


class BulkCostItem(BaseModel):
    barcode: str
    cost_price: float
    currency: str = "RUB"


class BulkCostRequest(BaseModel):
    items: list[BulkCostItem]


class TaxRateRequest(BaseModel):
    tax_rate: float


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _load_tax_info(db: AsyncSession, project: Project) -> dict:
    """Load tax settings from TaxRate table, fall back to project.tax_rate."""
    from datetime import date

    from backend.services.bdr_loaders import load_tax_settings

    today = date.today()
    tax_info = await load_tax_settings(db, project.id, today, today)

    # If no TaxRate rows configured, fall back to legacy project.tax_rate
    if tax_info.get("usn_rate", 0) == 0 and tax_info.get("nds_rate", 0) == 0:
        legacy_rate = float(project.tax_rate or 6)
        tax_info = {
            "tax_regime": "usn_income",
            "usn_rate": legacy_rate,
            "nds_rate": 0,
            "cost_as_expense": False,
        }

    return tax_info


async def _load_bdr_rates(db: AsyncSession, project_id: int):
    """Load BDR rates (daily + avg fallback) for profit calculation."""
    from backend.services.funnel.bdr_rates import get_bdr_rates

    return await get_bdr_rates(db, project_id)


# ─── Sync endpoints ─────────────────────────────────────────────────────────


@router.post("/sync", dependencies=[Depends(rate_limit_write)])
async def sync_funnel(
    body: SyncRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Sync WB sales funnel + advertising data for a date range (manual trigger)."""
    result = await funnel_service.run_funnel_sync(db, project.id, body.date_from, body.date_to)
    if result["status"] == "error" and not result["rows"]:
        raise HTTPException(400, result["errors"][0] if result["errors"] else "Sync failed")
    return result


@router.post("/resync_ads", dependencies=[Depends(rate_limit_write)])
async def resync_ads(
    project: Project = Depends(get_current_project),
):
    """Batch re-sync ALL ad data for the project (entire date range at once).
    Runs in background — returns immediately.
    """
    asyncio.create_task(funnel_service.batch_resync_ads(project.id))  # noqa: RUF006
    return {"status": "started", "message": f"Batch ad resync started for project {project.id}"}


@router.get("/sync_status")
async def get_sync_status(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get scheduler status, last sync info, and missing days count."""
    from backend.scheduler import get_scheduler_info
    from backend.scheduler.helpers import get_missing_dates
    from backend.services.funnel.sync import get_recent_sync_logs

    logs = await get_recent_sync_logs(db, project.id)

    # Count missing days for this project
    try:
        missing = await get_missing_dates(project.id)
        missing_days = len(missing)
    except Exception:
        missing_days = None

    return {
        "scheduler": get_scheduler_info(),
        "last_syncs": logs,
        "missing_days": missing_days,
    }


@router.post("/backfill", dependencies=[Depends(rate_limit_write)])
async def backfill_funnel(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger full backfill: find all missing days in last 90 and sync them.
    Runs in BACKGROUND — returns immediately, sync continues async.
    """
    from backend.scheduler.helpers import get_missing_dates

    pid = project.id
    missing = await get_missing_dates(pid)

    if not missing:
        return {"status": "ok", "message": "Нет пропущенных дней", "missing_count": 0}

    logger.info(f"Backfill: project {pid} — {len(missing)} missing days, starting background task")
    asyncio.create_task(funnel_service.run_backfill_bg(pid, missing))  # noqa: RUF006

    return {
        "status": "started",
        "message": f"Запущен фоновый backfill: {len(missing)} пропущенных дней",
        "total_missing": len(missing),
    }


# ─── Data endpoints ─────────────────────────────────────────────────────────


async def _postprocess_funnel_rows(
    db: AsyncSession,
    pid: int,
    data: list[dict],
    group_by: str,
    extended: bool,
    min_orders: int,
) -> list[dict]:
    """Расширенный режим (остатки/себестоимость/прогноз) + серверный порог по заказам."""
    data = funnel_service.apply_min_orders(data, min_orders)
    if extended:
        stock_map = await funnel_service.get_stock_cost_map(db, pid)
        funnel_service.merge_stock_costs(data, stock_map, group_by)
    return data


@router.get("/data")
async def get_funnel_data(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    brand: str | None = Query(None),
    vendor_code: str | None = Query(None),
    subject: str | None = Query(None),
    group_by: str = Query("day"),
    extended: bool = Query(False),
    min_orders: int = Query(0, ge=0),
    tag: str | None = Query(None),
    imt: str | None = Query(None),
    color: str | None = Query(None),
    subcat: bool = Query(False),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get funnel data. Supports grouping by day, sku, size, or detailed (per article filter).

    extended=true добавляет складские поля; min_orders скрывает строки с меньшим
    числом заказов (оба — для всех группировок, кроме day). tag/imt/color — фильтр
    по ярлыку/склейке/цвету во ВСЕХ группировках. group_by=size — дерево
    Категория→Размер→SKU (с оверрайдами/алиасами размеров); subcat=true добавляет
    уровень под-категории.
    """
    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await _load_bdr_rates(db, project.id)
    nm_filter = await funnel_service.resolve_filter_nm_ids(db, project.id, tag, imt, color)

    if group_by == "abc":
        # Use full SKU data + ABC classification
        data = await funnel_service.get_funnel_by_sku(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        # Add ABC classification
        from backend.services.funnel.ad_campaigns_service import _assign_abc

        _assign_abc(data, "revenue", "abc_revenue")
        _assign_abc(data, "profit", "abc_profit")
        data = await _postprocess_funnel_rows(db, project.id, data, "abc", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "abc",
        }

    if group_by == "sku" and not vendor_code:
        data = await funnel_service.get_funnel_by_sku(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        data = await _postprocess_funnel_rows(db, project.id, data, "sku", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "sku",
        }

    if group_by == "brand" and not vendor_code:
        data = await funnel_service.get_funnel_by_brand(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        data = await _postprocess_funnel_rows(db, project.id, data, "brand", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "brand",
        }

    if group_by == "tag" and not vendor_code:
        data = await funnel_service.get_funnel_by_tag(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        data = await _postprocess_funnel_rows(db, project.id, data, "tag", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "tag",
        }

    if group_by == "imt" and not vendor_code:
        data = await funnel_service.get_funnel_by_imt(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        data = await _postprocess_funnel_rows(db, project.id, data, "imt", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "imt",
        }

    if group_by == "subject" and not vendor_code:
        data = await funnel_service.get_funnel_by_subject(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        data = await _postprocess_funnel_rows(db, project.id, data, "subject", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "subject",
        }

    if group_by == "size" and not vendor_code:
        from backend.services import refs_service

        override_map, alias_map = await refs_service.build_size_resolver(db, project.id)
        cat_override_map = await refs_service.build_category_resolver(db, project.id)
        subcat_names = await refs_service.get_subcategory_names(db, project.id) if subcat else None
        data = await funnel_service.get_funnel_by_category_size(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
            override_map=override_map,
            alias_map=alias_map,
            cat_override_map=cat_override_map,
            subcat_names=subcat_names,
            split_by_subcategory=subcat,
        )
        data = await _postprocess_funnel_rows(db, project.id, data, "size", extended, min_orders)
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "size",
        }

    detailed = bool(vendor_code)

    if not detailed:
        data = await funnel_service.get_funnel_aggregated(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        return {
            "data": data,
            "tax_info": tax_info,
            "has_bdr": bool(bdr_rates_map),
            "detailed": False,
            "group_by": "day",
        }
    else:
        data = await funnel_service.get_funnel_detailed(
            db,
            project.id,
            tax_info,
            date_from,
            date_to,
            brand,
            vendor_code,
            subject,
            bdr_rates_map=bdr_rates_map,
            nm_ids=nm_filter,
        )
        return {"data": data, "tax_info": tax_info, "has_bdr": bool(bdr_rates_map), "detailed": True, "group_by": "day"}


@router.get("/summary")
async def get_funnel_summary(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    brand: str | None = Query(None),
    subject: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get summary totals for the period header."""
    return await funnel_service.get_summary(db, project.id, date_from, date_to, brand, subject)


@router.get("/filters")
async def get_funnel_filters(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get unique brands, subjects, dates for filter dropdowns."""
    return await funnel_service.get_filters(db, project.id)


@router.get("/colors")
async def get_funnel_colors(
    subject: str | None = Query(None),
    brand: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Цвета (распарсенные из артикулов) для фильтра, опционально по категории/бренду."""
    return await funnel_service.get_color_options(db, project.id, subject, brand)


# ─── Cost overrides ─────────────────────────────────────────────────────────


@router.get("/costs")
async def get_cost_overrides(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get all manual cost overrides + items without cost."""
    return await funnel_service.get_cost_overrides(db, project.id)


@router.get("/missing_costs")
async def get_missing_costs(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Products without cost_price that participate in funnel/BDR calculations."""
    return await funnel_service.get_missing_costs(db, project.id)


@router.post("/cost", dependencies=[Depends(rate_limit_write)])
async def set_cost_override(
    body: CostOverrideRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set or update manual cost price for an nmId."""
    result = await funnel_service.set_cost_override(db, project.id, body.nm_id, body.cost_price)
    # Cost overrides feed BDR, ОПиУ and pricing_markup readers — invalidate all project reports.
    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project.id)
    return result


@router.post("/costs/bulk", dependencies=[Depends(rate_limit_write)])
async def bulk_set_cost_overrides(
    body: BulkCostRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Bulk set cost prices by barcode or nm_id code.

    Resolves barcode → nm_id via Nomenclature table, then upserts WbCostOverride.
    """
    result = await funnel_service.bulk_set_cost_overrides(db, project.id, body.items)
    # Invalidate BDR and related report caches so "без себестоимости" count updates
    from backend.cache import invalidate_project_reports

    await invalidate_project_reports(project.id)
    return result


@router.get("/tax")
async def get_tax_rate(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get project tax rate."""
    return {"tax_rate": float(project.tax_rate or 6)}


@router.post("/tax", dependencies=[Depends(rate_limit_write)])
async def set_tax_rate(
    body: TaxRateRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Set project tax rate."""
    from backend.services.project_settings_service import set_tax_rate as _set_tax_rate

    new_rate = await _set_tax_rate(db, project, body.tax_rate)
    return {"status": "ok", "tax_rate": float(new_rate)}


# ─── Day Analysis ────────────────────────────────────────────────────────────


@router.get("/day-analysis")
async def get_day_analysis(
    target_date: str = Query(..., description="YYYY-MM-DD"),
    brand: str | None = Query(None),
    subject: str | None = Query(None),
    trend_days: int = Query(14, description="Days for trend window"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Day analysis: summary, comparison, top products, trend, anomalies."""
    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await _load_bdr_rates(db, project.id)
    return await funnel_service.get_day_analysis(
        db,
        project.id,
        tax_info,
        target_date,
        brand,
        subject,
        trend_days,
        bdr_rates_map=bdr_rates_map,
    )


@router.get("/trends")
async def get_product_trends(
    trend_days: int = Query(7, description="Trend window: 7, 14, or 30"),
    brand: str | None = Query(None),
    search: str | None = Query(None, description="Filter by vendor_code or nmId"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Per-product metrics with linear regression trends."""
    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await _load_bdr_rates(db, project.id)
    return await funnel_service.get_product_trends(
        db,
        project.id,
        tax_info,
        trend_days,
        brand,
        search,
        bdr_rates_map=bdr_rates_map,
    )


@router.get("/anomalies")
async def get_anomalies_report(
    period_days: int = Query(7),
    brand: str | None = Query(None),
    include_rf_stocks: bool = Query(False),
    min_orders: int = Query(5),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Anomalies report — detect problematic products across 5 patterns."""
    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await _load_bdr_rates(db, project.id)
    return await funnel_service.get_anomalies(
        db,
        project.id,
        tax_info,
        period_days=period_days,
        brand=brand,
        include_rf_stocks=include_rf_stocks,
        min_orders=min_orders,
        bdr_rates_map=bdr_rates_map,
    )


# ─── Capital analysis ──────────────────────────────────────────────────────


@router.get("/capital")
async def get_capital_report(
    period_days: int = Query(7),
    brand: str | None = Query(None),
    group_by: str = Query("brand"),
    parent_filter: str | None = Query(None),
    include_rf_stocks: bool = Query(False),
    elasticity: float = Query(1.8),
    illiquid_threshold: int = Query(60),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Capital analysis — liquidity classification, ROI, recommendations."""
    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await _load_bdr_rates(db, project.id)
    return await funnel_service.get_capital_analysis(
        db,
        project.id,
        tax_info,
        period_days=period_days,
        brand=brand,
        group_by=group_by,
        parent_filter=parent_filter,
        include_rf_stocks=include_rf_stocks,
        elasticity=elasticity,
        illiquid_threshold=illiquid_threshold,
        bdr_rates_map=bdr_rates_map,
    )


# ─── Daily Health Check ───────────────────────────────────────────────────


@router.get("/health_check")
async def get_health_check(
    brand: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Daily health check: urgent shipments, overdue assemblies, category A health, illiquid actions."""
    from backend.services.health_check_service import get_daily_health

    tax_info = await _load_tax_info(db, project)
    return await get_daily_health(
        db,
        project_id=project.id,
        tax_info=tax_info,
        brand=brand,
    )


# ─── Tariffs (WB commission rates) ──────────────────────────────────────────


@router.get("/tariffs", response_model=list[WbTariffSchema])
async def get_tariffs(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all WB tariffs for the project."""
    return await list_tariffs(db, project.id)


@router.post("/tariffs/upload", response_model=WbTariffUploadResult, dependencies=[Depends(rate_limit_write)])
async def upload_tariffs_xlsx(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload WB tariff xlsx file. Replaces all existing tariffs."""
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Ожидается файл .xlsx")
    data = await file.read()

    from backend.config import settings as app_settings

    max_bytes = app_settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Файл слишком большой. Максимум: {app_settings.MAX_UPLOAD_SIZE_MB} МБ",
        )

    from backend.utils.file_validation import validate_file_content

    validate_file_content(data, file.filename or "upload.xlsx")

    try:
        result = await upload_tariffs(db, project.id, data)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return result


@router.delete("/tariffs/{tariff_id}", dependencies=[Depends(rate_limit_write)])
async def remove_tariff(
    tariff_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a single tariff."""
    ok = await delete_tariff(db, project.id, tariff_id)
    if not ok:
        raise HTTPException(404, "Тариф не найден")
    return {"status": "ok"}


# ─── Ads Tab ──────────────────────────────────────────────────────────────────


@router.get("/ad_tab")
async def get_ad_tab(
    date_from: str = Query(...),
    date_to: str = Query(...),
    brand: str = Query(""),
    subject: str = Query(""),
    group_by: str = Query("sku"),
    db: AsyncSession = Depends(get_db),
    project: Project = Depends(get_current_project),
):
    """Get advertising data, optionally grouped by brand/subject/tag/imt."""
    from backend.services.funnel.ad_campaigns_service import get_ad_tab_data, get_ad_tab_grouped

    if group_by in ("brand", "subject", "tag", "imt"):
        return await get_ad_tab_grouped(db, project.id, date_from, date_to, brand, subject, group_by)
    return await get_ad_tab_data(db, project.id, date_from, date_to, brand, subject)


@router.get("/campaigns")
async def get_ad_campaigns_list(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Все рекламные кампании: бюджеты, расход/клики/CTR, ДРР и маржа за период (деф. 7 дней)."""
    from backend.services.funnel.ads_manager import list_ad_campaigns

    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await _load_bdr_rates(db, project.id)
    return await list_ad_campaigns(
        db, project.id, tax_info=tax_info, bdr_rates_map=bdr_rates_map, date_from=date_from, date_to=date_to,
    )


class ScheduleSettingRequest(BaseModel):
    campaign_id: int
    enabled: bool = False
    pause_hour: int = 0  # час МСК, когда ставить на паузу (после долива ВБ в 00:00)
    resume_hour: int = 9  # час МСК, когда запускать обратно


@router.get("/campaigns/schedule")
async def get_campaigns_schedule(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Настройки паузы по расписанию по кампаниям (исполняет scheduler-джоба ads_schedule)."""
    from backend.services.funnel.ads_manager import get_schedule_settings

    return await get_schedule_settings(db, project.id)


@router.get("/campaigns/wb-autopay")
async def get_campaigns_wb_autopay(
    campaign_id: int | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Где ВБ доливал бюджет за последние дни (детект автопополнения ВБ по событиям бюджета).

    API ВБ статус своего автопея не отдаёт — определяем по фактическим доливам.
    Кампании без записи в ответе = доливов со стороны ВБ не замечено.
    """
    from backend.services.funnel.ads_manager import get_wb_autopay_status

    return await get_wb_autopay_status(db, project.id, campaign_id)


@router.get("/campaigns/schedule/log")
async def get_campaigns_schedule_log(
    campaign_id: int | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Журнал пауз/запусков по расписанию (новые первыми); campaign_id — фильтр по кампании."""
    from backend.services.funnel.ads_manager import get_schedule_log

    log = await get_schedule_log(db, project.id)
    if campaign_id is not None:
        log = [e for e in log if int(e.get("campaign_id") or 0) == campaign_id]
    return log


@router.get("/campaigns/budget/ledger")
async def get_campaigns_budget_ledger(
    campaign_id: int | None = None,
    kind: str | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История бюджета (пополнения кампании/счёта + списания) из данных, парсимых из WB.
    campaign_id — по конкретной кампании; без него — по всему проекту («все кампании»).
    kind: None — всё; "topup" — только пополнения; "charge" — только списания."""
    from backend.services.funnel.ads_manager import get_budget_ledger

    return await get_budget_ledger(db, project.id, campaign_id, kind)


@router.post("/campaigns/schedule", dependencies=[Depends(rate_limit_write)])
async def set_campaigns_schedule(
    body: ScheduleSettingRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сохранить настройку паузы по расписанию. Кампанию не трогает —
    паузу/запуск делает только scheduler-тик. Возвращает {"settings": {...}}.
    """
    from backend.services.funnel.ads_manager import set_schedule_setting

    settings = await set_schedule_setting(
        db, project.id, body.campaign_id,
        {"enabled": body.enabled, "pause_hour": body.pause_hour, "resume_hour": body.resume_hour},
    )
    return {"settings": settings}


class CampaignStateRequest(BaseModel):
    active: bool  # True — запустить/возобновить, False — поставить на паузу


@router.post("/campaigns/{campaign_id}/state", dependencies=[Depends(rate_limit_write)])
async def set_campaign_state_endpoint(
    campaign_id: int,
    body: CampaignStateRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Запустить или поставить кампанию на паузу в WB (реальное изменение статуса)."""
    from backend.services.funnel.ads_manager import set_campaign_active

    result = await set_campaign_active(db, project.id, campaign_id, body.active)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось изменить статус кампании")
    return result


@router.post("/campaigns/{campaign_id}/refresh", dependencies=[Depends(rate_limit_write)])
async def refresh_campaign_ep(
    campaign_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Точечный догруз ОДНОЙ кампании из WB (деталь + бюджет + свежая дневная стата за 2 дня)
    и синхронная запись в зеркало. По кнопке «Обновить» на странице кампании."""
    from backend.services.funnel.ad_campaigns_service import refresh_one_campaign

    result = await refresh_one_campaign(db, project.id, campaign_id)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось обновить кампанию")
    return result


@router.post("/campaigns/{campaign_id}/stop", dependencies=[Depends(rate_limit_write)])
async def stop_campaign_ep(
    campaign_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Завершить кампанию в WB (необратимо)."""
    from backend.services.funnel.ads_manager import stop_campaign

    result = await stop_campaign(db, project.id, campaign_id)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось завершить кампанию")
    return result


class CampaignRenameRequest(BaseModel):
    name: str


@router.post("/campaigns/{campaign_id}/rename", dependencies=[Depends(rate_limit_write)])
async def rename_campaign_ep(
    campaign_id: int,
    body: CampaignRenameRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Переименовать кампанию в WB."""
    from backend.services.funnel.ads_manager import rename_campaign

    result = await rename_campaign(db, project.id, campaign_id, body.name)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось переименовать кампанию")
    return result


@router.delete("/campaigns/{campaign_id}", dependencies=[Depends(rate_limit_write)])
async def delete_campaign_ep(
    campaign_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Удалить кампанию в WB (только статус «Готова»)."""
    from backend.services.funnel.ads_manager import delete_campaign

    result = await delete_campaign(db, project.id, campaign_id)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось удалить кампанию")
    return result


class CampaignNmsRequest(BaseModel):
    add: list[int] = []
    delete: list[int] = []


@router.patch("/campaigns/{campaign_id}/nms", dependencies=[Depends(rate_limit_write)])
async def change_campaign_nms_ep(
    campaign_id: int,
    body: CampaignNmsRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Добавить/убрать товары кампании в WB."""
    from backend.services.funnel.ads_manager import change_campaign_nms

    result = await change_campaign_nms(db, project.id, campaign_id, body.add, body.delete)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось изменить товары кампании")
    return result


class CardBid(BaseModel):
    nm_id: int
    bid_rub: float
    placement: str = "search"


class CardBidsRequest(BaseModel):
    bids: list[CardBid]


@router.patch("/campaigns/{campaign_id}/card-bids", dependencies=[Depends(rate_limit_write)])
async def set_card_bids_ep(
    campaign_id: int,
    body: CardBidsRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Изменить ставки карточек товаров в WB."""
    from backend.services.funnel.ads_manager import set_card_bids

    result = await set_card_bids(db, project.id, campaign_id, [b.model_dump() for b in body.bids])
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось изменить ставки")
    return result


@router.get("/ad-subjects")
async def ad_subjects_ep(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Предметы, по которым можно создать рекламную кампанию."""
    from backend.services.funnel.ads_manager import get_ad_subjects

    return await get_ad_subjects(db, project.id)


class AdNmsRequest(BaseModel):
    subject_ids: list[int]


@router.post("/ad-nms")
async def ad_nms_ep(
    body: AdNmsRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Карточки товаров для кампании по выбранным предметам."""
    from backend.services.funnel.ads_manager import get_ad_nms

    return await get_ad_nms(db, project.id, body.subject_ids)


class CreateCampaignRequest(BaseModel):
    name: str
    nms: list[int]
    bid_type: str = "manual"
    payment_type: str = "cpm"
    placement_types: list[str] | None = None


@router.post("/campaigns/create", dependencies=[Depends(rate_limit_write)])
async def create_campaign_ep(
    body: CreateCampaignRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать рекламную кампанию в WB."""
    from backend.services.funnel.ads_manager import create_campaign

    result = await create_campaign(
        db, project.id, body.name, body.nms, body.bid_type, body.payment_type, body.placement_types,
    )
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось создать кампанию")
    return result


@router.get("/campaigns/{campaign_id}/history")
async def get_campaign_history_endpoint(
    campaign_id: int,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История кампании за выбранный период для графика: расход кампании + метрики товаров."""
    from backend.services.funnel.ads_manager import get_campaign_history

    return await get_campaign_history(db, project.id, campaign_id, date_from=date_from, date_to=date_to)


@router.get("/ad-article-catalog")
async def get_ad_article_catalog_ep(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Полный каталог артикулов (nm_id → артикул/предмет/бренд) для каскадных фильтров рекламы."""
    from backend.services.funnel.ads_manager import list_ad_article_catalog

    return await list_ad_article_catalog(db, project.id)


@router.get("/campaigns/{campaign_id}/metrics")
async def get_campaign_metrics_endpoint(
    campaign_id: int,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    nm_id: int | None = Query(None),
    zone: str | None = Query(None, pattern="^(search|recommendations)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Посуточные метрики кампании: статистика РК + воронка её товаров, со строкой-итогом.
    nm_id — воронка только по этому товару. zone — разбить РК-часть по зоне показов."""
    from backend.services.funnel.ads_manager import get_campaign_metrics

    # Разбивка по зоне нужна свежая посуточная статистика поиска — подгружаем on-demand
    if zone and date_from and date_to:
        from backend.services.funnel.ad_search_stats import sync_ad_search_daily

        try:
            await sync_ad_search_daily(db, project.id, campaign_id, date_from, date_to)
        except Exception:  # noqa: BLE001 — статистика не критична, отдадим что есть
            pass

    # СПП по дням из отчёта «Заказы» (wb_orders, поштучно → среднее за день) — для «Цены Клиенту».
    # Источник свежее и полнее финотчёта; тот же интерфейс BdrRatesLookup.
    from backend.services.funnel.orders_spp import get_orders_spp_map

    spp_map = await get_orders_spp_map(db, project.id)
    return await get_campaign_metrics(
        db, project.id, campaign_id, date_from=date_from, date_to=date_to, nm_id=nm_id, zone=zone,
        bdr_rates_map=spp_map,
    )


@router.get("/campaigns/{campaign_id}/metrics/by-zone")
async def get_campaign_zone_metrics_endpoint(
    campaign_id: int,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    zone: str = Query("total", pattern="^(total|search|recommendations)$"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Посуточные РК-метрики кампании в разрезе зоны показов (Всего / Поиск / Рекомендации).
    Только рекламная статистика; воронка по зонам не делится. Разбивка — только для CPM."""
    from backend.services.funnel.ads_manager import get_campaign_zone_metrics

    # Для «Поиска»/«Рекомендаций» нужна свежая посуточная статистика поиска — подгружаем on-demand
    if zone in ("search", "recommendations") and date_from and date_to:
        from backend.services.funnel.ad_search_stats import sync_ad_search_daily

        try:
            await sync_ad_search_daily(db, project.id, campaign_id, date_from, date_to)
        except Exception:  # noqa: BLE001 — статистика не критична, отдадим что есть
            pass

    return await get_campaign_zone_metrics(
        db, project.id, campaign_id, date_from=date_from, date_to=date_to, zone=zone,
    )


@router.get("/campaigns/{campaign_id}/hourly")
async def get_campaign_hourly_endpoint(
    campaign_id: int,
    date: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Почасовой расход кампании за день (восстановлен из снимков остатка бюджета, ~10 мин).
    Показов/кликов по часам WB не отдаёт — только расход. date по умолчанию — сегодня (МСК)."""
    from backend.services.funnel.ads_manager import get_hourly_spend

    return await get_hourly_spend(db, project.id, campaign_id, date=date)


@router.get("/campaigns/{campaign_id}/intraday")
async def get_campaign_intraday_endpoint(
    campaign_id: int,
    date: str | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Внутридневные показы/клики/расход по интервалам (снимки campaigns-stats).

    Данные копятся вперёд из кабинетной сессии (WB нативно почасовку не отдаёт). Пустой
    points[] = снимков за день ещё нет (сессия не задана/не набралось). date — сегодня (МСК).
    В ответе interval_min — текущая частота снимков проекта (для селектора на фронте)."""
    from backend.services.funnel.ads_manager import get_intraday_metrics
    from backend.services.settings_service import get_ads_snapshot_interval_min

    res = await get_intraday_metrics(db, project.id, campaign_id, date=date)
    if "error" not in res:
        res["interval_min"] = await get_ads_snapshot_interval_min(db, project.id)
    return res


class SnapshotIntervalRequest(BaseModel):
    interval_min: int  # 10 / 20 / 30 / 60 — частота снимков внутридневной статы (проект-глобально)


@router.put("/ads/snapshot-interval", dependencies=[Depends(rate_limit_write)])
async def set_ads_snapshot_interval_endpoint(
    body: SnapshotIntervalRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Задать частоту внутридневных снимков рекламы (проект-глобально): 10/20/30/60 мин."""
    from backend.services.settings_service import set_ads_snapshot_interval_min

    try:
        m = await set_ads_snapshot_interval_min(db, project.id, body.interval_min)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return {"interval_min": m}


class CollectPositionsRequest(BaseModel):
    nm_id: int
    phrases: list[str]
    max_pages: int = 5  # глубина сбора: сколько страниц выдачи (×100 позиций) проверять


@router.post("/positions/collect", dependencies=[Depends(rate_limit_write)])
async def collect_positions_endpoint(
    body: CollectPositionsRequest,
    project: Project = Depends(get_current_project),
):
    """Запустить фоновый сбор органических позиций товара по фразам (кластеризатор, кнопка Play).
    Идемпотентно: если сбор для (nm_id) уже идёт — вернёт текущий прогресс. Коммит порционный."""
    from backend.services.funnel.search_positions import start_collection

    return start_collection(project.id, body.nm_id, body.phrases, body.max_pages)


class CollectOneRequest(BaseModel):
    nm_id: int
    phrase: str
    max_pages: int = 5


@router.post("/positions/collect-one", dependencies=[Depends(rate_limit_write)])
async def collect_position_one_endpoint(
    body: CollectOneRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Синхронно собрать позицию ОДНОЙ фразы (кнопка-кругляшок в ячейке). Сразу отдаёт результат.
    throttled=true → WB ограничил («слишком частый запрос»)."""
    from backend.services.funnel.search_positions import collect_one

    return await collect_one(db, project.id, body.nm_id, body.phrase, body.max_pages)


@router.post("/positions/stop", dependencies=[Depends(rate_limit_write)])
async def stop_positions_endpoint(
    nm_id: int = Query(...),
    project: Project = Depends(get_current_project),
):
    """Остановить фоновый сбор позиций (кнопка Stop). Частичный результат сохраняется."""
    from backend.services.funnel.search_positions import stop_collection

    return stop_collection(project.id, nm_id)


@router.get("/positions/progress")
async def positions_progress_endpoint(
    nm_id: int = Query(...),
    project: Project = Depends(get_current_project),
):
    """Прогресс фонового сбора позиций для nm_id."""
    from backend.services.funnel.search_positions import get_progress

    return get_progress(project.id, nm_id)


@router.get("/positions/history")
async def positions_history_endpoint(
    nm_id: int = Query(...),
    phrase: str = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История позиций одной фразы (для спарклайна)."""
    from backend.services.funnel.search_positions import get_position_history

    return {"history": await get_position_history(db, project.id, nm_id, phrase)}


@router.get("/positions")
async def positions_map_endpoint(
    nm_id: int = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Карта позиций товара по фразам: {phrase: {position, prev, depth, at}} (последний+предыдущий снимок)."""
    from backend.services.funnel.search_positions import get_positions_map

    return {"nm_id": nm_id, "positions": await get_positions_map(db, project.id, nm_id)}


class DepositRequest(BaseModel):
    amount: int  # ₽, минимум 1000 (ограничение WB)
    source: int = 0  # 0 = счёт кабинета Продвижения, иные — баланс/бонусы


@router.post("/campaigns/{campaign_id}/deposit", dependencies=[Depends(rate_limit_write)])
async def deposit_campaign_budget_endpoint(
    campaign_id: int,
    body: DepositRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ручное пополнение бюджета кампании (реальные деньги!). Инициируется пользователем."""
    from backend.services.funnel.ads_manager import deposit_campaign_budget_manual

    result = await deposit_campaign_budget_manual(db, project.id, campaign_id, body.amount, body.source)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "Не удалось пополнить бюджет")
    return result


@router.get("/campaigns/{campaign_id}/zones")
async def get_campaign_zones_ep(
    campaign_id: int,
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
    nm_id: int | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Зоны показов кампании (поиск/рекомендации), ставки по зонам и правила ставки.

    Для CPC (зона одна) отдаёт ещё и её статистику за период.
    """
    from backend.services.funnel.ads_manager import get_campaign_zones

    return await get_campaign_zones(db, project.id, campaign_id, date_from, date_to, nm_id)


class CampaignZonesRequest(BaseModel):
    search: bool
    recommendations: bool


@router.put("/campaigns/{campaign_id}/zones", dependencies=[Depends(rate_limit_write)])
async def set_campaign_zones_ep(
    campaign_id: int,
    body: CampaignZonesRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Включить/выключить зоны показов в WB (только CPM с ручной ставкой).

    Передаются обе зоны разом — WB заменяет набор целиком.
    """
    from backend.services.funnel.ads_manager import set_campaign_zones

    result = await set_campaign_zones(
        db, project.id, campaign_id, {"search": body.search, "recommendations": body.recommendations},
    )
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось изменить зоны показов")
    return result


class ZoneBidRequest(BaseModel):
    zone: str  # search | recommendations
    bid: float  # ₽


@router.put("/campaigns/{campaign_id}/zone-bid", dependencies=[Depends(rate_limit_write)])
async def set_campaign_zone_bid_ep(
    campaign_id: int,
    body: ZoneBidRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сменить ставку кампании для зоны (Поиск/Рекомендации). Реальные деньги.
    Единая ставка → обе зоны; ручная → выбранная зона; CPC → поиск. Только статусы 4/9/11."""
    from backend.services.funnel.ads_manager import set_campaign_zone_bid

    result = await set_campaign_zone_bid(db, project.id, campaign_id, body.zone, body.bid)
    if not result["ok"]:
        raise HTTPException(400, result.get("error") or "Не удалось изменить ставку")
    return result


class CampaignBidRequest(BaseModel):
    bid: float  # ₽ — ставка по активной зоне (для инлайн-правки в списке)


@router.put("/campaigns/{campaign_id}/bid", dependencies=[Depends(rate_limit_write)])
async def set_campaign_bid_ep(
    campaign_id: int,
    body: CampaignBidRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сменить ставку кампании из СПИСКА (инлайн). Реальные деньги. Только статусы 4/9/11.
    Возвращает результат С кодом 200 (в т.ч. min_bid при отказе по аукционному минимуму),
    чтобы фронт показал понятную подсказку и кнопку «поставить минимум» — HTTPException
    съел бы структурный min_bid."""
    from backend.services.funnel.ads_manager import set_campaign_default_bid

    return await set_campaign_default_bid(db, project.id, campaign_id, body.bid)


class AdNmBackfillRequest(BaseModel):
    date_from: str | None = None  # ISO; по умолчанию — от создания старейшей кампании
    date_to: str | None = None


@router.post("/ads/nm-backfill", dependencies=[Depends(rate_limit_write)])
async def start_ad_nm_backfill(
    body: AdNmBackfillRequest | None = None,
    project: Project = Depends(get_current_project),
):
    """Фоновый сбор истории РК-статистики в разбивке по товарам (кампания × nmId × дата).

    Идёт окнами по 31 дню с паузами (rate limit WB). Копим у себя: WB историю не хранит вечно.
    """
    from datetime import date as _date

    from backend.services.funnel.ad_nm_stats import get_backfill_progress, run_ad_nm_backfill_bg

    if get_backfill_progress(project.id).get("status") == "running":
        return {"status": "already_running"}

    body = body or AdNmBackfillRequest()
    d_from = _date.fromisoformat(body.date_from) if body.date_from else None
    d_to = _date.fromisoformat(body.date_to) if body.date_to else None
    asyncio.create_task(run_ad_nm_backfill_bg(project.id, d_from, d_to))  # noqa: RUF006
    return {"status": "started"}


@router.get("/ads/nm-backfill/progress")
async def ad_nm_backfill_progress(project: Project = Depends(get_current_project)):
    """Статус фонового сбора разбивки по товарам."""
    from backend.services.funnel.ad_nm_stats import get_backfill_progress

    return get_backfill_progress(project.id)


@router.get("/campaigns/budget_gaps")
async def get_campaigns_budget_gaps(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Кампании, у которых сегодня кончился бюджет: час остановки + доливка до 00:00."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    return await get_budget_gaps(db, project.id)


@router.get("/campaigns/{campaign_id}/budget_gap_history")
async def get_campaign_budget_gap_history(
    campaign_id: int,
    days: int = 30,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """История недобора бюджета по дням для кампании: расход, час остановки, недобор к потенциалу."""
    from backend.services.funnel.ads_manager import get_budget_gap_history

    return await get_budget_gap_history(db, project.id, campaign_id, days)


class SyncCampaignsBody(BaseModel):
    """Приоритет догруза: кампании под текущим фильтром страницы тянутся первыми."""

    priority_campaign_ids: list[int] = Field(default_factory=list)


@router.post("/sync_campaigns", dependencies=[Depends(rate_limit_write)])
async def sync_campaigns(
    project: Project = Depends(get_current_project),
    body: SyncCampaignsBody | None = None,
):
    """Sync ad campaigns from WB (names, types, budgets). Runs in background."""
    from backend.services.funnel.ad_campaigns_service import get_sync_progress, sync_ad_campaigns_bg

    progress = get_sync_progress(project.id)
    if progress.get("status") in ("fetching_campaigns", "fetching_budgets", "saving"):
        return {"status": "already_running", **progress}

    priority_ids = body.priority_campaign_ids if body else []
    asyncio.create_task(sync_ad_campaigns_bg(project.id, priority_ids))  # noqa: RUF006
    return {"status": "started", "message": "Синхронизация запущена в фоне"}


@router.get("/sync_campaigns_progress")
async def sync_campaigns_progress(
    project: Project = Depends(get_current_project),
):
    """Get progress of background ad campaigns sync."""
    from backend.services.funnel.ad_campaigns_service import get_sync_progress

    return get_sync_progress(project.id)


@router.post("/sync_funnel_bg", dependencies=[Depends(rate_limit_write)])
async def sync_funnel_bg(
    date_from: str = Query(...),
    date_to: str = Query(...),
    project: Project = Depends(get_current_project),
):
    """Run full funnel sync in background with progress tracking."""
    from backend.services.funnel.sync import get_funnel_sync_progress, run_funnel_sync_bg

    progress = get_funnel_sync_progress(project.id)
    if progress.get("status") in ("fetching_ads", "fetching_funnel", "saving"):
        return {"status": "already_running", **progress}

    asyncio.create_task(run_funnel_sync_bg(project.id, date_from, date_to))  # noqa: RUF006
    return {"status": "started", "message": "Синхронизация воронки запущена"}


@router.get("/sync_funnel_progress")
async def sync_funnel_progress(
    project: Project = Depends(get_current_project),
):
    """Get progress of background funnel sync."""
    from backend.services.funnel.sync import get_funnel_sync_progress

    return get_funnel_sync_progress(project.id)


# ─── Unified Sync ────────────────────────────────────────────────────────────


@router.post("/unified_sync", dependencies=[Depends(rate_limit_write)])
async def unified_sync(
    date_from: str = Query(...),
    date_to: str = Query(...),
    project: Project = Depends(get_current_project),
):
    """Run unified ad sync: campaigns + budgets → funnel. Background task."""
    from backend.services.funnel.unified_sync import get_unified_sync_progress, run_unified_ad_sync_bg

    progress = get_unified_sync_progress(project.id)
    if progress.get("phase") in ("campaigns", "budgets", "funnel"):
        return {"status": "already_running", **progress}

    logger.info(f"Starting unified sync for project {project.id}: {date_from} → {date_to}")
    task = asyncio.create_task(run_unified_ad_sync_bg(project.id, date_from, date_to))
    task.add_done_callback(
        lambda t: logger.error(f"Unified sync task error: {t.exception()}") if t.exception() else None
    )
    return {"status": "started", "message": "Unified sync запущен"}


@router.get("/unified_sync_progress")
async def unified_sync_progress(
    project: Project = Depends(get_current_project),
):
    """Get progress of unified sync."""
    from backend.services.funnel.unified_sync import get_unified_sync_progress

    return get_unified_sync_progress(project.id)


@router.post("/first_sync", dependencies=[Depends(rate_limit_write)])
async def first_sync(
    project: Project = Depends(get_current_project),
):
    """Run first-time sync pipeline: nomenclature → campaigns → funnel 30d → backfill 60d."""
    from backend.services.funnel.unified_sync import get_first_sync_progress, run_first_sync_bg

    progress = get_first_sync_progress(project.id)
    if progress.get("phase") in ("nomenclature", "campaigns", "funnel", "backfill"):
        return {"status": "already_running", **progress}

    asyncio.create_task(run_first_sync_bg(project.id))  # noqa: RUF006
    return {"status": "started", "message": "Первичная синхронизация запущена"}


@router.get("/first_sync_progress")
async def first_sync_progress(
    project: Project = Depends(get_current_project),
):
    """Get progress of first sync pipeline."""
    from backend.services.funnel.unified_sync import get_first_sync_progress

    return get_first_sync_progress(project.id)


@router.get("/products")
async def get_funnel_products(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns a unique list of products seen in the funnel for tag assignment."""
    from sqlalchemy import func, select

    from backend.models.cost import Nomenclature
    from backend.models.integrations import WbFunnelDaily

    # Subquery: distinct products from funnel
    subq = (
        select(
            WbFunnelDaily.nm_id,
            func.max(WbFunnelDaily.brand).label("brand"),
            func.max(WbFunnelDaily.vendor_code).label("vendor_code"),
            func.max(WbFunnelDaily.subject).label("subject"),
        )
        .where(WbFunnelDaily.project_id == project.id)
        .group_by(WbFunnelDaily.nm_id)
        .subquery()
    )

    # Left join nomenclature to get imt_id
    result = await db.execute(
        select(
            subq.c.nm_id,
            subq.c.brand,
            subq.c.vendor_code,
            subq.c.subject,
            Nomenclature.imt_id,
        )
        .outerjoin(
            Nomenclature,
            (Nomenclature.article_wb == subq.c.nm_id) & (Nomenclature.project_id == project.id),
        )
        .order_by(subq.c.nm_id.desc())
    )
    products = []
    seen_nm = set()
    for r in result:
        if r.nm_id in seen_nm:
            continue
        seen_nm.add(r.nm_id)
        products.append(
            {
                "nm_id": r.nm_id,
                "brand": r.brand or "Другое",
                "vendor_code": r.vendor_code or str(r.nm_id),
                "subject": r.subject or "",
                "imt_id": r.imt_id,
            }
        )
    return {"products": products}


# ── Кластеризатор поисковых кампаний ──────────────────────────────────────────
class ClusterMinusRequest(BaseModel):
    nm_id: int
    norm_query: str
    action: str = "add"  # add | remove


@router.get("/campaigns/{campaign_id}/clusters")
async def get_campaign_clusters_ep(
    campaign_id: int,
    date_from: str = Query(...),
    date_to: str = Query(...),
    nm_id: int | None = Query(None, description="Считать кластеры только по этому товару кампании"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Поисковые кластеры кампании: статистика WB + классификация (голова/хвост/мусор),
    дневное распределение бюджета (%) и флаги минус-фраз."""
    from backend.services.funnel.cluster_analysis_service import get_campaign_clusters

    return await get_campaign_clusters(db, project.id, campaign_id, date_from, date_to, nm_id)


@router.post("/campaigns/{campaign_id}/clusters/minus", dependencies=[Depends(rate_limit_write)])
async def toggle_campaign_cluster_minus_ep(
    campaign_id: int,
    body: ClusterMinusRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Добавить/убрать кластер из минус-фраз кампании в кабинете WB (живое действие)."""
    from backend.services.funnel.cluster_analysis_service import toggle_cluster_minus

    return await toggle_cluster_minus(
        db, project.id, campaign_id, body.nm_id, body.norm_query, body.action
    )


@router.get("/categories")
async def list_ad_categories_ep(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Категории (subject) с рекламируемыми товарами — для селектора агрегации."""
    from backend.services.funnel.cluster_analysis_service import list_categories

    return await list_categories(db, project.id)


@router.get("/categories/clusters")
async def get_category_clusters_ep(
    subject: str = Query(...),
    date_from: str = Query(...),
    date_to: str = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """On-demand агрегация кластеров по всем CPM-поиск-кампаниям категории:
    средние/топ-запросы и разбивка по товарам (какие конвертят, какие нет)."""
    from backend.services.funnel.cluster_analysis_service import get_category_clusters

    return await get_category_clusters(db, project.id, subject, date_from, date_to)


class ProductMinusRequest(BaseModel):
    norm_query: str
    action: str = "add"  # add | remove


@router.get("/products/{nm_id}/clusters")
async def get_product_clusters_ep(
    nm_id: int,
    date_from: str = Query(...),
    date_to: str = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Поисковые кластеры одного артикула (nm_id) по всем его CPM-кампаниям —
    для drill-down страницы из категорийной аналитики."""
    from backend.services.funnel.cluster_analysis_service import get_product_clusters

    return await get_product_clusters(db, project.id, nm_id, date_from, date_to)


@router.post("/products/{nm_id}/clusters/minus", dependencies=[Depends(rate_limit_write)])
async def toggle_product_cluster_minus_ep(
    nm_id: int,
    body: ProductMinusRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Добавить/убрать кластер из минус-фраз по всем CPM-кампаниям артикула (живое действие WB)."""
    from backend.services.funnel.cluster_analysis_service import toggle_product_cluster_minus

    return await toggle_product_cluster_minus(db, project.id, nm_id, body.norm_query, body.action)


# «Паспорт» применения ставки для колонки «Стоит N дн»: с какого дня и от каких данных
# считали. Источник и точка отсчёта приходят с фронта (у него всё в строке таблицы).
class ClusterBidBasis(BaseModel):
    source: str | None = None       # recommendation | manual
    basis_drr: float | None = None  # ДРР фразы на момент применения, %
    basis_cpm: float | None = None  # оплаченная CPM на момент, ₽
    target_drr: float | None = None  # цель ДРР на момент


class ClusterBidRequest(ClusterBidBasis):
    nm_id: int
    norm_query: str
    bid: float
    # Перечитать ставку из WB после записи и вернуть verified. Массовое действие шлёт False
    # (не удваивать вызовы WB на каждую фразу) и перечитывает кластеры разом на фронте.
    verify: bool = True


class ClusterBidBulkItem(ClusterBidBasis):
    nm_id: int
    norm_query: str
    bid: float


class ClusterBidBulkRequest(BaseModel):
    items: list[ClusterBidBulkItem]


class ProductBidRequest(BaseModel):
    norm_query: str
    bid: float


@router.post("/campaigns/{campaign_id}/clusters/bid", dependencies=[Depends(rate_limit_write)])
async def set_campaign_cluster_bid_ep(
    campaign_id: int,
    body: ClusterBidRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ставка по кластеру кампании (живое действие WB; <100 показов WB не примет)."""
    from backend.services.funnel.cluster_analysis_service import set_cluster_bid

    return await set_cluster_bid(
        db, project.id, campaign_id, body.nm_id, body.norm_query, body.bid, verify=body.verify,
        source=body.source, basis_drr=body.basis_drr, basis_cpm=body.basis_cpm, target_drr=body.target_drr,
    )


@router.post("/campaigns/{campaign_id}/clusters/bid-bulk", dependencies=[Depends(rate_limit_write)])
async def set_campaign_cluster_bids_bulk_ep(
    campaign_id: int,
    body: ClusterBidBulkRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Массовая ставка по кластерам ОДНИМ запросом на пачку (WB normquery/bids батчевый —
    мгновенно, без rate-limit 429, в отличие от N поштучных вызовов)."""
    from backend.services.funnel.cluster_analysis_service import set_cluster_bids_bulk

    return await set_cluster_bids_bulk(db, project.id, campaign_id, [i.model_dump() for i in body.items])


@router.post("/products/{nm_id}/clusters/bid", dependencies=[Depends(rate_limit_write)])
async def set_product_cluster_bid_ep(
    nm_id: int,
    body: ProductBidRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ставка по кластеру артикула — по всем его CPM-кампаниям (живое действие WB)."""
    from backend.services.funnel.cluster_analysis_service import set_product_cluster_bid

    return await set_product_cluster_bid(db, project.id, nm_id, body.norm_query, body.bid)


@router.get("/products/{nm_id}/daily")
async def get_product_daily_ep(
    nm_id: int,
    date_from: str = Query(...),
    date_to: str = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Дневная статистика артикула (реклама + воронка продаж) — вкладка «По дням» карточки."""
    from backend.services.funnel.cluster_analysis_service import get_product_daily

    return await get_product_daily(db, project.id, nm_id, date_from, date_to)
