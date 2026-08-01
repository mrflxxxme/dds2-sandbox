# ruff: noqa: RUF001, RUF002, RUF003
"""Карта СПП: лесенка уровней внутри категории и обрывы между ними.

Главное, что здесь охраняется, — смысл «обрыва»: это пара СОСЕДНИХ уровней, где
СПП рушится вверх по цене, а не любая разница между дешёвыми и дорогими
товарами. Сравнение разных категорий на живых данных 2026-08-01 давало ложный
порог 2000 ₽ (+11 п.п.), которого при проверке по одному товару не было; внутри
одной категории тот же обрыв подтвердился и ручным замером (2185 → 1999 подняло
СПП с 26.4 % до 36.8 %).
"""

from backend.services.pricing.spp_map import (
    Level,
    build_levels,
    coverage_gaps,
    find_cliffs,
)


def _pts(*triples):
    """(цена, СПП) → точки с посчитанной ценой клиента."""
    return [(p, s, round(p * (1 - s / 100), 2)) for p, s in triples]


class TestBuildLevels:
    def test_buckets_by_step_and_takes_median(self):
        lv = build_levels(_pts((1980, 36.8), (2010, 36.8), (2020, 30.0), (2210, 24.8)), step=100)
        assert [x.price for x in lv] == [2000.0, 2200.0]
        assert lv[0].spp == 36.8  # медиана 30.0 / 36.8 / 36.8
        assert lv[0].n == 3

    def test_spread_is_kept(self):
        """Разброс на уровне — сигнал, что цена не единственный фактор СПП."""
        lv = build_levels(_pts((1500, 6.3), (1510, 32.4), (1490, 32.4)), step=100)
        assert lv[0].spp_min == 6.3
        assert lv[0].spp_max == 32.4

    def test_empty(self):
        assert build_levels([], step=100) == []


class TestCliffs:
    def _lv(self, price, spp, n=5):
        return Level(
            price=price, spp=spp, spp_min=spp, spp_max=spp,
            buyer_price=round(price * (1 - spp / 100)), n=n,
        )

    def test_finds_drop_between_neighbours(self):
        cliffs = find_cliffs([self._lv(2000, 36.8), self._lv(2100, 24.8)])
        assert len(cliffs) == 1
        c = cliffs[0]
        assert c["keep_below"] == 2000 and c["breaks_at"] == 2100
        assert c["drop"] == 12.0
        assert c["seller_gives"] == 100  # уступаем 100 ₽
        assert c["buyer_gains"] == 315  # 1579 − 1264
        assert c["leverage"] == 3.1  # 315 / 100

    def test_small_drop_is_not_a_cliff(self):
        assert find_cliffs([self._lv(2000, 26.0), self._lv(2100, 24.8)]) == []

    def test_growth_upwards_is_not_a_cliff(self):
        """СПП выше на дорогом уровне — это не «дороже нельзя»."""
        assert find_cliffs([self._lv(2000, 20.0), self._lv(2100, 32.0)]) == []

    def test_only_adjacent_levels_compared(self):
        """Дешёвый уровень с высоким СПП и дорогой с низким через пропуск — не обрыв."""
        levels = [self._lv(1000, 36.0), self._lv(1100, 35.0), self._lv(1200, 34.5)]
        assert find_cliffs(levels) == []

    def test_several_cliffs_in_one_category(self):
        levels = [self._lv(1500, 36.8), self._lv(1600, 24.8), self._lv(1700, 24.5), self._lv(1800, 10.8)]
        assert [c["breaks_at"] for c in find_cliffs(levels)] == [1600, 1800]


class TestGaps:
    def _lv(self, price):
        return Level(price=price, spp=30, spp_min=30, spp_max=30, buyer_price=price * 0.7, n=3)

    def test_reports_grid_levels_without_items(self):
        gaps = coverage_gaps([self._lv(999), self._lv(2000)])
        assert 1499 in gaps  # внутри диапазона и своих товаров там нет
        assert 999 not in gaps  # свой уровень рядом
        assert 5999 not in gaps  # вне диапазона категории — не наша забота

    def test_no_levels_no_gaps(self):
        assert coverage_gaps([]) == []
