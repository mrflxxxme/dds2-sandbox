# ruff: noqa: RUF001, RUF002, RUF003
"""Cold-start distribution — pure-logic tests (no DB).

Тесты на чистые функции `distribute()` и `pick_main_warehouse_per_district()`
из cold_start_distribution_service. Без БД, быстрые.
"""

import pytest

from backend.services.cold_start_distribution_service import (
    FALLBACK_DISTRICT_SHARE,
    distribute,
    distribute_multi,
    pick_main_warehouse_per_district,
    pick_warehouses_per_district,
)
from backend.services.warehouse_district import (
    DISTRICT_CENTRAL,
    DISTRICT_FAR_EAST_SIBERIA,
    DISTRICT_NORTHWEST,
    DISTRICT_SOUTH_CAUCASUS,
    DISTRICT_URAL,
    DISTRICT_VOLGA,
)


class TestDistribute:
    """Тесты на pure-функцию distribute()."""

    def test_distribute_pure_logic_simple(self) -> None:
        """Базовое распределение 100 шт по 6 ФО, без min-pack-pooling.

        Доли: central 0.30, volga 0.16, south 0.18, far 0.13, nw 0.09, ural 0.09 = 0.95
        После int():
            central=30, volga=16, south=18, far=13, nw=9, ural=9 = 95
        Leftover=5 → крупнейший (central) → central=35.
        Все qty >= min_pack=5 → нет pooling.
        """
        share = {
            DISTRICT_CENTRAL: 0.30,
            DISTRICT_VOLGA: 0.16,
            DISTRICT_SOUTH_CAUCASUS: 0.18,
            DISTRICT_FAR_EAST_SIBERIA: 0.13,
            DISTRICT_NORTHWEST: 0.09,
            DISTRICT_URAL: 0.09,
        }
        main_wh = {
            DISTRICT_CENTRAL: "Электросталь",
            DISTRICT_VOLGA: "Казань",
            DISTRICT_SOUTH_CAUCASUS: "Краснодар",
            DISTRICT_FAR_EAST_SIBERIA: "Новосибирск",
            DISTRICT_NORTHWEST: "СПБ Шушары",
            DISTRICT_URAL: "Екатеринбург - Перспективная 14",
        }
        result = distribute(total_qty=100, district_share=share, min_pack=5, main_wh_per_district=main_wh)

        # Сумма = total_qty (никаких потерь)
        assert sum(result.values()) == 100, f"Сумма ≠ 100: {result}"

        # Крупнейший ФО = central → получает остаток округлений
        assert result["Электросталь"] == 35, f"central должен = 30 + leftover 5 = 35: {result}"

        # Остальные — без leftover
        assert result["Казань"] == 16
        assert result["Краснодар"] == 18
        assert result["Новосибирск"] == 13
        assert result["СПБ Шушары"] == 9
        assert result["Екатеринбург - Перспективная 14"] == 9

    def test_distribute_pooling_below_min_pack(self) -> None:
        """ФО с qty<min_pack пулятся в крупнейший.

        total=20, share={central:0.50, volga:0.20, ural:0.10, far:0.10, nw:0.10}
        После int(): central=10, volga=4, ural=2, far=2, nw=2 = 20.
        min_pack=5 → volga, ural, far, nw < 5 → их qty (4+2+2+2=10) идёт в central.
        Итог: central=20.
        """
        share = {
            DISTRICT_CENTRAL: 0.50,
            DISTRICT_VOLGA: 0.20,
            DISTRICT_URAL: 0.10,
            DISTRICT_FAR_EAST_SIBERIA: 0.10,
            DISTRICT_NORTHWEST: 0.10,
        }
        main_wh = {
            DISTRICT_CENTRAL: "Коледино",
            DISTRICT_VOLGA: "Казань",
            DISTRICT_URAL: "Екатеринбург - Перспективная 14",
            DISTRICT_FAR_EAST_SIBERIA: "Новосибирск",
            DISTRICT_NORTHWEST: "СПБ Шушары",
        }
        result = distribute(total_qty=20, district_share=share, min_pack=5, main_wh_per_district=main_wh)
        assert sum(result.values()) == 20
        # Все ушли в central т.к. остальные < 5
        assert result.get("Коледино") == 20
        # Остальные склады отсутствуют (либо =0)
        assert "Казань" not in result
        assert "Новосибирск" not in result

    def test_distribute_zero_total(self) -> None:
        """Нулевой total → пустой результат."""
        share = {DISTRICT_CENTRAL: 0.5, DISTRICT_VOLGA: 0.5}
        main_wh = {DISTRICT_CENTRAL: "Коледино", DISTRICT_VOLGA: "Казань"}
        assert distribute(0, share, 5, main_wh) == {}

    def test_distribute_skip_abroad_and_unknown(self) -> None:
        """abroad/unknown пропускаются по умолчанию."""
        share = {
            DISTRICT_CENTRAL: 0.7,
            "abroad": 0.2,
            "unknown": 0.1,
        }
        main_wh = {DISTRICT_CENTRAL: "Коледино", "abroad": "Минск"}
        result = distribute(total_qty=100, district_share=share, min_pack=5, main_wh_per_district=main_wh)
        # Все 100 уходят в central т.к. abroad+unknown в skip_districts
        assert result.get("Коледино") == 100
        assert "Минск" not in result

    def test_distribute_no_warehouse_for_district_falls_back(self) -> None:
        """Если у ФО нет назначенного склада — qty уходит в крупнейший с складом."""
        share = {DISTRICT_CENTRAL: 0.5, DISTRICT_VOLGA: 0.5}
        # У volga нет main_wh — должен fall back на central
        main_wh = {DISTRICT_CENTRAL: "Коледино"}
        result = distribute(total_qty=100, district_share=share, min_pack=5, main_wh_per_district=main_wh)
        assert sum(result.values()) == 100
        assert result.get("Коледино") == 100

    def test_distribute_with_wb_fallback_share(self) -> None:
        """Smoke на FALLBACK_DISTRICT_SHARE — структура должна работать."""
        main_wh = {
            DISTRICT_CENTRAL: "Электросталь",
            DISTRICT_VOLGA: "Казань",
            DISTRICT_SOUTH_CAUCASUS: "Краснодар",
            DISTRICT_FAR_EAST_SIBERIA: "Новосибирск",
            DISTRICT_NORTHWEST: "СПБ Шушары",
            DISTRICT_URAL: "Екатеринбург - Перспективная 14",
        }
        result = distribute(
            total_qty=200,
            district_share=FALLBACK_DISTRICT_SHARE,
            min_pack=5,
            main_wh_per_district=main_wh,
        )
        # Сумма = 200 (abroad=5% пропускается, его доля идёт в крупнейший как leftover)
        assert sum(result.values()) == 200, f"Сумма ≠ 200: {result}"


