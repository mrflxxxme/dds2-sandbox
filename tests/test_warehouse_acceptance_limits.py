# ruff: noqa: RUF001, RUF002, RUF003
"""Unit tests for `_build_acceptance_limits` — the WB acceptance calendar.

Pure transform over `tariffs/v1/acceptance/coefficients` output →
AcceptanceLimitsResponse-shaped dict (warehouse × package_type × day).
"""

from backend.services.warehouse_acceptance_service import _build_acceptance_limits

WH = {507: "Коледино", 117501: "Подольск", 301809: "Краснодар (Тихорецкая)"}


def _entry(wid, box_type_id, date, coef, allow=True, **extra):
    e = {
        "warehouseID": wid,
        "boxTypeID": box_type_id,
        "date": date,
        "coefficient": coef,
        "allowUnload": allow,
    }
    e.update(extra)
    return e


class TestBuildAcceptanceLimits:
    def test_empty_input(self):
        assert _build_acceptance_limits([], {}) == {"warehouses": [], "dates": []}

    def test_single_free_day(self):
        raw = [_entry(507, 6, "2026-06-19T00:00:00Z", 0)]
        out = _build_acceptance_limits(raw, WH)
        assert out["dates"] == ["2026-06-19"]
        assert len(out["warehouses"]) == 1
        wh = out["warehouses"][0]
        assert wh["warehouse_id"] == 507
        assert wh["warehouse_name"] == "Коледино"
        assert wh["canonical_name"] == "Коледино"
        assert wh["box_type"] == "box"
        day = wh["days"][0]
        assert day == {
            "date": "2026-06-19",
            "coefficient": 0.0,
            "allow_unload": True,
            "is_free": True,
            "is_closed": False,
            "storage_coef": None,
            "delivery_coef": None,
        }

    def test_coefficient_one_is_free(self):
        out = _build_acceptance_limits([_entry(507, 6, "2026-06-19", 1)], WH)
        day = out["warehouses"][0]["days"][0]
        assert day["is_free"] is True
        assert day["is_closed"] is False

    def test_paid_day(self):
        out = _build_acceptance_limits([_entry(507, 6, "2026-06-19", 3)], WH)
        day = out["warehouses"][0]["days"][0]
        assert day["coefficient"] == 3.0
        assert day["is_free"] is False
        assert day["is_closed"] is False

    def test_closed_by_minus_one(self):
        out = _build_acceptance_limits([_entry(507, 6, "2026-06-19", -1)], WH)
        day = out["warehouses"][0]["days"][0]
        assert day["is_closed"] is True
        assert day["is_free"] is False

    def test_closed_by_allow_unload_false(self):
        out = _build_acceptance_limits([_entry(507, 6, "2026-06-19", 0, allow=False)], WH)
        day = out["warehouses"][0]["days"][0]
        assert day["is_closed"] is True
        assert day["is_free"] is False

    def test_box_type_id_mapping(self):
        raw = [
            _entry(507, 6, "2026-06-19", 0),  # box
            _entry(507, 5, "2026-06-19", 0),  # mono
            _entry(507, 2, "2026-06-19", 0),  # super
        ]
        out = _build_acceptance_limits(raw, WH)
        types = {w["box_type"] for w in out["warehouses"]}
        assert types == {"box", "mono", "super"}

    def test_unknown_box_type_id_skipped(self):
        out = _build_acceptance_limits([_entry(507, 99, "2026-06-19", 0)], WH)
        assert out["warehouses"] == []

    def test_unknown_warehouse_without_name_skipped(self):
        # id not in map AND no warehouseName → cannot resolve → skipped
        out = _build_acceptance_limits([_entry(999, 6, "2026-06-19", 0)], WH)
        assert out["warehouses"] == []

    def test_warehouse_name_fallback_from_entry(self):
        # id missing from map but entry carries warehouseName
        raw = [_entry(999, 6, "2026-06-19", 0, warehouseName="Коледино")]
        out = _build_acceptance_limits(raw, {})
        assert len(out["warehouses"]) == 1
        assert out["warehouses"][0]["canonical_name"] == "Коледино"

    def test_missing_date_skipped(self):
        raw = [{"warehouseID": 507, "boxTypeID": 6, "coefficient": 0, "allowUnload": True}]
        out = _build_acceptance_limits(raw, WH)
        assert out["warehouses"] == []

    def test_days_sorted_and_global_dates_union(self):
        raw = [
            _entry(507, 6, "2026-06-21", 0),
            _entry(507, 6, "2026-06-19", 0),
            _entry(117501, 6, "2026-06-20", 0),
        ]
        out = _build_acceptance_limits(raw, WH)
        assert out["dates"] == ["2026-06-19", "2026-06-20", "2026-06-21"]
        koledino = next(w for w in out["warehouses"] if w["canonical_name"] == "Коледино")
        assert [d["date"] for d in koledino["days"]] == ["2026-06-19", "2026-06-21"]

    def test_grouping_by_warehouse_and_box_type(self):
        raw = [
            _entry(507, 6, "2026-06-19", 0),
            _entry(507, 6, "2026-06-20", 1),
            _entry(507, 5, "2026-06-19", 0),
        ]
        out = _build_acceptance_limits(raw, WH)
        # one (Коледино, box) with 2 days + one (Коледино, mono) with 1 day
        box = next(w for w in out["warehouses"] if w["box_type"] == "box")
        mono = next(w for w in out["warehouses"] if w["box_type"] == "mono")
        assert len(box["days"]) == 2
        assert len(mono["days"]) == 1

    def test_sorted_by_canonical_then_box_order(self):
        raw = [
            _entry(507, 2, "2026-06-19", 0),  # Коледино super
            _entry(507, 6, "2026-06-19", 0),  # Коледино box
            _entry(117501, 6, "2026-06-19", 0),  # Подольск box
        ]
        out = _build_acceptance_limits(raw, WH)
        order = [(w["canonical_name"], w["box_type"]) for w in out["warehouses"]]
        # Коледино before Подольск; within Коледино box before super
        assert order == [("Коледино", "box"), ("Коледино", "super"), ("Подольск", "box")]

    def test_warehouse_filter_substring_case_insensitive(self):
        raw = [
            _entry(507, 6, "2026-06-19", 0),  # Коледино
            _entry(117501, 6, "2026-06-19", 0),  # Подольск
        ]
        out = _build_acceptance_limits(raw, WH, warehouse_filter="подоль")
        assert len(out["warehouses"]) == 1
        assert out["warehouses"][0]["canonical_name"] == "Подольск"

    def test_filter_matches_canonical_after_normalize(self):
        # raw "Краснодар (Тихорецкая)" normalizes to canonical "Краснодар"
        raw = [_entry(301809, 6, "2026-06-19", 0)]
        out = _build_acceptance_limits(raw, WH, warehouse_filter="краснодар")
        assert len(out["warehouses"]) == 1
        assert out["warehouses"][0]["canonical_name"] == "Краснодар"

    def test_storage_and_delivery_coef_coerced(self):
        raw = [_entry(507, 6, "2026-06-19", 2, storageCoef="1.6", deliveryCoef="2.15")]
        day = _build_acceptance_limits(raw, WH)["warehouses"][0]["days"][0]
        assert day["storage_coef"] == 1.6
        assert day["delivery_coef"] == 2.15
