"""Service for WB advertising campaigns — sync and query for Ads tab."""

import logging
from datetime import date as date_type, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any


def _parse_wb_created(value: Any) -> datetime | None:
    """WB createTime (ISO, часто с 'Z') → naive UTC datetime; при ошибке — None."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None

import pytz
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbAdCampaign, WbAdCampaignEvent, WbFunnelDaily
from backend.models.integrations import (
    WbAdCampaignDaily,
    WbAdCampaignSnapshot,
    WbWarehouseStock,
)
from backend.models.wb_finance import WbFinanceRow
from backend.services.funnel.wb_advertising_api import (
    fetch_ad_campaigns_detailed,
    fetch_campaign_budgets_batch,
)
from backend.services.funnel.wb_api_client import get_wb_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.funnel")

MSK = pytz.timezone("Europe/Moscow")

# Лимит времени на догрузку бюджетов (N+1 к WB, ~0.7с на кампанию). Строго МЕНЬШЕ
# AD_CAMPAIGNS_SYNC_TIMEOUT джобы: при равных лимитах внешний wait_for убивает корутину
# раньше, чем цикл успеет мягко остановиться и вернуть частичный результат — что и дало
# на проде 0% успешных синков с 2026-07-11 (кампаний доросло до 1319, цикл требует ~15 мин).
BUDGET_FETCH_TIME_BUDGET = 900  # 15 мин

# То же для лёгкого синка «только бюджеты» (sync_ad_budgets_only, лишь активные кампании):
# строго меньше AD_BUDGETS_SYNC_TIMEOUT. Здесь лимиты были ещё и перевёрнуты — внутренний
# дефолт 600с против внешних 300с, — так что мягкая остановка не срабатывала никогда.
BUDGET_ONLY_TIME_BUDGET = 420  # 7 мин

# In-memory progress tracker: {project_id: {status, campaigns_total, budgets_done, budgets_total, error}}
_sync_progress: dict[int, dict[str, Any]] = {}


def get_sync_progress(project_id: int) -> dict:
    """Get current sync progress for a project."""
    return _sync_progress.get(project_id, {"status": "idle"})


async def _sort_campaigns_by_spend(
    db: AsyncSession,
    project_id: int,
    campaign_ids: list[int],
    priority_ids: list[int] | None = None,
) -> list[int]:
    """Sort campaign IDs by spend in last 7 days (descending). High spenders first.

    Если задан ``priority_ids`` (кампании под текущим фильтром на странице) — они уходят
    в начало очереди на догруз бюджетов, а остальные следом; внутри каждой группы порядок
    по расходу сохраняется. Так пользователь первым получает свежие бюджеты по тому срезу,
    что видит на экране, даже если TIME_BUDGET обрежет хвост.
    """
    if not campaign_ids:
        return campaign_ids

    from datetime import timedelta

    seven_days_ago = utcnow().date() - timedelta(days=7)
    CD = WbAdCampaignDaily
    spend_q = (
        select(CD.campaign_id, func.coalesce(func.sum(CD.spend), Decimal("0")).label("total_spend"))
        .where(CD.project_id == project_id)
        .where(CD.campaign_id.in_(campaign_ids))
        .where(CD.date >= seven_days_ago)
        .group_by(CD.campaign_id)
    )
    rows = (await db.execute(spend_q)).all()
    spend_map: dict[int, Decimal] = {r.campaign_id: r.total_spend for r in rows}

    # Sort: campaigns with spend first (descending), then campaigns without spend
    by_spend = sorted(campaign_ids, key=lambda cid: float(spend_map.get(cid, Decimal("0"))), reverse=True)

    # Приоритет фильтра: стабильно вытягиваем отфильтрованные кампании вперёд, сохраняя
    # порядок по расходу внутри обеих групп.
    if priority_ids:
        prio_set = set(priority_ids)
        return [cid for cid in by_spend if cid in prio_set] + [cid for cid in by_spend if cid not in prio_set]

    return by_spend


async def sync_ad_campaigns(
    db: AsyncSession,
    project_id: int,
    priority_ids: list[int] | None = None,
    fetch_budgets: bool = True,
) -> dict:
    """Fetch campaign details + budgets from WB and upsert into wb_ad_campaigns.

    ``priority_ids`` — кампании под текущим фильтром страницы; их бюджеты тянутся первыми.

    ``fetch_budgets=False`` — режим планировщика: бюджеты НЕ трогаем, их владелец —
    sync_ad_budgets_only (активные кампании, каждые 10 мин). Раньше обе джобы тянули одни и
    те же активные кампании по rate-лимитированному /adv/v1/budget и дрались за него: на
    проде 2026-07-17 прогон бюджетов при наложении вырос 228с → 426с и срезался лимитом.
    Исключение — кампании, которых мы ещё не видели: у них нет «последнего известного»
    бюджета, и без разового запроса паузная новинка навсегда осталась бы с 0 (в
    sync_ad_budgets_only она не попадёт — там только статус 9).
    Ручной путь (кнопка «Синхронизировать» на странице рекламы) зовёт с дефолтным True:
    он редкий, запускается человеком и показывает прогресс «N/M бюджетов».
    """
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

    # Транзакцию, открытую чтениями выше, закрываем ДО походов в WB: дальше минуты HTTP,
    # а висящий idle in transaction выедает пул pgbouncer (см. .claude/rules/learnings.md).
    await db.commit()

    campaigns = await fetch_ad_campaigns_detailed(
        api_key,
        include_completed=True,
    )
    if not campaigns:
        _sync_progress[project_id] = {"status": "done", "campaigns_total": 0, "budgets_done": 0}
        return {"synced": 0}

    # Бюджет тянем только там, где он ещё может измениться: статус 7 («Завершена»)
    # необратим, остаток на такой кампании заморожен навсегда — на проекте 4 это 294
    # холостых запроса к WB из 1319. Строку в БД они не теряют: ниже сработает ветка
    # «budget is None → берём old_campaign.budget».
    from backend.services.funnel.ads_manager import CAMPAIGN_STATUS_COMPLETED

    campaign_ids = [
        c["advertId"]
        for c in campaigns
        if c.get("advertId") and c.get("status") != CAMPAIGN_STATUS_COMPLETED
    ]

    if fetch_budgets:
        # Prioritize campaigns by spend (last 7 days) — high spenders first for budget fetch.
        # Кампании под фильтром страницы (priority_ids) уходят в начало очереди.
        campaign_ids = await _sort_campaigns_by_spend(db, project_id, campaign_ids, priority_ids)
    else:
        # Режим планировщика: спрашиваем WB только про новинки (см. докстроку). Лёгкий
        # запрос ТОЛЬКО id — полную выборку для дифа берём ниже, уже ПОСЛЕ похода в WB,
        # иначе за минуты HTTP sync_ad_budgets_only обновит бюджеты, а мы затрём их
        # устаревшим снимком.
        known_ids = set(
            (
                await db.execute(
                    select(WbAdCampaign.campaign_id).where(WbAdCampaign.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
        campaign_ids = [cid for cid in campaign_ids if cid not in known_ids]

    await db.commit()  # чтения выше закрыли транзакцию → снова свободны перед WB

    _sync_progress[project_id] = {
        "status": "fetching_budgets",
        "campaigns_total": len(campaigns),
        "budgets_total": len(campaign_ids),
        "budgets_done": 0,
    }

    budgets = (
        await fetch_campaign_budgets_batch(
            api_key,
            campaign_ids,
            progress_cb=lambda done, total: _sync_progress.__setitem__(
                project_id,
                {**_sync_progress.get(project_id, {}), "budgets_done": done},
            ),
            time_budget=BUDGET_FETCH_TIME_BUDGET,
        )
        if campaign_ids
        else {}
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
                "advert_type": c.get("advert_type"),  # числовой тип WB (8=авто/рекоменд., 9=аукцион)
                "created_at": _parse_wb_created(c.get("create_time")),  # дата создания в WB
                "bid_mode": c.get("bid_mode"),  # unified/manual из WB bid_type (_parse_advert_item)
                "default_bid": c.get("default_bid"),  # ставка ₽ по активной зоне (из nm_settings.bids_kopecks)
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
                "advert_type": stmt.excluded.advert_type,
                # createTime от WB приоритетнее; если WB не прислал — сохраняем прежнее (в т.ч. бэкфилл)
                "created_at": func.coalesce(stmt.excluded.created_at, WbAdCampaign.created_at),
                "bid_mode": stmt.excluded.bid_mode,
                # ставку не затираем null'ом: WB иногда не отдаёт bids_kopecks — сохраняем прежнюю
                "default_bid": func.coalesce(stmt.excluded.default_bid, WbAdCampaign.default_bid),
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


async def sync_ad_campaigns_bg(project_id: int, priority_ids: list[int] | None = None) -> None:
    """Background wrapper — creates own DB session."""
    from backend.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await sync_ad_campaigns(db, project_id, priority_ids)
    except Exception as e:
        _sync_progress[project_id] = {"status": "error", "error": str(e)[:200]}
        logger.error(f"Ad campaigns bg sync failed for project {project_id}: {e}")


async def refresh_one_campaign(db: AsyncSession, project_id: int, campaign_id: int) -> dict:
    """Точечный догруз ОДНОЙ кампании из WB и синхронная запись в зеркало (по кнопке «Обновить»).

    Три узких запроса WB (~1–2с): деталь (имя/тип/статус/nm_ids/bid_mode), бюджет и свежая
    дневная стата за 2 дня (fullstats). Обновляет wb_ad_campaigns (+events на смену бюджета/
    статуса), wb_ad_campaign_daily и wb_ad_nm_daily. НЕ трогает весь кабинет.
    Возвращает {"ok": bool, "error": str | None}.
    """
    from backend.services.funnel.ad_nm_stats import upsert_ad_nm_daily
    from backend.services.funnel.wb_advertising_api import fetch_ad_stats, fetch_campaign_detail

    api_key = await get_wb_key(db, project_id, "wb_advert")
    if not api_key:
        api_key = await get_wb_key(db, project_id, "wb_analytics")
    if not api_key:
        api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        return {"ok": False, "error": "no_api_key"}

    detail = await fetch_campaign_detail(api_key, campaign_id)
    if not detail or not detail.get("advertId"):
        return {"ok": False, "error": "not_found"}

    budgets = await fetch_campaign_budgets_batch(api_key, [campaign_id])

    msk = pytz.timezone("Europe/Moscow")
    today = utcnow().astimezone(msk).date()
    yesterday = today - timedelta(days=1)
    stats = await fetch_ad_stats(api_key, [campaign_id], yesterday.isoformat(), today.isoformat())

    old = (
        await db.execute(
            select(WbAdCampaign)
            .where(WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id)
            .limit(1)
        )
    ).scalar_one_or_none()

    # WB не отдал бюджет (редко) → сохраняем прежний, а не обнуляем
    campaign_budget = budgets.get(campaign_id)
    if campaign_budget is None and old is not None:
        campaign_budget = old.budget

    now = utcnow()
    row = {
        "project_id": project_id,
        "campaign_id": campaign_id,
        "name": str(detail.get("name") or campaign_id),
        "campaign_type": detail.get("type"),
        "advert_type": detail.get("advert_type"),
        "created_at": _parse_wb_created(detail.get("create_time")),
        "bid_mode": detail.get("bid_mode"),
        "default_bid": detail.get("default_bid"),
        "status": detail.get("status") or (old.status if old else 9),
        "budget": campaign_budget or Decimal("0"),
        "nm_ids": detail.get("nm_ids") or (old.nm_ids if old else []),
        "updated_at": now,
    }

    # События изменения (как в sync_ad_campaigns): бюджет — только если WB реально отдал его
    events = []
    if old is not None:
        if campaign_id in budgets:
            ob, nb = float(old.budget or 0), float(campaign_budget or 0)
            if abs(ob - nb) >= 1:
                events.append(WbAdCampaignEvent(
                    project_id=project_id, campaign_id=campaign_id, event_type="budget_change",
                    old_value=str(round(ob, 2)), new_value=str(round(nb, 2))))
        if old.status != row["status"]:
            events.append(WbAdCampaignEvent(
                project_id=project_id, campaign_id=campaign_id, event_type="status_change",
                old_value=str(old.status), new_value=str(row["status"])))
    if events:
        db.add_all(events)

    stmt = pg_insert(WbAdCampaign).values([row])
    stmt = stmt.on_conflict_do_update(
        constraint="uq_ad_campaign_project",
        set_={
            "name": stmt.excluded.name,
            "campaign_type": stmt.excluded.campaign_type,
            "advert_type": stmt.excluded.advert_type,
            "created_at": func.coalesce(stmt.excluded.created_at, WbAdCampaign.created_at),
            "bid_mode": stmt.excluded.bid_mode,
            "default_bid": func.coalesce(stmt.excluded.default_bid, WbAdCampaign.default_bid),
            "status": stmt.excluded.status,
            "budget": stmt.excluded.budget,
            "nm_ids": stmt.excluded.nm_ids,
            "updated_at": stmt.excluded.updated_at,
        },
    )
    await db.execute(stmt)

    # Дневная стата за 2 дня (из того же fullstats-ответа — доп. запросов к WB нет)
    by_campaign = stats.get("_by_campaign") or {}
    camp_rows = []
    for date_str, camps in by_campaign.items():
        if date_str.startswith("_"):
            continue
        for cid, s in camps.items():
            camp_rows.append({
                "project_id": project_id,
                "campaign_id": cid,
                "date": date_type.fromisoformat(date_str),
                "views": s.get("views", 0),
                "clicks": s.get("clicks", 0),
                "spend": s.get("sum", 0),
            })
    if camp_rows:
        cstmt = pg_insert(WbAdCampaignDaily).values(camp_rows)
        cstmt = cstmt.on_conflict_do_update(
            constraint="uq_ad_campaign_daily",
            set_={"views": cstmt.excluded.views, "clicks": cstmt.excluded.clicks, "spend": cstmt.excluded.spend},
        )
        await db.execute(cstmt)

    await db.commit()

    # РК-статистика кампания×товар (та же форма, что фоновый синк) — best-effort
    try:
        await upsert_ad_nm_daily(db, project_id, stats.get("_by_nm_campaign") or {})
    except Exception as e:
        logger.error(f"refresh_one_campaign nm daily save error ({campaign_id}): {e}")

    return {"ok": True, "error": None}


async def sync_ad_budgets_only(db: AsyncSession, project_id: int) -> dict:
    """Fetch ONLY budgets for active campaigns (status=9). Lightweight sync for scheduler every 10 min.

    Detects budget changes and writes events.
    """
    api_key = await get_wb_key(db, project_id, "wb_advert")
    if not api_key:
        api_key = await get_wb_key(db, project_id, "wb_analytics")
    if not api_key:
        api_key = await get_wb_key(db, project_id, "wb")
    if not api_key:
        logger.warning(f"sync_ad_budgets_only: no WB key for project {project_id}")
        return {"updated": 0, "error": "no_api_key"}

    # Load active campaigns from DB
    active_q = select(WbAdCampaign).where(
        WbAdCampaign.project_id == project_id,
        WbAdCampaign.status == 9,
    )
    active_campaigns = (await db.execute(active_q)).scalars().all()
    if not active_campaigns:
        logger.info(f"sync_ad_budgets_only: no active campaigns for project {project_id}")
        return {"updated": 0}

    campaign_ids = [c.campaign_id for c in active_campaigns]
    existing = {c.campaign_id: c for c in active_campaigns}

    # Prioritize by spend
    campaign_ids = await _sort_campaigns_by_spend(db, project_id, campaign_ids)
    await db.commit()  # закрываем транзакцию перед минутами HTTP к WB

    budgets = await fetch_campaign_budgets_batch(
        api_key, campaign_ids, time_budget=BUDGET_ONLY_TIME_BUDGET
    )

    # Detect changes and write events
    now = utcnow()
    events = []
    updated_count = 0
    for cid, new_budget in budgets.items():
        old_campaign = existing.get(cid)
        if not old_campaign:
            continue

        old_budget = float(old_campaign.budget or 0)
        new_budget_f = float(new_budget or 0)

        if abs(old_budget - new_budget_f) >= 1:
            events.append(
                WbAdCampaignEvent(
                    project_id=project_id,
                    campaign_id=cid,
                    event_type="budget_change",
                    old_value=str(round(old_budget, 2)),
                    new_value=str(round(new_budget_f, 2)),
                )
            )

        # Update budget in DB
        old_campaign.budget = new_budget
        old_campaign.updated_at = now
        updated_count += 1

    if events:
        db.add_all(events)
        logger.info(f"sync_ad_budgets_only: {len(events)} budget changes for project {project_id}")

    await db.commit()
    logger.info(f"sync_ad_budgets_only: {updated_count}/{len(campaign_ids)} budgets updated for project {project_id}")
    return {"updated": updated_count, "events": len(events)}


async def snapshot_ad_intraday(db: AsyncSession, project_id: int) -> dict:
    """Снимок накопительных ВНУТРИДНЕВНЫХ счётчиков активных кампаний (показы/клики/расход).

    Зовётся планировщиком каждые 10 мин (per-project реже — гейт ads_snapshot_interval_min).
    Тянет «сегодняшний» (МСК) накопительный счётчик
    из кабинета рекламы (cmp campaigns-stats, сессия wb_portal_session) и пишет по строке
    WbAdCampaignSnapshot на кампанию. Дельта между снимками = интрадей-метрики
    (get_intraday_metrics). WB нативно почасовку не отдаёт — копим сами вперёд.

    Кабинетная сессия ≠ API-ключ: нет сессии → тихий skip (фича опциональна, синк не ломаем).
    Протухла сессия (401) → mark_wb_portal_expired, UI попросит обновить доступ WB.
    """
    from backend.integrations.wb_portal_client import WbPortalError, WbSessionExpired
    from backend.services.integrations_service import (
        get_wb_portal_client,
        mark_wb_portal_expired,
    )
    from backend.services.settings_service import get_ads_snapshot_interval_min

    try:
        client = await get_wb_portal_client(db, project_id)
    except ValueError as e:
        # ValueError здесь двузначен: сессии нет (норма, фича опциональна) ИЛИ ключ не
        # расшифровался (`_decrypt` → «Проверьте SECRET_KEY», уже поломка). Обе ветки
        # выглядят одинаково, поэтому отдаём текст наружу — он попадёт в сводку тика.
        return {"snapshots": 0, "skipped": "no_session", "detail": str(e)}

    # Гейт частоты: job тикает каждые 10 мин, но проект может хотеть реже (20/30/60).
    # Пропускаем, если с последнего снимка прошло меньше интервала (grace 5 мин на джиттер тика).
    interval_min = await get_ads_snapshot_interval_min(db, project_id)
    last_at = (
        await db.execute(
            select(func.max(WbAdCampaignSnapshot.captured_at)).where(
                WbAdCampaignSnapshot.project_id == project_id
            )
        )
    ).scalar()
    if last_at is not None and (utcnow() - last_at).total_seconds() / 60 < interval_min - 5:
        await client.aclose()
        return {"snapshots": 0, "skipped": "interval"}

    active = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id,
                WbAdCampaign.status == 9,
            )
        )
    ).scalars().all()
    if not active:
        await client.aclose()
        return {"snapshots": 0, "skipped": "no_active_campaigns"}
    campaign_ids = [c.campaign_id for c in active]

    today_msk = pytz.UTC.localize(utcnow()).astimezone(MSK).date()
    day_str = today_msk.isoformat()
    try:
        stats = await client.fetch_campaigns_stats(campaign_ids, day_str, day_str)
    except WbSessionExpired:
        await mark_wb_portal_expired(db, project_id)
        logger.warning(f"snapshot_ad_intraday: session expired for project {project_id}")
        return {"snapshots": 0, "error": "session_expired"}
    except WbPortalError as e:
        logger.warning(f"snapshot_ad_intraday: WB portal error for project {project_id}: {e}")
        return {"snapshots": 0, "error": "wb_error"}
    finally:
        await client.aclose()

    now = utcnow()
    rows = [
        WbAdCampaignSnapshot(
            project_id=project_id,
            campaign_id=s["campaign_id"],
            stat_date=today_msk,
            captured_at=now,
            views_cum=s["views"],
            clicks_cum=s["clicks"],
            spend_cum=Decimal(str(round(s["spend"], 2))),
        )
        for s in stats
    ]
    if rows:
        db.add_all(rows)
        await db.commit()
    logger.info(f"snapshot_ad_intraday: {len(rows)} snapshots for project {project_id}")
    return {"snapshots": len(rows)}


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
    campaigns_q = (
        select(WbAdCampaign)
        .where(
            WbAdCampaign.project_id == project_id,
        )
        .limit(5000)
    )
    campaigns = (await db.execute(campaigns_q)).scalars().all()

    # Load recent events (last 30 days)

    thirty_days_ago = utcnow() - timedelta(days=30)
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
        # Расход есть, заказов нет → ДРР бесконечен: отдаём None (JSON null), а не 0 —
        # иначе худшие товары выпадали из «Высокого ДРР» (фильтр drr > порога)
        drr = (adv_sum / orders_sum * 100) if orders_sum else (None if adv_sum > 0 else 0)

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
                "drr": round(drr, 2) if drr is not None else None,
                "bdr_revenue": round(bdr["revenue"], 2),
                "bdr_profit": round(bdr["profit"], 2),
                "stock_qty": stock_qty,
                "active_campaigns": sum(1 for c in product_campaigns if c.get("status") == 9),
                "campaigns": product_campaigns,
            }
        )

    # ABC classification
    _assign_abc(result, "bdr_revenue", "abc_revenue")
    _assign_abc(result, "bdr_profit", "abc_profit")

    return result


async def get_ad_tab_grouped(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    brand: str = "",
    subject: str = "",
    group_by: str = "brand",
) -> list[dict]:
    """Get ad tab data grouped by brand/subject/tag/imt with children (SKU rows)."""
    from collections import defaultdict

    from backend.models.cost import Nomenclature
    from backend.models.refs import ImtAlias, ProductTag, ProductTagMap

    # Reuse existing per-SKU data (includes campaigns, ABC, BDR, stock)
    sku_data = await get_ad_tab_data(db, project_id, date_from, date_to, brand, subject)

    # Build grouping key function
    nm_to_group: dict[int, list[str]] = {}

    if group_by == "brand":
        for item in sku_data:
            nm_to_group[item["nm_id"]] = [item.get("brand") or "Без бренда"]
    elif group_by == "subject":
        for item in sku_data:
            nm_to_group[item["nm_id"]] = [item.get("subject") or "Без категории"]
    elif group_by == "imt":
        # Load nm_id → imt_id mapping
        nom_result = await db.execute(
            select(Nomenclature.article_wb, Nomenclature.imt_id).where(
                Nomenclature.project_id == project_id,
                Nomenclature.imt_id.isnot(None),
            )
        )
        nm_to_imt: dict[int, int] = {}
        for article_wb, imt_id in nom_result:
            if article_wb and imt_id:
                nm_to_imt[article_wb] = imt_id

        # Load imt_id → alias
        alias_result = await db.execute(select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == project_id))
        imt_aliases: dict[int, str] = {r.imt_id: r.name for r in alias_result}

        for item in sku_data:
            imt_id = nm_to_imt.get(item["nm_id"])
            label = imt_aliases.get(imt_id, f"#{imt_id}") if imt_id else "Без склейки"
            nm_to_group[item["nm_id"]] = [label]
    elif group_by == "tag":
        # Load nm_id → tag names
        tag_result = await db.execute(
            select(ProductTagMap.nm_id, ProductTag.name)
            .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
            .where(ProductTagMap.project_id == project_id, ProductTag.is_deleted.is_(False))
        )
        nm_tags: dict[int, list[str]] = defaultdict(list)
        for nm_id, tag_name in tag_result:
            nm_tags[nm_id].append(tag_name)

        for item in sku_data:
            nm_to_group[item["nm_id"]] = nm_tags.get(item["nm_id"], ["Без ярлыка"])

    # Group items
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in sku_data:
        for grp_name in nm_to_group.get(item["nm_id"], ["—"]):
            groups[grp_name].append(item)

    # Aggregate per group
    result = []
    for grp_name, children in groups.items():
        views = sum(c["adv_views"] for c in children)
        clicks = sum(c["adv_clicks"] for c in children)
        adv_sum = sum(c["adv_sum"] for c in children)
        orders_sum = sum(c["orders_sum_rub"] for c in children)
        orders_count = sum(c["orders_count"] for c in children)
        bdr_revenue = sum(c.get("bdr_revenue", 0) for c in children)
        bdr_profit = sum(c.get("bdr_profit", 0) for c in children)
        stock_qty = sum(c.get("stock_qty", 0) for c in children)

        result.append(
            {
                "group_name": grp_name,
                "adv_views": views,
                "adv_clicks": clicks,
                "adv_sum": round(adv_sum, 2),
                "orders_sum_rub": round(orders_sum, 2),
                "orders_count": orders_count,
                "ctr": round((clicks / views * 100) if views else 0, 2),
                "cpc": round((adv_sum / clicks) if clicks else 0, 2),
                "cpm": round((adv_sum / views * 1000) if views else 0, 2),
                "drr": round(adv_sum / orders_sum * 100, 2) if orders_sum else (None if adv_sum > 0 else 0),
                "bdr_revenue": round(bdr_revenue, 2),
                "bdr_profit": round(bdr_profit, 2),
                "stock_qty": stock_qty,
                "product_count": len(children),
                "active_campaigns": sum(c.get("active_campaigns", 0) for c in children),
                "children": sorted(children, key=lambda x: x["adv_sum"], reverse=True),
            }
        )

    # ABC on grouped rows
    _assign_abc(result, "bdr_revenue", "abc_revenue")
    _assign_abc(result, "bdr_profit", "abc_profit")

    result.sort(key=lambda x: x["adv_sum"], reverse=True)
    return result
