# ruff: noqa: RUF001, RUF002, RUF003
"""Точки наблюдения СПП: запись снимка витрины и ретро из заказов.

Ключевое, что охраняют тесты: источники `card` и `orders` не смешиваются
(обезличенный СПП витрины против СПП заказа с кошельком покупателя — на живом
портфеле разница 12.1 п.п.), внутри дня в точку идёт медиана, а не каждый заказ,
и точка не пишется, если витрина ушла вперёд нашего снимка цен.
"""

from datetime import date
from decimal import Decimal

import pytest

from backend.models import WbPrice, WbSppObservation
from backend.models.wb_order import WbOrder
from backend.services.pricing.spp_points import (
    _spp_of,
    _upsert_points,
    backfill_from_orders,
    load_points,
    record_card_points,
)

D1 = date(2026, 7, 1)


class TestSppOf:
    def test_valid(self):
        assert _spp_of(2000, 1500) == 25.0

    @pytest.mark.parametrize("seller,buyer", [(0, 100), (100, 0), (100, 200), (1000, 10)])
    def test_nonsense_rejected(self, seller, buyer):
        assert _spp_of(seller, buyer) is None


@pytest.mark.asyncio
class TestPersistence:
    async def _obs(self, db, project_id):
        rows = (await db.execute(WbSppObservation.__table__.select())).mappings().all()
        return [r for r in rows if r["project_id"] == project_id]

    async def test_upsert_is_idempotent(self, db_session, project):
        point = {
            "nm_id": 777001,
            "observed_on": D1,
            "source": "card",
            "seller_price": Decimal("1999.00"),
            "buyer_price": Decimal("1263.00"),
            "spp_rate": Decimal("36.82"),
            "obs_count": 1,
        }
        await _upsert_points(db_session, project.id, [point, dict(point)])  # дубль в батче
        await _upsert_points(db_session, project.id, [{**point, "buyer_price": Decimal("1200.00")}])

        rows = [r for r in await self._obs(db_session, project.id) if r["nm_id"] == 777001]
        assert len(rows) == 1
        assert rows[0]["buyer_price"] == Decimal("1200.00")  # перезаписали, не размножили

    async def test_points_isolated_by_project(self, db_session, project, other_project):
        point = {
            "nm_id": 777002,
            "observed_on": D1,
            "source": "card",
            "seller_price": Decimal("1000.00"),
            "buyer_price": Decimal("800.00"),
            "spp_rate": Decimal("20.00"),
            "obs_count": 1,
        }
        await _upsert_points(db_session, project.id, [point])
        await _upsert_points(db_session, other_project.id, [point])

        mine = [p for p in await load_points(db_session, project.id, days=3650, source="card") if p.nm_id == 777002]
        theirs = [p for p in await load_points(db_session, other_project.id, days=3650, source="card") if p.nm_id == 777002]
        assert len(mine) == 1 and len(theirs) == 1

    async def test_backfill_takes_median_of_day(self, db_session, project):
        """Внутри дня СПП гуляет по покупателям — в точку идёт медиана, не каждый заказ."""
        from backend.utils.time import utcnow

        day = utcnow()
        for i, spp in enumerate((20, 30, 40)):
            db_session.add(
                WbOrder(
                    project_id=project.id,
                    srid=f"spp-test-{i}",
                    order_date=day,
                    nm_id=777003,
                    spp=spp,
                    price_with_disc=Decimal("2000.00"),
                    finished_price=Decimal(str(2000 * (1 - spp / 100))),
                    is_cancel=False,
                )
            )
        await db_session.commit()

        await backfill_from_orders(db_session, project.id, days=3)
        pts = [p for p in await load_points(db_session, project.id, days=3) if p.nm_id == 777003]
        assert len(pts) == 1
        assert pts[0].spp_rate == pytest.approx(30.0, abs=0.01)
        assert pts[0].weight == 3

    async def test_cancelled_orders_ignored(self, db_session, project):
        from backend.utils.time import utcnow

        db_session.add(
            WbOrder(
                project_id=project.id,
                srid="spp-test-cancel",
                order_date=utcnow(),
                nm_id=777004,
                spp=50,
                price_with_disc=Decimal("1000.00"),
                finished_price=Decimal("500.00"),
                is_cancel=True,
            )
        )
        await db_session.commit()

        await backfill_from_orders(db_session, project.id, days=3)
        assert not [p for p in await load_points(db_session, project.id, days=3) if p.nm_id == 777004]

    async def test_card_point_skipped_when_showcase_moved(self, db_session, project):
        """Витрина уехала вперёд нашего синка цен → точка была бы посчитана от старой цены."""
        db_session.add(
            WbPrice(
                project_id=project.id,
                nm_id=777005,
                base_price=Decimal("2500.00"),
                price=Decimal("2000.00"),
                discount=Decimal("20.00"),
                currency="RUB",
            )
        )
        await db_session.commit()

        res = await record_card_points(
            db_session, project.id, {777005: {"product": 1400.0, "basic": 3000.0}}
        )
        assert res["stale"] == 1
        assert not [p for p in await load_points(db_session, project.id, days=3, source="card") if p.nm_id == 777005]

        ok = await record_card_points(
            db_session, project.id, {777005: {"product": 1400.0, "basic": 2500.0}}
        )
        assert ok["written"] == 1
        pt = [p for p in await load_points(db_session, project.id, days=3, source="card") if p.nm_id == 777005][0]
        assert pt.spp_rate == pytest.approx(30.0, abs=0.01)  # 1 − 1400/2000
