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
    cross_hints,
    find_cliffs,
    global_levels,
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


class TestCrossHints:
    """Подсказки по другим категориям: ступени ВБ живут в цене, а не в категории."""

    def _lv(self, price, spp, n=5):
        return Level(price=price, spp=spp, spp_min=spp, spp_max=spp,
                     buyer_price=round(price * (1 - spp / 100)), n=n)

    def test_suggests_going_down_when_client_wins_more(self):
        """Уступаем 200 ₽ — клиент выигрывает 390 ₽: это ступенька, а не скидка."""
        levels = [self._lv(2200, 24.8)]  # клиент платит 1654
        glob = {2000.0: {"spp": 36.8, "categories": ["Ковры", "Шторы"], "n": 40}}
        cross_hints(levels, glob)
        h = levels[0].hint_down
        assert h is not None
        assert h["price"] == 2000.0 and h["buyer_price"] == 1264
        assert h["leverage"] >= 1.2

    def test_plain_discount_is_not_a_hint(self):
        """СПП тот же: клиент выигрывает ровно нашу уступку минус СПП — молчим."""
        levels = [self._lv(2200, 24.8)]
        glob = {2000.0: {"spp": 24.8, "categories": ["Пледы"], "n": 12}}
        cross_hints(levels, glob)
        assert levels[0].hint_down is None

    def test_suggests_going_up_when_client_price_holds(self):
        """Главные деньги: нашу цену поднять можно, а клиент почти не заметит."""
        levels = [self._lv(2200, 24.8)]  # клиент платит 1654
        glob = {2600.0: {"spp": 36.0, "categories": ["Пледы"], "n": 12}}  # клиент 1664
        cross_hints(levels, glob)
        h = levels[0].hint_up
        assert h is not None
        assert h["price"] == 2600.0 and h["gain"] == 400
        assert abs(h["buyer_delta"]) <= 20

    def test_going_up_that_hurts_client_is_silent(self):
        levels = [self._lv(2200, 24.8)]
        glob = {2600.0: {"spp": 24.8, "categories": ["Пледы"], "n": 12}}  # клиент 1955
        cross_hints(levels, glob)
        assert levels[0].hint_up is None

    def test_own_level_beats_foreign(self):
        """Есть свои товары на этой цене — чужой опыт не навязываем."""
        levels = [self._lv(2000, 10.0), self._lv(2200, 24.8)]
        glob = {2000.0: {"spp": 36.8, "categories": ["Ковры"], "n": 40}}
        cross_hints(levels, glob)
        assert levels[1].hint_down is None

    def test_weak_foreign_level_is_ignored(self):
        levels = [self._lv(2200, 24.8)]
        glob = {2000.0: {"spp": 36.8, "categories": ["Палатки"], "n": 2}}  # два товара — не ориентир
        cross_hints(levels, glob)
        assert levels[0].hint_down is None

    def test_global_levels_median_across_categories(self):
        glob = global_levels({
            "Ковры": [self._lv(2000, 36.8, n=40)],
            "Шторы": [self._lv(2000, 35.8, n=20)],
            "Пледы": [self._lv(2000, 36.8, n=5)],
        })
        assert glob[2000.0]["spp"] == 36.8
        assert glob[2000.0]["n"] == 65
        assert glob[2000.0]["categories"] == ["Ковры", "Пледы", "Шторы"]

    def test_up_hint_uses_own_levels_too(self):
        """Свой уровень выше по цене, но клиенту там дешевле — это и есть подсказка.

        Живой случай «Ковров» 2026-08-01: 4700 ₽ → клиент 3554 ₽, а 5000 ₽ →
        клиент 3159 ₽. Раньше свои уровни ГЛУШИЛИ подсказку, и очевидный ход не
        показывался вовсе.
        """
        levels = [self._lv(4700, 24.8, n=2), self._lv(5000, 36.8, n=33)]
        cross_hints(levels, {})
        h = levels[0].hint_up
        assert h is not None
        assert h["price"] == 5000 and h["gain"] == 300
        assert h["buyer_delta"] < 0  # и нам больше, и клиенту дешевле

    def test_up_hint_wins_over_down(self):
        """Если подъём клиенту не хуже снижения — показываем только подъём."""
        levels = [self._lv(4400, 24.8), self._lv(4300, 24.8), self._lv(5000, 36.8, n=33)]
        cross_hints(levels, {})
        assert levels[0].hint_up is not None
        assert levels[0].hint_down is None
