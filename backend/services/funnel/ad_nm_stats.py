"""РК-статистика в разбивке кампания × товар (nmId) × дата.

WB отдаёт nm-разбивку в том же ответе /adv/v3/fullstats, из которого мы берём итоги
кампании (days → apps → nms), поэтому наполнение таблицы не стоит ни одного лишнего
запроса к WB. Историю копим у себя: WB хранит статистику ограниченное время.

ВНИМАНИЕ: orders/orders_sum здесь — заказы, АТРИБУТИРОВАННЫЕ рекламе, а не все заказы
товара (те лежат в WbFunnelDaily по всем источникам трафика).
"""

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbAdCampaign, WbAdNmDaily

logger = logging.getLogger(__name__)

# WB отдаёт максимум 31 день за запрос; между окнами держим паузу (rate limit).
_WINDOW_DAYS = 31
_WINDOW_PAUSE_SEC = 10
# Фоновый темп: чанки кампаний по 50 шт, между ними пауза; на 429 ждём Retry-After.
# 5 секунд (темп интерактивного синка) WB не выдерживает на большом числе кампаний.
_CHUNK_PAUSE_SEC = 20
_MAX_429_RETRIES = 5
_UPSERT_BATCH = 1000
# WB доуточняет свежую статистику — двое последних суток перезаливаем при каждом догоне
_RECHECK_DAYS = 2


def _rows_from_stats(project_id: int, by_nm_campaign: dict) -> list[dict]:
    """{date: {campaign_id: {nm_id: stats}}} → строки для upsert."""
    rows: list[dict] = []
    for date_str, camps in (by_nm_campaign or {}).items():
        try:
            day = date.fromisoformat(date_str)
        except ValueError:
            continue  # служебные ключи вида "_by_campaign"
        for campaign_id, nms in camps.items():
            for nm_id, s in nms.items():
                rows.append(
                    {
                        "project_id": project_id,
                        "campaign_id": campaign_id,
                        "nm_id": nm_id,
                        "date": day,
                        "views": int(s.get("views") or 0),
                        "clicks": int(s.get("clicks") or 0),
                        "spend": round(float(s.get("sum") or 0), 2),
                        "atbs": int(s.get("atbs") or 0),
                        "orders": int(s.get("orders") or 0),
                        "shks": int(s.get("shks") or 0),
                        "orders_sum": round(float(s.get("sum_price") or 0), 2),
                    }
                )
    return rows


async def upsert_ad_nm_daily(db: AsyncSession, project_id: int, by_nm_campaign: dict) -> int:
    """Сохранить разбивку кампания × товар × дата. Возвращает число строк.

    Перезапись значениями WB (не GREATEST): ключ здесь включает campaign_id, поэтому
    частичный ответ не «размазывает» чужие цифры — отсутствующий чанк просто не приходит,
    а пришедший всегда полный по своей кампании.
    """
    rows = _rows_from_stats(project_id, by_nm_campaign)
    if not rows:
        return 0
    for i in range(0, len(rows), _UPSERT_BATCH):
        batch = rows[i : i + _UPSERT_BATCH]
        stmt = pg_insert(WbAdNmDaily).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_ad_nm_daily",
            set_={
                "views": stmt.excluded.views,
                "clicks": stmt.excluded.clicks,
                "spend": stmt.excluded.spend,
                "atbs": stmt.excluded.atbs,
                "orders": stmt.excluded.orders,
                "shks": stmt.excluded.shks,
                "orders_sum": stmt.excluded.orders_sum,
            },
        )
        await db.execute(stmt)
    await db.commit()
    logger.info(f"Ad nm daily upserted: {len(rows)} rows (project {project_id})")
    return len(rows)


def catch_up_window(last: date | None, earliest: date | None, today: date) -> date | None:
    """С какой даты догонять историю. None — догонять нечего.

    Пустая таблица → от создания старейшей кампании (первый проход после релиза).
    Иначе → от последнего залитого дня минус _RECHECK_DAYS: WB доуточняет свежую
    статистику, поэтому хвост перезаливаем (upsert идемпотентен).
    """
    if last is None:
        if earliest is None:
            return None            # у проекта нет кампаний
        return min(earliest, today)
    start = last - timedelta(days=_RECHECK_DAYS)
    return None if start > today else start


async def catch_up_ad_nm_daily(db: AsyncSession, project_id: int) -> dict:
    """Догнать историю разбивки по товарам до сегодняшнего дня.

    Пустая таблица (первый запуск после релиза) → тянем всю доступную глубину:
    от создания старейшей кампании. Иначе — только дни после последнего залитого.
    Идемпотентно: upsert по (project_id, campaign_id, nm_id, date).

    Последние двое суток перезаливаем всегда: WB доуточняет свежую статистику.
    """
    today = date.today()
    last = (
        await db.execute(select(func.max(WbAdNmDaily.date)).where(WbAdNmDaily.project_id == project_id))
    ).scalar_one_or_none()
    earliest = await _earliest_known_date(db, project_id) if last is None else None

    start = catch_up_window(last, earliest, today)
    if start is None:
        return {"ok": True, "skipped": "нет кампаний" if last is None else "история актуальна", "rows": 0}
    return await backfill_ad_nm_daily(db, project_id, start, today)


