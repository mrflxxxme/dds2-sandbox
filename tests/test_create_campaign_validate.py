"""
Tests for validate_create_campaign (backend/services/funnel/ads_manager.py).

Параметры создания кампании отбиваются у нас ДО похода в WB: ≤50 товаров, корректные
типы ставки/оплаты, зоны только допустимые, ручной CPM без зоны нигде не показывается.
"""

import pytest

from backend.services.funnel.ads_manager import CREATE_MAX_NMS, validate_create_campaign


def test_valid_manual_cpm():
    assert validate_create_campaign("Палатки", [1, 2], "manual", "cpm", ["search"]) is None


def test_valid_unified_no_placements():
    # У единой ставки зоны не задаются — валидно без placement_types
    assert validate_create_campaign("Палатки", [1], "unified", "cpm", None) is None


def test_empty_name():
    assert validate_create_campaign("  ", [1], "manual", "cpm", ["search"]) is not None


def test_no_nms():
    assert "товар" in validate_create_campaign("Палатки", [], "manual", "cpm", ["search"]).lower()


def test_too_many_nms():
    reason = validate_create_campaign("Палатки", list(range(CREATE_MAX_NMS + 1)), "manual", "cpm", ["search"])
    assert reason is not None
    assert str(CREATE_MAX_NMS) in reason


def test_bad_bid_type():
    assert validate_create_campaign("Палатки", [1], "auto", "cpm", ["search"]) is not None


def test_bad_payment_type():
    assert validate_create_campaign("Палатки", [1], "manual", "cpa", ["search"]) is not None


def test_bad_placement():
    reason = validate_create_campaign("Палатки", [1], "manual", "cpm", ["search", "banner"])
    assert reason is not None
    assert "banner" in reason


def test_manual_cpm_requires_zone():
    """Ручной CPM без зоны — кампания нигде не покажется. Это ловилось внутри if placement_types
    (мёртвая ветка) — регресс-тест на исправление."""
    reason = validate_create_campaign("Палатки", [1], "manual", "cpm", None)
    assert reason is not None
    assert "зон" in reason.lower()


def test_name_too_long():
    assert validate_create_campaign("x" * 51, [1], "manual", "cpm", ["search"]) is not None
    assert validate_create_campaign("x" * 50, [1], "manual", "cpm", ["search"]) is None
