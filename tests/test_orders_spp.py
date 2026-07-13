"""
Тест агрегации СПП по дням из отчёта «Заказы» (get_orders_spp_map).

СПП товара за день = среднее spp по его заказам за тот МСК-день; в ответе — доля (spp/100).
"""

from datetime import date, datetime

import pytz

from backend.models.wb_order import WbOrder
from backend.services.funnel.orders_spp import get_orders_spp_map

_MSK = pytz.timezone("Europe/Moscow")
NM = 900900900


def _order(project_id, srid, nm, day_msk, spp):
    return WbOrder(
        project_id=project_id, srid=srid, nm_id=nm,
        order_date=_MSK.localize(datetime(day_msk.year, day_msk.month, day_msk.day, 14, 0, 0)),
        spp=spp,
    )


async def test_orders_spp_daily_average(db_session, project):
    d = date(2026, 7, 10)
    db_session.add_all([
        _order(project.id, "s1", NM, d, 35),
        _order(project.id, "s2", NM, d, 40),
        _order(project.id, "s3", NM, d, 45),
        _order(project.id, "s4", NM, date(2026, 7, 9), 20),  # другой день
    ])
    await db_session.commit()
    m = await get_orders_spp_map(db_session, project.id, lookback_days=400)
    assert abs(m.get(NM, d).spp_rate - 0.40) < 1e-6      # (35+40+45)/3 = 40% → 0.40
    assert abs(m.get(NM, date(2026, 7, 9)).spp_rate - 0.20) < 1e-6
    assert m.get(NM, date(2026, 7, 1)) is None           # нет заказов в этот день


async def test_orders_spp_project_isolation(db_session, project, other_project):
    d = date(2026, 7, 10)
    db_session.add(_order(project.id, "iso1", NM, d, 50))
    await db_session.commit()
    m = await get_orders_spp_map(db_session, other_project.id, lookback_days=400)
    assert not m  # у другого проекта заказов нет
