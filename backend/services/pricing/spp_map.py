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
HINT_UP_SPAN = 1.0  # ступенька может быть далеко: у «Алмазной мозаики» это 1170 → 1500 ₽
HINT_UP_MAX_RISE = 50.0  # ₽: подъём допустим, только если клиент переплатит не больше этого
HINT_MIN_SUPPORT = 3  # чужой уровень с одним-двумя товарами — не ориентир
HINT_MIN_LEVERAGE = 1.2  # снижение показываем, только если клиент выигрывает БОЛЬШЕ нашей уступки
HINT_MIN_EFFECT_RUB = 100.0  # меньше — не повод трогать цену
HINT_MIN_EFFECT_PCT = 0.03  # …или 3 % от цены, что больше
HINT_MIN_STEP = 3.0  # п.п. разницы СПП — иначе это не ступенька, а сдвиг цены
SAFE_TOL = 1.0  # п.п.: товар с таким отклонением от медианы уровня всё ещё «на ступеньке»

#: Поиск порогов на общей оси цен (не зависит от шага сетки).
THRESHOLD_WINDOW = 6  # точек с каждой стороны в медиане
THRESHOLD_MIN_SIDE = 3  # меньше — не порог, а случайность
THRESHOLD_MIN_JUMP = 5.0  # п.п. — на сколько должен скакнуть СПП
THRESHOLD_BAND_TOL = 2.0  # п.п.: в пределах этого товар считается стоящим на той же полке
THRESHOLD_MERGE_PCT = 0.03  # границы ближе 3 % по цене — один и тот же порог
THRESHOLD_FUZZY_PCT = 0.02  # зазор шире 2 % цены — точное место порога неизвестно
THRESHOLD_CONFIRM_SPAN = 0.25  # в каком окне цен искать подтверждение внутри категории


@dataclass
class Level:
    price: float
    spp: float
    spp_min: float
    spp_max: float
    buyer_price: float
    n: int
    safe_price: float = 0.0  # максимальная РЕАЛЬНАЯ цена уровня, на которой СПП ещё держится
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
        spp = round(median(spps), 1)
        items = sorted(
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
        )
        out.append(
            Level(
                price=lvl,
                spp=spp,
                spp_min=round(min(spps), 1),
                spp_max=round(max(spps), 1),
                buyer_price=round(median([p[2] for p in v]), 0),
                n=len(v),
                safe_price=_safe_price(items, spp, lvl),
                items=items,
            )
        )
    return sorted(out, key=lambda x: x.price)


def _safe_price(items: list[dict], spp: float, fallback: float) -> float:
    """Самая дорогая РЕАЛЬНАЯ цена уровня, на которой СПП ещё держится.

    Уровень — это корзина шириной в шаг сетки, и порог ВБ вполне может проходить
    внутри неё: на 1499.14 ₽ дают 34.6 %, а на 1502 ₽ — уже 4.8 %, но обе цены
    лежат в корзине «1 500 ₽». Советовать «поднимите до 1 500» в такой ситуации
    значит советовать перешагнуть порог. Поэтому в подсказку едет не ярлык
    корзины, а последняя цена, на которой ступенька ЕСТЬ, округлённая ВНИЗ до
    рубля — ошибиться в безопасную сторону дешевле.
    """
    good = [it["price"] for it in items if it["spp"] >= spp - SAFE_TOL]
    return float(int(max(good))) if good else float(fallback)


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


def _band(spps: list[float], start: int, direction: int, ref: float, tol: float) -> int:
    """Сколько точек подряд от `start` держат СПП в пределах `tol` от `ref`."""
    i, n = start, 0
    while 0 <= i < len(spps) and abs(spps[i] - ref) <= tol:
        n += 1
        i += direction
    return n


