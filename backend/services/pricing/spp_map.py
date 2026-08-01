# ruff: noqa: RUF001, RUF002, RUF003
"""Карта СПП: категория × уровень цены → какой СПП ВБ даёт на этом уровне.

Отвечает на вопрос «при какой цене СПП выше» в том виде, в каком им можно
пользоваться: для каждой категории — лесенка своих цен и живой СПП на каждой
ступени, плюс обрывы, где СПП рушится при переходе на соседний уровень.

Почему это работает, хотя «кросс-секция врёт». Врёт она при сравнении РАЗНЫХ
категорий: товары, стоящие на 1999 ₽, отличаются от стоящих на 2200 ₽ не только
ценой. Внутри одной категории состав однороден, а измеряем мы теперь настоящий
СПП витрины (`source="card"`), а не среднее за 30 дней из финотчёта. Проверка
на живом портфеле 2026-08-01: «Шторы интерьерные» 2000 ₽ → 35.8 %, 2100 ₽ →
23.8 %; ровно тот же обрыв дал ручной замер (2185 → 1999 подняло СПП с 26.4 %
до 36.8 %).

Всё равно это НАБЛЮДЕНИЕ, а не обещание: там, где на уровне мало артикулов или
велик разброс, вывод слабый — поэтому в ответе всегда едут `n` и разброс.
"""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import timedelta
from statistics import median

import pytz
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbSppObservation
from backend.services import refs_service
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

_MSK = pytz.timezone("Europe/Moscow")
_MAX_POINTS = 200000
UNCATEGORIZED = "Без категории"

#: «Психологические» уровни, на которых обычно стоят ступени ВБ. Используются
#: только для поиска дырок в покрытии — какие цены стоит проверить пробой.
GRID = (299, 499, 599, 799, 999, 1199, 1499, 1799, 1999, 2499, 2999, 3499, 3999, 4999, 5999)
CLIFF_MIN_DROP = 3.0  # п.п. между соседними уровнями, чтобы назвать это обрывом
LEVEL_MIN_N = 1
HINT_UP_SPAN = 0.3  # насколько выше текущего уровня ищем «поднять без потери»
HINT_MIN_SUPPORT = 3  # чужой уровень с одним-двумя товарами — не ориентир
HINT_MIN_LEVERAGE = 1.2  # снижение показываем, только если клиент выигрывает БОЛЬШЕ нашей уступки
HINT_UP_TOLERANCE = 0.02  # «поднять незаметно»: цена клиента растёт не более чем на 2 %


@dataclass
class Level:
    price: float
    spp: float
    spp_min: float
    spp_max: float
    buyer_price: float
    n: int
    items: list[dict] = field(default_factory=list)  # артикулы уровня — раскрывается в UI
    hint_down: dict | None = None  # «в других категориях ниже СПП выше»
    hint_up: dict | None = None  # «выше можно встать без потери СПП»


def _parse_day(value: str | None) -> _date | None:
    """ISO-строка → дата (None, если пусто или мусор — вызывающий подставит своё)."""
    if not value:
        return None
    try:
        return _date.fromisoformat(value[:10])
    except ValueError:
        return None


def _bucket(price: float, step: int) -> float:
    return float(round(price / step) * step)


def build_levels(points: list[tuple], step: int) -> list[Level]:
    """[(цена, СПП, цена клиента[, nm_id, артикул])] → лесенка уровней с медианой СПП.

    Артикулы уровня едут вместе с ним: разброс СПП на одной цене — обычное дело,
    и первый вопрос к такой строке — «а какие именно товары».
    """
    by: dict[float, list[tuple]] = defaultdict(list)
    for pt in points:
        by[_bucket(pt[0], step)].append(pt)
    out = []
    for lvl, v in by.items():
        if len(v) < LEVEL_MIN_N:
            continue
        spps = [p[1] for p in v]
        out.append(
            Level(
                price=lvl,
                spp=round(median(spps), 1),
                spp_min=round(min(spps), 1),
                spp_max=round(max(spps), 1),
                buyer_price=round(median([p[2] for p in v]), 0),
                n=len(v),
                items=sorted(
                    (
                        {
                            "nm_id": p[3],
                            "vendor_code": p[4],
                            "price": round(p[0], 2),
                            "spp": round(p[1], 1),
                            "buyer_price": round(p[2], 0),
                        }
                        for p in v
                        if len(p) > 4
                    ),
                    key=lambda x: x["spp"],
                ),
            )
        )
    return sorted(out, key=lambda x: x.price)


