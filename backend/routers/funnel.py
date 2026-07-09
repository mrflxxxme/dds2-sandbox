# ruff: noqa: RUF002
"""
Router: /funnel — WB Sales funnel analytics (воронка продаж).
Thin HTTP layer — all business logic is in services/funnel/ package.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
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


class AutopaySettingRequest(BaseModel):
    campaign_id: int
    enabled: bool = False
    amount: float = 0
    hour: int = 9  # час пополнения, МСК
    threshold_pct: int = 50  # пополнять, только если открут за сутки ≥ порога


@router.get("/campaigns/autopay")
async def get_campaigns_autopay(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Настройки автопополнения по кампаниям (исполняет scheduler-джоба ads_autopay)."""
    from backend.services.funnel.ads_manager import get_autopay_settings

    return await get_autopay_settings(db, project.id)


@router.get("/campaigns/autopay/log")
async def get_campaigns_autopay_log(
    campaign_id: int | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Журнал автопополнений (новые первыми); campaign_id — фильтр по кампании."""
    from backend.services.funnel.ads_manager import get_autopay_log

    log = await get_autopay_log(db, project.id)
    if campaign_id is not None:
        log = [e for e in log if int(e.get("campaign_id") or 0) == campaign_id]
    return log


@router.post("/campaigns/autopay", dependencies=[Depends(rate_limit_write)])
async def set_campaigns_autopay(
    body: AutopaySettingRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сохранить настройку автопополнения; при включении — активировать кампанию.

    Возвращает {"settings": {...}, "activation": {ok,status,error} | None}.
    """
    from backend.services.funnel.ads_manager import save_autopay_and_maybe_activate

    return await save_autopay_and_maybe_activate(
        db, project.id, body.campaign_id,
        {"enabled": body.enabled, "amount": body.amount, "hour": body.hour, "threshold_pct": body.threshold_pct},
    )


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


@router.get("/campaigns/budget_gaps")
async def get_campaigns_budget_gaps(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Кампании, у которых сегодня кончился бюджет: час остановки + доливка до 00:00."""
    from backend.services.funnel.ads_manager import get_budget_gaps

    return await get_budget_gaps(db, project.id)


@router.post("/sync_campaigns", dependencies=[Depends(rate_limit_write)])
async def sync_campaigns(
    project: Project = Depends(get_current_project),
):
    """Sync ad campaigns from WB (names, types, budgets). Runs in background."""
    from backend.services.funnel.ad_campaigns_service import get_sync_progress, sync_ad_campaigns_bg

    progress = get_sync_progress(project.id)
    if progress.get("status") in ("fetching_campaigns", "fetching_budgets", "saving"):
        return {"status": "already_running", **progress}

    asyncio.create_task(sync_ad_campaigns_bg(project.id))  # noqa: RUF006
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