def find_thresholds(
    points: list[tuple],
    *,
    window: int = THRESHOLD_WINDOW,
    min_side: int = THRESHOLD_MIN_SIDE,
    min_jump: float = THRESHOLD_MIN_JUMP,
) -> list[dict]:
    """Пороги цены по ВСЕМУ портфелю: где СПП меняется скачком.

    Здесь мы не режем цены на корзины, а идём по общей оси цен: порог ВБ живёт
    в цене, а не в категории и уж точно не в шаге сетки. Для каждой границы между
    соседними ценами сравниваем медиану СПП `window` точек слева и справа; скачок
    от `min_jump` п.п. — кандидат в пороги. Подряд идущие кандидаты схлопываем:
    один порог обычно даёт несколько соседних границ.

    Ответ намеренно даёт ИНТЕРВАЛ (`up_to` … `from_price`), а не одно число:
    между 1 499.14 ₽ и 1 502 ₽ наблюдений нет, и где именно ВБ проводит черту —
    неизвестно. `fuzzy` помечает границы, где этот зазор широкий и место порога
    угадано грубо.

    `points` — [(цена, СПП, категория)] по всем категориям сразу.
    """
    pts = sorted(points, key=lambda p: p[0])
    if len(pts) < 2 * min_side:
        return []
    prices = [float(p[0]) for p in pts]
    spps = [float(p[1]) for p in pts]

    cands: list[tuple[int, float, float]] = []
    for i in range(len(pts) - 1):
        if prices[i + 1] - prices[i] < 0.01:  # та же цена — не граница
            continue
        below, above = spps[max(0, i + 1 - window) : i + 1], spps[i + 1 : i + 1 + window]
        if len(below) < min_side or len(above) < min_side:
            continue
        jump = median(above) - median(below)
        local = spps[i + 1] - spps[i]
        # оба условия обязательны: окно ловит смену полки, но срабатывает и на
        # подходе к ней (медиана уезжает раньше самой границы), а локальный скачок
        # без окна поймал бы одиночный товар, которому ВБ ещё не применил ступеньку
        if abs(jump) >= min_jump and abs(local) >= min_jump:
            cands.append((i, jump, local))

    groups: list[list[tuple[int, float, float]]] = []
    for cand in cands:
        i = cand[0]
        if groups and prices[i] - prices[groups[-1][-1][0]] <= prices[i] * THRESHOLD_MERGE_PCT:
            groups[-1].append(cand)
        else:
            groups.append([cand])

    out = []
    for g in groups:
        i, _jump, _local = max(g, key=lambda x: (abs(x[2]), abs(x[1])))
        # полки считаем от самой границы, а не от окна: окно захватывает соседей
        # за порогом и уводит и медиану, и длину полки
        n_below = _band(spps, i, -1, spps[i], THRESHOLD_BAND_TOL)
        n_above = _band(spps, i + 1, 1, spps[i + 1], THRESHOLD_BAND_TOL)
        below_pts, above_pts = pts[i + 1 - n_below : i + 1], pts[i + 1 : i + 1 + n_above]
        spp_below = round(median([p[1] for p in below_pts]), 1)
        spp_above = round(median([p[1] for p in above_pts]), 1)
        jump = round(spp_above - spp_below, 1)
        if n_below < 2 or n_above < 2 or abs(jump) < min_jump:
            continue
        confirmed = _confirm_in_category(pts, i, jump, min_jump)
        if not confirmed:
            continue  # порог виден только при сравнении РАЗНЫХ категорий — это не порог
        cats = {p[2] for p in below_pts + above_pts if len(p) > 2}
        out.append(
            {
                "up_to": round(prices[i], 2),
                "from_price": round(prices[i + 1], 2),
                "spp_below": spp_below,
                "spp_above": spp_above,
                "jump": jump,
                "n_below": n_below,
                "n_above": n_above,
                "band_from": round(prices[i + 1 - n_below], 2),
                "band_to": round(prices[i + n_above], 2),
                "categories": sorted(cats),  # все — по ним UI подсвечивает список слева
                "categories_count": len(cats),
                "confirmed_by": confirmed[:4],
                "fuzzy": prices[i + 1] - prices[i] > prices[i] * THRESHOLD_FUZZY_PCT,
            }
        )
    return out


