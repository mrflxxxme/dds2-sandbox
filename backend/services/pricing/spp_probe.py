# ruff: noqa: RUF001, RUF002, RUF003
"""Проба цены: единственное место, где DDS2 пишет цену в ВБ.

Зачем. Карта СПП знает только те уровни, на которых наши товары уже стоят. Про
цену, где своих товаров нет, сказать нечего — узнать можно, только поставив её и
посмотрев на витрину. Проба это и делает: ставит целевую цену, опрашивает
card-API, фиксирует, когда и как изменился СПП, и возвращает цену обратно.

Замер 2026-08-01 задал главный параметр: ВБ применил ступеньку через ~4 часа
после смены цены (товар 937180966 при неизменной цене 1499 ₽ получил СПП 6.3 %
→ 32.4 %). Поэтому пробе нужны часы, а не минуты; опрос — раз в несколько минут.

Что защищает:
  * снимок «до» пишется в журнал ДО первой записи в ВБ — откатывать есть чем
    даже если процесс упадёт;
  * возврат цены идёт в `finally`, и его провал попадает в журнал как ошибка;
  * цена ниже пола (себестоимость + расходы) не ставится вовсе;
  * шаг ограничен: дальше `MAX_STEP_PCT` от текущей цены не ходим;
  * одновременно по товару может идти только одна проба.
"""

import asyncio
import logging
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbPrice, WbSppProbe
from backend.utils.time import utcnow

logger = logging.getLogger("dds.pricing")

MAX_STEP_PCT = 0.35  # дальше 35 % от текущей цены проба не ходит
POLL_SEC = 300  # опрос витрины раз в 5 минут
HOLD_SEC = 4 * 3600  # держим цену 4 часа: столько занимала реакция ВБ в замере
MAX_HOLD_SEC = 12 * 3600


class ProbeRefused(ValueError):
    """Проба не запущена: нарушено одно из ограничений (пол цены, шаг, дубль)."""


async def _card_price(nm_id: int) -> float | None:
    """Цена клиента с витрины сейчас (None — если card-API не ответил)."""
    from backend.integrations.wb_card_api import fetch_card_prices

    got = await fetch_card_prices([nm_id])
    info = got.get(nm_id)
    return float(info["product"]) if info else None


def _spp(seller: float, buyer: float | None) -> float | None:
    if not buyer or seller <= 0:
        return None
    return round((1 - buyer / seller) * 100, 2)


async def start_probe(
    db: AsyncSession,
    project_id: int,
    nm_id: int,
    target_price: float,
    *,
    floor_price: float | None = None,
    hold_sec: int = HOLD_SEC,
) -> WbSppProbe:
    """Проверить ограничения, записать снимок «до» и создать строку журнала.

    Саму цену НЕ ставит — это делает `run_probe`, уже имея на руках запись, по
    которой можно откатиться. Разделено намеренно: между «решили» и «поставили»
    не должно быть состояния, о котором никто не знает.
    """
    if hold_sec > MAX_HOLD_SEC:
        raise ProbeRefused(f"Слишком долго держать цену: {hold_sec} с (максимум {MAX_HOLD_SEC})")

    running = (
        await db.execute(
            select(WbSppProbe).where(
                WbSppProbe.project_id == project_id,
                WbSppProbe.nm_id == nm_id,
                WbSppProbe.status == "RUNNING",
            )
        )
    ).scalars().first()
    if running:
        raise ProbeRefused(f"По товару {nm_id} уже идёт проба #{running.id}")

    price_row = (
        await db.execute(
            select(WbPrice).where(WbPrice.project_id == project_id, WbPrice.nm_id == nm_id)
        )
    ).scalars().first()
    if not price_row or not price_row.price:
        raise ProbeRefused(f"У товара {nm_id} нет синканой цены — сначала обновите цены")

    seller_now = float(price_row.price)
    step = abs(target_price - seller_now) / seller_now
    if step > MAX_STEP_PCT:
        raise ProbeRefused(
            f"Шаг {step * 100:.0f} % больше предела {MAX_STEP_PCT * 100:.0f} %: "
            f"{seller_now:.2f} → {target_price:.2f} ₽"
        )
    if floor_price is not None and target_price < floor_price:
        raise ProbeRefused(f"Цена {target_price:.2f} ₽ ниже пола {floor_price:.2f} ₽")

    buyer_before = await _card_price(nm_id)
    probe = WbSppProbe(
        project_id=project_id,
        nm_id=nm_id,
        status="RUNNING",
        base_price_before=price_row.base_price or Decimal(str(seller_now)),
        discount_before=price_row.discount or Decimal("0"),
        seller_price_before=Decimal(str(round(seller_now, 2))),
        buyer_price_before=Decimal(str(buyer_before)) if buyer_before else None,
        spp_before=Decimal(str(_spp(seller_now, buyer_before) or 0)) if buyer_before else None,
        target_price=Decimal(str(round(target_price, 2))),
        started_at=utcnow(),
    )
    db.add(probe)
    await db.commit()
    await db.refresh(probe)
    logger.info(
        "проба цены #%d: %d %.2f → %.2f ₽ (клиент сейчас %s)",
        probe.id, nm_id, seller_now, target_price, buyer_before,
    )
    return probe


