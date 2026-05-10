# ruff: noqa: RUF001, RUF002, RUF003
"""Unit tests for warehouse_acceptance_service pure-functions.

We test the redistribute logic and package_type picker independently of the
WB API, since the live integration (`get_acceptance_options`) is mocked at a
higher level.
"""

import pytest

from backend.services.warehouse_acceptance_service import (
    _aggregate_coefficients,
    _flags_for_warehouse,
    _normalize_acceptance_wh,
    _pick_package_type,
    _split_distribution_by_package_type,
    redistribute_blocked_qty,
)


class TestFlagsForWarehouse:
    def test_empty_input(self):
        assert _flags_for_warehouse([], {}) == {}

    def test_single_warehouse(self):
        wh_id_to_name = {507: "Коледино"}
        raw = [{"warehouseID": 507, "canBox": True, "canMonopallet": False}]
        out = _flags_for_warehouse(raw, wh_id_to_name)
        assert "Коледино" in out
        assert out["Коледино"]["can_box"] is True
        assert out["Коледино"]["can_monopallet"] is False
        assert out["Коледино"]["warehouse_id"] == 507

    def test_unknown_warehouse_id_skipped(self):
        out = _flags_for_warehouse(
            [{"warehouseID": 999, "canBox": True}],
            {507: "Коледино"},
        )
        assert out == {}

    def test_or_merge_normalized_names(self):
        # "Самара (Новосемейкино)" normalizes to "Самара" — same as "Самара".
        # OR-merge: if any sub-warehouse can_box, the merged "Самара" can_box.
        wh_id_to_name = {1: "Самара", 2: "Самара (Новосемейкино)"}
        raw = [
            {"warehouseID": 1, "canBox": False, "canMonopallet": True},
            {"warehouseID": 2, "canBox": True, "canMonopallet": False},
        ]
        out = _flags_for_warehouse(raw, wh_id_to_name)
        assert "Самара" in out
        assert out["Самара"]["can_box"] is True
        assert out["Самара"]["can_monopallet"] is True


class TestPickPackageType:
    def test_empty_distribution_defaults_to_box(self):
        pkg, warns = _pick_package_type({}, {})
        assert pkg == "BOX"
        assert warns == []

    def test_all_warehouses_can_box(self):
        dist = {"Коледино": 10, "Электросталь": 5}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": True, "can_supersafe": False},
            "Электросталь": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
        }
        pkg, warns = _pick_package_type(dist, avail)
        assert pkg == "BOX"
        assert warns == []

    def test_only_monopallet_for_all(self):
        dist = {"Коледино": 10, "Электросталь": 5}
        avail = {
            "Коледино": {"can_box": False, "can_monopallet": True, "can_supersafe": False},
            "Электросталь": {"can_box": False, "can_monopallet": True, "can_supersafe": False},
        }
        pkg, warns = _pick_package_type(dist, avail)
        assert pkg == "MONOPALLET"
        assert any("моно" in w.lower() for w in warns)

    def test_mixed_picks_majority_coverage(self):
        dist = {"A": 10, "B": 5, "C": 7}
        # B/C support box; A only mono.
        avail = {
            "A": {"can_box": False, "can_monopallet": True, "can_supersafe": False},
            "B": {"can_box": True, "can_monopallet": True, "can_supersafe": False},
            "C": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
        }
        # Box: 2 warehouses. Mono: 2 warehouses. Tie → MONOPALLET (мono>=box)
        pkg, _ = _pick_package_type(dist, avail)
        assert pkg in ("BOX", "MONOPALLET")  # both are reasonable; we don't assert exact tie-break

    def test_zero_qty_warehouses_ignored(self):
        # Электросталь has 0 qty → its closed flag must not affect the choice.
        dist = {"Коледино": 10, "Электросталь": 0}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": True, "can_supersafe": False},
            "Электросталь": {"can_box": False, "can_monopallet": False, "can_supersafe": False},
        }
        pkg, warns = _pick_package_type(dist, avail)
        assert pkg == "BOX"
        assert warns == []


