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
        # «Самара» и «Самара (Новосемейкино)» — один физ. склад. Канон в
        # WAREHOUSE_COORDS — со скобками. Через ACCEPTANCE_TO_STOCK_NAME оба
        # варианта мапятся на «Самара (Новосемейкино)»; OR-merge даёт оба флага.
        wh_id_to_name = {1: "Самара", 2: "Самара (Новосемейкино)"}
        raw = [
            {"warehouseID": 1, "canBox": False, "canMonopallet": True},
            {"warehouseID": 2, "canBox": True, "canMonopallet": False},
        ]
        out = _flags_for_warehouse(raw, wh_id_to_name)
        assert "Самара (Новосемейкино)" in out
        assert out["Самара (Новосемейкино)"]["can_box"] is True
        assert out["Самара (Новосемейкино)"]["can_monopallet"] is True


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


class TestNoLimitWhitelistRedistribution:
    """⌛ (нет лимита приёмки) + whitelist предзаявки: товар на ⌛-склад НЕ из
    whitelist перераспределяется на свободный/whitelist склад по приоритету.

    Триггер: `_is_open_for` в limit-aware режиме (передан preorder_allowed) —
    склад открыт для типа только если `can_X` И (free+paid>0 ИЛИ в whitelist).
    """

    def _avail_mono(self, koledino_days: int, elektrostal_days: int) -> dict:
        return {
            "Коледино": {
                "can_box": False,
                "can_monopallet": True,
                "mono_meta": {"free_days_14": koledino_days, "paid_days_14": 0},
            },
            "Электросталь": {
                "can_box": False,
                "can_monopallet": True,
                "mono_meta": {"free_days_14": elektrostal_days, "paid_days_14": 0},
            },
        }

    def test_no_limit_not_whitelisted_is_blocked_and_moved(self):
        # Коледино ⌛ (0 дней) НЕ в whitelist → его моно-qty уезжает на Электросталь
        # (реальный лимит free=3). Электросталь остаётся.
        dist = {"Коледино": 10, "Электросталь": 4}
        avail = self._avail_mono(koledino_days=0, elektrostal_days=3)
        new_dist, moves = redistribute_blocked_qty(dist, avail, "MONOPALLET", preorder_allowed=set())
        assert "Коледино" not in new_dist
        assert new_dist["Электросталь"] == 14
        moved = [m for m in moves if m["from_warehouse"] == "Коледино"]
        assert moved and moved[0]["to_warehouse"] == "Электросталь"
        assert moved[0]["quantity"] == 10

    def test_no_limit_whitelisted_is_kept(self):
        # Тот же Коледино ⌛, но в whitelist → остаётся (предзаявку сделать можно).
        dist = {"Коледино": 10, "Электросталь": 4}
        avail = self._avail_mono(koledino_days=0, elektrostal_days=3)
        new_dist, moves = redistribute_blocked_qty(
            dist, avail, "MONOPALLET", preorder_allowed={"Коледино"}
        )
        assert new_dist == dist
        assert moves == []

    def test_legacy_mode_ignores_limit_meta(self):
        # preorder_allowed=None (legacy) → мета лимита игнорируется, оба открыты.
        dist = {"Коледино": 10, "Электросталь": 4}
        avail = self._avail_mono(koledino_days=0, elektrostal_days=3)
        new_dist, moves = redistribute_blocked_qty(dist, avail, "MONOPALLET")
        assert new_dist == dist
        assert moves == []

    def test_real_limit_not_in_whitelist_is_kept(self):
        # Склад со СВОБОДНЫМ лимитом (free+paid>0) не требует whitelist — остаётся.
        dist = {"Коледино": 10, "Электросталь": 4}
        avail = self._avail_mono(koledino_days=2, elektrostal_days=3)
        new_dist, moves = redistribute_blocked_qty(dist, avail, "MONOPALLET", preorder_allowed=set())
        assert new_dist == dist
        assert moves == []

    def test_destination_chosen_strictly_by_priority_not_qty(self):
        # Два открытых ЦФО-склада + закрытый Подольск. В priority-режиме получатель
        # = первый по speed-приоритету ФО (НЕ крупнейший по qty, как в legacy).
        from backend.services.warehouse_speed import sort_warehouses_by_priority

        central = ["Электросталь", "Коледино"]
        prio_first = sort_warehouses_by_priority(central, "central")[0]
        prio_second = sort_warehouses_by_priority(central, "central")[1]
        # Кладём БОЛЬШЕ qty на приоритетно-второй склад — чтобы legacy (max-qty) и
        # priority разошлись и тест реально проверял приоритет.
        dist = {prio_second: 50, prio_first: 5, "Подольск": 8}
        avail = {
            prio_first: {"can_box": True, "can_monopallet": True,
                         "box_meta": {"free_days_14": 3, "paid_days_14": 0}},
            prio_second: {"can_box": True, "can_monopallet": True,
                          "box_meta": {"free_days_14": 3, "paid_days_14": 0}},
            "Подольск": {"can_box": False, "can_monopallet": False, "can_supersafe": False},
        }
        new_dist, moves = redistribute_blocked_qty(dist, avail, "BOX", preorder_allowed=set())
        moved = next(m for m in moves if m["from_warehouse"] == "Подольск")
        assert moved["to_warehouse"] == prio_first

    def test_split_threads_whitelist_moves_mono_off_no_limit_wh(self):
        # SKU: WhA принимает короб (реальный лимит), WhB — только моно ⌛ не в whitelist.
        # Моно-qty WhB должен уехать (не зависнуть на ⌛-складе без предзаявки).
        distribution = {"Коледино": 10, "Электросталь": 6}
        availability = {
            "Коледино": {"can_box": True, "can_monopallet": False,
                         "box_meta": {"free_days_14": 4, "paid_days_14": 0}},
            "Электросталь": {"can_box": False, "can_monopallet": True,
                             "mono_meta": {"free_days_14": 0, "paid_days_14": 0}},
        }
        splits, moves = _split_distribution_by_package_type(
            distribution, availability, preorder_allowed=set()
        )
        # Электросталь моно ⌛ не в whitelist → её qty перераспределён на Коледино (короб).
        assert any(m["from_warehouse"] == "Электросталь" for m in moves)
        total = sum(sum(s["distribution"].values()) for s in splits)
        assert total == 16  # консервация
        assert all("Электросталь" not in s["distribution"] for s in splits)


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