def _price_and_discount(target: float, base_price: float, discount: float) -> tuple[int, int]:
    """Целевая цена витрины → (цена до скидки, скидка %) для API ВБ.

    ВБ принимает базу и скидку, а витрина = база × (1 − скидка). Базу не трогаем
    (её видит покупатель как зачёркнутую), подбираем скидку — так проба меняет
    ровно одно число и откат тоже одношаговый.
    """
    if base_price <= 0:
        return int(round(target)), 0
    disc = round((1 - target / base_price) * 100)
    disc = max(0, min(int(disc), 99))
    return int(round(base_price)), disc


async def run_probe(db: AsyncSession, probe: WbSppProbe, *, hold_sec: int = HOLD_SEC) -> WbSppProbe:
    """Поставить цену, дождаться реакции витрины, вернуть цену обратно.

    Возврат — в `finally`: даже если опрос упадёт или процесс отменят, цена
    уедет назад. Провал возврата пишется в журнал отдельно — это единственная
    ситуация, где после пробы остаётся чужая цена.
    """
    from backend.integrations.wb_api import WBApiClient
    from backend.services.integrations_service import _get_wb_key

    _key, api_key = await _get_wb_key(db, probe.project_id)
    client = WBApiClient(api_key, project_id=probe.project_id)

    target = float(probe.target_price)
    base = float(probe.base_price_before)
    price_arg, disc_arg = _price_and_discount(target, base, float(probe.discount_before))
    buyer_before = float(probe.buyer_price_before) if probe.buyer_price_before else None
    started: datetime = probe.started_at

    try:
        await client.set_price(probe.nm_id, price_arg, disc_arg)
        await db.commit()  # не держим транзакцию через долгое ожидание

        deadline = asyncio.get_running_loop().time() + hold_sec
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(POLL_SEC)
            probe.polls += 1
            buyer_now = await _card_price(probe.nm_id)
            if buyer_now is None:
                continue
            probe.buyer_price_after = Decimal(str(buyer_now))
            probe.seller_price_after = Decimal(str(round(target, 2)))
            spp_now = _spp(target, buyer_now)
            probe.spp_after = Decimal(str(spp_now)) if spp_now is not None else None
            await db.commit()
            # витрина сдвинулась — цель достигнута, дальше держать цену незачем
            if buyer_before is None or abs(buyer_now - buyer_before) > 1:
                probe.reacted_after_sec = int((utcnow() - started).total_seconds())
                await db.commit()
                break

        probe.status = "OK"
    except asyncio.CancelledError:
        probe.status = "CANCELLED"
        raise
    except Exception as e:  # noqa: BLE001 — в журнал, не наружу: цену всё равно вернём
        probe.status = "ERROR"
        probe.error = str(e)[:1000]
        logger.error("проба #%d: %s", probe.id, e)
    finally:
        try:
            back_price, back_disc = _price_and_discount(
                float(probe.seller_price_before), base, float(probe.discount_before)
            )
            await client.set_price(probe.nm_id, back_price, back_disc)
            probe.reverted = True
        except Exception as e:  # noqa: BLE001
            probe.reverted = False
            probe.error = (probe.error or "") + f" | ЦЕНА НЕ ВОЗВРАЩЕНА: {e}"[:500]
            probe.status = "ERROR"
            logger.error("проба #%d: цена НЕ возвращена — %s", probe.id, e)
        probe.finished_at = utcnow()
        await db.commit()

    return probe