class TestPickMainWarehouse:
    """Тесты на pure-функцию pick_main_warehouse_per_district()."""

    def test_pick_main_warehouse_excludes(self) -> None:
        """Электросталь исключён → выбирается Коледино (top-2 для CENTRAL).

        traffic={Электросталь:1000, Коледино:500}, excluded={Электросталь}
        → CENTRAL → Коледино (т.к. Электросталь в excluded).
        """
        traffic = {"Электросталь": 1000, "Коледино": 500}
        excluded = {"Электросталь"}
        result = pick_main_warehouse_per_district(traffic, excluded)
        assert result.get(DISTRICT_CENTRAL) == "Коледино"

    def test_pick_main_warehouse_top_by_traffic(self) -> None:
        """Без excluded — выбирается top-1 по трафику в каждом ФО."""
        traffic = {
            "Коледино": 500,
            "Электросталь": 1000,  # > Коледино → главный CENTRAL
            "Казань": 200,
            "Сарапул": 100,
        }
        result = pick_main_warehouse_per_district(traffic, excluded=set())
        assert result.get(DISTRICT_CENTRAL) == "Электросталь"
        assert result.get(DISTRICT_VOLGA) == "Казань"

    def test_pick_main_warehouse_unknown_skipped(self) -> None:
        """Склады не из эталонного справочника игнорируются."""
        traffic = {"Несуществующий склад": 9999, "Коледино": 100}
        result = pick_main_warehouse_per_district(traffic, excluded=set())
        assert result.get(DISTRICT_CENTRAL) == "Коледино"
        # Несуществующий не должен попасть ни в один ФО
        assert "Несуществующий склад" not in result.values()

    def test_pick_main_warehouse_all_excluded(self) -> None:
        """Если все склады ФО в excluded — этот ФО отсутствует в результате."""
        traffic = {"Электросталь": 1000, "Коледино": 500}
        excluded = {"Электросталь", "Коледино"}
        result = pick_main_warehouse_per_district(traffic, excluded)
        assert DISTRICT_CENTRAL not in result

    def test_pick_main_warehouse_empty_traffic(self) -> None:
        """Пустой traffic → пустой результат."""
        assert pick_main_warehouse_per_district({}, excluded=set()) == {}