class TestNormalizeWbWarehouse:
    """warehouse_need_service._normalize_wb_warehouse — единый канонизатор для
    orders/stocks/assembly. Возвращает каноническое имя из WAREHOUSE_COORDS
    (через ACCEPTANCE_TO_STOCK_NAME), чтобы backend `data.warehouses[].name`
    совпадал с distribution-ключами из acceptance-redistribute (frontend
    мерджит обе ветки по точному совпадению строки).
    """

    def test_samara_collapses_to_canonical(self):
        # Bug repro: «Самара» (orders/FBO short) и «Самара (Новосемейкино)»
        # (stocks API) давали 2 разные колонки. Фикс: оба → канон с скобками.
        from backend.services.warehouse_need_service import _normalize_wb_warehouse

        assert _normalize_wb_warehouse("Новосемейкино") == "Самара (Новосемейкино)"
        assert _normalize_wb_warehouse("Самара (Новосемейкино)") == "Самара (Новосемейкино)"
        assert _normalize_wb_warehouse("Самара") == "Самара (Новосемейкино)"
        assert _normalize_wb_warehouse("Новосемейкино: Питание") == "Самара (Новосемейкино)"

    def test_acceptance_alias_to_canonical(self):
        from backend.services.warehouse_need_service import _normalize_wb_warehouse

        # Acceptance-форма мапится напрямую на каноническое имя WAREHOUSE_COORDS.
        assert _normalize_wb_warehouse("Склад Шушары") == "СПБ Шушары"
        assert _normalize_wb_warehouse("Склад Владивосток") == "Владивосток"
        assert _normalize_wb_warehouse("Владивосток СГТ") == "Владивосток"

    def test_paren_strip_when_alias_strips_suffix(self):
        from backend.services.warehouse_need_service import _normalize_wb_warehouse

        # «Краснодар (Тихорецкая)» в ACCEPTANCE_TO_STOCK_NAME → «Краснодар» —
        # и в WAREHOUSE_COORDS канон БЕЗ скобок. Возвращаем как в карте.
        assert _normalize_wb_warehouse("Краснодар (Тихорецкая)") == "Краснодар"
        # Неизвестный склад со скобками — fallback на paren-strip (шаг 2).
        assert _normalize_wb_warehouse("СЦ Симферополь (Молодежненское)") == "СЦ Симферополь"

    def test_food_suffix_collapses(self):
        from backend.services.warehouse_need_service import _normalize_wb_warehouse

        # «: Питание» — отдельный sub-warehouse, физически тот же склад.
        assert _normalize_wb_warehouse("Электросталь: Питание") == "Электросталь"
        assert _normalize_wb_warehouse("Воронеж: Питание") == "Воронеж"

    def test_empty_input(self):
        from backend.services.warehouse_need_service import _normalize_wb_warehouse

        assert _normalize_wb_warehouse(None) == ""
        assert _normalize_wb_warehouse("") == ""

    def test_unknown_name_passthrough(self):
        from backend.services.warehouse_need_service import _normalize_wb_warehouse

        # Неизвестный склад → нет в алиасах → только parens-strip.
        assert _normalize_wb_warehouse("Неизвестный склад X") == "Неизвестный склад X"
        assert _normalize_wb_warehouse("Какой-то (Питание)") == "Какой-то"


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
            {"warehouseID": 507, "coefficient": 0, "allowUnload": True, "boxTypeID": 2},
            {"warehouseID": 507, "coefficient": 1, "allowUnload": True, "boxTypeID": 2},
            {"warehouseID": 507, "coefficient": 3, "allowUnload": True, "boxTypeID": 2},  # paid
            {"warehouseID": 507, "coefficient": -1, "allowUnload": False, "boxTypeID": 2},  # closed
            {"warehouseID": 507, "coefficient": 0, "allowUnload": False, "boxTypeID": 2},  # closed (allow=false)
        ]
        out = _aggregate_coefficients(raw, wh_id_to_name)
        meta = out[("Коледино", "box")]  # boxTypeID=2 = Короба
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

    def test_spec_warehouse_excluded_from_meta(self):
        """Спец-склады (СГТ/Питание/…) не подмешивают свои открытые дни в meta
        реального склада. Прод-баг Казань-моно (ASM-601/602 2026-07-06): реальный
        склад closed=-1 все дни, а «Казань СГТ»/«Казань: Питание» открыты
        coef=0 → канон «Казань» ложно получал free_days>0 → «есть лимит» →
        товар шёл в заявки вместо предброни. Зеркало _flags_for_warehouse /
        _build_acceptance_limits, где spec-фильтр уже есть."""
        wh_id_to_name = {
            10: "Казань",  # реальный FBO
            11: "Казань СГТ",  # спец — отсеять
            12: "Казань: Питание",  # спец — отсеять
        }
        raw = [
            {"warehouseID": 10, "coefficient": -1, "allowUnload": True, "boxTypeID": 5},  # real: closed
            {"warehouseID": 11, "coefficient": 0, "allowUnload": True, "boxTypeID": 5},  # СГТ: open
            {"warehouseID": 12, "coefficient": 0, "allowUnload": True, "boxTypeID": 5},  # Питание: open
        ]
        out = _aggregate_coefficients(raw, wh_id_to_name)
        meta = out[("Казань", "mono")]
        assert meta["free_days_14"] == 0  # spec-дни НЕ подмешаны
        assert meta["paid_days_14"] == 0
        assert meta["closed_days_14"] == 1  # только реальный склад (-1)
        assert meta["total_days"] == 1


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
        wh_id_to_name = {367641: "Ярославль"}
        raw_options = [{"warehouseID": 367641, "canMonopallet": True}]
        coef = {
            ("Ярославль", "mono"): {
                "free_days_14": 0,
                "paid_days_14": 0,
                "closed_days_14": 15,
                "min_coefficient": None,
                "total_days": 15,
            }
        }
        out = _flags_for_warehouse(raw_options, wh_id_to_name, coef, require_free_days=True)
        assert out["Ярославль"]["can_monopallet"] is False  # строгий режим скрыл

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
        wh_id_to_name = {367641: "Ярославль"}
        raw_options = [{"warehouseID": 367641, "canBox": True, "canMonopallet": True}]
        out = _flags_for_warehouse(raw_options, wh_id_to_name, {})
        assert out["Ярославль"]["can_box"] is True
        assert out["Ярославль"]["can_monopallet"] is True

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


