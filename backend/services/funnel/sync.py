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

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbFunnelDaily
from backend.services.funnel.wb_api_client import (
    fetch_ad_campaigns,
    fetch_ad_stats,
    fetch_funnel,
    fetch_funnel_history,
    get_wb_key,
)

logger = logging.getLogger("dds.funnel")


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
        return {"status": "error", "rows": 0, "days": 0, "errors": ["Пустой диапазон дат"]}

    # Use same cost sources as BDR (weighted average + overrides)
    from backend.services.bdr_loaders import load_avg_costs, load_cost_overrides

    cost_map: dict = {}
    try:
        cost_by_article = await load_avg_costs(db, pid)
        for art, cost in cost_by_article.items():
            cost_map[art] = Decimal(str(cost))
        cost_overrides = await load_cost_overrides(db, pid)
        for nm_id, cost in cost_overrides.items():
            cost_map[nm_id] = Decimal(str(cost))
    except Exception as e:
        logger.warning(f"Cost lookup failed: {e}")

    # Fetch ad campaigns
    campaign_ids = await fetch_ad_campaigns(adv_key, include_completed=include_completed_campaigns)

    # Fetch ad stats for the whole range
    ad_stats = {}
    if campaign_ids:
        ad_stats = await fetch_ad_stats(adv_key, campaign_ids, dates[0], dates[-1])

    # ── Try bulk history endpoint first (7 days/request, requires Джем) ────
    # Split into 7-day windows and fetch all funnel data upfront
    HISTORY_WINDOW = 7
    funnel_by_day: dict = {}  # {date_str: {nm_id: {...}}}
    use_history = True

    for i in range(0, len(dates), HISTORY_WINDOW):
        window = dates[i : i + HISTORY_WINDOW]
        w_from, w_to = window[0], window[-1]

        if use_history:
            history_data = await fetch_funnel_history(analytics_key, w_from, w_to)
            if history_data is None:
                # 402 — Джем not available, fall back to per-day for ALL remaining
                use_history = False
                logger.info("Falling back to per-day funnel fetch (no Джем)")
            else:
                funnel_by_day.update(history_data)
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
            except Exception as e:
                logger.error(f"Funnel fetch error {ds}: {e}")

    if use_history and funnel_by_day:
        logger.info(
            f"Funnel history mode: {len(funnel_by_day)} days fetched "
            f"in {(len(dates) + HISTORY_WINDOW - 1) // HISTORY_WINDOW} requests "
            f"(vs {len(dates)} requests per-day)"
        )

    # ── Upsert per day ────────────────────────────────────────────────────
    total_rows = 0
    errors = []
    for date_str in dates:
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
                cost = cost_map.get(nm_id) or cost_map.get(fd.get("vendor_code"))

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
                    "adv_views": ad.get("views", 0),
                    "adv_clicks": ad.get("clicks", 0),
                    "adv_sum": ad.get("sum", 0),
                    "cost_price": cost,
                }
                rows_to_upsert.append(row)

            if rows_to_upsert:
                stmt = pg_insert(WbFunnelDaily).values(rows_to_upsert)

                # Base fields to always update
                update_fields = {
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
                    "cost_price": stmt.excluded.cost_price,
                }

                # Only overwrite ad fields if we have REAL ad data for this day
                if has_ad_data:
                    update_fields["adv_views"] = stmt.excluded.adv_views
                    update_fields["adv_clicks"] = stmt.excluded.adv_clicks
                    update_fields["adv_sum"] = stmt.excluded.adv_sum

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

    return {"status": "ok", "rows": total_rows, "days": len(dates), "errors": errors[:5]}


# ─── Background tasks (re-exported from backfill.py) ────────────────────────

from backend.services.funnel.backfill import (  # noqa: F401
    batch_resync_ads,
    run_backfill_bg,
)
