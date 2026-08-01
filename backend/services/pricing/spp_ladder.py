# ruff: noqa: RUF001, RUF002, RUF003
"""«Ступеньки СПП»: где снижение нашей цены на рубли роняет цену клиента на сотни.

СПП у ВБ — не гладкая функция цены, а лестница: замер 2026-08-01 по 718 живым
точкам показал порог 2000 ₽ (ниже него медиана СПП 36.8 %, выше — 25.8 %), то
есть −11 ₽ нашей цены = −220 ₽ для клиента. Максимизировать сам СПП бессмысленно
(его максимум = минимальная цена), поэтому ищем РЫЧАГ: Δ цены клиента / Δ нашей
цены, при ограничении снизу по цене безубытка.

Два подвоха, из-за которых наивный расчёт врёт:
  * СПП плавает при неизменной цене (кошелёк покупателя + фон акций ВБ): у одного
    nm при цене 2227 ₽ он гулял 20–32 % по дням. → нормируем на дневной фон
    проекта, иначе детектор ловит ложные ступеньки на смене месяца.
  * Товар обычно стоит по одну сторону порога, и своей истории с другой стороны у
    него нет. → порог ищем по ВСЕМУ проекту, а величину скачка уточняем по своим
    точкам, когда они есть с обеих сторон.

Модуль ничего не пишет в ВБ: только считает и советует.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, time, timedelta
from decimal import Decimal
from statistics import median

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

# ─── параметры детектора ──────────────────────────────────────────────────
WINDOW = 0.06  # окно вокруг порога, доля цены (±6 %)
MIN_SIDE = 5  # сколько точек нужно с каждой стороны порога
MIN_JUMP = 2.0  # минимальный скачок СПП, п.п.
MIN_PRODUCTS = 5  # товаров, перешагивавших порог, — иначе порогу верить нельзя
MIN_AGREE = 0.6  # доля этих товаров, у которых скачок подтвердился
LEVEL_BUCKET = 25  # шаг сетки уровней цены товара, ₽
LEVEL_MIN_POINTS = 3  # наблюдений на уровне, чтобы уровень считался
LEVEL_FRESH_DAYS = 45  # уровень старше — доказательство слабое (акции ВБ уехали)
GRID_STEP = 100  # сетка кандидатов-порогов, ₽ (ступеньки ВБ стоят на круглых)
DEDUPE = 0.08  # два порога ближе 8 % друг к другу — оставляем больший скачок
MAX_DROP = 0.10  # насколько ниже текущей цены готовы искать порог (10 %)
GUARD_ZONE = 0.03  # «сидим у порога снизу» — если цена в пределах 3 % под ним
MIN_LEVERAGE = 2.0  # рычаг ниже этого не показываем: обычная скидка, не ступенька


@dataclass(frozen=True)
class Point:
    """Одно наблюдение: наша цена → СПП → цена клиента за конкретный день."""

    nm_id: int
    day: _date
    seller_price: float
    spp_rate: float  # %
    buyer_price: float
    weight: int = 1


@dataclass
class Step:
    """Порог: ниже него СПП систематически выше (подтверждён парным тестом)."""

    threshold: float
    spp_below: float  # медиана нормированного СПП под порогом, п.п. к фону
    spp_above: float
    jump: float  # медиана СВОИХ скачков товаров, перешагивавших порог, п.п.
    n_below: int  # точек в окне под порогом (справочно — кросс-секция врёт)
    n_above: int
    n_products: int = 0  # товаров с точками по обе стороны — вес доказательства
    agree_pct: float = 0.0  # доля таких товаров, у которых скачок подтвердился


@dataclass
class Level:
    """Уровень цены товара, на котором он реально стоял, с его СПП."""

    price: float
    rel_spp: float  # нормированный СПП на этом уровне, п.п. к дневному фону
    spp: float  # сырой СПП, %
    n: int  # наблюдений
    last_day: _date


# ─────────────────────────── чистая математика ────────────────────────────


def daily_background(points: list[Point]) -> dict[_date, float]:
    """Фон дня = медиана СПП по всему проекту за этот день.

    Снимает общий дрейф акций ВБ: в мае весь портфель ходил на 25 %, в июле на
    37 % — без вычитания фона любая смена цены между месяцами выглядит порогом.
    """
    by_day: dict[_date, list[float]] = defaultdict(list)
    for p in points:
        by_day[p.day].append(p.spp_rate)
    return {d: median(v) for d, v in by_day.items()}


def _rel(points: list[Point], bg: dict[_date, float]) -> list[tuple[float, float]]:
    """[(цена, нормированный СПП)] — СПП минус фон своего дня."""
    return [(p.seller_price, p.spp_rate - bg.get(p.day, 0.0)) for p in points]


def detect_steps(
    points: list[Point],
    *,
    window: float = WINDOW,
    min_side: int = MIN_SIDE,
    min_jump: float = MIN_JUMP,
    min_products: int = MIN_PRODUCTS,
    min_agree: float = MIN_AGREE,
) -> list[Step]:
    """Найти пороги цены, на которых СПП скачком меняется.

    Тест ПАРНЫЙ: порог засчитывается, только если товары, у которых есть точки по
    обе стороны, дружно показывают скачок (медиана своих скачков ≥ `min_jump` и
    доля согласных ≥ `min_agree`). Простое сравнение медиан «ниже порога / выше»
    по всему портфелю врёт: на замере 2026-08-01 кросс-секция давала на 2000 ₽
    скачок +11 п.п., а парный тест по 74 товарам — −0.4 п.п. Разница была не в
    цене, а в составе товаров, которые на этой цене стоят.

    Кандидаты — круглые цены (сетка `GRID_STEP`): лестница ВБ стоит на них, а
    «плавающий» порог из непрерывной оптимизации ловил бы шум.
    """
    bg = daily_background(points)
    rel = _rel(points, bg)
    if not rel:
        return []
    lo = min(p for p, _ in rel)
    hi = max(p for p, _ in rel)
    if hi <= 0:
        return []

    by_nm: dict[int, list[Point]] = defaultdict(list)
    for p in points:
        by_nm[p.nm_id].append(p)

    found: list[Step] = []
    t = max(GRID_STEP, int(lo // GRID_STEP) * GRID_STEP)
    while t <= hi + GRID_STEP:
        below = [s for pr, s in rel if t * (1 - window) <= pr < t]
        above = [s for pr, s in rel if t <= pr <= t * (1 + window)]
        if len(below) >= min_side and len(above) >= min_side:
            jumps = [j for ps in by_nm.values() if (j := own_jump(ps, float(t), bg, window=window)) is not None]
            if len(jumps) >= min_products:
                paired = median(jumps)
                agree = sum(1 for j in jumps if j >= min_jump) / len(jumps)
                if paired >= min_jump and agree >= min_agree:
                    found.append(
                        Step(
                            threshold=float(t),
                            spp_below=round(median(below), 2),
                            spp_above=round(median(above), 2),
                            jump=round(paired, 2),
                            n_below=len(below),
                            n_above=len(above),
                            n_products=len(jumps),
                            agree_pct=round(agree * 100, 1),
                        )
                    )
        t += GRID_STEP

    # соседние кандидаты описывают один и тот же порог — оставляем сильнейший
    kept: list[Step] = []
    for st in sorted(found, key=lambda s: -s.jump):
        if all(abs(st.threshold - k.threshold) / k.threshold > DEDUPE for k in kept):
            kept.append(st)
    return sorted(kept, key=lambda s: s.threshold)


def own_jump(points: list[Point], threshold: float, bg: dict[_date, float], *, window: float = WINDOW) -> float | None:
    """Скачок СПП на пороге по СВОИМ точкам товара (None, если их мало)."""
    below = [p.spp_rate - bg.get(p.day, 0.0) for p in points if threshold * (1 - window) <= p.seller_price < threshold]
    above = [p.spp_rate - bg.get(p.day, 0.0) for p in points if threshold <= p.seller_price <= threshold * (1 + window)]
    if len(below) < 2 or len(above) < 2:
        return None
    return round(median(below) - median(above), 2)


def own_levels(
    points: list[Point],
    bg: dict[_date, float],
    *,
    bucket: int = LEVEL_BUCKET,
    min_points: int = LEVEL_MIN_POINTS,
) -> list[Level]:
    """Уровни цены, на которых товар реально стоял, с медианным СПП на каждом.

    Это самое сильное доказательство, какое у нас есть: сравниваются цены ОДНОГО
    товара, а не разных, — состав портфеля ничего не искажает.
    """
    buckets: dict[float, list[Point]] = defaultdict(list)
    for p in points:
        buckets[round(p.seller_price / bucket) * bucket].append(p)
    out = [
        Level(
            price=lvl,
            rel_spp=round(median([p.spp_rate - bg.get(p.day, 0.0) for p in ps]), 2),
            spp=round(median([p.spp_rate for p in ps]), 2),
            n=len(ps),
            last_day=max(p.day for p in ps),
        )
        for lvl, ps in buckets.items()
        if len(ps) >= min_points
    ]
    return sorted(out, key=lambda x: x.price)


def current_level(levels: list[Level], price: float, *, tol: float = 0.02) -> Level | None:
    """Уровень, на котором товар стоит сейчас (если он в своей истории есть)."""
    near = [x for x in levels if abs(x.price - price) <= price * tol]
    return max(near, key=lambda x: x.n) if near else None


def best_own_level(
    levels: list[Level],
    price: float,
    *,
    max_drop: float = MAX_DROP,
    min_jump: float = MIN_JUMP,
) -> tuple[Level, float] | None:
    """Уровень ниже текущей цены, где СПП товара был заметно выше → (уровень, скачок).

    Из подходящих берём САМЫЙ ДОРОГОЙ: цель — отдать как можно меньше своей цены.
    """
    cur = current_level(levels, price)
    if cur is None:
        return None
    cand = [
        (lv, round(lv.rel_spp - cur.rel_spp, 2))
        for lv in levels
        if price * (1 - max_drop) <= lv.price < price and lv.rel_spp - cur.rel_spp >= min_jump
    ]
    return max(cand, key=lambda t: t[0].price) if cand else None


def nearest_step_below(price: float, steps: list[Step], *, max_drop: float = MAX_DROP) -> Step | None:
    """Ближайший порог под текущей ценой, до которого не дальше `max_drop`."""
    reachable = [s for s in steps if price * (1 - max_drop) <= s.threshold <= price]
    return max(reachable, key=lambda s: s.threshold) if reachable else None


def guard_step(price: float, steps: list[Step], *, zone: float = GUARD_ZONE) -> Step | None:
    """Порог прямо НАД ценой: подниматься выше него дорого — предупреждаем."""
    over = [s for s in steps if price < s.threshold <= price * (1 + zone)]
    return min(over, key=lambda s: s.threshold) if over else None


def target_price_for(threshold: float) -> float:
    """Цена на ступеньке: рубль под порогом (2000 → 1999)."""
    return round(threshold - 1, 2)


def evaluate(
    *,
    current_price: float,
    current_spp: float,
    current_buyer: float | None,
    target: float,
    jump: float,
) -> dict:
    """Что будет, если встать на целевую цену: цена клиенту, дельты, рычаг."""
    buyer_now = current_buyer if current_buyer is not None else current_price * (1 - current_spp / 100)
    target_spp = max(0.0, min(current_spp + jump, 90.0))
    target_buyer = round(target * (1 - target_spp / 100), 2)

    drop_seller = round(current_price - target, 2)
    drop_buyer = round(buyer_now - target_buyer, 2)
    leverage = round(drop_buyer / drop_seller, 1) if drop_seller > 0.01 else None
    return {
        "target_price": target,
        "target_spp": round(target_spp, 2),
        "target_buyer_price": target_buyer,
        "drop_seller": drop_seller,
        "drop_buyer": drop_buyer,
        "leverage": leverage,
    }


# ─────────────────────────── источники точек ──────────────────────────────


async def _upsert_points(db: AsyncSession, project_id: int, rows: list[dict]) -> int:
    """UPSERT точек чанками (лимит asyncpg на параметры в одном statement)."""
    if not rows:
        return 0
    # дедуп ключей внутри батча — иначе CardinalityViolation на ON CONFLICT
    uniq: dict[tuple, dict] = {}
    for r in rows:
        uniq[(r["nm_id"], r["observed_on"], r["source"], r["seller_price"])] = r
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

    today = pytz.UTC.localize(utcnow()).astimezone(_MSK).date()

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


# ─────────────────────────── сборка ответа ────────────────────────────────


def _confidence(step: Step) -> str:
    """Насколько верим порогу проекта: по числу товаров, что его перешагивали."""
    if step.n_products >= 15 and step.agree_pct >= 75:
        return "высокая"
    return "средняя" if step.n_products >= 8 else "низкая"


def _unit_economics(r: dict, drop_seller: float) -> tuple[float | None, float | None]:
    """Прибыль на единицу сейчас и после снижения цены (None — если не из чего)."""
    orders = int(r.get("orders_count") or 0)
    revenue = float(r.get("revenue") or 0)
    if orders <= 0 or revenue <= 0:
        return None, None
    unit_now = round(float(r.get("profit") or 0) / orders, 2)
    # доля цены, которая доходит до нас (после удержаний ВБ и налога) — ровно её
    # мы и теряем с каждого снятого рубля
    keep = (revenue - float(r.get("wb_expenses") or 0) - float(r.get("tax") or 0)) / revenue
    return unit_now, round(unit_now - drop_seller * max(0.0, keep), 2)


async def get_spp_ladder(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    days: int = 120,
    min_leverage: float = MIN_LEVERAGE,
    max_drop_pct: float = MAX_DROP * 100,
    only_in_stock: bool = True,
) -> dict:
    """Строки-советы «встать на ступеньку» + найденные пороги проекта."""
    from backend.services.pricing.markup import _compute_rows

    points = await load_points(db, project_id, days=days)
    steps = detect_steps(points)
    bg = daily_background(points)
    by_nm: dict[int, list[Point]] = defaultdict(list)
    for p in points:
        by_nm[p.nm_id].append(p)

    base = await _compute_rows(db, project_id, date_from, date_to)
    today = pytz.UTC.localize(utcnow()).astimezone(_MSK).date()
    rows: list[dict] = []
    below_floor = 0

    for r in base.get("rows") or []:
        price = r.get("current_price")
        if not price or float(price) <= 0:
            continue
        if only_in_stock and int(r.get("wb_stock") or 0) <= 0:
            continue
        price = float(price)
        nm_id = int(r["nm_id"])
        spp = float(r.get("spp_rate") or 0)
        buyer = float(r["buyer_price"]) if r.get("buyer_price") else None
        own = by_nm.get(nm_id, [])

        # 1) своя история товара — доказательство без примеси состава портфеля
        levels = own_levels(own, bg)
        own_target = best_own_level(levels, price, max_drop=max_drop_pct / 100)
        # 2) порог по проекту — если своей истории по обе стороны нет
        step = None if own_target else nearest_step_below(price, steps, max_drop=max_drop_pct / 100)
        guard = guard_step(price, steps)
        if own_target is None and step is None and guard is None:
            continue

        common = {
            "nm_id": nm_id,
            "vendor_code": r.get("vendor_code"),
            "brand": r.get("brand"),
            "category": r.get("category"),
            "current_price": price,
            "buyer_price": buyer,
            "spp_rate": spp,
            "cost_price": r.get("cost_price"),
            "orders_count": int(r.get("orders_count") or 0),
            "wb_stock": int(r.get("wb_stock") or 0),
            "own_points": len(own),
        }

        if own_target is not None or step is not None:
            if own_target is not None:
                level, jump = own_target
                target, threshold = level.price, level.price
                source = "своя история"
                fresh_days = (today - level.last_day).days
                conf = "высокая" if fresh_days <= LEVEL_FRESH_DAYS and level.n >= 5 else "средняя"
                evidence = f"{level.n} набл., последнее {fresh_days} дн назад"
            else:
                assert step is not None
                jump = step.jump
                target, threshold = target_price_for(step.threshold), step.threshold
                source = "порог по проекту"
                conf = _confidence(step)
                evidence = f"{step.n_products} товаров перешагивали, согласны {step.agree_pct:.0f} %"
                fresh_days = None

            ev = evaluate(
                current_price=price, current_spp=spp, current_buyer=buyer, target=target, jump=jump
            )
            if ev["leverage"] is None or ev["leverage"] < min_leverage:
                continue
            floor = r.get("breakeven_with_adv") or r.get("breakeven_price")
            if floor is not None and ev["target_price"] < float(floor):
                below_floor += 1  # ступенька есть, но она ниже безубытка — не советуем
                continue
            unit_now, unit_after = _unit_economics(r, ev["drop_seller"])
            rows.append(
                {
                    **common,
                    "verdict": "step_down",
                    "threshold": threshold,
                    "jump": jump,
                    "jump_source": source,
                    "confidence": conf,
                    "evidence": evidence,
                    "evidence_days_ago": fresh_days,
                    "floor": float(floor) if floor is not None else None,
                    "drop_seller_pct": round(ev["drop_seller"] / price * 100, 2),
                    "unit_profit_now": unit_now,
                    "unit_profit_after": unit_after,
                    "impact": round(ev["drop_buyer"] * common["orders_count"], 2),
                    **ev,
                }
            )
        else:
            # сидим прямо под порогом: трогать цену вверх нельзя — потеряем ступеньку
            assert guard is not None
            rows.append(
                {
                    **common,
                    "verdict": "hold",
                    "threshold": guard.threshold,
                    "jump": guard.jump,
                    "jump_source": "порог по проекту",
                    "confidence": _confidence(guard),
                    "evidence": f"{guard.n_products} товаров перешагивали, согласны {guard.agree_pct:.0f} %",
                    "evidence_days_ago": None,
                    "floor": None,
                    "target_price": None,
                    "target_spp": None,
                    "target_buyer_price": None,
                    "drop_seller": None,
                    "drop_buyer": None,
                    "leverage": None,
                    "drop_seller_pct": None,
                    "unit_profit_now": None,
                    "unit_profit_after": None,
                    "impact": 0.0,
                }
            )

    rows.sort(key=lambda x: (x["verdict"] != "step_down", -(x["impact"] or 0), -(x["leverage"] or 0)))
    last_day = max((p.day for p in points), default=None)
    return {
        "rows": rows,
        "steps": [
            {
                "threshold": s.threshold,
                "spp_below": s.spp_below,
                "spp_above": s.spp_above,
                "jump": s.jump,
                "n_below": s.n_below,
                "n_above": s.n_above,
                "n_products": s.n_products,
                "agree_pct": s.agree_pct,
            }
            for s in steps
        ],
        "stats": {
            "points": len(points),
            "days": days,
            "nm_with_points": len(by_nm),
            "steps_found": len(steps),
            "step_down": sum(1 for x in rows if x["verdict"] == "step_down"),
            "hold": sum(1 for x in rows if x["verdict"] == "hold"),
            "skipped_below_floor": below_floor,
            "last_point_on": last_day.isoformat() if last_day else None,
        },
    }