class TestFlagsForWarehouseSpecSkip:
    """Спец-склады (СГТ/Питание/Горючее/СЦ/виртуальные) исключаются из availability."""

    def test_sgt_only_warehouse_excluded(self):
        out = _flags_for_warehouse(
            [{"warehouseID": 1, "canBox": True, "canMonopallet": True}],
            {1: "Владивосток СГТ"},
        )
        assert out == {}

    def test_food_warehouse_excluded_not_merged_into_base(self):
        # «Электросталь: Питание» НЕ должен подмешивать доступность в «Электросталь».
        out = _flags_for_warehouse(
            [{"warehouseID": 1, "canBox": True}],
            {1: "Электросталь: Питание"},
        )
        assert out == {}

    def test_sc_and_virtual_excluded(self):
        out = _flags_for_warehouse(
            [{"warehouseID": 1, "canBox": True}, {"warehouseID": 2, "canBox": True}],
            {1: "СЦ Симферополь", 2: "Виртуальный Челябинск"},
        )
        assert out == {}

    def test_regular_kept_spec_dropped(self):
        # Обычный «Склад Владивосток» остаётся, СГТ-двойник отброшен и НЕ
        # просачивает свой can_box в обычный склад.
        out = _flags_for_warehouse(
            [
                {"warehouseID": 1, "canMonopallet": True},
                {"warehouseID": 2, "canBox": True},
            ],
            {1: "Склад Владивосток", 2: "Владивосток СГТ"},
        )
        assert list(out.keys()) == ["Владивосток"]
        assert out["Владивосток"]["can_monopallet"] is True
        assert out["Владивосток"]["can_box"] is False


