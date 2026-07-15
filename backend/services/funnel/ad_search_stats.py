"""Посуточная статистика зоны «Поиск» — из WB /adv/v1/normquery/stats.

WB не отдаёт разбивку метрик кампании по зонам показов по дням. Единственный источник —
статистика поисковых кластеров с детализацией по дням: суммируя кластеры по (nm_id, date),
получаем метрики зоны «Поиск». Зона «Рекомендации» = итог кампании (wb_ad_nm_daily) минус
поиск (считается на чтении, не хранится).

Наполняется on-demand при заходе в кампанию с выбранной зоной, и кнопкой в «Сырых данных».
"""

import logging
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.integrations import WbAdCampaign, WbAdSearchDaily

logger = logging.getLogger(__name__)


async def sync_ad_search_daily(
    db: AsyncSession, project_id: int, campaign_id: int, date_from: str, date_to: str,
) -> dict:
    """Загрузить посуточную статистику поиска кампании за период → wb_ad_search_daily.

    Только CPM (у CPC поиск = вся кампания). Идемпотентно: ON CONFLICT DO UPDATE
    (кластеры и их суммы у WB могут пересчитываться задним числом).
    """
    from backend.services.funnel.ads_manager import _get_advert_api_key
    from backend.services.funnel.wb_advertising_api import fetch_search_daily

    camp = (
        await db.execute(
            select(WbAdCampaign).where(
                WbAdCampaign.project_id == project_id, WbAdCampaign.campaign_id == campaign_id,
            ).limit(1)
        )
    ).scalar_one_or_none()
    if camp is None:
        return {"status": "error", "error": "Кампания не найдена", "rows": 0}
    if (camp.campaign_type or "").lower() != "cpm":
        return {"status": "skip", "error": "Разбивка по зонам только для CPM", "rows": 0}
    nm_ids = camp.nm_ids or []
    if not nm_ids:
        return {"status": "ok", "rows": 0}

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"status": "error", "error": "Не настроен API-ключ «Продвижение»", "rows": 0}

    agg = await fetch_search_daily(api_key, campaign_id, nm_ids, date_from, date_to)
    rows = [
        {
            "project_id": project_id, "campaign_id": campaign_id, "nm_id": nm_id,
            "date": datetime.strptime(day, "%Y-%m-%d").date(),
            "views": v["views"], "clicks": v["clicks"], "spend": v["spend"],
            "atbs": v["atbs"], "orders": v["orders"], "shks": v["shks"],
        }
        for (nm_id, day), v in agg.items() if day
    ]
    if rows:
        from backend.utils.batching import chunked

        for chunk in chunked(rows):
            stmt = pg_insert(WbAdSearchDaily).values(chunk)
            await db.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_ad_search_daily",
                    set_={
                        "views": stmt.excluded.views, "clicks": stmt.excluded.clicks,
                        "spend": stmt.excluded.spend, "atbs": stmt.excluded.atbs,
                        "orders": stmt.excluded.orders, "shks": stmt.excluded.shks,
                    },
                )
            )
        await db.commit()
    logger.info("Ad search-daily sync: campaign %s, %s..%s — %s строк", campaign_id, date_from, date_to, len(rows))
    return {"status": "ok", "rows": len(rows)}


async def get_search_daily(
    db: AsyncSession, project_id: int, campaign_id: int,
    date_from: date, date_to: date, nm_id: int | None = None,
) -> dict[date, dict]:
    """Метрики зоны «Поиск» по дням (сумма товаров кампании или один товар)."""
    from sqlalchemy import func as f

    conds = [
        WbAdSearchDaily.project_id == project_id,
        WbAdSearchDaily.campaign_id == campaign_id,
        WbAdSearchDaily.date >= date_from,
        WbAdSearchDaily.date <= date_to,
    ]
    if nm_id is not None:
        conds.append(WbAdSearchDaily.nm_id == nm_id)
    rows = (
        await db.execute(
            select(
                WbAdSearchDaily.date,
                f.coalesce(f.sum(WbAdSearchDaily.views), 0),
                f.coalesce(f.sum(WbAdSearchDaily.clicks), 0),
                f.coalesce(f.sum(WbAdSearchDaily.spend), 0),
                f.coalesce(f.sum(WbAdSearchDaily.atbs), 0),
                f.coalesce(f.sum(WbAdSearchDaily.orders), 0),
            ).where(*conds).group_by(WbAdSearchDaily.date)
        )
    ).all()
    return {
        r[0]: {"views": int(r[1]), "clicks": int(r[2]), "spend": float(r[3]), "atbs": int(r[4]), "orders": int(r[5])}
        for r in rows
    }
