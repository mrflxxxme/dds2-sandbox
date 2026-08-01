# ruff: noqa: RUF001, RUF002, RUF003
"""«Ступеньки СПП»: детектор порогов, своя история товара, юнит-математика.

Главный охраняемый инвариант — детектор НЕ верит кросс-секции. На живых данных
2026-08-01 сравнение «медиана СПП ниже 2000 ₽ против выше» давало +11 п.п., а
парный тест по 74 товарам, реально переходившим порог, — −0.4 п.п.: разница была
в составе товаров, а не в цене. Тест `test_detect_steps_rejects_mix_confound`
держит именно этот случай.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from backend.models import WbPrice, WbSppObservation
from backend.models.wb_order import WbOrder
from backend.services.pricing.spp_ladder import (
    Level,
    Point,
    Step,
    _spp_of,
    _upsert_points,
    backfill_from_orders,
    best_own_level,
    current_level,
    daily_background,
    detect_steps,
    evaluate,
    guard_step,
    load_points,
    nearest_step_below,
    own_jump,
    own_levels,
    record_card_points,
    target_price_for,
)

D1 = date(2026, 7, 1)


def _pt(nm, day_offset, price, spp, buyer=None):
    day = D1 + timedelta(days=day_offset)
    return Point(
        nm_id=nm,
        day=day,
        seller_price=price,
        spp_rate=spp,
        buyer_price=buyer if buyer is not None else round(price * (1 - spp / 100), 2),
    )


class TestBackground:
    def test_median_per_day(self):
        pts = [_pt(1, 0, 1000, 20), _pt(2, 0, 1000, 30), _pt(3, 0, 1000, 40), _pt(1, 1, 1000, 10)]
        bg = daily_background(pts)
        assert bg[D1] == 30
        assert bg[D1 + timedelta(days=1)] == 10

    def test_drift_alone_makes_no_step(self):
        """Общий подъём СПП по всему портфелю — это фон, а не ступенька цены."""
        pts = []
        for d in range(20):
            for nm in range(10):
                # цена у каждого товара своя и не меняется, СПП растёт у всех сразу
                pts.append(_pt(nm, d, 1800 + nm * 50, 20 + d))
        assert detect_steps(pts) == []


class TestDetectSteps:
    def _crossing(self, n_products=8, spp_below=33.0, spp_above=25.0):
        """Товары, реально переходившие порог 2000 ₽, + контрольная группа.

        Контроль обязателен: фон дня считается по всему портфелю, и если ВЕСЬ
        портфель переезжает в один день, это по определению фон, а не эффект цены.
        """
        pts = [_pt(9000 + nm, d, 3000, 25.0) for nm in range(20) for d in range(12)]
        for nm in range(n_products):
            for d in range(6):
                pts.append(_pt(nm, d, 2100, spp_above))
                pts.append(_pt(nm, d + 6, 1950, spp_below))
        return pts

    def test_paired_evidence_confirms(self):
        steps = detect_steps(self._crossing())
        assert [s.threshold for s in steps] == [2000.0]
        assert steps[0].jump == pytest.approx(8.0, abs=0.5)
        assert steps[0].n_products == 8
        assert steps[0].agree_pct == 100.0

    def test_rejects_mix_confound(self):
        """Дешёвые товары с высоким СПП + дорогие с низким, но никто не переходил.

        Ровно то, что дала кросс-секция на пороге 2000 ₽ на живых данных.
        """
        pts = []
        for nm in range(10):  # вечно ниже порога, СПП высокий
            for d in range(12):
                pts.append(_pt(1000 + nm, d, 1950, 37))
        for nm in range(10):  # вечно выше порога, СПП низкий
            for d in range(12):
                pts.append(_pt(2000 + nm, d, 2100, 26))
        assert detect_steps(pts) == []

    def test_needs_enough_products(self):
        assert detect_steps(self._crossing(n_products=3)) == []

    def test_needs_agreement(self):
        """Половина товаров скачет, половина — наоборот: порогу не верим."""
        pts = self._crossing(n_products=5)
        for nm in range(5, 10):  # столько же товаров с обратным поведением
            for d in range(6):
                pts.append(_pt(nm, d, 2100, 33))
                pts.append(_pt(nm, d + 6, 1950, 25))
        assert detect_steps(pts) == []

    def test_own_jump_needs_both_sides(self):
        pts = [_pt(1, d, 1950, 35) for d in range(5)]
        assert own_jump(pts, 2000.0, daily_background(pts)) is None


class TestStepSelection:
    STEPS = [
        Step(threshold=2000, spp_below=5, spp_above=-3, jump=8, n_below=50, n_above=50, n_products=9),
        Step(threshold=5000, spp_below=4, spp_above=0, jump=4, n_below=30, n_above=30, n_products=9),
    ]

    def test_nearest_below_within_reach(self):
        assert nearest_step_below(2100, self.STEPS).threshold == 2000

    def test_too_far_is_not_offered(self):
        assert nearest_step_below(2500, self.STEPS) is None  # −20 % при лимите 10 %

    def test_guard_when_sitting_under_threshold(self):
        assert guard_step(1990, self.STEPS).threshold == 2000
        assert guard_step(1700, self.STEPS) is None

    def test_target_is_rouble_under(self):
        assert target_price_for(2000) == 1999


class TestOwnHistory:
    """Уровни цены одного товара. Фон дня передаём явно (`{}` = без нормировки),
    иначе на выборке из одного товара фон вырождается в его же СПП."""

    def _levels(self):
        pts = [_pt(1, d, 2230, 30) for d in range(6)] + [_pt(1, d + 6, 1990, 40) for d in range(6)]
        return own_levels(pts, {})

    def test_levels_bucketed_with_medians(self):
        lv = self._levels()
        assert [x.price for x in lv] == [2000.0, 2225.0]
        assert lv[0].spp == 40 and lv[1].spp == 30

    def test_current_level_matches_price(self):
        assert current_level(self._levels(), 2230).price == 2225.0
        assert current_level(self._levels(), 1500) is None

    def test_best_own_level_prefers_cheapest_sacrifice(self):
        """Из подходящих уровней берём самый дорогой — отдаём меньше своей цены."""
        pts = (
            [_pt(1, d, 2200, 30) for d in range(6)]
            + [_pt(1, d + 6, 2100, 36) for d in range(6)]
            + [_pt(1, d + 12, 2050, 38) for d in range(6)]
        )
        best = best_own_level(own_levels(pts, {}), 2200)
        assert best is not None
        level, jump = best
        assert level.price == 2100.0  # не 2050: тот же эффект дешевле не нужен
        assert jump == pytest.approx(6.0, abs=0.5)

    def test_background_neutralizes_global_promo(self):
        """Цена упала одновременно с общей акцией ВБ — заслуги цены тут нет."""
        pts = [_pt(1, d, 2200, 30) for d in range(6)] + [_pt(1, d + 6, 2100, 38) for d in range(6)]
        # весь портфель в те же дни поднялся на те же 8 п.п. → это фон
        pts += [_pt(500 + nm, d, 3000, 30) for nm in range(10) for d in range(6)]
        pts += [_pt(500 + nm, d + 6, 3000, 38) for nm in range(10) for d in range(6)]
        own = [p for p in pts if p.nm_id == 1]
        assert best_own_level(own_levels(own, daily_background(pts)), 2200) is None

    def test_no_level_without_current_anchor(self):
        """Нет своих точек на текущей цене — сравнивать не с чем."""
        lv = [Level(price=1500, rel_spp=10, spp=40, n=9, last_day=D1)]
        assert best_own_level(lv, 2200) is None

    def test_small_jump_is_not_a_step(self):
        pts = [_pt(1, d, 2200, 30) for d in range(6)] + [_pt(1, d + 6, 2100, 30.5) for d in range(6)]
        assert best_own_level(own_levels(pts, daily_background(pts)), 2200) is None


class TestEvaluate:
    def test_leverage_math(self):
        ev = evaluate(current_price=2010, current_spp=26.0, current_buyer=1487.4, target=1999, jump=11.0)
        assert ev["target_spp"] == 37.0
        assert ev["target_buyer_price"] == pytest.approx(1259.37, abs=0.01)
        assert ev["drop_seller"] == 11.0
        assert ev["drop_buyer"] == pytest.approx(228.03, abs=0.01)
        assert ev["leverage"] == pytest.approx(20.7, abs=0.2)

    def test_buyer_price_derived_when_missing(self):
        ev = evaluate(current_price=1000, current_spp=20.0, current_buyer=None, target=900, jump=0.0)
        assert ev["drop_buyer"] == pytest.approx(80.0, abs=0.01)  # 800 − 720
        assert ev["leverage"] == pytest.approx(0.8, abs=0.01)  # рычага нет — обычная скидка

    def test_spp_clamped(self):
        ev = evaluate(current_price=1000, current_spp=85.0, current_buyer=150.0, target=900, jump=20.0)
        assert ev["target_spp"] == 90.0


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
