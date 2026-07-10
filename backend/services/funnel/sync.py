"""
Funnel sync — core data synchronization logic.

Handles:
- Daily funnel sync (fetch from WB + upsert into DB)
- Background backfill for missing days
- Batch ad data re-sync
"""

import asyncio
import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func as sa_func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.models.integrations import WbAdCampaignDaily
from backend.services.funnel.ad_nm_stats import upsert_ad_nm_daily
from backend.services.funnel.wb_api_client import (
    fetch_ad_campaigns,
    fetch_ad_stats,
    fetch_funnel,
    fetch_funnel_history,
    get_wb_key,
)

logger = logging.getLogger("dds.funnel")

# In-memory progress tracker for funnel sync
from typing import Any

_funnel_sync_progress: dict[int, dict[str, Any]] = {}


def get_funnel_sync_progress(project_id: int) -> dict:
    """Get current funnel sync progress."""
    return _funnel_sync_progress.get(project_id, {"status": "idle"})


async def run_funnel_sync_bg(project_id: int, date_from: str, date_to: str) -> None:
    """Run funnel sync in background with progress tracking."""
    from backend.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await run_funnel_sync(db, project_id, date_from, date_to)
    except Exception as e:
        _funnel_sync_progress[project_id] = {"status": "error", "error": str(e)[:200]}
        logger.error(f"Funnel sync bg error: {e}")