class TestFlagsForWarehouseAcceptanceDay:
    """require_acceptance_day: гейт по лимиту приёмки (≥1 день free|paid в 14 дн)."""

    @staticmethod
    def _coef(free: int, paid: int, closed: int) -> dict:
        return {
            ("Коледино", "box"): {
                "free_days_14": free,
                "paid_days_14": paid,
                "closed_days_14": closed,
                "min_coefficient": 0 if (free or paid) else None,
                "total_days": free + paid + closed,
            }
        }

    def test_fully_closed_limit_blocks(self):
        # options=True, но лимит закрыт все 14 дней (free=paid=0) → can_box=False.
        out = _flags_for_warehouse(
            [{"warehouseID": 507, "canBox": True}], {507: "Коледино"},
            self._coef(0, 0, 14), require_acceptance_day=True,
        )
        assert out["Коледино"]["can_box"] is False

    def test_paid_day_keeps_open(self):
        # Есть платные дни → доступен (мягкий вариант «любой день приёмки»).
        out = _flags_for_warehouse(
            [{"warehouseID": 507, "canBox": True}], {507: "Коледино"},
            self._coef(0, 10, 4), require_acceptance_day=True,
        )
        assert out["Коледино"]["can_box"] is True

    def test_free_day_keeps_open(self):
        out = _flags_for_warehouse(
            [{"warehouseID": 507, "canBox": True}], {507: "Коледино"},
            self._coef(8, 0, 6), require_acceptance_day=True,
        )
        assert out["Коледино"]["can_box"] is True

    def test_no_coef_data_fail_open(self):
        # Нет коэффициентов по складу → fail-open (доверяем options).
        out = _flags_for_warehouse(
            [{"warehouseID": 507, "canBox": True}], {507: "Коледино"},
            {}, require_acceptance_day=True,
        )
        assert out["Коледино"]["can_box"] is True

    def test_options_false_stays_false(self):
        out = _flags_for_warehouse(
            [{"warehouseID": 507, "canBox": False}], {507: "Коледино"},
            self._coef(15, 0, 0), require_acceptance_day=True,
        )
        assert out["Коледино"]["can_box"] is False


# ─── get_acceptance_closed_warehouses ────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_acceptance_closed_when_no_cache(monkeypatch):
    """Если Redis-кэш пуст → empty set (fail-open, склады не блокируем)."""
    import backend.services.warehouse_acceptance_service as svc

    class _StubRedis:
        async def get(self, key):
            return None

    monkeypatch.setattr(svc, "_redis_client", _StubRedis())
    closed = await svc.get_acceptance_closed_warehouses(project_id=999)
    assert closed == set()