def _confirm_in_category(
    pts: list[tuple], i: int, jump: float, min_jump: float, *, span: float = THRESHOLD_CONFIRM_SPAN
) -> list[str]:
    """Категории, у которых тот же скачок виден по СВОИМ товарам с обеих сторон.

    Без этой проверки в список лезут ложные пороги: «Алмазная мозаика» ровно
    стоит на 4.9 % по всему диапазону 550–1180 ₽, «Кружки» — на 10.8 %, и место,
    где на общей оси одни сменяются другими, выглядит как ступенька ВБ. Настоящий
    порог обязан быть виден внутри одной категории — там состав однороден
    (порог 1499 ₽ подтверждают «Чехлы для мебели»: 10.8 % до и 36.8 % после).

    Смотрим не на «полки» (они обрываются по допуску в 2 п.п.), а на всё, что
    стоит в пределах `span` от границы: товар категории может быть в стороне.
    """
    lo: dict[str, list[float]] = defaultdict(list)
    hi: dict[str, list[float]] = defaultdict(list)
    edge_lo, edge_hi = pts[i][0], pts[i + 1][0]
    for p in pts:
        if len(p) < 3:
            continue
        if edge_lo * (1 - span) <= p[0] <= edge_lo:
            lo[p[2]].append(p[1])
        elif edge_hi <= p[0] <= edge_hi * (1 + span):
            hi[p[2]].append(p[1])
    out = []
    for cat in sorted(lo.keys() & hi.keys()):
        if len(lo[cat]) < 2 or len(hi[cat]) < 2:
            continue  # один товар с каждой стороны — не подтверждение
        own = median(hi[cat]) - median(lo[cat])
        if abs(own) >= min_jump and (own > 0) == (jump > 0):
            out.append(cat)
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
    acc: dict[float, list[tuple[float, str, int, float]]] = defaultdict(list)
    for cat, levels in per_cat.items():
        for lv in levels:
            acc[lv.price].append((lv.spp, cat, lv.n, lv.safe_price or lv.price))
    out: dict[float, dict] = {}
    for price, v in acc.items():
        spp = round(median([s for s, _, _, _ in v]), 1)
        # безопасная цена — только у тех, кто ступеньку реально получил
        safe = [sp for s, _, _, sp in v if s >= spp - SAFE_TOL]
        out[price] = {
            "spp": spp,
            "categories": sorted({c for _, c, _, _ in v}),
            "n": sum(n for _, _, n, _ in v),
            "safe": max(safe) if safe else price,
        }
    return out


def is_flat(levels: list[Level], *, min_levels: int = 5, min_step: float = HINT_MIN_STEP) -> bool:
    """У категории СПП одинаков на всех её уровнях — ступенек в этом диапазоне нет.

    «Алмазная мозаика» 2026-08-01: девятнадцать уровней от 550 до 1180 ₽ и СПП
    4.8–5.0 % на каждом. Чужой уровень с 10.8 % ВНУТРИ этого диапазона — ложный
    совет: мы там уже стояли и знаем, что цена ни при чём. За пределами
    диапазона (те же 1500 ₽) наши данные не говорят ничего, и чужой ориентир
    остаётся единственным — его показываем.
    """
    spps = [lv.spp for lv in levels]
    return len(spps) >= min_levels and max(spps) - min(spps) < min_step


def _build_candidates(levels: list[Level], glob: dict[float, dict]) -> dict[float, dict]:
    """Куда вообще можно встать: свои уровни категории + чужие там, где своих нет.

    Свои сильнее: именно из-за приоритета чужих очевидный ход «4700 → 5000 ₽»
    у «Ковров» когда-то не показывался вовсе. А если своя категория ровная,
    чужие вообще не берём — см. `is_flat`.
    """
    own_prices = {lv.price for lv in levels}
    # ровная категория: чужие ориентиры внутри нашего ценового диапазона молчат
    lo, hi = (min(own_prices), max(own_prices)) if own_prices and is_flat(levels) else (0.0, 0.0)
    out: dict[float, dict] = {
        p: {
            "spp": g["spp"],
            "safe": g.get("safe", p),
            # цену клиента считаем на той цене, которую и советуем, — не на ярлыке корзины
            "buyer": round(g.get("safe", p) * (1 - g["spp"] / 100)),
            "cats": g["categories"][:3],
        }
        for p, g in glob.items()
        if p not in own_prices and g["n"] >= HINT_MIN_SUPPORT and not (lo <= p <= hi)
    }
    # уровень с одним-двумя товарами — не ориентир и в своей категории тоже
    out.update(
        {
            lv.price: {
                "spp": lv.spp,
                "safe": lv.safe_price or lv.price,
                "buyer": round((lv.safe_price or lv.price) * (1 - lv.spp / 100)),
                "cats": [],
            }
            for lv in levels
            if lv.n >= HINT_MIN_SUPPORT
        }
    )
    return out