async def run_funnel_sync(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    include_completed_campaigns: bool = False,
) -> dict:
    """
    Core funnel sync: fetch WB data for date range and upsert into DB.
    Callable from both the POST /sync endpoint and the background scheduler.
    Returns {status, rows, days, errors}.
    """
    pid = project_id

    # Get API keys
    analytics_key = await get_wb_key(db, pid, "wb_analytics")
    if not analytics_key:
        analytics_key = await get_wb_key(db, pid, "wb")
    if not analytics_key:
        return {"status": "error", "rows": 0, "days": 0, "errors": ["API ключ WB не найден"]}

    adv_key = await get_wb_key(db, pid, "wb_advert")
    if not adv_key:
        adv_key = analytics_key

    # Build date range
    d_from = date.fromisoformat(date_from)
    d_to = date.fromisoformat(date_to)
    dates = []
    d = d_from
    while d <= d_to:
        dates.append(d.isoformat())
        d += timedelta(days=1)

    if not dates:
        _funnel_sync_progress[pid] = {"status": "idle"}
        return {"status": "error", "rows": 0, "days": 0, "errors": ["Пустой диапазон дат"]}

    _funnel_sync_progress[pid] = {
        "status": "fetching_ads",
        "days_total": len(dates),
        "days_done": 0,
    }

    # Use same cost sources as BDR. lifetime_avg: weighted average + overrides
    # (legacy path, kept identical). fifo/moving_avg: per-unit as-of cost from the
    # valuation engine (eff_now[method]), keyed by lowered article_seller (= vendor_code
    # lowercased); overrides still seed nm_id keys as a fallback.
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides
    from backend.services.cost.valuation import (
        DEFAULT_METHOD,
        compute_project_valuation,
    )
    from backend.services.settings_service import get_valuation_method

    cost_map: dict = {}
    try:
        method = await get_valuation_method(db, pid)
        if method == DEFAULT_METHOD:
            cost_by_article = await load_avg_costs(db, pid)
            for art, cost in cost_by_article.items():
                cost_map[art] = Decimal(str(cost))
        else:
            valuation = await compute_project_valuation(db, pid)
            for sku, v in valuation.items():
                eff = Decimal(str(v["eff_now"][method]))
                if eff > 0:
                    cost_map[sku] = eff
        cost_overrides = await load_cost_overrides(db, pid)
        for nm_id, cost in cost_overrides.items():
            cost_map.setdefault(nm_id, Decimal(str(cost)))
    except Exception as e:
        logger.warning(f"Cost lookup failed: {e}")

    # Fetch ad campaigns
    _funnel_sync_progress[pid]["detail"] = "Загрузка списка кампаний..."
    campaign_ids = await fetch_ad_campaigns(adv_key, include_completed=include_completed_campaigns)
    _funnel_sync_progress[pid]["detail"] = f"Найдено {len(campaign_ids)} кампаний, загрузка статистики..."

    # Fetch ad stats for the whole range
    ad_stats = {}
    if campaign_ids:
        ad_stats = await fetch_ad_stats(adv_key, campaign_ids, dates[0], dates[-1])

    _funnel_sync_progress[pid] = {
        "status": "fetching_funnel",
        "days_total": len(dates),
        "days_done": 0,
    }

    # ── Try bulk history endpoint first (5 days/request, requires Джем) ────
    # WB limits history to recent dates; older dates fall back to per-day
    HISTORY_WINDOW = 5
    funnel_by_day: dict = {}  # {date_str: {nm_id: {...}}}
    use_history = True

    for i in range(0, len(dates), HISTORY_WINDOW):
        window = dates[i : i + HISTORY_WINDOW]
        w_from, w_to = window[0], window[-1]

        if use_history:
            history_data = await fetch_funnel_history(analytics_key, w_from, w_to)
            if history_data is None:
                # 402/400 — Джем not available or date too old, fall back to per-day
                use_history = False
                logger.info("Falling back to per-day funnel fetch (Джем unavailable or date limit exceeded)")
            else:
                funnel_by_day.update(history_data)
                _funnel_sync_progress[pid]["days_done"] = len(funnel_by_day)
                logger.info(f"History batch {w_from}→{w_to}: {len(history_data)} days")
                if i + HISTORY_WINDOW < len(dates):
                    await asyncio.sleep(1)
                continue

        # Fallback: fetch per day
        for idx, ds in enumerate(window):
            if idx > 0:
                await asyncio.sleep(1)
            try:
                day_data = await fetch_funnel(analytics_key, ds)
                if day_data:
                    funnel_by_day[ds] = day_data
                _funnel_sync_progress[pid]["days_done"] = len(funnel_by_day)
            except Exception as e:
                logger.error(f"Funnel fetch error {ds}: {e}")

    if use_history and funnel_by_day:
        logger.info(
            f"Funnel history mode: {len(funnel_by_day)} days fetched "
            f"in {(len(dates) + HISTORY_WINDOW - 1) // HISTORY_WINDOW} requests "
            f"(vs {len(dates)} requests per-day)"
        )

    _funnel_sync_progress[pid] = {
        "status": "saving",
        "days_total": len(dates),
        "days_done": 0,
    }

    # ── Upsert per day ────────────────────────────────────────────────────
    total_rows = 0
    errors = []
    for day_idx, date_str in enumerate(dates):
        funnel_data = funnel_by_day.get(date_str)
        if not funnel_data:
            continue

        try:
            # Check if we have real ad data for this day
            day_ads = ad_stats.get(date_str) or {}
            has_ad_data = bool(day_ads)

            rows_to_upsert = []
            for nm_id, fd in funnel_data.items():
                ad = day_ads.get(nm_id, {})
                # cost_map for string keys is lowercased (load_avg_costs); WB vendor_code may differ in case
                vc = (fd.get("vendor_code") or "").lower()
                unit_cost: float | None = cost_map.get(nm_id) or (cost_map.get(vc) if vc else None)

                row = {
                    "project_id": pid,
                    "date": date.fromisoformat(date_str),
                    "nm_id": nm_id,
                    "vendor_code": fd["vendor_code"],
                    "subject": fd["subject"],
                    "brand": fd["brand"],
                    "open_card": fd["open_card"],
                    "add_to_cart": fd["add_to_cart"],
                    "orders_count": fd["orders_count"],
                    "orders_sum_rub": fd["orders_sum_rub"],
                    "buyout_percent": fd["buyout_percent"],
                    "cart_to_order_pct": fd["cart_to_order_pct"],
                    "add_to_cart_pct": fd["add_to_cart_pct"],
                    "avg_price": fd.get("avg_price", 0),
                    "stocks_wb": fd.get("stocks_wb", 0),
                    "stocks_mp": fd.get("stocks_mp", 0),
                    "localization_percent": fd.get("localization_percent"),
                    "time_to_ready_minutes": fd.get("time_to_ready_minutes"),
                    "adv_views": ad.get("views", 0),
                    "adv_clicks": ad.get("clicks", 0),
                    "adv_sum": ad.get("sum", 0),
                    "cost_price": unit_cost,
                }
                rows_to_upsert.append(row)

            if rows_to_upsert:
                stmt = pg_insert(WbFunnelDaily).values(rows_to_upsert)

                # Base fields to always update.
                # `Any` type — values are mix of `excluded.<col>` (KeyedColumnElement)
                # and `func.greatest(...)` (FunctionElement), both valid for set_= in
                # on_conflict_do_update but mypy needs the union widened.
                update_fields: dict[str, Any] = {
                    "vendor_code": stmt.excluded.vendor_code,
                    "subject": stmt.excluded.subject,
                    "brand": stmt.excluded.brand,
                    "open_card": stmt.excluded.open_card,
                    "add_to_cart": stmt.excluded.add_to_cart,
                    "orders_count": stmt.excluded.orders_count,
                    "orders_sum_rub": stmt.excluded.orders_sum_rub,
                    "buyout_percent": stmt.excluded.buyout_percent,
                    "cart_to_order_pct": stmt.excluded.cart_to_order_pct,
                    "add_to_cart_pct": stmt.excluded.add_to_cart_pct,
                    "avg_price": stmt.excluded.avg_price,
                    "stocks_wb": stmt.excluded.stocks_wb,
                    "stocks_mp": stmt.excluded.stocks_mp,
                    "localization_percent": stmt.excluded.localization_percent,
                    "time_to_ready_minutes": stmt.excluded.time_to_ready_minutes,
                    "cost_price": stmt.excluded.cost_price,
                }

                # Ad fields: GREATEST guarantees we never overwrite a real value with 0.
                # Why: fetch_ad_stats can return PARTIAL data for a day (some chunks 429/SKIPPED).
                # Then has_ad_data=True (day_ads non-empty) but for many nm_ids ad={} → excluded=0.
                # Without GREATEST the upsert would zero out previously-synced real values.
                # Trade-off: if a campaign legitimately drops to 0 we keep the higher prior value
                # (acceptable — better stale-overstate than silent zero-out for ad spend dashboards).
                if has_ad_data:
                    update_fields["adv_views"] = sa_func.greatest(stmt.excluded.adv_views, WbFunnelDaily.adv_views)
                    update_fields["adv_clicks"] = sa_func.greatest(stmt.excluded.adv_clicks, WbFunnelDaily.adv_clicks)
                    update_fields["adv_sum"] = sa_func.greatest(stmt.excluded.adv_sum, WbFunnelDaily.adv_sum)

                stmt = stmt.on_conflict_do_update(
                    constraint="uq_funnel_daily",
                    set_=update_fields,
                )
                await db.execute(stmt)
                await db.commit()
                total_rows += len(rows_to_upsert)
                logger.info(
                    f"Funnel synced {date_str}: {len(rows_to_upsert)} rows"
                    f"{' (with ads)' if has_ad_data else ' (no ad data, preserved existing)'}"
                )
        except Exception as e:
            logger.error(f"Funnel sync error for {date_str}: {e}")
            errors.append(str(e))
            try:
                await db.rollback()
            except Exception:
                pass

        _funnel_sync_progress[pid] = {
            "status": "saving",
            "days_total": len(dates),
            "days_done": day_idx + 1,
        }

    # ── Upsert per-campaign daily stats ─────────────────────────────────────
    by_campaign = ad_stats.get("_by_campaign") or {}
    if by_campaign:
        try:
            camp_rows = []
            for date_str, camps in by_campaign.items():
                for camp_id, stats in camps.items():
                    camp_rows.append(
                        {
                            "project_id": pid,
                            "campaign_id": camp_id,
                            "date": date.fromisoformat(date_str),
                            "views": stats.get("views", 0),
                            "clicks": stats.get("clicks", 0),
                            "spend": stats.get("sum", 0),
                        }
                    )
            if camp_rows:
                BATCH = 1000
                for i in range(0, len(camp_rows), BATCH):
                    batch = camp_rows[i : i + BATCH]
                    stmt = pg_insert(WbAdCampaignDaily).values(batch)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_ad_campaign_daily",
                        set_={
                            "views": stmt.excluded.views,
                            "clicks": stmt.excluded.clicks,
                            "spend": stmt.excluded.spend,
                        },
                    )
                    await db.execute(stmt)
                await db.commit()
                logger.info(f"Campaign daily stats upserted: {len(camp_rows)} rows")
        except Exception as e:
            logger.error(f"Campaign daily stats save error: {e}")
            try:
                await db.rollback()
            except Exception:
                pass

    # ── Upsert РК-статистики в разбивке кампания × товар ────────────────────
    # Данные уже есть в том же ответе fullstats — лишних запросов к WB нет.
    by_nm_campaign = ad_stats.get("_by_nm_campaign") or {}
    if by_nm_campaign:
        try:
            await upsert_ad_nm_daily(db, pid, by_nm_campaign)
        except Exception as e:
            logger.error(f"Ad nm daily save error: {e}")
            try:
                await db.rollback()
            except Exception:
                pass

    _funnel_sync_progress[pid] = {
        "status": "done",
        "days_total": len(dates),
        "days_done": len(dates),
        "rows": total_rows,
    }

    return {"status": "ok", "rows": total_rows, "days": len(dates), "errors": errors[:5]}


# ─── Background tasks (re-exported from backfill.py) ────────────────────────

from backend.services.funnel.backfill import (  # noqa: F401
    batch_resync_ads,
    run_backfill_bg,
)


async def get_recent_sync_logs(db: AsyncSession, project_id: int, limit: int = 10) -> list[dict]:
    """Get recent wb_funnel sync log entries for a project."""
    from sqlalchemy import select

    from backend.models.integrations import IntegrationKey, SyncLog

    key_ids = select(IntegrationKey.id).where(
        IntegrationKey.project_id == project_id,
        IntegrationKey.is_deleted == False,  # noqa: E712
    )
    result = await db.execute(
        select(SyncLog)
        .where(
            SyncLog.service == "wb_funnel",
            SyncLog.integration_id.in_(key_ids),
        )
        .order_by(SyncLog.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": s.id,
            "sync_type": s.sync_type,
            "status": s.status,
            "rows_inserted": s.rows_inserted,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "error_msg": s.error_msg,
        }
        for s in result.scalars()
    ]
