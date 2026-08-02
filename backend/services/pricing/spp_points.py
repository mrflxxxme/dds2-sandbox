# ruff: noqa: RUF001, RUF002, RUF003
"""Точки наблюдения СПП: снимок витрины и ретро из заказов.

Слой данных для карты СПП (`spp_map.py`). Одна точка = (товар, день, наша цена)
→ СПП и цена клиента.

Два источника, которые НЕЛЬЗЯ смешивать:
  * `card` — обезличенный СПП витрины: то, что видит любой покупатель. Основной.
  * `orders` — СПП конкретного заказа из отчёта ВБ, туда входит кошелёк
    покупателя. Разница по медиане 12.1 п.п. (замер 2026-08-01), поэтому в одной
    кривой им делать нечего. Годится как ретро-история за 90 дней.

Модуль ничего не пишет в ВБ.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, time, timedelta
from decimal import Decimal

import pytz
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbPrice, WbSppObservation
from backend.models.wb_order import WbOrder
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

_MSK = pytz.timezone("Europe/Moscow")
_MAX_NM = 20000
_MAX_POINTS = 200000
_INSERT_CHUNK = 2000  # 10 колонок × 2000 = 20k параметров < лимита asyncpg (32767)


@dataclass(frozen=True)
class Point:
    """Одно наблюдение: наша цена → СПП → цена клиента за конкретный день."""

    nm_id: int
    day: _date
    seller_price: float
    spp_rate: float  # %
    buyer_price: float
    weight: int = 1


# ─────────────────────────── источники точек ──────────────────────────────


async def _upsert_points(db: AsyncSession, project_id: int, rows: list[dict]) -> int:
    """UPSERT точек чанками (лимит asyncpg на параметры в одном statement)."""
    if not rows:
        return 0
    # дедуп ключей внутри батча — иначе CardinalityViolation на ON CONFLICT
    uniq: dict[tuple, dict] = {}
    for r in rows:
        uniq[(r["nm_id"], r["observed_on"], r.get("observed_hour", 0), r["source"], r["seller_price"])] = r
    payload = list(uniq.values())

    written = 0
    for i in range(0, len(payload), _INSERT_CHUNK):
        chunk = payload[i : i + _INSERT_CHUNK]
        stmt = pg_insert(WbSppObservation).values([{**c, "project_id": project_id} for c in chunk])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_spp_obs_point",
            set_={
                "buyer_price": stmt.excluded.buyer_price,
                "spp_rate": stmt.excluded.spp_rate,
                "obs_count": stmt.excluded.obs_count,
            },
        )
        await db.execute(stmt)
        written += len(chunk)
    await db.commit()
    return written


def _spp_of(seller: float, buyer: float) -> float | None:
    """СПП % от нашей цены; None — если цифры невозможные (шум/рассинхрон)."""
    if seller <= 0 or buyer <= 0:
        return None
    spp = (1 - buyer / seller) * 100
    return round(spp, 2) if -1.0 <= spp <= 95.0 else None


async def snapshot_from_card(db: AsyncSession, project_id: int) -> dict:
    """Сходить в card-API и записать точки за сегодня (ручной прогон из UI).

    Ходит наружу ДОЛГО (сотни nm батчами), поэтому читает БД и сразу коммитит:
    держать транзакцию через внешний HTTP нельзя — сервер-коннект уходит в
    `idle in transaction` и выедает пул pgbouncer.
    """
    from backend.integrations.wb_card_api import fetch_card_prices

    nm_ids = list(
        (
            await db.execute(
                select(WbPrice.nm_id)
                .where(WbPrice.project_id == project_id, WbPrice.price.isnot(None))
                .limit(_MAX_NM)
            )
        ).scalars().all()
    )
    await db.commit()  # ДО похода наружу
    if not nm_ids:
        return {"status": "OK", "requested": 0, "written": 0, "stale": 0}

    card = await fetch_card_prices([int(n) for n in nm_ids])
    return await record_card_points(db, project_id, card)


async def snapshot_now(db: AsyncSession, project_id: int) -> dict:
    """Полный срез: сначала СИНК ЦЕН, потом витрина.

    Порядок здесь — не украшение. Снимок сверяет нашу `base_price` с `basic`
    витрины и расходящиеся точки пропускает (`stale`): иначе СПП считался бы от
    цены, которой уже нет. Если цены не обновить, в `stale` улетает всё, чему
    меняли цену после последнего синка — на живом портфеле 2026-08-02 это было
    401 товар из 750, больше половины. Кнопка «Снять срез» обязана давать срез
    целиком, а не ту половину, что случайно не менялась.
    """
    from backend.services.pricing.sync import sync_wb_prices

    log = await sync_wb_prices(db, project_id)
    snap = await snapshot_from_card(db, project_id)
    return {
        "snapshot": snap,
        "prices": {
            "status": log.status,
            "rows": log.rows_inserted,
            "synced_at": log.finished_at.isoformat() if log.finished_at else None,
        },
    }


async def record_card_points(db: AsyncSession, project_id: int, card: dict[int, dict]) -> dict:
    """Готовый ответ card-API × наши цены витрины → точки за сегодняшний день.

    Вынесено отдельно, чтобы регулярный синк (`sync_card_spp`) писал историю тем
    же ответом, что уже забрал, — второй раз дёргать card-API незачем.
    """
    rows = (
        await db.execute(
            select(WbPrice.nm_id, WbPrice.price, WbPrice.base_price)
            .where(WbPrice.project_id == project_id, WbPrice.price.isnot(None))
            .limit(_MAX_NM)
        )
    ).all()
    prices = {int(nm): (float(p), float(b) if b is not None else None) for nm, p, b in rows if p and float(p) > 0}
    if not prices or not card:
        return {"status": "OK", "requested": len(prices), "written": 0, "stale": 0}

    now_msk = pytz.UTC.localize(utcnow()).astimezone(_MSK)
    today, hour = now_msk.date(), now_msk.hour

    points: list[dict] = []
    stale = 0
    for nm, info in card.items():
        pair = prices.get(nm)
        if not pair:
            continue
        seller, base = pair
        buyer = float(info.get("product") or 0)
        basic = float(info.get("basic") or 0)
        # витрина ушла вперёд нашего синка цен — точка была бы посчитана от
        # устаревшей цены продавца, а это ровно та ошибка, ради которой всё
        if base and basic and abs(base - basic) > 1:
            stale += 1
            continue
        spp = _spp_of(seller, buyer)
        if spp is None:
            continue
        points.append(
            {
                "nm_id": nm,
                "observed_on": today,
                "observed_hour": hour,
                "source": "card",
                "seller_price": Decimal(str(round(seller, 2))),
                "buyer_price": Decimal(str(round(buyer, 2))),
                "spp_rate": Decimal(str(spp)),
                "obs_count": 1,
            }
        )

    written = await _upsert_points(db, project_id, points)
    logger.info(
        "СПП-снимок: проект %d — %d точек (запрошено %d, разъехалось %d)",
        project_id, written, len(prices), stale,
    )
    return {"status": "OK", "requested": len(prices), "written": written, "stale": stale}


async def backfill_from_orders(db: AsyncSession, project_id: int, days: int = 90) -> dict:
    """Ретро-точки из `wb_orders`: поштучный `spp` + `price_with_disc` за 90 дней.

    Внутри дня СПП гуляет по покупателям (кошелёк/WB Клуб), поэтому берём медиану
    заказов дня на одном уровне цены, а не каждый заказ отдельной точкой.
    """
    today_msk = pytz.UTC.localize(utcnow()).astimezone(_MSK).date()
    since = _MSK.localize(datetime.combine(today_msk - timedelta(days=days), time.min))

    O = WbOrder
    day = func.date(func.timezone("Europe/Moscow", O.order_date))
    lvl = func.round(O.price_with_disc)  # копейки внутри дня — шум округления ВБ
    med_spp = func.percentile_cont(0.5).within_group(O.spp.asc())
    med_fin = func.percentile_cont(0.5).within_group(O.finished_price.asc())

    rows = (
        await db.execute(
            select(O.nm_id, day.label("d"), lvl.label("lvl"), med_spp, med_fin, func.count())
            .where(
                O.project_id == project_id,
                O.is_cancel.is_(False),
                O.spp.isnot(None),
                O.price_with_disc.isnot(None),
                O.finished_price.isnot(None),
                O.order_date >= since,
            )
            .group_by(O.nm_id, day, lvl)
            .limit(_MAX_POINTS)
        )
    ).all()

    points: list[dict] = []
    for nm_id, d, level, spp, fin, cnt in rows:
        if nm_id is None or d is None or not level or not fin:
            continue
        seller = float(level)
        buyer = float(fin)
        spp_pct = _spp_of(seller, buyer)
        if spp_pct is None:
            continue
        points.append(
            {
                "nm_id": int(nm_id),
                "observed_on": d,
                "observed_hour": 0,  # источник дневной: медиана заказов дня
                "source": "orders",
                "seller_price": Decimal(str(round(seller, 2))),
                "buyer_price": Decimal(str(round(buyer, 2))),
                "spp_rate": Decimal(str(spp_pct)),
                "obs_count": int(cnt),
            }
        )

    written = await _upsert_points(db, project_id, points)
    logger.info("СПП-бэкфилл из заказов: проект %d — %d точек за %d дн", project_id, written, days)
    return {"status": "OK", "written": written, "days": days}


async def load_points(
    db: AsyncSession, project_id: int, days: int = 120, source: str = "orders"
) -> list[Point]:
    """Точки проекта за последние `days` дней ОДНОГО источника.

    Источники нельзя смешивать в одной кривой: `card` — обезличенный СПП с
    витрины, `orders` — СПП конкретного заказа, куда входит кошелёк покупателя.
    На замере 2026-08-01 разница составила 12.1 п.п. по медиане.
    """
    since = pytz.UTC.localize(utcnow()).astimezone(_MSK).date() - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                WbSppObservation.nm_id,
                WbSppObservation.observed_on,
                WbSppObservation.seller_price,
                WbSppObservation.spp_rate,
                WbSppObservation.buyer_price,
                WbSppObservation.obs_count,
            )
            .where(
                WbSppObservation.project_id == project_id,
                WbSppObservation.observed_on >= since,
                WbSppObservation.source == source,
            )
            .limit(_MAX_POINTS)
        )
    ).all()
    return [
        Point(
            nm_id=int(nm),
            day=d,
            seller_price=float(sp),
            spp_rate=float(spp),
            buyer_price=float(bp),
            weight=int(w or 1),
        )
        for nm, d, sp, spp, bp, w in rows
    ]
