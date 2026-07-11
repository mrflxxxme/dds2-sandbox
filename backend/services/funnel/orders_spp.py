"""
Средний СПП по дням из отчёта «Заказы» (wb_orders) — для метрики «Цена Клиенту».

СПП в wb_orders хранится ПОШТУЧНО: 1 строка = 1 заказ, поле `spp` (%). Средний СПП товара
за день = AVG(spp) по его заказам за тот МСК-день (пример: заказы 35/40/45% → 40%). Отдаём в
форме BdrRatesLookup (spp_rate — доля 0..0.95), чтобы drop-in подставлялось в
get_campaign_metrics вместо финансового СПП (тот же интерфейс `.get(nm_id, date).spp_rate`).

Заказы обновляются каждые ~30 мин, хранятся 90 дней — источник свежее и полнее финотчёта.
Нет заказов за день → нет СПП → «Цена Клиенту» = сама цена.
"""

from datetime import datetime, time, timedelta

import pytz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.wb_order import WbOrder
from backend.services.funnel.bdr_rates import BdrRates, BdrRatesLookup
from backend.utils.time import utcnow

_MSK = pytz.timezone("Europe/Moscow")


async def get_orders_spp_map(
    db: AsyncSession, project_id: int, lookback_days: int = 120
) -> BdrRatesLookup:
    """{(nm_id, МСК-день): BdrRates(spp_rate=доля)} — средний СПП товара за день из заказов."""
    today_msk = pytz.UTC.localize(utcnow()).astimezone(_MSK).date()
    since_dt = _MSK.localize(datetime.combine(today_msk - timedelta(days=lookback_days), time.min))

    O = WbOrder
    day = func.date(func.timezone("Europe/Moscow", O.order_date))  # МСК-календарный день заказа
    rows = (
        await db.execute(
            select(O.nm_id, day.label("d"), func.avg(O.spp).label("spp"))
            .where(
                O.project_id == project_id,
                O.spp.isnot(None),
                O.order_date >= since_dt,  # по индексу ix_wb_orders_project_nm_date
            )
            .group_by(O.nm_id, day)
        )
    ).all()

    daily: dict[tuple[int, object], BdrRates] = {}
    for nm_id, d, spp in rows:
        if nm_id is None or d is None or spp is None:
            continue
        rate = max(0.0, min(float(spp) / 100.0, 0.95))  # % → доля, зажим как у финансового СПП
        daily[(int(nm_id), d)] = BdrRates(to_pay_rate=0.0, spp_rate=rate, buyout_pct=0.0)
    return BdrRatesLookup(daily, {})
