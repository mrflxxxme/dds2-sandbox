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
    WbAdNmDaily,
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

# Страховочный потолок выборки товаров из воронки (одна строка на nm_id за период)
_FUNNEL_ROWS_CAP = 20000

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

# Момент последнего успешного синка кампаний — в project_settings, а НЕ в _sync_progress:
# словарь прогресса живёт в памяти процесса (умирает с рестартом и не виден из воркера,
# где крутится плановый синк), а на странице рекламы отметка нужна при обычной загрузке.
ADS_LAST_SYNC_KEY = "ads_last_sync_at"


def get_sync_progress(project_id: int) -> dict:
    """Get current sync progress for a project."""
    return _sync_progress.get(project_id, {"status": "idle"})


async def get_last_sync_at(db: AsyncSession, project_id: int) -> str | None:
    """ISO-время (UTC, с суффиксом Z) последнего успешного синка кампаний или None."""
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, ADS_LAST_SYNC_KEY)
    if not raw:
        return None
    return raw if raw.endswith("Z") or "+" in raw[10:] else f"{raw}Z"


async def _mark_synced(db: AsyncSession, project_id: int) -> None:
    """Записать отметку об успешном синке. Не роняем синк из-за неё."""
    from backend.services.settings_service import set_setting

    try:
        await set_setting(db, project_id, ADS_LAST_SYNC_KEY, utcnow().isoformat())
    except Exception as e:  # noqa: BLE001 — отметка вторична к самому синку
        # Откат обязателен: после провалившегося flush сессия остаётся в PendingRollback
        # и любой следующий запрос по ней падает уже не по своей вине.
        await db.rollback()
        logger.warning("Не удалось записать %s для проекта %s: %s", ADS_LAST_SYNC_KEY, project_id, e)


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
        await _mark_synced(db, project_id)

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
    Берёт «сегодняшний» (МСК) накопительный счётчик из уже синканной официальной статистики WB
    (таблица wb_ad_campaign_daily, наполняется из adv/v3/fullstats) и пишет по строке
    WbAdCampaignSnapshot на кампанию. Дельта между снимками = интрадей-метрики
    (get_intraday_metrics). WB нативно почасовку не отдаёт — копим сами вперёд.

    Ноль обращений к WB: читаем свою таблицу. Снимок пишем только когда накопительный счётчик
    изменился с прошлого раза (официальная статистика обновляется реже тика).
    """
    from backend.services.settings_service import get_ads_snapshot_interval_min

    # Источник — уже синканная официальная статистика WB: таблица wb_ad_campaign_daily,
    # которую sync_ad_campaigns наполняет из adv/v3/fullstats каждые 30 мин. За «сегодня»
    # там лежит накопительный итог кампании на момент последнего синка. Ходить в WB из этой
    # джобы НЕЛЬЗЯ: полный fullstats ловит 429 (~3 мин на проект) и дерётся за rate-лимит с
    # основным синком. Раньше источником была кабинетная сессия (wb_portal_session) — хрупкая
    # (на проде отвалилась молча, снимков ноль с 09.07) и на локалке мёртвая (sync-prod гасит
    # ключ). Теперь ноль обращений к WB, а клики — настоящие (кабинет восстанавливал из CTR).

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
        return {"snapshots": 0, "skipped": "no_active_campaigns"}
    campaign_ids = [c.campaign_id for c in active]

    today_msk = pytz.UTC.localize(utcnow()).astimezone(MSK).date()

    # Накопительный «сегодня» по кампаниям из уже синканной официальной статистики.
    daily = (
        await db.execute(
            select(WbAdCampaignDaily.campaign_id, WbAdCampaignDaily.views, WbAdCampaignDaily.clicks, WbAdCampaignDaily.spend)
            .where(
                WbAdCampaignDaily.project_id == project_id,
                WbAdCampaignDaily.date == today_msk,
                WbAdCampaignDaily.campaign_id.in_(campaign_ids),
            )
        )
    ).all()
    by_camp = {r.campaign_id: {"views": r.views or 0, "clicks": r.clicks or 0, "sum": float(r.spend or 0)} for r in daily}
    if not by_camp:
        # Синк рекламы ещё не наполнил сегодняшний день — не поломка, ждём его тика.
        return {"snapshots": 0, "skipped": "no_rows_from_wb"}

    # Прошлый накопительный счётчик по кампаниям (последний снимок дня): если WB с тех пор
    # ничего не обновил, снимок не пишем — иначе график зарастает нулевыми интервалами.
    # Официальный fullstats обновляется реже кабинета (~30–60 мин), тик — 10 мин.
    prev_rows = (
        await db.execute(
            select(WbAdCampaignSnapshot.campaign_id, WbAdCampaignSnapshot.views_cum, WbAdCampaignSnapshot.clicks_cum)
            .where(
                WbAdCampaignSnapshot.project_id == project_id,
                WbAdCampaignSnapshot.stat_date == today_msk,
                WbAdCampaignSnapshot.captured_at == last_at,
            )
        )
    ).all() if last_at is not None else []
    prev = {r.campaign_id: (r.views_cum or 0, r.clicks_cum or 0) for r in prev_rows}

    now = utcnow()
    rows = []
    changed = False
    for cid in campaign_ids:
        cs = by_camp.get(cid)
        if not cs:
            continue
        views, clicks = int(cs.get("views", 0)), int(cs.get("clicks", 0))
        spend = float(cs.get("sum", 0))
        if prev.get(cid, (0, 0)) != (views, clicks):
            changed = True
        rows.append(
            WbAdCampaignSnapshot(
                project_id=project_id,
                campaign_id=cid,
                stat_date=today_msk,
                captured_at=now,
                views_cum=views,
                clicks_cum=clicks,
                spend_cum=Decimal(str(round(spend, 2))),
            )
        )
    # Первый снимок дня (prev пуст) пишем всегда — это база отсчёта; дальше только при изменении.
    if rows and (not prev or changed):
        db.add_all(rows)
        await db.commit()
        logger.info(f"snapshot_ad_intraday: {len(rows)} snapshots for project {project_id}")
        return {"snapshots": len(rows)}
    return {"snapshots": 0, "skipped": "unchanged"}


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
    include_no_ads: bool = False,
    limit: int = 500,
) -> list[dict]:
    """Get advertising data grouped by product with linked campaigns.

    include_no_ads=True снимает отсечку «был расход» — товар без рекламы приезжает с нулями
    (нужно вкладке «Склейки»: склейка показывается целиком, а не только её рекламируемой частью).
    """
    F = WbFunnelDaily

    # Расход/показы/клики берём из РЕКЛАМНОЙ статистики WB (adv/v3/fullstats), а не из
    # рекламных колонок отчёта воронки: воронка их сильно недосчитывает. Замер на проде
    # за 1–20.07: воронка 5.0 млн ₽ против 11.5 млн ₽ у fullstats при фактических списаниях
    # 13.2 млн ₽ — у 485 товаров расхождение, у 37 в воронке ноль при реальном расходе.
    # Из воронки остаются ЗАКАЗЫ: там они по всем источникам трафика, а ДРР считается
    # от всей выручки товара, не только от рекламной атрибуции.
    ND = WbAdNmDaily
    nm_ad_q = (
        select(
            ND.nm_id,
            func.coalesce(func.sum(ND.views), 0).label("views"),
            func.coalesce(func.sum(ND.clicks), 0).label("clicks"),
            func.coalesce(func.sum(ND.spend), Decimal("0")).label("spend"),
        )
        .where(ND.project_id == project_id)
        .where(ND.date >= date_type.fromisoformat(date_from))
        .where(ND.date <= date_type.fromisoformat(date_to))
        .group_by(ND.nm_id)
    )
    nm_ad: dict[int, dict] = {
        r.nm_id: {"views": int(r.views or 0), "clicks": int(r.clicks or 0), "spend": float(r.spend or 0)}
        for r in (await db.execute(nm_ad_q)).all()
    }

    # Воронка задаёт состав строк и несёт паспорт товара + заказы. Товаров с расходом, но
    # без строки в воронке, не бывает (проверено на проде) — состав от этого не страдает.
    query = (
        select(
            F.nm_id,
            func.max(F.vendor_code).label("vendor_code"),
            func.max(F.subject).label("subject"),
            func.max(F.brand).label("brand"),
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

    # Одна строка на товар, поэтому выборка ограничена каталогом проекта; страховочный
    # потолок — от разрастания каталога, реальный limit применяется после сортировки
    query = query.group_by(F.nm_id).order_by(F.nm_id).limit(_FUNNEL_ROWS_CAP)

    rows = (await db.execute(query)).all()
    # Отсечка «была реклама» и сортировка по расходу переехали из SQL сюда: расход теперь
    # в другой таблице. nm_id вторичным ключом — иначе усечение лимитом недетерминированно
    if not include_no_ads:
        rows = [r for r in rows if nm_ad.get(r.nm_id, {}).get("spend", 0) > 0]
    rows = sorted(rows, key=lambda r: (-nm_ad.get(r.nm_id, {}).get("spend", 0), r.nm_id))[:limit]

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

    # Load current WB stock per nm_id (sum across all warehouses,
    # кроме 🔥-игнорируемых: фантом сгоревшего склада не считается остатком)
    from backend.services.settings_service import get_stock_ignored_set

    ignored = await get_stock_ignored_set(db, project_id)
    S = WbWarehouseStock
    stock_q = (
        select(
            S.nm_id,
            func.coalesce(func.sum(S.quantity), 0).label("stock_qty"),
        )
        .where(S.project_id == project_id)
        .group_by(S.nm_id)
    )
    if ignored:
        stock_q = stock_q.where(S.warehouse_name.notin_(sorted(ignored)))
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

    # Статистика кампании В РАЗРЕЗЕ ТОВАРА (campaign × nm), а не итог кампании.
    # Кампания живёт под конкретным артикулом, и её итог по всем товарам там врал: у кампании
    # 36019399 итог 432 622 ₽ приходится на один артикул, а показывался бы под каждым из её
    # шести. Побочно nm-разбивка ещё и полнее итогов (886 кампаний против 844).
    ND = WbAdNmDaily
    nm_daily_q = (
        select(
            ND.campaign_id,
            ND.nm_id,
            func.coalesce(func.sum(ND.views), 0).label("views"),
            func.coalesce(func.sum(ND.clicks), 0).label("clicks"),
            func.coalesce(func.sum(ND.spend), Decimal("0")).label("spend"),
            func.coalesce(func.sum(ND.orders), 0).label("orders"),
            func.coalesce(func.sum(ND.orders_sum), Decimal("0")).label("orders_sum"),
        )
        .where(ND.project_id == project_id)
        .where(ND.date >= date_type.fromisoformat(date_from))
        .where(ND.date <= date_type.fromisoformat(date_to))
        .group_by(ND.campaign_id, ND.nm_id)
    )
    nm_daily_rows = (await db.execute(nm_daily_q)).all()
    camp_stats: dict[tuple[int, int], dict] = {}
    for cd in nm_daily_rows:
        camp_stats[(cd.campaign_id, cd.nm_id)] = {
            "views": int(cd.views or 0),
            "clicks": int(cd.clicks or 0),
            "spend": float(cd.spend or 0),
            "orders": int(cd.orders or 0),
            "orders_sum": float(cd.orders_sum or 0),
        }

    # Build nm_id → campaigns mapping
    nm_campaigns: dict[int, list[dict]] = {}
    for c in campaigns:
        for nm_id in c.nm_ids or []:
            cs = camp_stats.get((c.campaign_id, nm_id), {})
            c_views = cs.get("views", 0)
            c_clicks = cs.get("clicks", 0)
            c_spend = cs.get("spend", 0)
            # ВНИМАНИЕ: заказы здесь — АТРИБУТИРОВАННЫЕ рекламе (WB fullstats), а не все заказы
            # товара, как в строках выше (те из WbFunnelDaily по всем источникам). Поэтому и
            # drr_ad назван отдельно: это ДРР по атрибуции, он не сходится с ДРР товара.
            c_orders = cs.get("orders", 0)
            c_orders_sum = cs.get("orders_sum", 0)
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
                    "orders": c_orders,
                    "orders_sum": round(c_orders_sum, 2),
                    "ctr": round((c_clicks / c_views * 100) if c_views else 0, 2),
                    "cpc": round((c_spend / c_clicks) if c_clicks else 0, 2),
                    "cpm": round((c_spend / c_views * 1000) if c_views else 0, 2),
                    "drr_ad": round(c_spend / c_orders_sum * 100, 2)
                    if c_orders_sum
                    else (None if c_spend > 0 else 0),
                    "events": campaign_events.get(c.campaign_id, [])[:10],
                }
            )

    result = []
    for r in rows:
        ad = nm_ad.get(r.nm_id, {})
        views = ad.get("views", 0)
        clicks = ad.get("clicks", 0)
        adv_sum = ad.get("spend", 0.0)
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


# Потолок строк для вкладки «Склейки»: тянем и товары без рекламы, поэтому выборка шире,
# чем у обычного ad_tab (500). Склейка обязана приезжать целиком — усечение посреди
# склейки исказило бы её сумму.
GLUE_SKU_LIMIT = 5000


async def get_ad_glue_data(
    db: AsyncSession,
    project_id: int,
    date_from: str,
    date_to: str,
    brand: str = "",
    subject: str = "",
) -> list[dict]:
    """Реклама в разрезе склеек WB: строка = карточка (imt_id), дети = её артикулы.

    Товар без склейки — строка-одиночка (is_glue=False), товар без рекламы — с нулями.
    Метрики-отношения (CTR/CPC/CPM/ДРР) считаются от сумм склейки, не усредняются по детям.
    """
    from backend.models.cost import Nomenclature
    from backend.models.refs import ImtAlias

    sku_data = await get_ad_tab_data(
        db, project_id, date_from, date_to, brand, subject, include_no_ads=True, limit=GLUE_SKU_LIMIT
    )

    nom_result = await db.execute(
        select(Nomenclature.article_wb, Nomenclature.imt_id).where(
            Nomenclature.project_id == project_id,
            Nomenclature.imt_id.isnot(None),
        )
    )
    nm_to_imt: dict[int, int] = {row.article_wb: row.imt_id for row in nom_result if row.article_wb and row.imt_id}

    alias_result = await db.execute(select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == project_id))
    imt_aliases: dict[int, str] = {r.imt_id: r.name for r in alias_result}

    # Ключ группы: imt_id склейки либо («одиночка», nm_id) — товары без склейки НЕ схлопываются
    groups: dict[tuple[bool, int], list[dict]] = {}
    for item in sku_data:
        imt_id = nm_to_imt.get(item["nm_id"])
        key = (True, imt_id) if imt_id else (False, item["nm_id"])
        groups.setdefault(key, []).append(item)

    result = []
    for (is_glue, key_id), children in groups.items():
        children.sort(key=lambda x: x["adv_sum"], reverse=True)
        views = sum(c["adv_views"] for c in children)
        clicks = sum(c["adv_clicks"] for c in children)
        adv_sum = sum(c["adv_sum"] for c in children)
        orders_sum = sum(c["orders_sum_rub"] for c in children)

        # Кампания, накрывающая несколько артикулов одной склейки, приходит в каждом ребёнке —
        # без дедупа по campaign_id её бюджет сложился бы столько раз, сколько у неё товаров
        camps: dict[int, dict] = {}
        for child in children:
            for camp in child.get("campaigns", []):
                camps[camp["campaign_id"]] = camp

        head = children[0]
        result.append(
            {
                "imt_id": key_id if is_glue else None,
                "is_glue": is_glue,
                "glue_name": (imt_aliases.get(key_id) or head.get("vendor_code") or f"#{key_id}")
                if is_glue
                else (head.get("vendor_code") or f"#{head['nm_id']}"),
                "nm_ids": [c["nm_id"] for c in children],
                "brand": head.get("brand"),
                "subject": head.get("subject"),
                "adv_views": views,
                "adv_clicks": clicks,
                "adv_sum": round(adv_sum, 2),
                "orders_sum_rub": round(orders_sum, 2),
                "orders_count": sum(c["orders_count"] for c in children),
                "ctr": round((clicks / views * 100) if views else 0, 2),
                "cpc": round((adv_sum / clicks) if clicks else 0, 2),
                "cpm": round((adv_sum / views * 1000) if views else 0, 2),
                "drr": round(adv_sum / orders_sum * 100, 2) if orders_sum else (None if adv_sum > 0 else 0),
                "bdr_revenue": round(sum(c.get("bdr_revenue", 0) for c in children), 2),
                "bdr_profit": round(sum(c.get("bdr_profit", 0) for c in children), 2),
                "stock_qty": sum(c.get("stock_qty", 0) for c in children),
                "product_count": len(children),
                "budget_total": round(sum(c["budget"] for c in camps.values()), 2),
                "campaign_count": len(camps),
                "active_campaigns": sum(1 for c in camps.values() if c.get("status") == 9),
                "campaign_types": sorted({c["campaign_type"] for c in camps.values() if c.get("campaign_type")}),
                "children": children,
            }
        )

    _assign_abc(result, "bdr_revenue", "abc_revenue")
    _assign_abc(result, "bdr_profit", "abc_profit")

    result.sort(key=lambda x: x["adv_sum"], reverse=True)
    return result
