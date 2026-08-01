# ruff: noqa: RUF001, RUF002, RUF003
"""План прогонов цены: куда ставить пробы, чтобы узнать то, чего ещё не знаем.

Карта СПП видит ровно те цены, на которых наши товары уже стоят. Всё между ними —
белое пятно: порог ВБ может быть там, а может и не быть, и отличить одно от
другого нельзя, пока туда кто-нибудь не встанет. Этот модуль отвечает на вопрос
«какую цену поставить следующей», выбирая ходы по одному признаку — сколько
неизвестности они снимают.

Три вида целей, по убыванию ценности:
  * `narrow` — сузить ИЗВЕСТНЫЙ порог. Мы уже знаем, что между 4726.8 и 4999.02 ₽
    СПП скачет с 24.8 % до 36.8 %, но где именно проходит черта — не знаем.
    Проба в середине зазора делит его пополам: 272 ₽ → 136 → 68 → … Порог
    локализуется до рубля за ~8 проб вместо 272 сплошным перебором.
  * `grid` — «психологическая» цена (1999, 2999, 4999…) внутри белого пятна.
    Пороги ВБ садятся именно на такие числа, поэтому пятно с круглой ценой
    внутри проверяем не серединой, а самой этой ценой.
  * `explore` — просто широкое белое пятно. Проба посередине.

План НЕ хранится: он каждый раз пересчитывается из накопленных наблюдений.
Поэтому результат пробы автоматически меняет следующий шаг — отдельного
состояния сканирования, которое может рассинхронизироваться, не существует.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbSppProbe
from backend.services.pricing.spp_map import GRID
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

SCAN_MIN_GAP_RUB = 50.0  # пятно уже 50 ₽ — там уже нечего искать
SCAN_MIN_GAP_PCT = 0.03  # …или уже 3 % цены, что больше
SCAN_MAX_STEP_PCT = 0.35  # дальше проба не ходит (зеркало spp_probe.MAX_STEP_PCT)
SCAN_NEAR_PCT = 0.10  # «рядом с целью» — в пределах 10 % цены
SCAN_TOP = 40  # длина очереди: дальше планировать бессмысленно, данные изменятся
SCAN_NARROW_MIN_GAP = 3.0  # порог уже локализован до пары рублей — 4 часа пробы того не стоят

#: Рамки безопасности для маржи (канон Дениса 2026-08-01). Вниз ходим коротко —
#: там теряется маржа на каждом заказе; вверх можно дальше — там теряются заказы,
#: но не деньги с них. Эти же числа продублированы гардом в `spp_probe`.
SCAN_MAX_DOWN_RUB = 300.0
SCAN_MAX_UP_RUB = 1000.0

#: Копейки в пробной цене обязательны: ровное «1999.00» в данных не отличить от
#: обычной цены товара, а с хвостом видно, что это была проба.
PROBE_KOPECKS = 0.14


def with_kopecks(price: float, kopecks: float = PROBE_KOPECKS) -> float:
    """Цена пробы всегда с дробной частью: целые рубли не ставим принципиально."""
    return round(int(price) + kopecks, 2)


def _donor(
    target: float,
    points: list[tuple],
    max_step_pct: float,
    max_down: float = SCAN_MAX_DOWN_RUB,
    max_up: float = SCAN_MAX_UP_RUB,
) -> dict | None:
    """Кем пробовать цель: артикул, которому до неё ближе всех идти.

    Двигаем того, кто и так стоит рядом: меньше шаг — меньше и риск потерять
    заказы, пока проба висит. Ограничений два, и оба обязательны: рублёвое окно
    маржи (−`max_down` / +`max_up`) и процентный предел шага. Цель, до которой ни
    один артикул не дотягивается в этих рамках, в очередь не попадает вовсе.
    """
    best = None
    for price, _spp, cat, nm, vendor in points:
        if price <= 0:
            continue
        delta = target - price
        if delta < -max_down or delta > max_up:
            continue
        step = abs(delta) / price
        if step > max_step_pct:
            continue
        if best is None or abs(delta) < best[0]:
            best = (abs(delta), {"nm_id": nm, "vendor_code": vendor, "category": cat,
                                 "price": round(price, 2), "delta": round(delta, 2),
                                 "step_pct": round(step * 100, 1)})
    return best[1] if best else None


def _nearby(target: float, prices: list[float], pct: float = SCAN_NEAR_PCT) -> int:
    """Сколько наших артикулов стоит рядом с целью — кому этот участок вообще важен."""
    return sum(1 for p in prices if abs(p - target) <= target * pct)


def plan_probes(
    points: list[tuple],
    thresholds: list[dict],
    *,
    grid: tuple[int, ...] = GRID,
    top: int = SCAN_TOP,
    max_step_pct: float = SCAN_MAX_STEP_PCT,
    min_gap_rub: float = SCAN_MIN_GAP_RUB,
    min_gap_pct: float = SCAN_MIN_GAP_PCT,
) -> list[dict]:
    """[(цена, СПП, категория, nm_id, артикул)] + пороги → очередь проб.

    Возвращает цели, отсортированные по снимаемой неизвестности: сперва сужение
    известных порогов, потом круглые цены в пятнах, потом сами пятна.
    """
    if not points:
        return []
    prices = sorted(p[0] for p in points)
    lo, hi = prices[0], prices[-1]
    seen: set[int] = set()  # цель в рублях — чтобы narrow и explore не дублировались
    out: list[dict] = []

    def add(kind: str, target: float, gap: float, why: str, rank: float) -> None:
        key = int(round(target))
        if key in seen or not (lo <= target <= hi):
            return
        price = with_kopecks(key)
        donor = _donor(price, points, max_step_pct)
        if donor is None:
            return  # некем пробовать: ближайший артикул вне окна маржи или шага
        seen.add(key)
        out.append({
            "kind": kind,
            "price": price,
            "gap_before": round(gap, 2),
            "gap_after": round(gap / 2, 2),
            "why": why,
            "nearby": _nearby(target, prices),
            "donor": donor,
            "rank": rank,
        })

    # 1. Сузить известные пороги — тут неизвестность точно есть, и она измерима
    for t in thresholds:
        gap = t["from_price"] - t["up_to"]
        if gap < SCAN_NARROW_MIN_GAP:
            continue
        add(
            "narrow",
            (t["up_to"] + t["from_price"]) / 2,
            gap,
            f'порог между {t["up_to"]:.0f} и {t["from_price"]:.0f} ₽ '
            f'({t["spp_below"]:.1f}% → {t["spp_above"]:.1f}%): зазор {gap:.0f} ₽',
            rank=1_000_000 + gap,
        )

    # 2–3. Белые пятна на оси; круглая цена внутри пятна важнее его середины
    uniq = sorted(set(prices))
    for a, b in zip(uniq, uniq[1:], strict=False):
        gap = b - a
        if gap < max(min_gap_rub, a * min_gap_pct):
            continue
        near = [g for g in grid if a < g < b]
        if near:
            g = min(near, key=lambda x: abs(x - (a + b) / 2))
            add("grid", float(g), gap,
                f"круглая цена {g} ₽ в пустом промежутке {a:.0f}–{b:.0f} ₽ — "
                "пороги ВБ садятся именно на такие числа",
                rank=100_000 + gap)
        else:
            add("explore", (a + b) / 2, gap,
                f"между {a:.0f} и {b:.0f} ₽ наблюдений нет — порог может быть здесь",
                rank=gap)

    out.sort(key=lambda x: (-x["rank"], x["price"]))
    for row in out:
        row.pop("rank")
    return out[:top]


SCAN_REACTION_TOL_RUB = 1.0  # меньше рубля — это округление, а не реакция витрины
SCAN_BATCH_WAIT_SEC = 20 * 60  # сколько всего ждём реакции витрины на пачку
SCAN_POLL_SEC = 60  # как часто опрашиваем card-API
SCAN_FIRST_POLL_SEC = 45  # первый опрос раньше: обычно витрина отвечает за минуту
SCAN_MAX_BATCH = 40


def is_reaction(buyer_before: float, buyer_now: float, tol: float = SCAN_REACTION_TOL_RUB) -> bool:
    """Витрина пересчитана? Признак один — цена клиента сдвинулась в рублях.

    Пока она стоит на прежнем значении, ВБ ещё не применил новую цену, и любой
    вывод о СПП на ней будет ложью: получилось бы «на этой цене ступеньки нет»
    там, где её просто не успели показать.
    """
    return abs(buyer_now - buyer_before) >= tol


@dataclass
class _Running:
    """Проба, которой уже поставлена цена: что вернуть и с чем сравнивать."""

    probe: WbSppProbe
    target: float
    buyer_before: float


async def run_scan_batch(
    db: AsyncSession,
    project_id: int,
    targets: list[dict],
    *,
    max_wait_sec: int = SCAN_BATCH_WAIT_SEC,
    poll_sec: int = SCAN_POLL_SEC,
) -> dict:
    """Поставить всю пачку цен разом, дождаться реакции витрины и вернуть цены.

    Пачкой, а не по одной: цены независимы, а ждать реакции всё равно приходится
    всем вместе — два десятка проб подряд заняли бы сутки, пачкой это минуты.

    Реакцией считаем ИЗМЕНЕНИЕ цены клиента в рублях. Если она не двинулась,
    значит ВБ ещё не пересчитал витрину, и наблюдение НЕ пишется: записать старый
    СПП рядом с новой ценой хуже, чем не записать ничего — это выглядело бы как
    честное «здесь ступеньки нет».

    Возврат цен — в `finally` и поштучно сразу после реакции: держать пробную
    цену дольше, чем нужно для замера, незачем.
    """
    import asyncio

    from backend.integrations.wb_api import WBApiClient
    from backend.integrations.wb_card_api import fetch_card_prices
    from backend.services.integrations_service import _get_wb_key
    from backend.services.pricing.spp_probe import (
        ProbeRefused,
        _price_and_discount,
        record_observation,
        start_probe,
    )

    _key, api_key = await _get_wb_key(db, project_id)
    client = WBApiClient(api_key, project_id=project_id)

    started: list[_Running] = []
    refused: list[dict] = []
    for t in targets[:SCAN_MAX_BATCH]:
        try:
            probe = await start_probe(db, project_id, int(t["nm_id"]), float(t["price"]))
            started.append(
                _Running(probe, float(t["price"]), float(probe.buyer_price_before or 0))
            )
        except ProbeRefused as e:
            refused.append({"nm_id": t["nm_id"], "price": t["price"], "reason": str(e)})

    applied: list[_Running] = []  # цены, реально ушедшие в ВБ, — их обязательно вернуть
    reacted: list[dict] = []
    errors: list[dict] = []
    try:
        for row in started:
            try:
                price_arg, disc_arg = _price_and_discount(
                    row.target,
                    float(row.probe.base_price_before),
                    float(row.probe.discount_before),
                    prefer_kopecks=True,
                )
                await client.set_price(row.probe.nm_id, price_arg, disc_arg)
                applied.append(row)
            except Exception as e:  # noqa: BLE001 — одна не ставшая цена не рушит пачку
                row.probe.status = "ERROR"
                row.probe.error = str(e)[:1000]
                row.probe.reverted = True  # до ВБ не дошло — возвращать нечего
                row.probe.finished_at = utcnow()
                errors.append({"nm_id": row.probe.nm_id, "error": str(e)[:200]})
        applied_count = len(applied)
        await db.commit()

        waiting = list(applied)
        waited = 0
        delay = SCAN_FIRST_POLL_SEC
        while waiting and waited < max_wait_sec:
            await asyncio.sleep(delay)
            waited += delay
            delay = poll_sec
            got = await fetch_card_prices([r.probe.nm_id for r in waiting])
            for row in list(waiting):
                info = got.get(row.probe.nm_id)
                if not info:
                    continue
                buyer_now = float(info["product"])
                row.probe.polls += 1
                if not is_reaction(row.buyer_before, buyer_now):
                    continue  # витрина ещё не пересчитана — ждём дальше
                spp_now = round((1 - buyer_now / row.target) * 100, 2)
                row.probe.buyer_price_after = Decimal(str(buyer_now))
                row.probe.seller_price_after = Decimal(str(round(row.target, 2)))
                row.probe.spp_after = Decimal(str(spp_now))
                row.probe.reacted_after_sec = waited
                row.probe.status = "OK"
                await record_observation(db, project_id, row.probe.nm_id, row.target, buyer_now)
                reacted.append({
                    "nm_id": row.probe.nm_id,
                    "price": round(row.target, 2),
                    "buyer_before": row.buyer_before,
                    "buyer_after": buyer_now,
                    "spp": spp_now,
                    "after_sec": waited,
                })
                waiting.remove(row)
                await _revert(client, row, applied)
            await db.commit()

        for row in list(waiting):  # не дождались — цену назад, наблюдение не пишем
            row.probe.status = "NO_REACTION"
            await _revert(client, row, applied)
        await db.commit()
    finally:
        # по КОПИИ: _revert вычёркивает строку из `applied`, и обход самого списка
        # пропускал бы каждую вторую невозвращённую цену
        for row in list(applied):
            await _revert(client, row, applied)
        await db.commit()

    return {
        "launched": len(started),
        "applied": applied_count,
        "reacted": reacted,
        "no_reaction": [r.probe.nm_id for r in started if r.probe.status == "NO_REACTION"],
        "refused": refused,
        "errors": errors,
        "waited_sec": waited if started else 0,
    }


async def _revert(client: Any, row: _Running, applied: list[_Running]) -> None:
    """Вернуть цену пробы и вычеркнуть её из списка «поставленных»."""
    from backend.services.pricing.spp_probe import _price_and_discount

    if row not in applied:
        return  # уже возвращена
    applied.remove(row)
    probe = row.probe
    try:
        back_price, back_disc = _price_and_discount(
            float(probe.seller_price_before),
            float(probe.base_price_before),
            float(probe.discount_before),
        )
        await client.set_price(probe.nm_id, back_price, back_disc)
        probe.reverted = True
    except Exception as e:  # noqa: BLE001
        probe.reverted = False
        probe.status = "ERROR"
        probe.error = (probe.error or "") + f" | ЦЕНА НЕ ВОЗВРАЩЕНА: {e}"[:500]
        logger.error("проба #%d: цена НЕ возвращена — %s", probe.id, e)
    probe.finished_at = utcnow()


async def get_scan_plan(
    db: AsyncSession, project_id: int, *, date_from: str | None = None, date_to: str | None = None, top: int = SCAN_TOP
) -> dict:
    """План прогонов по живым данным проекта: очередь целей + во что она обойдётся."""
    from backend.services.pricing import spp_map as spp_map_service
    from backend.services.pricing.spp_probe import HOLD_SEC

    raw = await spp_map_service.get_spp_map(
        db, project_id, date_from=date_from, date_to=date_to, step=1
    )
    points = [
        (it["price"], it["spp"], c["category"], it["nm_id"], it["vendor_code"])
        for c in raw["categories"]
        for lv in c["levels"]
        for it in lv["items"]
    ]
    plan = plan_probes(points, raw["thresholds"], top=top)
    return {
        "plan": plan,
        "summary": scan_summary(plan, HOLD_SEC / 3600),
        "limits": {
            "max_down_rub": SCAN_MAX_DOWN_RUB,
            "max_up_rub": SCAN_MAX_UP_RUB,
            "max_step_pct": round(SCAN_MAX_STEP_PCT * 100),
            "hold_hours": round(HOLD_SEC / 3600, 1),
            "kopecks": PROBE_KOPECKS,
        },
        "thresholds": raw["thresholds"],
    }


def scan_summary(plan: list[dict], hold_hours: float) -> dict:
    """Во что обойдётся очередь: проб, часов подряд и сколько её можно распараллелить.

    Пробы по РАЗНЫМ артикулам идут одновременно (каждая занимает только свой
    товар), поэтому реальное время — это часы × длина очереди ÷ число доноров.
    """
    donors = {row["donor"]["nm_id"] for row in plan}
    return {
        "probes": len(plan),
        "donors": len(donors),
        "hours_sequential": round(len(plan) * hold_hours, 1),
        "hours_parallel": round(len(plan) * hold_hours / max(1, len(donors)), 1),
        "median_step_pct": round(median([row["donor"]["step_pct"] for row in plan]), 1)
        if plan else 0,
    }