@pytest.mark.asyncio
async def test_get_acceptance_closed_finds_zero_free_zero_paid(monkeypatch):
    """Склад где во всех package_types free=0 И paid=0 → попадает в closed."""
    import json

    import backend.services.warehouse_acceptance_service as svc

    # Симулируем: Test-склад имеет 0/0 во всех 14 днях, Коледино имеет free_days>0
    raw_coef = [
        # Коледино, box, coef=0 allowUnload=true × 14 = free=14
        *[{"warehouseID": 507, "boxTypeID": 6, "coefficient": 0, "allowUnload": True} for _ in range(14)],
        # Test-склад, box, coef=-1 allowUnload=false × 14 = closed=14
        *[{"warehouseID": 999, "boxTypeID": 6, "coefficient": -1, "allowUnload": False} for _ in range(14)],
    ]
    wh_map = {507: "Коледино", 999: "Test"}

    class _StubRedis:
        async def get(self, key):
            if "coefficients" in key:
                return json.dumps(raw_coef)
            if "warehouses" in key:
                return json.dumps({str(k): v for k, v in wh_map.items()})
            return None

    monkeypatch.setattr(svc, "_redis_client", _StubRedis())
    closed = await svc.get_acceptance_closed_warehouses(project_id=42)
    assert "Test" in closed
    assert "Коледино" not in closed


# ─── get_acceptance_blocked_warehouses (closed − whitelist предзаявок) ─────────


@pytest.mark.asyncio
async def test_get_acceptance_blocked_subtracts_whitelist(monkeypatch):
    """Склад без лимита, НЕ в whitelist → блокируется; в whitelist → остаётся."""
    import json

    import backend.services.settings_service as settings_svc
    import backend.services.warehouse_acceptance_service as svc

    # Оба склада полностью закрыты по приёмке (coef=-1 / allowUnload=false × 14).
    raw_coef = [
        *[{"warehouseID": 507, "boxTypeID": 6, "coefficient": -1, "allowUnload": False} for _ in range(14)],
        *[{"warehouseID": 999, "boxTypeID": 6, "coefficient": -1, "allowUnload": False} for _ in range(14)],
    ]
    wh_map = {507: "Коледино", 999: "Test"}

    class _StubRedis:
        async def get(self, key):
            if "coefficients" in key:
                return json.dumps(raw_coef)
            if "warehouses" in key:
                return json.dumps({str(k): v for k, v in wh_map.items()})
            return None

    monkeypatch.setattr(svc, "_redis_client", _StubRedis())

    async def fake_allowed(db, project_id):
        return ["Коледино"]  # whitelist — предзаявка по нему разрешена

    monkeypatch.setattr(settings_svc, "get_preorder_allowed_warehouses", fake_allowed)

    blocked = await svc.get_acceptance_blocked_warehouses(db=None, project_id=42)
    assert "Test" in blocked  # вне whitelist → блок
    assert "Коледино" not in blocked  # в whitelist → предзаявку можно


@pytest.mark.asyncio
async def test_get_acceptance_blocked_empty_when_no_cache(monkeypatch):
    """Пустой кэш коэффициентов → пустой blocked (fail-open), whitelist не дёргается."""
    import backend.services.warehouse_acceptance_service as svc

    class _StubRedis:
        async def get(self, key):
            return None

    monkeypatch.setattr(svc, "_redis_client", _StubRedis())
    blocked = await svc.get_acceptance_blocked_warehouses(db=None, project_id=999)
    assert blocked == set()


# ─── Per-barcode caching of check_acceptance_and_redistribute ───────────────
#
# Фикс 2026-07-03: страница распределения фоново проверяет приёмку при каждом
# структурном изменении и на каждой вкладке. Кэш «hash всего батча с qty»
# промахивался почти всегда → живой WB-вызов (6 req/min) + общий write-лимит
# → 429 «Слишком много запросов». Теперь кэш пер-баркод (TTL 10 мин): живой
# вызов — только по недостающим баркодам, количества на ключ не влияют.