def find_cliffs(levels: list[Level], *, min_drop: float = CLIFF_MIN_DROP) -> list[dict]:
    """Пары соседних уровней, между которыми СПП обваливается вверх по цене.

    Именно это и есть «дороже нельзя»: слева от обрыва ВБ доплачивает, справа —
    нет. Наша выгода от перехода влево = сколько ВБ добавит покупателю сверх
    того, что мы уступили.
    """
    out = []
    for lo, hi in zip(levels, levels[1:], strict=False):
        drop = lo.spp - hi.spp
        if drop >= min_drop:
            give = lo.price - hi.price  # < 0: цену снижаем
            gain = hi.buyer_price - lo.buyer_price  # сколько выигрывает клиент
            out.append(
                {
                    "keep_below": lo.price,
                    "breaks_at": hi.price,
                    "spp_below": lo.spp,
                    "spp_above": hi.spp,
                    "drop": round(drop, 1),
                    "seller_gives": round(-give, 0),
                    "buyer_gains": round(gain, 0),
                    "leverage": round(gain / -give, 1) if give < 0 else None,
                    "n_below": lo.n,
                    "n_above": hi.n,
                }
            )
    return out


def coverage_gaps(levels: list[Level], grid: tuple[int, ...] = GRID) -> list[int]:
    """Уровни сетки, где у категории нет ни одного товара — кандидаты на пробу."""
    have = {lv.price for lv in levels}
    span = (min(have, default=0), max(have, default=0))
    return [g for g in grid if span[0] <= g <= span[1] and not any(abs(g - h) <= 50 for h in have)]


def global_levels(per_cat: dict[str, list[Level]]) -> dict[float, dict]:
    """Уровень цены → СПП по ВСЕМ категориям (медиана медиан) + кто это подтверждает.

    Ступени ВБ живут не в категории, а в цене: если в «Коврах» на 1999 ₽ дают
    36.8 %, то и «Шторам» на этом уровне, скорее всего, дадут столько же. Такой
    ориентир — единственный способ что-то сказать про уровень, на котором у
    категории нет ни одного товара.
    """
    acc: dict[float, list[tuple[float, str, int]]] = defaultdict(list)
    for cat, levels in per_cat.items():
        for lv in levels:
            acc[lv.price].append((lv.spp, cat, lv.n))
    return {
        price: {
            "spp": round(median([s for s, _, _ in v]), 1),
            "categories": sorted({c for _, c, _ in v}),
            "n": sum(n for _, _, n in v),
        }
        for price, v in acc.items()
    }


def cross_hints(
    levels: list[Level],
    glob: dict[float, dict],
    *,
    min_leverage: float = HINT_MIN_LEVERAGE,
    up_span: float = HINT_UP_SPAN,
    up_tolerance: float = HINT_UP_TOLERANCE,
) -> None:
    """Проставить уровням подсказку «что сделать с ценой» (мутирует `levels`).

    Показываем ровно два случая, в которых цену стоит трогать:
      * ВНИЗ — клиент выигрывает БОЛЬШЕ, чем мы уступаем (рычаг > 1). Обычная
        скидка рычагом ≤ 1 подсказкой не является: это просто потеря маржи.
      * ВВЕРХ — нашу цену поднимаем, а цена клиента не растёт: разницу добирает
        СПП. Бывает и так, что клиент при этом платит МЕНЬШЕ (Ковры 2026-08-01:
        4300 ₽ → клиент 3210 ₽, а 5000 ₽ → клиент 3159 ₽) — чистая победа.
    Всё остальное — молчим, чтобы колонка не пестрила бессмысленными строками.

    Кандидаты — СВОИ уровни категории (лучшее доказательство) плюс чужие оттуда,
    где своих товаров нет. Приоритет своих: раньше свои уровни, наоборот,
    ГЛУШИЛИ подсказку, и очевидный подъём 4300 → 5000 оставался незамеченным.
    """
    own_prices = {lv.price for lv in levels}
    candidates: dict[float, dict] = {
        p: {"spp": g["spp"], "buyer": round(p * (1 - g["spp"] / 100)), "cats": g["categories"][:3], "n": g["n"]}
        for p, g in glob.items()
        if p not in own_prices and g["n"] >= HINT_MIN_SUPPORT
    }
    candidates.update(
        {lv.price: {"spp": lv.spp, "buyer": lv.buyer_price, "cats": [], "n": lv.n} for lv in levels}
    )

    for lv in levels:
        best_down: tuple[float, dict] | None = None
        best_up: tuple[float, dict] | None = None

        for p, c in candidates.items():
            if p == lv.price:
                continue
            hint = {
                "price": p,
                "spp": c["spp"],
                "buyer_price": c["buyer"],
                "categories": c["cats"],
            }
            if p < lv.price:
                give = lv.price - p
                gain = lv.buyer_price - c["buyer"]
                lev = round(gain / give, 1) if give > 0 else None
                if lev is not None and lev >= min_leverage:
                    hint["gain"] = round(gain, 0)
                    hint["leverage"] = lev
                    if best_down is None or p > best_down[0]:  # ближе к текущей — уступаем меньше
                        best_down = (p, hint)
            elif p <= lv.price * (1 + up_span) and c["buyer"] <= lv.buyer_price * (1 + up_tolerance):
                hint["gain"] = round(p - lv.price, 0)  # прибавка к нашей цене
                hint["buyer_delta"] = round(c["buyer"] - lv.buyer_price, 0)
                if best_up is None or p > best_up[0]:  # самый дорогой из безболезненных
                    best_up = (p, hint)

        lv.hint_up = best_up[1] if best_up else None
        lv.hint_down = best_down[1] if best_down else None
        # подъём строго лучше снижения (клиенту не хуже, а нам больше) — снижение
        # в такой строке только сбивает: оставляем один ход
        if lv.hint_up and lv.hint_down and lv.hint_up["buyer_price"] <= lv.hint_down["buyer_price"]:
            lv.hint_down = None


