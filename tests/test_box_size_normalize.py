"""Канонизация box_size: кириллическая «х», знак умножения «×», латинская «X»
→ латинская строчная «x». Баг V-0027 / 2049985828474: ФЗ отдал '60x40x50', а
box_size_override сохранился как '60х40х50' (кириллица) → «разные коробки»."""

from backend.utils.box_size import normalize_box_size


def test_cyrillic_x_normalized():
    assert normalize_box_size("60х40х50") == "60x40x50"  # Cyrillic х (U+0445)


def test_latin_x_unchanged():
    assert normalize_box_size("60x40x50") == "60x40x50"


def test_multiplication_sign_and_capital():
    assert normalize_box_size("60×40×50") == "60x40x50"
    assert normalize_box_size("60X40X50") == "60x40x50"


def test_mixed_separators_equal_after_normalize():
    assert normalize_box_size("60х40x50") == normalize_box_size("60x40х50") == "60x40x50"


def test_none_and_empty():
    assert normalize_box_size(None) is None
    assert normalize_box_size("") is None
    assert normalize_box_size("   ") is None


def test_trims_whitespace():
    assert normalize_box_size("  60х40х50  ") == "60x40x50"
