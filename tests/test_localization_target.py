"""Тесты чистого helper'а распределения «до целевой локализации» (цель 75%).

Helper не ходит в БД: на вход — спрос по округам, текущее покрытие (сток+сборка+
транзит) по округам, веса складов (anchor/воришки), потребность по складам. На
выход — аллокация по складам, которая концентрируется на anchor'ах, не затаривает
округ сверх его спроса и останавливается по достижении target_pct локализации.
"""
import random

from backend.services.localization_target import (
    greedy_allocate_to_target,
    localization_pct,
)


# ── localization_pct ─────────────────────────────────────────────────────────
def test_localization_pct_basic() -> None:
    # local = min(100,80)+min(50,60) = 130; total 150 → 86.67%
    pct = localization_pct(
        demand_by_okrug={"central": 100, "volga": 50},
        covered_by_okrug={"central": 80, "volga": 60},
        total_demand=150,
    )
    assert round(pct, 2) == 86.67


def test_localization_pct_abroad_in_denominator() -> None:
    # 90 локального спроса покрыто, но total=100 (10 — зарубеж, не локализуем)
    pct = localization_pct(
        demand_by_okrug={"central": 90},
        covered_by_okrug={"central": 90},
        total_demand=100,
    )
    assert pct == 90.0


def test_localization_pct_zero_demand_no_div_by_zero() -> None:
    assert localization_pct({}, {}, 0) == 0.0


def test_localization_pct_caps_coverage_at_demand() -> None:
    # переизбыток в округе не засчитывается выше спроса округа
    pct = localization_pct({"central": 50}, {"central": 999}, 100)
    assert pct == 50.0  # min(50,999)=50 / 100


# ── greedy_allocate_to_target ────────────────────────────────────────────────
def test_greedy_stops_at_target_and_prefers_anchor() -> None:
    # цель 75% от total 80 = 60 локального покрытия.
    # A (volga, вес 2.0) и B (central, вес 1.0), у каждого спрос 40.
    alloc = greedy_allocate_to_target(
        cap_total=100,
        wh_demand={"A": 40, "B": 40},
        wh_weight={"A": 2.0, "B": 1.0},
        wh_okrug={"A": "volga", "B": "central"},
        demand_by_okrug={"volga": 40, "central": 40},
        existing_by_okrug={},
        total_demand=80,
        target_pct=75.0,
    )
    # anchor A заполняется первым целиком (40), B добивает до цели (20)
    assert alloc["A"] == 40
    assert alloc["B"] == 20
    assert sum(alloc.values()) == 60  # ровно до 75% локализации


def test_greedy_never_exceeds_cap_or_demand() -> None:
    alloc = greedy_allocate_to_target(
        cap_total=10,  # мало
        wh_demand={"A": 40, "B": 40},
        wh_weight={"A": 2.0, "B": 1.0},
        wh_okrug={"A": "volga", "B": "central"},
        demand_by_okrug={"volga": 40, "central": 40},
        existing_by_okrug={},
        total_demand=80,
        target_pct=75.0,
    )
    assert sum(alloc.values()) <= 10
    for wh, q in alloc.items():
        assert 0 <= q <= 40


def test_greedy_skips_saturated_okrug_no_overstock() -> None:
    # central уже покрыт стоком/транзитом полностью → туда не льём (не перетаривать)
    alloc = greedy_allocate_to_target(
        cap_total=100,
        wh_demand={"A": 40, "B": 40},
        wh_weight={"A": 1.0, "B": 5.0},  # B (central) — самый «весомый», но насыщен
        wh_okrug={"A": "volga", "B": "central"},
        demand_by_okrug={"volga": 40, "central": 40},
        existing_by_okrug={"central": 40},  # central уже на 100% спроса
        total_demand=80,
        target_pct=75.0,
    )
    assert alloc["B"] == 0  # насыщенный округ пропущен несмотря на вес
    assert alloc["A"] > 0


def test_greedy_already_at_target_ships_nothing() -> None:
    # существующее покрытие уже даёт ≥75% → ничего не отгружаем (не перетаривать)
    alloc = greedy_allocate_to_target(
        cap_total=100,
        wh_demand={"A": 40},
        wh_weight={"A": 1.0},
        wh_okrug={"A": "volga"},
        demand_by_okrug={"volga": 40},
        existing_by_okrug={"volga": 40},
        total_demand=50,  # 40/50 = 80% ≥ 75
        target_pct=75.0,
    )
    assert sum(alloc.values()) == 0


def test_greedy_abroad_demand_caps_achievable_localization() -> None:
    # 30% спроса — зарубеж (нет округа-склада) → максимум локализации 70% < target 75.
    # Алгоритм выливает весь полезный cap, но цель недостижима — не зацикливается.
    alloc = greedy_allocate_to_target(
        cap_total=1000,
        wh_demand={"A": 70},
        wh_weight={"A": 1.0},
        wh_okrug={"A": "central"},
        demand_by_okrug={"central": 70},  # 70 локализуемого
        existing_by_okrug={},
        total_demand=100,  # 30 зарубеж
        target_pct=75.0,
    )
    assert alloc["A"] == 70  # выбрали весь локализуемый спрос
    assert sum(alloc.values()) == 70


