"""Unit-тесты КТР/КРП таблиц для отчёта Локализация (ИЛ + ИРП).

Проверяем граничные значения и точечные кейсы по таблицам Wildberries
(действуют с 23.03.2026).
"""

from decimal import Decimal

import pytest

from backend.services.localization_tariff import (
    KRP_TABLE,
    KTR_TABLE,
    get_krp,
    get_ktr,
    status_label,
)

D = Decimal


# ─── Граничные значения КТР ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "loc_pct,expected",
    [
        # Нижняя граница — 0..5% → 2.00 (худший)
        (D("0.00"), D("2.00")),
        (D("4.99"), D("2.00")),
        # 5..10 → 1.80
        (D("5.00"), D("1.80")),
        (D("9.99"), D("1.80")),
        # 25..30 → 1.55
        (D("29.99"), D("1.55")),
        # 30..35 → 1.50
        (D("30.00"), D("1.50")),
        # 60..75 → 1.00 (нейтральный)
        (D("60.00"), D("1.00")),
        (D("74.99"), D("1.00")),
        # 75..80 → 0.90 (excellent)
        (D("75.00"), D("0.90")),
        # 95..100.01 → 0.50 (минимум)
        (D("95.00"), D("0.50")),
        (D("100.00"), D("0.50")),
    ],
)
def test_get_ktr_boundaries(loc_pct: Decimal, expected: Decimal):
    assert get_ktr(loc_pct) == expected


# ─── Граничные значения КРП ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "loc_pct,expected",
    [
        # 60..100 → 0.00% (без удержания)
        (D("60.00"), D("0.00")),
        (D("100.00"), D("0.00")),
        # 59.99 → 2.00 (минимальный КРП)
        (D("59.99"), D("2.00")),
        (D("55.00"), D("2.00")),
        # 50..55 → 2.05
        (D("54.99"), D("2.05")),
        (D("50.00"), D("2.05")),
        # 45..50 → 2.05
        (D("49.99"), D("2.05")),
        # 40..45 → 2.10
        (D("44.99"), D("2.10")),
        # 0..5 → 2.50 (максимум удержания)
        (D("4.99"), D("2.50")),
        (D("0.00"), D("2.50")),
    ],
)
def test_get_krp_boundaries(loc_pct: Decimal, expected: Decimal):
    assert get_krp(loc_pct) == expected


def test_get_ktr_returns_decimal_type():
    assert isinstance(get_ktr(D("50.00")), Decimal)


def test_get_krp_returns_decimal_type():
    assert isinstance(get_krp(D("50.00")), Decimal)


def test_tables_cover_full_range():
    """Каждая % от 0..100 должна попасть в КТР и КРП."""
    for pct in range(0, 101):
        ktr = get_ktr(D(str(pct)))
        krp = get_krp(D(str(pct)))
        # Никаких fallback (никаких 1.00 кроме настоящего диапазона 60..75)
        assert ktr in {row[2] for row in KTR_TABLE}, f"pct={pct} ktr={ktr} fallback"
        assert krp in {row[2] for row in KRP_TABLE}, f"pct={pct} krp={krp} fallback"


def test_status_label_thresholds():
    """Проверяем переходы статусов по КТР."""
    # 95..100 → ktr=0.50 → excellent
    assert status_label(D("96.00")) == "excellent"
    # 75..80 → ktr=0.90 → excellent
    assert status_label(D("76.00")) == "excellent"
    # 60..75 → ktr=1.00 → neutral
    assert status_label(D("65.00")) == "neutral"
    # 55..60 → ktr=1.05 → neutral
    assert status_label(D("56.00")) == "neutral"
    # 50..55 → ktr=1.10 → weak
    assert status_label(D("52.00")) == "weak"
    # 30..35 → ktr=1.50 → critical
    assert status_label(D("32.00")) == "critical"
    # 0..5 → ktr=2.00 → critical
    assert status_label(D("3.00")) == "critical"