@pytest.mark.unit
class TestConstantsAndShape:
    """Sanity-проверки на константы из симулятора."""

    def test_fallback_share_keys_are_valid_districts(self) -> None:
        """Все ключи FALLBACK_DISTRICT_SHARE — валидные district keys."""
        from backend.services.warehouse_district import (
            DISTRICT_ABROAD,
            DISTRICT_LABELS,
        )

        for key in FALLBACK_DISTRICT_SHARE:
            assert key in DISTRICT_LABELS, f"{key} нет в DISTRICT_LABELS"
        # abroad специально включён в fallback
        assert DISTRICT_ABROAD in FALLBACK_DISTRICT_SHARE

    def test_fallback_share_sums_to_one(self) -> None:
        """Доли FALLBACK ~ 1.0 (допуск 0.001)."""
        s = sum(FALLBACK_DISTRICT_SHARE.values())
        assert 0.99 <= s <= 1.01, f"Сумма долей FALLBACK_DISTRICT_SHARE = {s}, ожидается ~1.0"


@pytest.mark.unit
class TestPickWarehousesPerDistrict:
    """Тесты на pick_warehouses_per_district() — top-N складов на округ."""

    def test_picks_top_n_per_district(self) -> None:
        """top_n=2 → возвращает первые два склада округа по трафику."""
        traffic = {
            "Электросталь": 500,
            "Подольск": 300,
            "Тула": 100,
            "Краснодар": 400,
        }
        out = pick_warehouses_per_district(traffic, excluded=set(), top_n=2)
        # central: Электросталь, Подольск (Тула — отрезана top_n=2)
        central = out["central"]
        assert len(central) == 2
        assert central[0][0] == "Электросталь"
        assert central[1][0] == "Подольск"
        assert out["south_caucasus"] == [("Краснодар", 400)]

    def test_excluded_warehouses_skipped(self) -> None:
        """Excluded склады не попадают в результат."""
        traffic = {"Электросталь": 500, "Подольск": 300}
        out = pick_warehouses_per_district(traffic, excluded={"Электросталь"}, top_n=3)
        assert out["central"] == [("Подольск", 300)]

    def test_top_n_none_returns_all(self) -> None:
        """top_n=None → все склады округа."""
        traffic = {"Электросталь": 100, "Подольск": 50, "Тула": 25}
        out = pick_warehouses_per_district(traffic, excluded=set(), top_n=None)
        assert len(out["central"]) == 3


