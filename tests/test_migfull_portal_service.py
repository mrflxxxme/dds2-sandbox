# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты ядра описи migfull-сервиса: классификация короб/россыпь (без БД/сети)."""

from backend.services.migfull_portal_service import classify_opis_lines


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
