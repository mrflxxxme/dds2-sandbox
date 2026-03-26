"""Service for WB advertising campaigns — sync and query for Ads tab."""

import logging
from datetime import date as date_type
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbAdCampaign, WbAdCampaignEvent, WbFunnelDaily
from backend.models.integrations import WbAdCampaignDaily, WbWarehouseStock
from backend.models.wb_finance import WbFinanceRow
from backend.services.funnel.wb_advertising_api import (
    fetch_ad_campaigns_detailed,
    fetch_campaign_budgets_batch,
)
from backend.services.funnel.wb_api_client import get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.funnel")

# In-memory progress tracker: {project_id: {status, campaigns_total, budgets_done, budgets_total, error}}
_sync_progress: dict[int, dict[str, Any]] = {}


def get_sync_progress(project_id: int) -> dict:
    """Get current sync progress for a project."""
    return _sync_progress.get(project_id, {"status": "idle"})


async def sync_ad_campaigns(db: AsyncSession, project_id: int) -> dict:
    """Fetch campaign details + budgets from WB and upsert into wb_ad_campaigns."""
    _sync_progress[project_id] = {"status": "fetching_campaigns"}

    api_key = await get_wb_key(db, project_id, "wb_advert")
    if not api_key:
        api_key = await get_wb_key(db, project_id, "wb_analytics")
    if not api_key:
        api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        logger.warning(f"No WB key for project {project_id}")
        _sync_progress[project_id] = {"status": "error", "error": "no_api_key"}
        return {"synced": 0, "error": "no_api_key"}

    campaigns = await fetch_ad_campaigns_detailed(
        api_key,
        include_completed=True,
    )
    if not campaigns:
        _sync_progress[project_id] = {"status": "done", "campaigns_total": 0, "budgets_done": 0}
        return {"synced": 0}

    campaign_ids = [c["advertId"] for c in campaigns if c.get("advertId")]
    _sync_progress[project_id] = {
        "status": "fetching_budgets",
        "campaigns_total": len(campaigns),
        "budgets_total": len(campaign_ids),
        "budgets_done": 0,
    }

    budgets = await fetch_campaign_budgets_batch(
        api_key,
        campaign_ids,
        progress_cb=lambda done, total: _sync_progress.__setitem__(
            project_id,
            {**_sync_progress.get(project_id, {}), "budgets_done": done},
        ),
    )

    _sync_progress[project_id] = {
        **_sync_progress.get(project_id, {}),
        "status": "saving",
    }

    # Load current campaigns for change detection and budget preservation
    existing_q = select(WbAdCampaign).where(WbAdCampaign.project_id == project_id)
    existing = {c.campaign_id: c for c in (await db.execute(existing_q)).scalars().all()}

    now = utcnow()
    rows = []
    for c in campaigns:
        cid = c.get("advertId")
        if not cid:
            continue
        # Keep old budget if this campaign's budget wasn't fetched (time budget exceeded)
        old_campaign = existing.get(cid)
        campaign_budget = budgets.get(cid)
        if campaign_budget is None and old_campaign:
            campaign_budget = old_campaign.budget
        rows.append(
            {
                "project_id": project_id,
                "campaign_id": cid,
                "name": str(c.get("name") or cid),
                "campaign_type": c.get("type"),
                "status": c.get("status") or 9,
                "budget": campaign_budget or Decimal("0"),
                "nm_ids": c.get("nm_ids") or [],
                "updated_at": now,
            }
        )

    # Detect changes and write events
    # Set of campaign IDs where budget was actually fetched from WB
    budgets_fetched_ids = set(budgets.keys())

    events = []
    for row in rows:
        cid = row["campaign_id"]
        old = existing.get(cid)
        if not old:
            continue  # new campaign, no history yet

        # Budget change — only if budget was actually fetched (not defaulted to 0)
        if cid in budgets_fetched_ids:
            old_budget = float(old.budget or 0)
            new_budget = float(row["budget"] or 0)
            if abs(old_budget - new_budget) >= 1:
                events.append(
                    WbAdCampaignEvent(
                        project_id=project_id,
                        campaign_id=cid,
                        event_type="budget_change",
                        old_value=str(round(old_budget, 2)),
                        new_value=str(round(new_budget, 2)),
                    )
                )

        # Status change
        old_status = old.status
        new_status = row["status"]
        if old_status != new_status:
            events.append(
                WbAdCampaignEvent(
                    project_id=project_id,
                    campaign_id=cid,
                    event_type="status_change",
                    old_value=str(old_status),
                    new_value=str(new_status),
                )
            )

    if events:
        db.add_all(events)
        logger.info(f"Ad campaign events: {len(events)} changes for project {project_id}")

    # Upsert campaigns
    if rows:
        stmt = pg_insert(WbAdCampaign).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ad_campaign_project",
            set_={
                "name": stmt.excluded.name,
                "campaign_type": stmt.excluded.campaign_type,
                "status": stmt.excluded.status,
                "budget": stmt.excluded.budget,
                "nm_ids": stmt.excluded.nm_ids,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await db.execute(stmt)
        await db.commit()

    _sync_progress[project_id] = {
        "status": "done",
        "campaigns_total": len(rows),
        "budgets_done": len(budgets),
        "budgets_total": len(campaign_ids),
    }
    logger.info(f"Ad campaigns synced: {len(rows)} for project {project_id}")
    return {"synced": len(rows)}


async def sync_ad_campaigns_bg(project_id: int) -> None:
    """Background wrapper — creates own DB session."""
    from backend.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await sync_ad_campaigns(db, project_id)
    except Exception as e:
        _sync_progress[project_id] = {"status": "error", "error": str(e)[:200]}
        logger.error(f"Ad campaigns bg sync failed for project {project_id}: {e}")


def _assign_abc(items: list[dict], value_key: str, abc_key: str) -> None:
    """Assign ABC category based on cumulative share (A=80%, B=95%, C=rest)."""
    total = sum(max(item.get(value_key, 0), 0) for item in items)
    if total <= 0:
        for item in items:
            item[abc_key] = "C"
        return

    sorted_items = sorted(items, key=lambda x: x.get(value_key, 0), reverse=True)
    cumulative = 0.0
    for item in sorted_items:
        val = max(item.get(value_key, 0), 0)
        cumulative += val
        share = cumulative / total
        if share <= 0.80:
            item[abc_key] = "A"
        elif share <= 0.95:
            item[abc_key] = "B"
        else:
            item[abc_key] = "C"


async def get_ad_tab_data(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    brand: str = "",
    subject: str = "",
) -> list[dict]:
    """Get advertising data grouped by product with linked campaigns."""
    F = WbFunnelDaily

    query = (
        select(
            F.nm_id,
            func.max(F.vendor_code).label("vendor_code"),
            func.max(F.subject).label("subject"),
            func.max(F.brand).label("brand"),
            func.coalesce(func.sum(F.adv_views), 0).label("adv_views"),
            func.coalesce(func.sum(F.adv_clicks), 0).label("adv_clicks"),
            func.coalesce(func.sum(F.adv_sum), Decimal("0")).label("adv_sum"),
            func.coalesce(
                func.sum(F.orders_sum_rub),
                Decimal("0"),
            ).label("orders_sum_rub"),
            func.coalesce(func.sum(F.orders_count), 0).label("orders_count"),
        )
        .where(F.project_id == project_id)
        .where(F.date >= date_type.fromisoformat(date_from))
        .where(F.date <= date_type.fromisoformat(date_to))
    )

    if brand:
        query = query.where(F.brand == brand)
    if subject:
        query = query.where(F.subject == subject)

    query = query.group_by(F.nm_id).having(func.sum(F.adv_sum) > 0).order_by(func.sum(F.adv_sum).desc()).limit(500)

    rows = (await db.execute(query)).all()

    # Load all campaigns for project
    campaigns_q = select(WbAdCampaign).where(
        WbAdCampaign.project_id == project_id,
    )
    campaigns = (await db.execute(campaigns_q)).scalars().all()

    # Load recent events (last 30 days)

    thirty_days_ago = utcnow() - __import__("datetime").timedelta(days=30)
    events_q = (
        select(WbAdCampaignEvent)
        .where(WbAdCampaignEvent.project_id == project_id)
        .where(WbAdCampaignEvent.created_at >= thirty_days_ago)
        .order_by(WbAdCampaignEvent.created_at.desc())
        .limit(2000)
    )
    events_rows = (await db.execute(events_q)).scalars().all()

    # Load BDR data (revenue + profit per nm_id for period)
    R = WbFinanceRow
    bdr_q = (
        select(
            R.nm_id,
            func.coalesce(func.sum(R.retail_amount), Decimal("0")).label("revenue"),
            func.coalesce(func.sum(R.ppvz_for_pay), Decimal("0")).label("to_pay"),
            func.coalesce(func.sum(R.delivery_rub), Decimal("0")).label("logistics"),
            func.coalesce(func.sum(R.penalty), Decimal("0")).label("penalties"),
            func.coalesce(func.sum(R.storage_fee), Decimal("0")).label("storage"),
            func.coalesce(func.sum(R.acceptance), Decimal("0")).label("acceptance"),
        )
        .where(R.project_id == project_id)
        .where(R.rr_dt >= date_type.fromisoformat(date_from))
        .where(R.rr_dt <= date_type.fromisoformat(date_to))
        .group_by(R.nm_id)
    )
    bdr_rows = (await db.execute(bdr_q)).all()
    bdr_map: dict[int, dict] = {}
    for b in bdr_rows:
        revenue = float(b.revenue or 0)
        profit = float(
            (b.to_pay or 0) - (b.logistics or 0) - (b.penalties or 0) - (b.storage or 0) - (b.acceptance or 0)
        )
        bdr_map[b.nm_id] = {"revenue": revenue, "profit": profit}

    # Load current WB stock per nm_id (sum across all warehouses)
    S = WbWarehouseStock
    stock_q = (
        select(
            S.nm_id,
            func.coalesce(func.sum(S.quantity), 0).label("stock_qty"),
        )
        .where(S.project_id == project_id)
        .group_by(S.nm_id)
    )
    stock_rows = (await db.execute(stock_q)).all()
    stock_map: dict[int, int] = {s.nm_id: int(s.stock_qty) for s in stock_rows}

    # Build campaign_id → events mapping
    campaign_events: dict[int, list[dict]] = {}
    for e in events_rows:
        if e.campaign_id not in campaign_events:
            campaign_events[e.campaign_id] = []
        campaign_events[e.campaign_id].append(
            {
                "event_type": e.event_type,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
        )

    # Load per-campaign daily stats for the period
    CD = WbAdCampaignDaily
    camp_daily_q = (
        select(
            CD.campaign_id,
            func.coalesce(func.sum(CD.views), 0).label("views"),
            func.coalesce(func.sum(CD.clicks), 0).label("clicks"),
            func.coalesce(func.sum(CD.spend), Decimal("0")).label("spend"),
        )
        .where(CD.project_id == project_id)
        .where(CD.date >= date_type.fromisoformat(date_from))
        .where(CD.date <= date_type.fromisoformat(date_to))
        .group_by(CD.campaign_id)
    )
    camp_daily_rows = (await db.execute(camp_daily_q)).all()
    camp_stats: dict[int, dict] = {}
    for cd in camp_daily_rows:
        camp_stats[cd.campaign_id] = {
            "views": int(cd.views or 0),
            "clicks": int(cd.clicks or 0),
            "spend": float(cd.spend or 0),
        }

    # Build nm_id → campaigns mapping
    nm_campaigns: dict[int, list[dict]] = {}
    for c in campaigns:
        cs = camp_stats.get(c.campaign_id, {})
        c_views = cs.get("views", 0)
        c_clicks = cs.get("clicks", 0)
        c_spend = cs.get("spend", 0)
        for nm_id in c.nm_ids or []:
            if nm_id not in nm_campaigns:
                nm_campaigns[nm_id] = []
            nm_campaigns[nm_id].append(
                {
                    "campaign_id": c.campaign_id,
                    "name": c.name,
                    "campaign_type": c.campaign_type,
                    "status": c.status,
                    "budget": float(c.budget or 0),
                    "views": c_views,
                    "clicks": c_clicks,
                    "spend": round(c_spend, 2),
                    "ctr": round((c_clicks / c_views * 100) if c_views else 0, 2),
                    "cpc": round((c_spend / c_clicks) if c_clicks else 0, 2),
                    "cpm": round((c_spend / c_views * 1000) if c_views else 0, 2),
                    "events": campaign_events.get(c.campaign_id, [])[:10],
                }
            )

    result = []
    for r in rows:
        views = int(r.adv_views or 0)
        clicks = int(r.adv_clicks or 0)
        adv_sum = float(r.adv_sum or 0)
        orders_sum = float(r.orders_sum_rub or 0)

        ctr = (clicks / views * 100) if views else 0
        cpc = (adv_sum / clicks) if clicks else 0
        cpm = (adv_sum / views * 1000) if views else 0
        drr = (adv_sum / orders_sum * 100) if orders_sum else 0

        product_campaigns = nm_campaigns.get(r.nm_id, [])

        # BDR data for ABC
        bdr = bdr_map.get(r.nm_id, {"revenue": 0, "profit": 0})

        # Stock: текущие остатки
        stock_qty = stock_map.get(r.nm_id, 0)

        result.append(
            {
                "nm_id": r.nm_id,
                "vendor_code": r.vendor_code,
                "subject": r.subject,
                "brand": r.brand,
                "adv_views": views,
                "adv_clicks": clicks,
                "adv_sum": round(adv_sum, 2),
                "orders_sum_rub": round(orders_sum, 2),
                "orders_count": int(r.orders_count or 0),
                "ctr": round(ctr, 2),
                "cpc": round(cpc, 2),
                "cpm": round(cpm, 2),
                "drr": round(drr, 2),
                "bdr_revenue": round(bdr["revenue"], 2),
                "bdr_profit": round(bdr["profit"], 2),
                "stock_qty": stock_qty,
                "campaigns": product_campaigns,
            }
        )

    # ABC classification
    _assign_abc(result, "bdr_revenue", "abc_revenue")
    _assign_abc(result, "bdr_profit", "abc_profit")

    return result