@pytest.mark.unit
class TestDistributeMulti:
    """Тесты на distribute_multi() — qty внутри округа делится между складами."""

    def test_splits_qty_within_district_by_traffic(self) -> None:
        """100 шт, central=100% доля, 2 склада 70/30 трафика → 70/30."""
        share = {"central": 1.0}
        wh_per_district = {"central": [("Электросталь", 700), ("Подольск", 300)]}
        out = distribute_multi(100, share, min_pack=5, wh_per_district=wh_per_district)
        assert out == {"Электросталь": 70, "Подольск": 30}

    def test_min_pack_bump_within_district(self) -> None:
        """Склад с qty < min_pack → bump до min_pack за счёт крупнейшего склада."""
        share = {"central": 1.0}
        wh_per_district = {"central": [("Электросталь", 95), ("Подольск", 5)]}
        # 100 шт, 2 склада, min_pack=10 → 100 ≥ 10×2=20, bump доступен
        # Подольск 5 < 10, deficit=5, Электросталь 95 → 90, Подольск → 10
        out = distribute_multi(100, share, min_pack=10, wh_per_district=wh_per_district)
        assert out == {"Электросталь": 90, "Подольск": 10}
        assert sum(out.values()) == 100

    def test_min_pack_pool_when_total_too_small(self) -> None:
        """Если qty округа < min_pack × N_складов — все мелкие в biggest_wh (нет bump)."""
        share = {"central": 1.0}
        wh_per_district = {"central": [("Электросталь", 70), ("Подольск", 30)]}
        # 15 шт, 2 склада, min_pack=10 → 15 < 20, bump НЕ применяется → all → Электросталь
        out = distribute_multi(15, share, min_pack=10, wh_per_district=wh_per_district)
        assert out == {"Электросталь": 15}

    def test_district_without_warehouses_pools_to_biggest(self) -> None:
        """Округ без складов → qty в крупнейший склад крупнейшего ФО."""
        share = {"central": 0.5, "volga": 0.5}
        wh_per_district = {"central": [("Электросталь", 100)]}  # volga нет
        out = distribute_multi(100, share, min_pack=5, wh_per_district=wh_per_district)
        # volga (50) → fallback в Электросталь (центральный округ)
        assert out == {"Электросталь": 100}

    def test_empty_qty_returns_empty(self) -> None:
        out = distribute_multi(0, {"central": 1.0}, min_pack=5, wh_per_district={"central": [("Электросталь", 1)]})
        assert out == {}

    def test_district_bump_up_to_min_pack(self) -> None:
        """ФО с малой долей bump-ятся до min_pack за счёт крупнейшего ФО.

        Это ключевая логика: ural получает не пул в central, а свои
        min_pack штук, чтобы географическое покрытие сохранилось.
        """
        share = {"central": 0.6, "south_caucasus": 0.3, "ural": 0.1}
        wh_per_district = {
            "central": [("Электросталь", 1)],
            "south_caucasus": [("Краснодар", 1)],
            "ural": [("Екатеринбург", 1)],
        }
        # 30 шт, min_pack=5
        # raw: central=18, south=9, ural=3
        # ural=3 < 5, deficit=2, central=18-2=16, ural=5
        # south=9 ≥ 5, без изменений
        out = distribute_multi(30, share, min_pack=5, wh_per_district=wh_per_district)
        assert out == {"Электросталь": 16, "Краснодар": 9, "Екатеринбург": 5}
        assert sum(out.values()) == 30

    def test_district_bump_skipped_when_biggest_too_small(self) -> None:
        """Bump не применяется если biggest упадёт ниже min_pack."""
        share = {"central": 0.5, "ural": 0.5}
        wh_per_district = {
            "central": [("Электросталь", 1)],
            "ural": [("Екатеринбург", 1)],
        }
        # 8 шт, min_pack=5
        # raw: central=4, ural=4 → leftover=0
        # biggest_d = central. ural=4<5, deficit=1, central=4-1=3 < min_pack=5 → skip bump
        # Финал: central=4, ural=4. Но 4<min_pack тоже у central — он biggest, не bump.
        out = distribute_multi(8, share, min_pack=5, wh_per_district=wh_per_district)
        # Оба <min_pack, но bump skipped — оригинальные значения сохраняются
        assert sum(out.values()) == 8