def hint_for(
    price: float,
    spp: float,
    buyer: float,
    candidates: dict[float, dict],
    *,
    min_leverage: float = HINT_MIN_LEVERAGE,
    up_span: float = HINT_UP_SPAN,
    max_rise: float = HINT_UP_MAX_RISE,
    min_step: float = HINT_MIN_STEP,
    min_effect: float = HINT_MIN_EFFECT_RUB,
) -> tuple[dict | None, dict | None]:
    """Подсказка «что сделать с ценой» для одной точки → (вниз, вверх).

    Совет выдаём только там, где есть НАСТОЯЩАЯ ступенька:
      * СПП на целевом уровне отличается минимум на `min_step` п.п. — иначе это
        не ступенька, а обычный сдвиг цены на пару процентов;
      * эффект не меньше `min_effect` ₽ (или 3 % цены) — ход на 10 ₽ показывать
        незачем;
      * ВНИЗ — клиент выигрывает БОЛЬШЕ нашей уступки (рычаг > 1); скидка с
        рычагом ≤ 1 — не подсказка, а потеря маржи;
      * ВВЕРХ — цена клиента не растёт вовсе (лучший расклад: «Ковры» 4700 ₽ →
        клиент 3554 ₽, а 5000 ₽ → клиент 3159 ₽) либо растёт не больше чем на
        `max_rise` ₽. Прежнее правило «ВБ съедает половину подъёма» пропускало
        ходы вроде «Кружки» 800 → 1500 ₽, где клиент переплачивал 273 ₽.

    Советуем не ярлык корзины, а `safe` — последнюю цену, на которой ступенька
    реально наблюдалась (1 499 ₽, а не 1 500 ₽: порог может быть внутри корзины).
    """
    best_down: tuple[tuple, dict] | None = None
    best_up: tuple[tuple, dict] | None = None

    for p, c in candidates.items():
        if abs(p - price) < 1:
            continue
        target = c.get("safe") or p
        hint = {"price": target, "spp": c["spp"], "buyer_price": c["buyer"], "categories": c["cats"]}
        if target < price:
            give = price - target
            gain = buyer - c["buyer"]
            lev = round(gain / give, 1) if give > 0 else None
            if (
                lev is not None
                and lev >= min_leverage
                and c["spp"] - spp >= min_step
                and gain >= max(min_effect, buyer * HINT_MIN_EFFECT_PCT)
            ):
                hint["gain"] = round(gain, 0)
                hint["leverage"] = lev
                down_key = (target,)  # ближе к текущей цене — уступаем меньше
                if best_down is None or down_key > best_down[0]:
                    best_down = (down_key, hint)
        elif target > price:
            rise = c["buyer"] - buyer  # < 0: клиенту ещё и дешевле
            if (
                target <= price * (1 + up_span)
                and c["spp"] - spp >= min_step  # ступенька, а не просто рост цены
                and target - price >= max(min_effect, price * HINT_MIN_EFFECT_PCT)
                and rise <= max_rise
            ):
                hint["gain"] = round(target - price, 0)  # прибавка к нашей цене
                hint["buyer_delta"] = round(rise, 0)
                # сперва ходы, где клиенту не дороже, и уже среди них — самый дорогой
                up_key = (rise <= 0, target)
                if best_up is None or up_key > best_up[0]:
                    best_up = (up_key, hint)

    down = best_down[1] if best_down else None
    up = best_up[1] if best_up else None
    # Подъём глушит снижение, только когда клиенту от него НЕ ХУЖЕ: тогда он строго
    # сильнее (нам больше, клиенту не дороже) и второй совет только спорил бы с ним.
    # Если же подъём стоит клиенту денег — оба хода остаются: «Панели стеновые»
    # 2200 ₽ могут пойти вверх на 2335 (клиенту +47) или вниз на 1999, где ступенька
    # 32.3 % и клиент платит на 368 ₽ меньше. Это выбор маржи против объёма, и он наш.
    if up and up.get("buyer_delta", 0) <= 0:
        down = None
    return down, up


