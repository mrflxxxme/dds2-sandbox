# ruff: noqa: RUF001, RUF002, RUF003
"""Карта СПП: лесенка уровней внутри категории и обрывы между ними.

Главное, что здесь охраняется, — смысл «обрыва»: это пара СОСЕДНИХ уровней, где
СПП рушится вверх по цене, а не любая разница между дешёвыми и дорогими
товарами. Сравнение разных категорий на живых данных 2026-08-01 давало ложный
порог 2000 ₽ (+11 п.п.), которого при проверке по одному товару не было; внутри
одной категории тот же обрыв подтвердился и ручным замером (2185 → 1999 подняло
СПП с 26.4 % до 36.8 %).
"""

import pytest

from backend.services.pricing import spp_scan as scan
from backend.services.pricing.spp_map import (
    Level,
    build_levels,
    coverage_gaps,
    cross_hints,
    find_cliffs,
    find_thresholds,
    global_levels,
    is_flat,
    lag_flags,
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

    def test_tiny_move_without_step_is_silent(self):
        """Соседний уровень, СПП тот же — это сдвиг цены, а не ступенька."""
        levels = [self._lv(2600, 24.8), self._lv(2500, 24.8)]
        cross_hints(levels, {})
        assert levels[0].hint_down is None and levels[0].hint_up is None

    def test_step_too_small_in_roubles_is_silent(self):
        """СПП скачет, но выигрыш 40 ₽ — ради этого цену не трогают."""
        levels = [self._lv(1000, 24.8), self._lv(980, 29.0)]
        cross_hints(levels, {})
        assert levels[0].hint_down is None

    def test_thin_level_is_not_a_target(self):
        """Целевой уровень с одним товаром — не ориентир даже в своей категории."""
        levels = [self._lv(4700, 24.8, n=5), self._lv(5000, 36.8, n=1)]
        cross_hints(levels, {})
        assert levels[0].hint_up is None

    def test_items_get_their_own_hints(self):
        """На одном уровне СПП у товаров разный — совет тоже разный.

        Живой случай: артикул на 2001 ₽ получает 24.8 %, соседи на 1999 ₽ —
        36.8 %; уступив рубль, он отдал бы клиенту 241 ₽.
        """
        lv_hi = self._lv(2000, 36.8, n=40)
        lv_hi.items = [
            {"nm_id": 1, "vendor_code": "a", "price": 2001.0, "spp": 24.8, "buyer_price": 1504.0},
            {"nm_id": 2, "vendor_code": "b", "price": 1999.0, "spp": 36.8, "buyer_price": 1263.0},
        ]
        cross_hints([lv_hi], {})
        assert lv_hi.items[0]["hint_down"]["price"] == 2000.0
        assert lv_hi.items[0]["hint_down"]["buyer_price"] == 1264
        assert lv_hi.items[1]["hint_down"] is None and lv_hi.items[1]["hint_up"] is None


class TestLagFlag:
    """СПП ниже соседей С ТОЙ ЖЕ ценой — ВБ ещё не применил ступеньку.

    Разбор 2026-08-01: товар за 1499.00 ₽ имел 6.3 % против 32.4 % у полусотни
    соседей за 1499.14 ₽ — выглядело как «на целой цене ступенька не работает».
    Через четыре часа при неизменной цене он получил те же 32.4 %, и был
    единственным из 734 товаров, кто сдвинулся. Значит дело в задержке.
    """

    def _lv(self, items):
        lv = Level(price=1500, spp=32.4, spp_min=6.3, spp_max=32.4, buyer_price=1014, n=len(items))
        lv.items = items
        return lv

    def _it(self, nm, price, spp):
        return {"nm_id": nm, "vendor_code": str(nm), "price": price, "spp": spp,
                "buyer_price": round(price * (1 - spp / 100))}

    def test_flags_item_behind_its_peers(self):
        lv = self._lv([self._it(1, 1499.00, 6.3)] + [self._it(i, 1499.14, 32.4) for i in range(2, 6)])
        lag_flags([lv])
        assert lv.items[0]["lag_hint"]["delta"] == pytest.approx(26.1, abs=0.1)
        assert lv.items[0]["lag_hint"]["peers"] == 4
        assert "lag_hint" not in lv.items[1]

    def test_different_price_is_not_a_lag(self):
        """2001 ₽ против 1999 ₽ — это порог, а не задержка: сравниваем в пределах рубля."""
        lv = self._lv([self._it(1, 2001.00, 24.8)] + [self._it(i, 1999.20, 36.8) for i in range(2, 6)])
        lag_flags([lv])
        assert "lag_hint" not in lv.items[0]

    def test_needs_enough_peers(self):
        lv = self._lv([self._it(1, 1499.00, 6.3), self._it(2, 1499.14, 32.4)])
        lag_flags([lv])
        assert "lag_hint" not in lv.items[0]


class TestFlatCategory:
    """Категория с ровным СПП: чужие ориентиры внутри её диапазона — ложь.

    «Алмазная мозаика» 2026-08-01: 19 уровней от 550 до 1180 ₽ и СПП 4.8–5.0 %
    на каждом. Совет «опустить до 870 ₽, там 10.8 %» брался из чужих категорий и
    вводил в заблуждение — мы на этих ценах стояли и знаем, что цена ни при чём.
    А вот про 1500 ₽ наши данные не говорят ничего: туда подсказка нужна.
    """

    def _flat(self):
        return [
            Level(price=p, spp=4.9, spp_min=4.9, spp_max=4.9, buyer_price=round(p * 0.951), n=5)
            for p in (600.0, 800.0, 900.0, 1000.0, 1100.0)
        ]

    def test_detects_flat(self):
        assert is_flat(self._flat()) is True

    def test_foreign_inside_range_is_ignored(self):
        levels = self._flat()
        cross_hints(levels, {870.0: {"spp": 10.8, "categories": ["Кружки"], "n": 20}})
        assert all(lv.hint_down is None and lv.hint_up is None for lv in levels)

    def test_foreign_outside_range_still_works(self):
        """Уровень 1100 ₽ → 1500 ₽: клиент платит 981 вместо 1046 — ему дешевле."""
        levels = self._flat()
        cross_hints(levels, {1500.0: {"spp": 34.6, "categories": ["Панели"], "n": 36}})
        h = levels[4].hint_up  # уровень 1100 ₽
        assert h is not None and h["price"] == 1500.0
        assert h["buyer_delta"] < 0

    def test_up_that_costs_client_too_much_is_silent(self):
        """Кружки 800 → 1500 ₽: нам +700, но клиенту +220 — это не сделка, а рост цены.

        Прежнее правило «ВБ съедает половину подъёма» такой ход пропускало, и в
        разделе висел совет поднять цену, от которого клиент платит на 273 ₽
        больше. Порог теперь жёсткий: не дороже 50 ₽ для клиента.
        """
        levels = self._flat()
        cross_hints(levels, {1500.0: {"spp": 34.6, "categories": ["Панели"], "n": 36}})
        assert levels[1].hint_up is None  # уровень 800 ₽

    def test_small_rise_for_client_is_allowed(self):
        """Клиенту дороже на 40 ₽ — в пределах допуска, ход показываем."""
        levels = [
            Level(price=1000.0, spp=4.9, spp_min=4.9, spp_max=4.9, buyer_price=951, n=5),
            Level(price=1200.0, spp=17.5, spp_min=17.5, spp_max=17.5, buyer_price=990, n=5),
        ]
        cross_hints(levels, {})
        h = levels[0].hint_up
        assert h is not None and h["price"] == 1200.0 and h["buyer_delta"] == 39


class TestSafePrice:
    """Советуем цену, которая НАБЛЮДАЛАСЬ, а не ярлык корзины.

    Порог ВБ вполне может проходить внутри одной корзины: 1499.14 ₽ даёт 34.6 %,
    а 1502 ₽ — уже 4.8 %, и обе цены лежат в «1 500 ₽» при шаге 100. Совет
    «поднимите до 1 500» в такой ситуации = совет перешагнуть порог.
    """

    def _items(self, *triples):
        return [
            (p, s, round(p * (1 - s / 100), 2), nm, f"art{nm}") for p, s, nm in triples
        ]

    def test_level_keeps_last_price_that_still_gets_the_step(self):
        lv = build_levels(
            self._items((1499.14, 34.6, 1), (1499.14, 34.6, 2), (1499.5, 34.6, 3), (1502.0, 4.8, 4)),
            step=100,
        )
        assert lv[0].price == 1500.0
        assert lv[0].safe_price == 1499.0  # округляем ВНИЗ: ошибаться безопаснее в эту сторону

    def test_hint_advises_the_safe_price_not_the_bucket(self):
        levels = [
            Level(price=1200.0, spp=4.9, spp_min=4.9, spp_max=4.9, buyer_price=1141, n=5),
            Level(price=1500.0, spp=34.6, spp_min=34.6, spp_max=34.6,
                  buyer_price=981, n=5, safe_price=1499.0),
        ]
        cross_hints(levels, {})
        h = levels[0].hint_up
        assert h is not None and h["price"] == 1499.0

    def test_no_items_falls_back_to_the_level(self):
        lv = build_levels(_pts((1980, 36.8), (2010, 36.8)), step=100)
        assert lv[0].safe_price == 2000.0


class TestUpBeatsDown:
    def test_only_one_move_is_shown(self):
        """Раньше строка предлагала разом опустить и поднять — совет спорил с собой."""
        levels = [
            Level(price=1200.0, spp=4.8, spp_min=4.8, spp_max=4.8, buyer_price=1142, n=5),
            Level(price=1070.0, spp=11.4, spp_min=11.4, spp_max=11.4, buyer_price=948, n=5),
            Level(price=1500.0, spp=34.6, spp_min=34.6, spp_max=34.6, buyer_price=981, n=5),
        ]
        cross_hints(levels, {})
        assert levels[0].hint_up is not None
        assert levels[0].hint_down is None


class TestThresholds:
    """Пороги цены ищем на общей оси, не оглядываясь на шаг сетки и категорию."""

    def _band(self, start, spp, count, cat="Ковры", step=1.0):
        return [(start + i * step, spp, cat) for i in range(count)]

    def test_finds_the_price_where_spp_jumps(self):
        th = find_thresholds(self._band(1400, 4.8, 6) + self._band(1499, 34.6, 6))
        assert len(th) == 1
        t = th[0]
        assert (t["up_to"], t["from_price"]) == (1405.0, 1499.0)
        assert (t["spp_below"], t["spp_above"]) == (4.8, 34.6)
        assert t["jump"] == 29.8
        assert (t["n_below"], t["n_above"]) == (6, 6)
        assert t["categories"] == ["Ковры"] and t["confirmed_by"] == ["Ковры"]
        assert t["fuzzy"] is True  # между 1405 и 1499 ₽ товаров нет — место порога грубое

    def test_change_of_category_is_not_a_threshold(self):
        """Ложный порог 2026-08-01: «Алмазная мозаика» ровно на 4.9 %, «Кружки» — на 10.8 %.

        На общей оси цен место, где одни сменяются другими, выглядит ступенькой,
        хотя цена тут ни при чём. Порог обязан подтвердиться внутри категории.
        """
        pts = self._band(1000, 4.9, 6, "Алмазная мозаика") + self._band(1030, 11.9, 6, "Кружки")
        assert find_thresholds(pts) == []

    def test_close_boundary_is_not_fuzzy(self):
        th = find_thresholds(self._band(1495, 34.6, 6, step=0.5) + self._band(1502, 4.8, 6))
        assert len(th) == 1
        assert th[0]["fuzzy"] is False
        assert th[0]["jump"] == -29.8  # вверх по цене СПП рушится — это обрыв

    def test_single_laggard_is_not_a_threshold(self):
        """Один товар без применённой ступеньки посреди полки — не порог."""
        pts = self._band(1000, 34.6, 5) + [(1005.0, 4.8, "Ковры")] + self._band(1006, 34.6, 5)
        assert find_thresholds(pts) == []

    def test_smooth_portfolio_has_no_thresholds(self):
        assert find_thresholds(self._band(500, 4.9, 40)) == []

    def test_too_few_points(self):
        assert find_thresholds(self._band(500, 4.9, 3)) == []


class TestHintCanon:
    """Правила подсказок, зафиксированные Денисом 2026-08-01."""

    def _lv(self, price, spp, n=5, items=None):
        lv = Level(price=price, spp=spp, spp_min=spp, spp_max=spp,
                   buyer_price=round(price * (1 - spp / 100)), n=n)
        lv.items = items or []
        return lv

    def test_client_overpay_capped(self):
        """Подъём с переплатой клиента больше 50 ₽ не советуем.

        Отвергнутое правило «ВБ съедает половину подъёма» такой ход пропускало:
        800 → 1500 ₽ при переплате клиента 273 ₽ выглядело приемлемым.
        """
        levels = [self._lv(800, 4.9)]
        cross_hints(levels, {1500.0: {"spp": 34.6, "categories": ["Панели"], "n": 36}})
        assert levels[0].hint_up is None

    def test_small_overpay_allowed(self):
        levels = [self._lv(1200, 4.8)]  # клиент 1142
        cross_hints(levels, {1500.0: {"spp": 34.6, "categories": ["Панели"], "n": 36}})  # клиент 981
        assert levels[0].hint_up is not None

    def test_single_move_per_row(self):
        """Есть «поднять» — «опустить» в той же строке не показываем."""
        levels = [self._lv(4700, 24.8), self._lv(4000, 30.0), self._lv(5000, 36.8, n=33)]
        cross_hints(levels, {})
        assert levels[0].hint_up is not None
        assert levels[0].hint_down is None

    def test_advises_observed_price_not_bucket_label(self):
        """Советуем 1999 ₽ (так реально стоят), а не 2000 ₽ — ярлык корзины.

        Уровни строим через `build_levels`: реальную цену уровня считает он.
        """
        pts = [(2200.0, 24.8, 1654.0, i, str(i)) for i in range(5)]
        pts += [(1999.0, 36.8, 1264.0, 100 + i, str(i)) for i in range(40)]
        levels = build_levels(pts, step=100)
        cross_hints(levels, {})
        top = [lv for lv in levels if lv.price == 2200.0][0]
        assert top.hint_down["price"] == 1999.0


    def test_deep_cut_is_not_a_hint(self):
        """Уступка глубже 300 ₽ — уже не ход на ступеньку, а срезание маржи.

        У «Ковров» со 2 600 ₽ до 1 999 ₽ это −639 ₽ с единицы: клиент выигрывает
        больше, но такой ход менеджеру предлагать нельзя.
        """
        levels = [self._lv(2600, 24.8), self._lv(1999, 36.8, n=80)]
        cross_hints(levels, {})
        assert levels[0].hint_down is None

    def test_shallow_cut_survives(self):
        levels = [self._lv(2200, 24.8), self._lv(1999, 36.8, n=80)]
        cross_hints(levels, {})
        assert levels[0].hint_down is not None
        assert levels[0].price - levels[0].hint_down["price"] <= 300


class TestSchemaMatchesService:
    """Схема ответа обязана знать все поля сервиса.

    `confirmed_by` жил в сервисе и в типах фронта, но не в `SppThreshold` —
    FastAPI молча вырезал его по response_model, и раздел падал на
    `t.confirmed_by.length` уже в браузере. Ошибка ровно того класса, который
    тесты и должны ловить до выката.
    """

    def test_threshold_schema_has_every_key(self):
        from backend.schemas.pricing import SppThreshold

        pts = [(1400 + i, 4.8, "Ковры") for i in range(6)]
        pts += [(1499 + i, 34.6, "Ковры") for i in range(6)]
        produced = find_thresholds(pts)
        assert produced, "нужен хотя бы один порог, иначе тест ничего не проверяет"
        assert set(produced[0]) <= set(SppThreshold.model_fields)

    def test_level_schema_has_every_key(self):
        from backend.schemas.pricing import SppLevel

        lv = build_levels(_pts((1980, 36.8), (2010, 36.8)), step=100)[0]
        assert set(vars(lv)) <= set(SppLevel.model_fields)


class TestBothMovesWhenUpCostsClient:
    """Подъём глушит снижение только тогда, когда клиенту от него не хуже.

    «Панели стеновые» 2026-08-01: с 2200 ₽ можно уйти вверх на 2335 (клиенту
    +47 ₽) или вниз на 1999, где ступенька 32.3 % и клиент платит на 368 ₽
    меньше. Прятать второй ход нельзя — это выбор маржи против объёма.
    """

    def _lv(self, price, spp, n=5):
        return Level(price=price, spp=spp, spp_min=spp, spp_max=spp,
                     buyer_price=round(price * (1 - spp / 100)), n=n)

    def test_step_down_beats_a_rise_that_gives_client_nothing(self):
        """Подъём даёт клиенту ~0, спуск на ступеньку — сотни рублей: главный ход вниз."""
        levels = [self._lv(2101, 20.3), self._lv(1999, 32.3), self._lv(2225, 24.8)]
        cross_hints(levels, {})
        lv = levels[0]
        assert lv.hint_down is not None and lv.hint_down["price"] == 1999
        assert lv.hint_up is None  # ход в строке один
        assert lv.hint_down["alt_kind"] == "up"  # отвергнутый подъём — в подсказке
        assert lv.hint_down["alt_price"] == 2225

    def test_rise_wins_when_it_also_drops_the_client_price(self):
        """«Ковры» 4700 → 5000: и нам больше, и клиенту дешевле — снижение не нужно."""
        levels = [self._lv(4700, 24.8), self._lv(4600, 29.0), self._lv(5000, 36.8, n=33)]
        cross_hints(levels, {})
        assert levels[0].hint_up is not None and levels[0].hint_up["price"] == 5000
        assert levels[0].hint_down is None


class TestHintsNeedAThreshold:
    """Совет двигать цену выдаём, только если по дороге есть подтверждённый порог.

    Аудит 19 категорий 2026-08-01 вылавливал ровно этот класс пустых советов:
    «Панелям стеновым» с 2500 ₽ предлагалось уйти на 2436 ₽ (СПП 24.8 %), но
    2436 — уровень «Ковров», у которых СПП выше по всему диапазону. Разница
    категорий, а не цены.
    """

    def _lv(self, price, spp, n=5):
        return Level(price=price, spp=spp, spp_min=spp, spp_max=spp,
                     buyer_price=round(price * (1 - spp / 100)), n=n)

    def _th(self, up_to, from_price):
        return [{"up_to": up_to, "from_price": from_price}]

    def test_move_without_threshold_is_silent(self):
        levels = [self._lv(2500, 20.3), self._lv(2436, 24.8)]
        cross_hints(levels, {}, self._th(1999.69, 2001.0))  # порог далеко внизу
        assert levels[0].hint_down is None and levels[0].hint_up is None

    def test_move_across_threshold_survives(self):
        levels = [self._lv(2200, 20.3), self._lv(1999, 32.3)]
        cross_hints(levels, {}, self._th(1999.69, 2001.0))
        assert levels[0].hint_down is not None

    def test_target_flush_with_the_threshold_still_counts(self):
        """`safe` округляется ВНИЗ и упирается в порог: 4999 против 4999.02.

        По букве «hi >= from_price» такой ход не пересекал бы порог, и «Одеяла»
        4300 → 4999 молча пропадали. Черта — середина зазора.
        """
        levels = [self._lv(4300, 24.8), self._lv(4999, 36.8, n=33)]
        cross_hints(levels, {}, self._th(4726.8, 4999.02))
        assert levels[0].hint_up is not None and levels[0].hint_up["price"] == 4999

    def test_without_thresholds_nothing_is_filtered(self):
        levels = [self._lv(2500, 20.3), self._lv(2436, 24.8)]
        cross_hints(levels, {})  # порогов не передали — старое поведение
        assert levels[0].hint_down is not None


class TestScanPlan:
    """План прогонов: куда ставить пробы, чтобы узнать то, чего ещё нет в данных."""

    def _pts(self, *rows):
        """(цена, СПП) → точка с категорией и артикулом-донором."""
        return [(p, s, "Ковры", 1000 + i, f"art{i}") for i, (p, s) in enumerate(rows)]

    def test_narrows_a_known_threshold_by_half(self):
        pts = self._pts((4700, 24.8), (4720, 24.8), (4999.02, 36.8), (5000, 36.8))
        th = [{"up_to": 4726.8, "from_price": 4999.02, "spp_below": 24.8, "spp_above": 36.8}]
        plan = scan.plan_probes(pts, th)
        top = plan[0]
        assert top["kind"] == "narrow"
        assert 4862 <= top["price"] <= 4864  # середина зазора
        assert top["gap_before"] > top["gap_after"] * 1.9  # делим пополам

    def test_every_probe_price_has_kopecks(self):
        pts = self._pts((1400, 4.8), (1410, 4.8), (1700, 32.4), (1750, 32.4))
        plan = scan.plan_probes(pts, [])
        assert plan, "на таком разбросе цен пятна обязаны найтись"
        assert all(p["price"] % 1 != 0 for p in plan)  # ровных рублей не ставим

    def test_round_price_inside_a_gap_wins_over_its_middle(self):
        """Пороги ВБ садятся на 1999/2999/4999 — пятно проверяем этой ценой."""
        pts = self._pts((1800, 24.8), (1810, 24.8), (2300, 24.8), (2310, 24.8))
        plan = scan.plan_probes(pts, [])
        assert any(p["kind"] == "grid" and int(p["price"]) == 1999 for p in plan)

    def test_target_outside_the_margin_window_is_dropped(self):
        """Вниз дальше 300 ₽ и вверх дальше 1000 ₽ не ходим — цель без донора выпадает."""
        pts = self._pts((1000, 4.8), (1010, 4.8), (5000, 36.8), (5010, 36.8))
        plan = scan.plan_probes(pts, [])
        for row in plan:
            d = row["donor"]
            assert -300 <= d["delta"] <= 1000

    def test_narrow_gap_of_a_rouble_is_not_worth_a_probe(self):
        pts = self._pts((1990, 36.8), (1999.69, 36.8), (2001, 25.8), (2100, 25.8))
        th = [{"up_to": 1999.69, "from_price": 2001.0, "spp_below": 36.8, "spp_above": 25.8}]
        assert all(p["kind"] != "narrow" for p in scan.plan_probes(pts, th))

    def test_empty(self):
        assert scan.plan_probes([], []) == []


class TestProbeGuards:
    """Рамки маржи проверяет тот, кто пишет цену в ВБ, а не только планировщик."""

    def test_price_for_wb_prefers_kopecks(self):
        from backend.services.pricing.spp_probe import _price_and_discount

        base, disc = _price_and_discount(1999.14, 6518, 70, prefer_kopecks=True)
        got = base * (1 - disc / 100)
        assert round(got % 1, 2) != 0  # ровных рублей в пробе не ставим
        assert abs(got - 1999.14) < 5  # …и всё же попадаем в цель

    def test_revert_returns_exactly_the_old_price(self):
        """Возврат обязан вернуть ровно то, что стояло, даже если это ровный рубль."""
        from backend.services.pricing.spp_probe import _price_and_discount

        base, disc = _price_and_discount(2160.0, 2160, 0)
        assert base * (1 - disc / 100) == 2160.0


class TestBatchReaction:
    """Наблюдение пишем только после реакции витрины — иначе запишем ложь."""

    def test_price_moved_is_a_reaction(self):
        assert scan.is_reaction(1675.0, 1353.0) is True

    def test_same_price_is_not_a_reaction(self):
        """ВБ ещё не пересчитал: старый СПП на новой цене выглядел бы как «ступеньки нет»."""
        assert scan.is_reaction(1675.0, 1675.0) is False

    def test_kopecks_are_not_a_reaction(self):
        assert scan.is_reaction(1675.0, 1675.4) is False