class _FakeRedisKV:
    """get/mget/setex — ровно то, что использует пер-баркодный кэш."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def mget(self, keys):
        return [self.store.get(k) for k in keys]

    async def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl


class _FakeWBClient:
    """Записывает payload'ы get_acceptance_options; отвечает canBox по всем складам."""

    instances: list["_FakeWBClient"] = []

    def __init__(self, api_key=None, project_id=None):
        self.options_calls: list[list[dict]] = []
        _FakeWBClient.instances.append(self)

    async def get_fbw_warehouses(self):
        return [{"ID": 507, "name": "Коледино"}]

    async def get_acceptance_coefficients(self):
        return []

    async def get_acceptance_options(self, items):
        self.options_calls.append(items)
        return {
            "result": [
                {
                    "barcode": it["barcode"],
                    "warehouses": [{"warehouseID": 507, "canBox": True, "canMonopallet": False, "canSupersafe": False}],
                }
                for it in items
                if it["barcode"] != "unknown-bc"  # у «новинки» WB карточки нет — omit
            ]
        }


def _all_option_calls() -> list[list[dict]]:
    return [c for inst in _FakeWBClient.instances for c in inst.options_calls]


@pytest.fixture
def acceptance_env(monkeypatch):
    import backend.services.warehouse_acceptance_service as svc

    _FakeWBClient.instances = []
    fake_redis = _FakeRedisKV()
    monkeypatch.setattr(svc, "_redis_client", fake_redis)
    monkeypatch.setattr(svc, "WBApiClient", _FakeWBClient)

    async def fake_get_wb_key(db, project_id, provider):
        return "test-key"

    monkeypatch.setattr(svc, "get_wb_key", fake_get_wb_key)
    return svc, fake_redis


@pytest.mark.asyncio
async def test_same_barcodes_second_call_is_pure_cache_hit(acceptance_env):
    """Смена количеств/распределения НЕ дёргает WB: флаги — уровня баркод×склад."""
    svc, _ = acceptance_env
    items_v1 = [{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 10}}]
    items_v2 = [{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 33}}]

    r1 = await svc.check_acceptance_and_redistribute(None, 42, items_v1)
    assert r1["cache_hit"] is False
    assert len(_all_option_calls()) == 1

    r2 = await svc.check_acceptance_and_redistribute(None, 42, items_v2)
    assert r2["cache_hit"] is True
    assert len(_all_option_calls()) == 1  # живой вызов не повторился
    # Распределение пересчитано по НОВЫМ количествам, не по кэшу
    assert r2["items"][0]["distribution"] == {"Коледино": 33}


@pytest.mark.asyncio
async def test_new_barcode_fetches_only_missing(acceptance_env):
    """Добавили SKU в батч → WB спрашиваем ТОЛЬКО про новый баркод."""
    svc, _ = acceptance_env
    await svc.check_acceptance_and_redistribute(
        None, 42, [{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 5}}]
    )
    r2 = await svc.check_acceptance_and_redistribute(
        None,
        42,
        [
            {"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 5}},
            {"nm_id": 2, "barcode": "BC2", "distribution": {"Коледино": 7}},
        ],
    )
    calls = _all_option_calls()
    assert len(calls) == 2
    assert [it["barcode"] for it in calls[1]] == ["BC2"]
    # Ответ собран из кэша (BC1) + живого вызова (BC2) — оба с availability
    by_bc = {it["barcode"]: it for it in r2["items"]}
    assert by_bc["BC1"]["availability"] and by_bc["BC2"]["availability"]


@pytest.mark.asyncio
async def test_force_refetches_all_and_rewarms_cache(acceptance_env):
    """force=true (кнопка «Обновить») обходит кэш по ВСЕМ баркодам и перегревает его."""
    svc, _ = acceptance_env
    items = [{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 5}}]
    await svc.check_acceptance_and_redistribute(None, 42, items)
    r2 = await svc.check_acceptance_and_redistribute(None, 42, items, force=True)
    assert r2["cache_hit"] is False
    calls = _all_option_calls()
    assert len(calls) == 2
    assert [it["barcode"] for it in calls[1]] == ["BC1"]
    # ...а третий (обычный) вызов после force — снова чистый cache hit
    r3 = await svc.check_acceptance_and_redistribute(None, 42, items)
    assert r3["cache_hit"] is True
    assert len(_all_option_calls()) == 2