_backfill_progress: dict[int, dict] = {}


def get_backfill_progress(project_id: int) -> dict:
    """Статус фонового бэкфилла разбивки по товарам."""
    return _backfill_progress.get(project_id) or {"status": "idle"}


async def run_ad_nm_backfill_bg(project_id: int, date_from: date | None = None, date_to: date | None = None) -> None:
    """Фоновая обёртка: своя сессия (сессия запроса к этому моменту уже закрыта)."""
    from backend.database import AsyncSessionLocal

    _backfill_progress[project_id] = {"status": "running"}
    try:
        async with AsyncSessionLocal() as db:
            res = await backfill_ad_nm_daily(db, project_id, date_from, date_to)
        _backfill_progress[project_id] = {"status": "done" if res.get("ok") else "error", **res}
    except asyncio.CancelledError:
        _backfill_progress[project_id] = {"status": "cancelled"}
        raise
    except Exception as e:
        logger.error(f"Ad nm backfill failed for project {project_id}: {e}")
        _backfill_progress[project_id] = {"status": "error", "error": str(e)}


async def _earliest_known_date(db: AsyncSession, project_id: int) -> date | None:
    """Самая ранняя дата, с которой имеет смысл тянуть: создание старейшей кампании."""
    res = await db.execute(
        select(func.min(WbAdCampaign.created_at)).where(WbAdCampaign.project_id == project_id)
    )
    dt = res.scalar_one_or_none()
    return dt.date() if dt else None


async def backfill_ad_nm_daily(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    """Загрузить историю разбивки по товарам за всю доступную глубину.

    Идём окнами по 31 дню от старых дат к новым, с паузой между окнами. Каждое окно
    коммитится сразу: прерывание не теряет уже загруженное, повторный запуск дозальёт
    остальное (upsert идемпотентен).
    """
    from backend.services.funnel.ads_manager import _get_advert_api_key
    from backend.services.funnel.wb_advertising_api import fetch_ad_campaigns, fetch_ad_stats

    api_key = await _get_advert_api_key(db, project_id)
    if not api_key:
        return {"ok": False, "error": "Нет WB-ключа с доступом к рекламе."}

    campaign_ids = await fetch_ad_campaigns(api_key, include_completed=True)
    if not campaign_ids:
        return {"ok": False, "error": "У проекта нет рекламных кампаний."}

    end = date_to or date.today()
    start = date_from or await _earliest_known_date(db, project_id) or (end - timedelta(days=365))
    if start > end:
        return {"ok": False, "error": "Пустой диапазон дат."}

    total_rows = 0
    windows_done = 0
    skipped_chunks = 0
    errors: list[str] = []
    cursor = start
    while cursor <= end:
        win_end = min(cursor + timedelta(days=_WINDOW_DAYS - 1), end)
        try:
            # Полнота важнее скорости: без 300-секундного бюджета, длинные паузы,
            # настойчивые ретраи на 429 — историю WB потом уже не отдаст.
            stats = await fetch_ad_stats(
                api_key,
                campaign_ids,
                cursor.isoformat(),
                win_end.isoformat(),
                time_budget=None,
                chunk_pause=_CHUNK_PAUSE_SEC,
                max_429_retries=_MAX_429_RETRIES,
            )
            total_rows += await upsert_ad_nm_daily(db, project_id, stats.get("_by_nm_campaign") or {})
            windows_done += 1
            missed = int(stats.get("_skipped_chunks") or 0)
            if missed:
                skipped_chunks += missed
                # Не тихое усечение: окно записано частично, повторный запуск дозальёт
                errors.append(f"{cursor}→{win_end}: не получено {missed} чанк(ов) кампаний")
        except Exception as e:
            errors.append(f"{cursor}→{win_end}: {e}")
            logger.warning(f"Ad nm backfill window {cursor}→{win_end} failed: {e}")
            await db.rollback()
        cursor = win_end + timedelta(days=1)
        if cursor <= end:
            await asyncio.sleep(_WINDOW_PAUSE_SEC)

    logger.info(
        f"Ad nm backfill done: project {project_id}, {start}→{end}, "
        f"{windows_done} windows, {total_rows} rows, "
        f"{skipped_chunks} skipped chunks, {len(errors)} errors"
    )
    return {
        "ok": True,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "windows": windows_done,
        "rows": total_rows,
        "skipped_chunks": skipped_chunks,
        "errors": errors,
    }