def test_greedy_zero_cap_or_zero_demand() -> None:
    assert greedy_allocate_to_target(0, {"A": 5}, {"A": 1.0}, {"A": "volga"},
                                     {"volga": 5}, {}, 5, 75.0) == {"A": 0}
    assert greedy_allocate_to_target(100, {"A": 5}, {"A": 1.0}, {"A": "volga"},
                                     {"volga": 5}, {}, 0, 75.0) == {"A": 0}


def test_greedy_fractional_okrug_demand_not_starved() -> None:
    """Дробный спрос ФО (<1 шт на горизонте) НЕ зануляет long-tail SKU.

    Регресс floor(headroom): 6 ФО × спрос 0.9, клетки need=1, can_send=5 →
    раньше allocated={все 0} (floor(0.9)=0), 5 шт терялись при target=100.
    Теперь ceil добивает дробный запас до целой штуки: раздаём все 5.
    """
    whs = [f"w{i}" for i in range(6)]
    alloc = greedy_allocate_to_target(
        5,
        {w: 1 for w in whs},
        {w: 1.0 for w in whs},
        {w: f"fo{i}" for i, w in enumerate(whs)},
        {f"fo{i}": 0.9 for i in range(6)},
        {},
        5.4,
        target_pct=100.0,
    )
    assert sum(alloc.values()) == 5  # весь can_send роздан
    assert all(v <= 1 for v in alloc.values())  # не больше клетки

    # Дробный хвост >1: 3 ФО × 3.6, клетки 4, cap 12 → раньше 3+3+3=9 (терялись 3).
    alloc2 = greedy_allocate_to_target(
        12,
        {"a": 4, "b": 4, "c": 4},
        {"a": 1.0, "b": 1.0, "c": 1.0},
        {"a": "f1", "b": "f2", "c": "f3"},
        {"f1": 3.6, "f2": 3.6, "f3": 3.6},
        {},
        10.8,
        target_pct=100.0,
    )
    assert alloc2 == {"a": 4, "b": 4, "c": 4}

    # Перелив ограничен дробной частью: второй склад того же ФО не доливает.
    alloc3 = greedy_allocate_to_target(
        10,
        {"a1": 5, "a2": 5},
        {"a1": 2.0, "a2": 1.0},
        {"a1": "f1", "a2": "f1"},
        {"f1": 3.6},
        {},
        3.6,
        target_pct=100.0,
    )
    assert alloc3 == {"a1": 4, "a2": 0}  # ceil(3.6)=4 в якорь, ФО насыщен


def test_fuzz_conservation_and_bounds() -> None:
    """Фаззинг: Σalloc ≤ cap, alloc[wh] ≤ demand[wh], покрытие округа ≤ спрос округа."""
    rng = random.Random(20260629)
    okrugs = ["central", "volga", "ural", "northwest", "south_caucasus", "far_east_siberia"]
    for _ in range(3000):
        n_wh = rng.randint(1, 8)
        whs = [f"w{i}" for i in range(n_wh)]
        wh_okrug = {w: rng.choice(okrugs) for w in whs}
        wh_demand = {w: rng.randint(0, 200) for w in whs}
        wh_weight = {w: round(rng.uniform(0.6, 2.0), 2) for w in whs}
        demand_by_okrug: dict[str, float] = {}
        for w in whs:
            demand_by_okrug[wh_okrug[w]] = demand_by_okrug.get(wh_okrug[w], 0) + wh_demand[w]
        existing_by_okrug = {o: float(rng.randint(0, 100)) for o in okrugs if rng.random() < 0.4}
        abroad = rng.randint(0, 80)
        total_demand = sum(demand_by_okrug.values()) + abroad
        cap = rng.randint(0, 500)
        target = float(rng.choice([50, 60, 75, 90]))

        alloc = greedy_allocate_to_target(
            cap, wh_demand, wh_weight, wh_okrug,
            demand_by_okrug, existing_by_okrug, total_demand, target,
        )
        # консервация и границы
        assert sum(alloc.values()) <= cap, "Σalloc > cap"
        for w in whs:
            assert 0 <= alloc[w] <= wh_demand[w], "alloc вне [0, demand]"
        # округ не перетарен сверх спроса (с учётом existing)
        covered = dict(existing_by_okrug)
        for w in whs:
            covered[wh_okrug[w]] = covered.get(wh_okrug[w], 0) + alloc[w]
        for o, dem in demand_by_okrug.items():
            # добавленное нами покрытие не должно превышать спрос округа,
            # если до этого округ ещё не был перенасыщен existing-стоком
            if existing_by_okrug.get(o, 0) <= dem:
                assert covered[o] <= dem + 0.5, f"перетарен округ {o}"