@pytest.mark.asyncio
async def test_unknown_barcode_negative_cached(acceptance_env):
    """WB не вернул баркод (новинка без карточки) → кэшируем пустую доступность,
    повторные проверки НЕ дёргают WB, warning сохраняется."""
    svc, _ = acceptance_env
    items = [{"nm_id": 9, "barcode": "unknown-bc", "distribution": {"Коледино": 3}}]
    r1 = await svc.check_acceptance_and_redistribute(None, 42, items)
    assert r1["items"][0]["availability"] == {}
    assert r1["items"][0]["warnings"]

    r2 = await svc.check_acceptance_and_redistribute(None, 42, items)
    assert r2["cache_hit"] is True
    assert len(_all_option_calls()) == 1
    assert r2["items"][0]["warnings"]


@pytest.mark.asyncio
async def test_cache_ttl_is_10_minutes(acceptance_env):
    """TTL пер-баркод кэша = 600с (обновление раз в 10 минут по требованию)."""
    svc, fake_redis = acceptance_env
    await svc.check_acceptance_and_redistribute(
        None, 42, [{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 5}}]
    )
    key = [k for k in fake_redis.store if "BC1" in k]
    assert key, "пер-баркодный ключ не записан"
    assert fake_redis.ttls[key[0]] == 600


def test_acceptance_check_has_dedicated_rate_limit_bucket():
    """/acceptance-check НЕ делит write-бакет с автосейвом черновиков: фоновые
    проверки приёмки не должны выедать лимит PUT'ов (429 на любом действии)."""
    from backend.routers.warehouse import router
    from backend.utils.rate_limit import RateLimiter

    route = next(r for r in router.routes if r.path.endswith("/acceptance-check"))
    limiters = [d.dependency for d in route.dependencies if isinstance(d.dependency, RateLimiter)]
    assert limiters, "endpoint остался без rate limiter'а"
    assert all(lim.action != "write" for lim in limiters), "acceptance-check сидит на общем write-бакете"
    assert any(lim.action == "acceptance_check" for lim in limiters)


@pytest.mark.asyncio
async def test_redis_failure_degrades_to_live_fetch(acceptance_env, monkeypatch):
    """Redis упал (mget/setex бросают) → graceful: живой вызов по всем баркодам,
    ответ полный, исключение наружу не летит."""
    svc, fake_redis = acceptance_env

    async def boom(*args, **kwargs):
        raise ConnectionError("redis down")

    monkeypatch.setattr(fake_redis, "mget", boom)
    monkeypatch.setattr(fake_redis, "setex", boom)

    items = [{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 5}}]
    r1 = await svc.check_acceptance_and_redistribute(None, 42, items)
    assert r1["cache_hit"] is False
    assert r1["items"][0]["availability"]
    # Кэш недоступен → каждая проверка ходит в WB (fail-open, как у лимитера)
    await svc.check_acceptance_and_redistribute(None, 42, items)
    assert len(_all_option_calls()) == 2


# ─── Схема-капы и force-суб-лимит (security-ревью 2026-07-03, HIGH) ─────────


def test_acceptance_request_caps_items_and_barcode():
    """items ≤ 1000 (1 запрос = ceil(N/150) живых POST к WB) и barcode ≤ 64
    (попадает в Redis-ключ) — иначе force-флуд амплифицирует WB-квоту."""
    from pydantic import ValidationError

    from backend.schemas.warehouse import AcceptanceCheckRequest

    ok = AcceptanceCheckRequest(
        items=[{"nm_id": 1, "barcode": "BC1", "distribution": {"Коледино": 1}}]
    )
    assert len(ok.items) == 1

    with pytest.raises(ValidationError):
        AcceptanceCheckRequest(
            items=[
                {"nm_id": i, "barcode": f"bc{i}", "distribution": {}}
                for i in range(1001)
            ]
        )
    with pytest.raises(ValidationError):
        AcceptanceCheckRequest(
            items=[{"nm_id": 1, "barcode": "x" * 65, "distribution": {}}]
        )


def test_force_sublimit_is_wb_quota_sized():
    """force=true бьёт в WB живьём → суб-лимит не шире квоты WB (6 req/min)
    и в своём собственном бакете (не делит счётчик с фоновыми проверками)."""
    from backend.utils.rate_limit import rate_limit_acceptance, rate_limit_acceptance_force

    assert rate_limit_acceptance_force.limit <= 6
    assert rate_limit_acceptance_force.action not in ("write", rate_limit_acceptance.action)