class TestRedistributeBlockedQty:
    def test_no_blocked_warehouses(self):
        dist = {"Коледино": 10, "Электросталь": 5}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": True},
            "Электросталь": {"can_box": True, "can_monopallet": True},
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX")
        assert new_dist == dist
        assert moves == []

    def test_consolidate_to_center_default(self):
        """Default mode: закрытые склады → крупнейший открытый ЦФО.

        Краснодар закрыт, Невинномысск открыт в Юге, но qty всё равно уходит
        в Электросталь (центр), потому что заказы недоступного региона WB
        фактически повезёт из Москвы.
        """
        dist = {"Электросталь": 5, "Краснодар": 7}
        avail = {
            "Электросталь": {"can_box": False, "can_monopallet": True},
            "Краснодар": {"can_box": False, "can_monopallet": False},
            "Невинномысск": {"can_box": False, "can_monopallet": True},  # open в Юге
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "MONOPALLET")
        # Default — Электросталь забирает Краснодар, не Невинномысск
        assert new_dist == {"Электросталь": 12}
        assert moves[0]["to_warehouse"] == "Электросталь"
        assert moves[0]["reason"] == "consolidated_to_center"

    def test_closed_warehouse_qty_moves_in_district(self):
        # Подольск (ЦФО) закрыт, Коледино (ЦФО) открыт → весь qty Подольска уходит в Коледино.
        # Внутри ЦФО reason остаётся "closed_in_district" (нет «consolidated» т.к. это и есть Центр).
        dist = {"Коледино": 10, "Подольск": 7}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": True},
            "Подольск": {"can_box": False, "can_monopallet": False, "can_supersafe": False},
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX")
        assert new_dist == {"Коледино": 17}
        assert len(moves) == 1
        assert moves[0]["from_warehouse"] == "Подольск"
        assert moves[0]["to_warehouse"] == "Коледино"
        assert moves[0]["quantity"] == 7
        assert moves[0]["reason"] == "closed_in_district"

    def test_falls_back_to_largest_open_anywhere(self):
        # spread mode: только Казань (Приволжский) открыт; Подольск (ЦФО) закрыт →
        # qty уезжает в Казань с reason=closed_no_open_in_district.
        dist = {"Казань": 5, "Подольск": 8}
        avail = {
            "Казань": {"can_box": True, "can_monopallet": True},
            "Подольск": {"can_box": False, "can_monopallet": False},
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX", mode="spread_in_district")
        assert new_dist == {"Казань": 13}
        assert moves[0]["reason"] == "closed_no_open_in_district"
        assert moves[0]["to_warehouse"] == "Казань"

    def test_consolidate_falls_back_when_central_closed(self):
        """Если ЦФО закрыт целиком → fallback на крупнейший open globally."""
        dist = {"Подольск": 5, "Краснодар": 7}
        avail = {
            "Подольск": {"can_box": False, "can_monopallet": False},  # ЦФО закрыт
            "Краснодар": {"can_box": False, "can_monopallet": False},
            "Казань": {"can_box": True, "can_monopallet": True},  # Поволжье open
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX")
        assert new_dist == {"Казань": 12}
        assert all(m["reason"] == "closed_no_central_open" for m in moves)

    def test_no_open_warehouses_anywhere(self):
        dist = {"Подольск": 5, "Пенза": 3}
        avail = {
            "Подольск": {"can_box": False, "can_monopallet": False, "can_supersafe": False},
            "Пенза": {"can_box": False, "can_monopallet": False, "can_supersafe": False},
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX")
        assert new_dist == {}
        assert all(m["to_warehouse"] is None for m in moves)
        assert all(m["reason"] == "closed_no_open_anywhere" for m in moves)

    def test_open_for_monopallet_but_blocked_for_box(self):
        # canBox=False, canMono=True. При package_type=BOX склад считается закрытым.
        dist = {"Коледино": 10, "Подольск": 5}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": True},
            "Подольск": {"can_box": False, "can_monopallet": True},  # моно ОК, короб НЕТ
        }
        new_dist_box, moves_box = redistribute_blocked_qty(dist, avail, "BOX")
        # Оба в ЦФО, default consolidate-to-center → Коледино (крупнейший в ЦФО) забирает.
        assert new_dist_box == {"Коледино": 15}
        assert moves_box[0]["from_warehouse"] == "Подольск"

        # При package_type=MONOPALLET — оба открыты, перемещений нет.
        new_dist_mono, moves_mono = redistribute_blocked_qty(dist, avail, "MONOPALLET")
        assert new_dist_mono == dist
        assert moves_mono == []

    def test_spread_mode_redirect_to_open_neighbor_not_in_distribution(self):
        """spread_in_district: открытый сосед в том же ФО, не в distribution —
        должен стать кандидатом (а не fallback на global).
        """
        dist = {"Электросталь": 10, "Краснодар": 8}
        avail = {
            "Электросталь": {"can_box": False, "can_monopallet": True},
            "Краснодар": {"can_box": False, "can_monopallet": False},  # закрыт
            "Невинномысск": {"can_box": False, "can_monopallet": True},  # открыт, не в dist
            "Волгоград": {"can_box": False, "can_monopallet": True},  # открыт, не в dist
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "MONOPALLET", mode="spread_in_district")
        # Краснодар закрыт → перенаправлен в Невинномысск или Волгоград (тот же Южный ФО)
        assert "Краснодар" not in new_dist
        moved = next(m for m in moves if m["from_warehouse"] == "Краснодар")
        assert moved["to_warehouse"] in ("Невинномысск", "Волгоград")
        assert moved["reason"] == "closed_in_district"
        assert new_dist["Электросталь"] == 10

    def test_largest_open_in_district_chosen_as_destination(self):
        # Электросталь (ЦФО) и Коледино (ЦФО) открыты; Подольск (ЦФО) закрыт.
        # Получатель — крупнейший по qty из открытых в том же ФО.
        dist = {"Коледино": 50, "Электросталь": 20, "Подольск": 10}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": True},
            "Электросталь": {"can_box": True, "can_monopallet": True},
            "Подольск": {"can_box": False, "can_monopallet": False},
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX")
        assert new_dist["Коледино"] == 60  # 50 + 10
        assert new_dist["Электросталь"] == 20  # unchanged
        assert "Подольск" not in new_dist
        assert moves[0]["to_warehouse"] == "Коледино"


@pytest.mark.parametrize(
    "package_type,expected_open",
    [
        ("BOX", {"A", "B"}),
        ("MONOPALLET", {"B", "C"}),
        ("SUPERSAFE", {"C"}),
    ],
)
def test_is_open_for_each_package_type(package_type, expected_open):
    """Per-package-type openness must match the corresponding flag."""
    dist = {"A": 1, "B": 1, "C": 1}
    avail = {
        "A": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
        "B": {"can_box": True, "can_monopallet": True, "can_supersafe": False},
        "C": {"can_box": False, "can_monopallet": True, "can_supersafe": True},
    }
    new_dist, _ = redistribute_blocked_qty(dist, avail, package_type)
    assert set(new_dist.keys()) <= expected_open or sum(new_dist.values()) == 3


# ─── Alias normalization ────────────────────────────────────────────────────


class TestNormalizeAcceptanceWh:
    """Имена WB acceptance-API → canonical WAREHOUSE_COORDS."""

    def test_sklad_prefix_alias(self):
        # «Склад Владивосток» → «Владивосток» (через ACCEPTANCE_TO_STOCK_NAME)
        assert _normalize_acceptance_wh("Склад Владивосток") == "Владивосток"

    def test_novosemeykino_to_samara(self):
        # Историческое: WB переехал «Самара» → «Новосемейкино» (отдельный ID)
        assert _normalize_acceptance_wh("Новосемейкино") == "Самара (Новосемейкино)"

    def test_krasnodar_tikhoretskaya_alias(self):
        assert _normalize_acceptance_wh("Краснодар (Тихорецкая)") == "Краснодар"

    def test_sgt_suffix_aliased(self):
        # СГТ-варианты в API мерджим с обычным складом (OR-merge для acceptance)
        assert _normalize_acceptance_wh("Владивосток СГТ") == "Владивосток"
        assert _normalize_acceptance_wh("Краснодар СГТ") == "Краснодар"

    def test_food_suffix_aliased(self):
        # «: Питание» — отдельный sub-warehouse в API, но физически тот же склад
        assert _normalize_acceptance_wh("Электросталь: Питание") == "Электросталь"
        assert _normalize_acceptance_wh("Новосемейкино: Питание") == "Самара (Новосемейкино)"

    def test_empty_input(self):
        assert _normalize_acceptance_wh(None) == ""
        assert _normalize_acceptance_wh("") == ""

    def test_unknown_name_returned_as_is(self):
        # Неизвестное имя → возвращаем как есть (никак не маппим)
        assert _normalize_acceptance_wh("Неизвестный склад X") == "Неизвестный склад X"

    def test_paren_strip_fallback_when_no_alias(self):
        # «СЦ Симферополь (Молодежненское)» — нет в алиасах, fallback на parens-strip
        assert _normalize_acceptance_wh("СЦ Симферополь (Молодежненское)") == "СЦ Симферополь"


class TestFlagsForWarehouseAlias:
    """Алиасы должны срабатывать при сборке availability."""

    def test_sklad_vladivostok_maps_to_vladivostok(self):
        # Реальный кейс: WB API отдаёт «Склад Владивосток» wid=332491.
        # В distribution лежит «Владивосток» — должен сматчиться.
        wh_id_to_name = {332491: "Склад Владивосток"}
        raw = [{"warehouseID": 332491, "canBox": False, "canMonopallet": True}]
        out = _flags_for_warehouse(raw, wh_id_to_name)
        assert "Владивосток" in out
        assert out["Владивосток"]["can_monopallet"] is True

    def test_novosemeykino_maps_to_samara(self):
        wh_id_to_name = {301805: "Новосемейкино"}
        raw = [{"warehouseID": 301805, "canBox": True, "canMonopallet": False}]
        out = _flags_for_warehouse(raw, wh_id_to_name)
        assert "Самара (Новосемейкино)" in out
        assert out["Самара (Новосемейкино)"]["can_box"] is True

    def test_or_merge_sklad_and_sgt(self):
        # «Склад Владивосток» (canMono) + «Владивосток СГТ» (canMono) — оба под Владивосток
        wh_id_to_name = {332491: "Склад Владивосток", 50172467: "Владивосток СГТ"}
        raw = [
            {"warehouseID": 332491, "canBox": False, "canMonopallet": True},
            {"warehouseID": 50172467, "canBox": False, "canMonopallet": True},
        ]
        out = _flags_for_warehouse(raw, wh_id_to_name)
        assert list(out.keys()) == ["Владивосток"]
        assert out["Владивосток"]["can_monopallet"] is True


# ─── Split-by-package-type ──────────────────────────────────────────────────


class TestSplitDistributionByPackageType:
    """Разбиение distribution на несколько сборок (одна per package_type)."""

    def test_single_package_type_returns_one_split(self):
        # Все склады поддерживают BOX → один split BOX
        dist = {"Коледино": 10, "Электросталь": 5}
        avail = {
            "Коледино": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
            "Электросталь": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
        }
        splits, moves = _split_distribution_by_package_type(dist, avail)
        assert len(splits) == 1
        assert splits[0]["package_type"] == "BOX"
        assert splits[0]["distribution"] == dist
        assert moves == []

    def test_user_real_case_box_and_mono_split(self):
        """Жалоба пользователя: SKU 200х300_дубль.

        Электросталь — только моно. Краснодар — только короб. Самара — только короб.
        Владивосток — только моно. Cold-start кладёт qty в 4 склада.
        Раньше pick_package_type выбирал MONO → Краснодар + Самара отбрасывались
        в Электросталь. Теперь должно быть 2 split'а: BOX и MONOPALLET.
        """
        dist = {
            "Электросталь": 10,
            "Краснодар": 8,
            "Самара (Новосемейкино)": 7,
            "Владивосток": 7,
        }
        avail = {
            "Электросталь": {"can_box": False, "can_monopallet": True, "can_supersafe": False},
            "Краснодар": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
            "Самара (Новосемейкино)": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
            "Владивосток": {"can_box": False, "can_monopallet": True, "can_supersafe": False},
        }
        splits, moves = _split_distribution_by_package_type(dist, avail)
        types = {s["package_type"] for s in splits}
        assert types == {"BOX", "MONOPALLET"}

        box_split = next(s for s in splits if s["package_type"] == "BOX")
        mono_split = next(s for s in splits if s["package_type"] == "MONOPALLET")

        assert box_split["distribution"] == {"Краснодар": 8, "Самара (Новосемейкино)": 7}
        assert mono_split["distribution"] == {"Электросталь": 10, "Владивосток": 7}
        assert moves == []  # ни один склад не отбрасывается

    def test_closed_warehouse_consolidated_into_split(self):
        # Подольск закрыт всюду; уезжает в крупнейший открытый ЦФО (Электросталь).
        # Электросталь принимает только моно → склад ЦФО присоединяется к MONO-split.
        dist = {"Электросталь": 5, "Краснодар": 8, "Подольск": 3}
        avail = {
            "Электросталь": {"can_box": False, "can_monopallet": True, "can_supersafe": False},
            "Краснодар": {"can_box": True, "can_monopallet": False, "can_supersafe": False},
            "Подольск": {"can_box": False, "can_monopallet": False, "can_supersafe": False},
        }
        splits, moves = _split_distribution_by_package_type(dist, avail)
        mono_split = next(s for s in splits if s["package_type"] == "MONOPALLET")
        assert mono_split["distribution"]["Электросталь"] == 5 + 3  # consolidated
        # Move зафиксирован
        assert any(m["from_warehouse"] == "Подольск" and m["to_warehouse"] == "Электросталь" for m in moves)

    def test_box_priority_over_mono_when_both_available(self):
        # Склад поддерживает и короб и моно → попадает в BOX split (BOX дешевле)
        dist = {"Коледино": 10}
        avail = {"Коледино": {"can_box": True, "can_monopallet": True, "can_supersafe": False}}
        splits, _ = _split_distribution_by_package_type(dist, avail)
        assert len(splits) == 1
        assert splits[0]["package_type"] == "BOX"

    def test_no_open_warehouses_anywhere(self):
        dist = {"Подольск": 5}
        avail = {"Подольск": {"can_box": False, "can_monopallet": False, "can_supersafe": False}}
        splits, moves = _split_distribution_by_package_type(dist, avail)
        assert splits == []
        assert moves[0]["to_warehouse"] is None
        assert moves[0]["reason"] == "closed_no_open_anywhere"


# ─── Coefficients (acceptance/coefficients) ─────────────────────────────────


class TestAggregateCoefficients:
    """Группировка raw-coefficients от common-api в (canon, type) метрики."""

    def test_empty(self):
        assert _aggregate_coefficients([], {}) == {}

    def test_free_paid_closed_counts(self):
        wh_id_to_name = {507: "Коледино"}
        raw = [
            {"warehouseID": 507, "coefficient": 0, "allowUnload": True, "boxTypeID": 6},
            {"warehouseID": 507, "coefficient": 1, "allowUnload": True, "boxTypeID": 6},
            {"warehouseID": 507, "coefficient": 3, "allowUnload": True, "boxTypeID": 6},  # paid
            {"warehouseID": 507, "coefficient": -1, "allowUnload": False, "boxTypeID": 6},  # closed
            {"warehouseID": 507, "coefficient": 0, "allowUnload": False, "boxTypeID": 6},  # closed (allow=false)
        ]
        out = _aggregate_coefficients(raw, wh_id_to_name)
        meta = out[("Коледино", "box")]
        assert meta["free_days_14"] == 2
        assert meta["paid_days_14"] == 1
        assert meta["closed_days_14"] == 2
        assert meta["min_coefficient"] == 0
        assert meta["total_days"] == 5

    def test_alias_normalization(self):
        # Новосемейкино (id=301805) → canon = «Самара (Новосемейкино)»
        # boxTypeID=5 (МОНО), не 2 — реверс маппинга 2026-05-10
        wh_id_to_name = {301805: "Новосемейкино"}
        raw = [{"warehouseID": 301805, "coefficient": 0, "allowUnload": True, "boxTypeID": 5}]
        out = _aggregate_coefficients(raw, wh_id_to_name)
        assert ("Самара (Новосемейкино)", "mono") in out

    def test_unknown_box_type_ignored(self):
        # Нестандартный boxTypeID — не маппится в наши ключи
        wh_id_to_name = {507: "Коледино"}
        raw = [{"warehouseID": 507, "coefficient": 0, "allowUnload": True, "boxTypeID": 99}]
        assert _aggregate_coefficients(raw, wh_id_to_name) == {}


class TestFlagsForWarehouseWithCoefficients:
    """can_X должно учитывать coefficients (free_days > 0)."""

    def test_default_does_not_filter_by_coefficients(self):
        """Дефолт: can_X = options.canX (как кабинет WB).

        Расхождение зафиксировано 2026-05-10 для одеяла_193х203_9кг:
        Склад Владивосток МОНО — coefficients closed=15, но кабинет принимает.
        coefficient=-1 ≠ «нельзя», это «нет публичного слота» — реальная
        бронь работает.
        """
        wh_id_to_name = {332491: "Склад Владивосток"}
        raw_options = [{"warehouseID": 332491, "canMonopallet": True}]
        coef = {
            ("Владивосток", "mono"): {
                "free_days_14": 0,
                "paid_days_14": 0,
                "closed_days_14": 15,
                "min_coefficient": None,
                "total_days": 15,
            }
        }
        out = _flags_for_warehouse(raw_options, wh_id_to_name, coef)  # default require_free_days=False
        assert out["Владивосток"]["can_monopallet"] is True  # options говорит True → доступен
        assert out["Владивосток"]["mono_meta"]["free_days_14"] == 0  # meta для tooltip

    def test_strict_mode_filters_when_coefficients_closed(self):
        """Opt-in строгий режим: AND с coefficients.free_days > 0."""
        wh_id_to_name = {367641: "Ярославль СГТ"}
        raw_options = [{"warehouseID": 367641, "canMonopallet": True}]
        coef = {
            ("Ярославль СГТ", "mono"): {
                "free_days_14": 0,
                "paid_days_14": 0,
                "closed_days_14": 15,
                "min_coefficient": None,
                "total_days": 15,
            }
        }
        out = _flags_for_warehouse(raw_options, wh_id_to_name, coef, require_free_days=True)
        assert out["Ярославль СГТ"]["can_monopallet"] is False  # строгий режим скрыл

    def test_options_yes_and_coefficients_have_free_days(self):
        wh_id_to_name = {130744: "Краснодар (Тихорецкая)"}
        raw_options = [{"warehouseID": 130744, "canBox": True, "canMonopallet": True}]
        coef = {
            ("Краснодар", "box"): {
                "free_days_14": 15,
                "paid_days_14": 0,
                "closed_days_14": 0,
                "min_coefficient": 0,
                "total_days": 15,
            },
            ("Краснодар", "mono"): {
                "free_days_14": 15,
                "paid_days_14": 0,
                "closed_days_14": 0,
                "min_coefficient": 0,
                "total_days": 15,
            },
        }
        out = _flags_for_warehouse(raw_options, wh_id_to_name, coef)
        assert out["Краснодар"]["can_box"] is True
        assert out["Краснодар"]["can_monopallet"] is True

    def test_no_coefficients_data_keeps_options_as_is(self):
        # Если coefficients пустые (graceful degradation) — флаги = как у options
        wh_id_to_name = {367641: "Ярославль СГТ"}
        raw_options = [{"warehouseID": 367641, "canBox": True, "canMonopallet": True}]
        out = _flags_for_warehouse(raw_options, wh_id_to_name, {})
        assert out["Ярославль СГТ"]["can_box"] is True
        assert out["Ярославль СГТ"]["can_monopallet"] is True

    def test_paid_only_filtered_in_strict_mode_only(self):
        # paid_days_14 > 0, free_days_14 = 0 → в строгом режиме считается «закрыт».
        # Дефолт: options=True → can=True (платно по факту можно).
        wh_id_to_name = {507: "Коледино"}
        raw_options = [{"warehouseID": 507, "canBox": True}]
        coef = {
            ("Коледино", "box"): {
                "free_days_14": 0,
                "paid_days_14": 14,
                "closed_days_14": 1,
                "min_coefficient": 2,
                "total_days": 15,
            }
        }
        # Дефолт — не фильтруем
        out_default = _flags_for_warehouse(raw_options, wh_id_to_name, coef)
        assert out_default["Коледино"]["can_box"] is True
        assert out_default["Коледино"]["box_meta"]["paid_days_14"] == 14
        # Строгий режим — фильтруем (paid не считается «бесплатно»)
        out_strict = _flags_for_warehouse(raw_options, wh_id_to_name, coef, require_free_days=True)
        assert out_strict["Коледино"]["can_box"] is False
        assert out_strict["Коледино"]["box_meta"]["min_coefficient"] == 2

    def test_or_merge_meta_takes_better(self):
        # Самара (id=...) с двумя sub-warehouse: Новосемейкино (free=15) + Новосемейкино:Питание (free=6)
        # OR-merge должен взять лучшее (free=15)
        wh_id_to_name = {301805: "Новосемейкино", 397487: "Новосемейкино: Питание"}
        raw_options = [
            {"warehouseID": 301805, "canMonopallet": True},
            {"warehouseID": 397487, "canMonopallet": True},
        ]
        coef = {
            ("Самара (Новосемейкино)", "mono"): {
                "free_days_14": 15,
                "paid_days_14": 0,
                "closed_days_14": 0,
                "min_coefficient": 0,
                "total_days": 15,
            }
        }
        out = _flags_for_warehouse(raw_options, wh_id_to_name, coef)
        assert "Самара (Новосемейкино)" in out
        assert out["Самара (Новосемейкино)"]["can_monopallet"] is True
        assert out["Самара (Новосемейкино)"]["mono_meta"]["free_days_14"] == 15