async def _category_map(db: AsyncSession, project_id: int) -> dict[int, tuple[str, str | None]]:
    """nm_id → (категория, артикул продавца). Категория: override справочника → предмет ВБ."""
    from backend.services.pricing.markup import _load_meta_map

    meta = await _load_meta_map(db, project_id)
    overrides = await refs_service.get_category_overrides(db, project_id)
    return {
        nm: (overrides.get(nm) or (m.get("subject") or UNCATEGORIZED), m.get("article_seller"))
        for nm, m in meta.items()
    }


async def get_spp_map(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    step: int = 100,
    source: str = "card",
    category: str | None = None,
) -> dict:
    """Карта «категория × цена → СПП» по снимкам витрины за выбранный период."""
    today = pytz.UTC.localize(utcnow()).astimezone(_MSK).date()
    since = _parse_day(date_from) or today
    until = _parse_day(date_to) or today
    rows = (
        await db.execute(
            select(
                WbSppObservation.nm_id,
                WbSppObservation.observed_on,
                WbSppObservation.seller_price,
                WbSppObservation.spp_rate,
                WbSppObservation.buyer_price,
            )
            .where(
                WbSppObservation.project_id == project_id,
                WbSppObservation.observed_on >= since,
                WbSppObservation.observed_on <= until,
                WbSppObservation.source == source,
            )
            .limit(_MAX_POINTS)
        )
    ).all()

    cats = await _category_map(db, project_id)
    by_cat: dict[str, list[tuple]] = defaultdict(list)
    days_seen: set[_date] = set()
    for nm, day, price, spp, buyer in rows:
        cat, vendor = cats.get(int(nm), (UNCATEGORIZED, None))
        if category and cat != category:
            continue
        by_cat[cat].append((float(price), float(spp), float(buyer), int(nm), vendor))
        days_seen.add(day)

    per_cat = {cat: build_levels(pts, step) for cat, pts in by_cat.items()}
    glob = global_levels(per_cat)

    out: list[dict] = []
    for cat, levels in per_cat.items():
        cross_hints(levels, glob)
        out.append(
            {
                "category": cat,
                "nm_count": len(by_cat[cat]),
                "levels": [lv.__dict__ for lv in levels],
                "cliffs": find_cliffs(levels),
                "gaps": coverage_gaps(levels),
            }
        )
    out.sort(key=lambda c: (-len(c["cliffs"]), -int(c["nm_count"])))

    return {
        "categories": out,
        "stats": {
            "source": source,
            "date_from": since.isoformat(),
            "date_to": until.isoformat(),
            "step": step,
            "points": len(rows),
            "categories_count": len(out),
            "with_cliffs": sum(1 for c in out if c["cliffs"]),
            "last_snapshot_on": max(days_seen).isoformat() if days_seen else None,
        },
    }


async def get_level_history(
    db: AsyncSession, project_id: int, nm_ids: list[int], *, days: int = 30, source: str = "card"
) -> list[dict]:
    """История «цена → СПП» по дням для набора артикулов (проверка гипотезы во времени)."""
    if not nm_ids:
        return []
    since = pytz.UTC.localize(utcnow()).astimezone(_MSK).date() - timedelta(days=days)
    rows = (
        await db.execute(
            select(
                WbSppObservation.observed_on,
                WbSppObservation.seller_price,
                WbSppObservation.spp_rate,
                WbSppObservation.buyer_price,
            )
            .where(
                WbSppObservation.project_id == project_id,
                WbSppObservation.nm_id.in_(nm_ids[:500]),
                WbSppObservation.observed_on >= since,
                WbSppObservation.source == source,
            )
            .limit(_MAX_POINTS)
        )
    ).all()

    by_day: dict[_date, list[tuple[float, float, float]]] = defaultdict(list)
    for day, price, spp, buyer in rows:
        by_day[day].append((float(price), float(spp), float(buyer)))
    return [
        {
            "day": d.isoformat(),
            "price": round(median([p for p, _, _ in v]), 0),
            "spp": round(median([s for _, s, _ in v]), 1),
            "buyer_price": round(median([b for _, _, b in v]), 0),
            "n": len(v),
        }
        for d, v in sorted(by_day.items())
    ]