def lag_flags(levels: list[Level], *, min_step: float = HINT_MIN_STEP, tol: float = 1.0) -> None:
    """Пометить товары, у которых СПП НИЖЕ соседей с ПРАКТИЧЕСКИ ТОЙ ЖЕ ценой.

    Разбор случая 2026-08-01. Товар 937180966 стоял 1499.00 ₽ и имел СПП 6.3 %,
    а 52 соседа за 1499.14 ₽ — 32.4 %; выглядело как «ступенька не работает на
    целой цене». Через четыре часа при НЕИЗМЕННОЙ цене 1499.00 он получил те же
    32.4 %, и из 734 товаров с неизменной ценой он был ЕДИНСТВЕННЫМ, кто
    сдвинулся, — то есть это не общий сдвиг ВБ, а его личная догонялка. Значит
    дело не в копейках, а в задержке: ВБ применяет ступеньку не мгновенно.

    Поэтому флаг говорит именно то, что известно: «СПП ниже соседей с той же
    ценой». Сравниваем в пределах ±`tol` ₽ — на большей разнице это уже другая
    цена, и разбираться с ней должна обычная подсказка (у товара за 2001 ₽ при
    соседях за 1999 ₽ дело в пороге, а не в задержке).
    """
    for lv in levels:
        for it in lv.items:
            peers = [
                o["spp"] for o in lv.items
                if o is not it and abs(o["price"] - it["price"]) <= tol
            ]
            if len(peers) < 3:
                continue
            peer_spp = median(peers)
            if peer_spp - it["spp"] < min_step:
                continue
            it["lag_hint"] = {
                "peer_spp": round(peer_spp, 1),
                "delta": round(peer_spp - it["spp"], 1),
                "peers": len(peers),
                "buyer_price": round(it["price"] * (1 - peer_spp / 100)),
            }


def cross_hints(levels: list[Level], glob: dict[float, dict]) -> None:
    """Проставить подсказки уровням И каждому артикулу внутри уровня (мутирует).

    Артикулам отдельно, потому что на одном уровне цены СПП у товаров разный:
    у соседей по строке рекомендации могут не совпадать.
    """
    candidates = _build_candidates(levels, glob)
    for lv in levels:
        lv.hint_down, lv.hint_up = hint_for(lv.price, lv.spp, lv.buyer_price, candidates)
        for it in lv.items:
            d, u = hint_for(it["price"], it["spp"], it["buyer_price"], candidates)
            it["hint_down"], it["hint_up"] = d, u
    lag_flags(levels)


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
                WbSppObservation.observed_hour,
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

    # снимки часовые: за период у товара их много, а в карте он должен быть один
    # раз — иначе уровень считает его N раз и «Артикулов» врёт. Берём последний.
    latest: dict[int, tuple] = {}
    days_seen: set[_date] = set()
    for nm, day, hour, price, spp, buyer in rows:
        key = int(nm)
        stamp = (day, hour)
        if key not in latest or stamp > latest[key][0]:
            latest[key] = (stamp, float(price), float(spp), float(buyer))
        days_seen.add(day)

    cats = await _category_map(db, project_id)
    by_cat: dict[str, list[tuple]] = defaultdict(list)
    all_points: list[tuple] = []  # пороги ищем по всему портфелю, фильтр категории им не указ
    for nm, (_stamp, price, spp, buyer) in latest.items():
        cat, vendor = cats.get(nm, (UNCATEGORIZED, None))
        all_points.append((price, spp, cat))
        if category and cat != category:
            continue
        by_cat[cat].append((price, spp, buyer, nm, vendor))

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
        "thresholds": find_thresholds(all_points),
        "stats": {
            "source": source,
            "date_from": since.isoformat(),
            "date_to": until.isoformat(),
            "step": step,
            "points": len(latest),
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
