# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты ядра описи migfull-сервиса: классификация короб/россыпь (без БД/сети)."""

from backend.services.migfull_portal_service import classify_opis_lines, resolve_destination

# Справочник как из read-API: ВСЕ склады помечены delivery_type='direct' (по историческим
# отгрузкам), хотя портал отдаёт их и для pickup с тем же numeric id.
_DESTS = [
    {"id": 110, "name": "ВБ | Тула Алексин", "delivery_type": "direct", "marketplace_id": 2},
    {"id": 90, "name": "ВБ | МО Электросталь", "delivery_type": "direct", "marketplace_id": 2},
    {"id": 156, "name": "ВБ | Екатеринбург Перспективный", "delivery_type": "direct", "marketplace_id": 2},
    {"id": 114, "name": "ВБ | Краснодар", "delivery_type": "direct", "marketplace_id": 2},
]


def test_box_line_exact_multiple():
    lines, warnings = classify_opis_lines(
        {"2049985828273": 40},
        box_for_piece={"2049985828273": ("12049985828273", 5, "ELKA короб 5 шт")},
        name_for_barcode={"2049985828273": "ELKA"},
    )
    assert warnings == []
    assert len(lines) == 1
    line = lines[0]
    assert line.is_box is True
    assert line.barcode == "12049985828273"  # ШК короба (ITF14)
    assert line.quantity == 8  # 40 / 5 коробов
    assert line.units_per_box == 5
    assert line.pieces == 40


def test_non_divisible_falls_back_to_loose_with_warning():
    lines, warnings = classify_opis_lines(
        {"2049985828273": 42},  # 42 не кратно 5
        box_for_piece={"2049985828273": ("12049985828273", 5, "ELKA короб 5 шт")},
        name_for_barcode={"2049985828273": "ELKA"},
    )
    assert len(warnings) == 1
    assert "не кратно" in warnings[0]
    assert len(lines) == 1
    line = lines[0]
    assert line.is_box is False
    assert line.barcode == "2049985828273"  # россыпь EAN13
    assert line.quantity == 42  # штуки
    assert line.pieces == 42


def test_loose_line_when_no_box_mapping():
    lines, warnings = classify_opis_lines(
        {"2049985828273": 7},
        box_for_piece={},
        name_for_barcode={"2049985828273": "палас"},
    )
    assert warnings == []
    assert lines[0].is_box is False
    assert lines[0].quantity == 7
    assert lines[0].name == "палас"


def test_units_per_box_one_treated_as_loose():
    # короб «по 1 шт» (upb<=1) — не короб, отправляем россыпью без warning
    lines, warnings = classify_opis_lines(
        {"111": 3},
        box_for_piece={"111": ("999", 1, "x")},
        name_for_barcode={"111": "x"},
    )
    assert warnings == []
    assert lines[0].is_box is False
    assert lines[0].barcode == "111"


def test_zero_and_empty_skipped():
    lines, warnings = classify_opis_lines(
        {"111": 0},
        box_for_piece={},
        name_for_barcode={"111": "x"},
    )
    assert lines == []
    assert warnings == []


def test_lines_sorted_by_name():
    lines, _ = classify_opis_lines(
        {"b": 1, "a": 1},
        box_for_piece={},
        name_for_barcode={"b": "Zebra", "a": "Alpha"},
    )
    assert [line.name for line in lines] == ["Alpha", "Zebra"]


# ─── Резолв склада назначения (наш WB-склад → migfull numeric id) ─────────────


def test_resolve_by_name_returns_numeric_id():
    m = resolve_destination("Тула", None, _DESTS)
    assert m is not None and m["id"] == 110


def test_resolve_delivery_type_none_ignores_index_type():
    # Заявка pickup, но индекс типизирован 'direct' — при delivery_type=None матч работает.
    assert resolve_destination("Электросталь", None, _DESTS)["id"] == 90


def test_resolve_pickup_filter_would_drop_direct_index():
    # Демонстрация БАГА, ради которого резолвим по имени: явный pickup отсекает 'direct'-склады.
    assert resolve_destination("Тула", "pickup", _DESTS) is None


def test_resolve_no_match_returns_none():
    assert resolve_destination("Владивосток", None, _DESTS) is None


def test_resolve_disambiguates_unique_top():
    assert resolve_destination("Екатеринбург Перспективная 14", None, _DESTS)["id"] == 156


def test_resolve_empty_name_returns_none():
    assert resolve_destination(None, None, _DESTS) is None
