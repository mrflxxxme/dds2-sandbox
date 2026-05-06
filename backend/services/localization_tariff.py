# ruff: noqa: RUF002, RUF003
"""Wildberries Локализация: таблицы КТР (для ИЛ) и КРП (для ИРП).

Действительны с 23.03.2026.

КТР (Коэффициент Территориального Распределения) — множитель для
логистики (применяется к (литр × коэф_склада)).

КРП (Коэффициент Распределения Продаж) — процент к цене товара,
дополнительное удержание для слабо локализованных артикулов.

Граничное правило (lo ≤ pct < hi). Верхняя граница последнего
диапазона — 100.01, чтобы 100% попадало в самый «зелёный» бакет.

См. backend/DOMAIN_LOCALIZATION.md для подробного описания.
"""

from decimal import Decimal

# Доля локализации, % → КТР (Коэффициент Территориального Распределения)
# Применяется к (литр × коэф_склада) при расчёте логистики.
KTR_TABLE: list[tuple[Decimal, Decimal, Decimal]] = [
    # (lower_pct, upper_pct, ktr_value)
    (Decimal("95.00"), Decimal("100.01"), Decimal("0.50")),
    (Decimal("90.00"), Decimal("95.00"), Decimal("0.60")),
    (Decimal("85.00"), Decimal("90.00"), Decimal("0.70")),
    (Decimal("80.00"), Decimal("85.00"), Decimal("0.80")),
    (Decimal("75.00"), Decimal("80.00"), Decimal("0.90")),
    (Decimal("60.00"), Decimal("75.00"), Decimal("1.00")),
    (Decimal("55.00"), Decimal("60.00"), Decimal("1.05")),
    (Decimal("50.00"), Decimal("55.00"), Decimal("1.10")),
    (Decimal("45.00"), Decimal("50.00"), Decimal("1.20")),
    (Decimal("40.00"), Decimal("45.00"), Decimal("1.30")),
    (Decimal("35.00"), Decimal("40.00"), Decimal("1.40")),
    (Decimal("30.00"), Decimal("35.00"), Decimal("1.50")),
    (Decimal("25.00"), Decimal("30.00"), Decimal("1.55")),
    (Decimal("20.00"), Decimal("25.00"), Decimal("1.60")),
    (Decimal("15.00"), Decimal("20.00"), Decimal("1.70")),
    (Decimal("10.00"), Decimal("15.00"), Decimal("1.75")),
    (Decimal("5.00"), Decimal("10.00"), Decimal("1.80")),
    (Decimal("0.00"), Decimal("5.00"), Decimal("2.00")),
]

# Доля локализации, % → КРП (Коэффициент Распределения Продаж).
# Применяется к цене товара (% удержания дополнительно к комиссии).
KRP_TABLE: list[tuple[Decimal, Decimal, Decimal]] = [
    (Decimal("60.00"), Decimal("100.01"), Decimal("0.00")),
    (Decimal("55.00"), Decimal("60.00"), Decimal("2.00")),
    (Decimal("50.00"), Decimal("55.00"), Decimal("2.05")),
    (Decimal("45.00"), Decimal("50.00"), Decimal("2.05")),
    (Decimal("40.00"), Decimal("45.00"), Decimal("2.10")),
    (Decimal("35.00"), Decimal("40.00"), Decimal("2.10")),
    (Decimal("30.00"), Decimal("35.00"), Decimal("2.15")),
    (Decimal("25.00"), Decimal("30.00"), Decimal("2.20")),
    (Decimal("20.00"), Decimal("25.00"), Decimal("2.25")),
    (Decimal("15.00"), Decimal("20.00"), Decimal("2.30")),
    (Decimal("10.00"), Decimal("15.00"), Decimal("2.35")),
    (Decimal("5.00"), Decimal("10.00"), Decimal("2.45")),
    (Decimal("0.00"), Decimal("5.00"), Decimal("2.50")),
]


def get_ktr(loc_pct: Decimal) -> Decimal:
    """КТР по доле локализации (lo ≤ pct < hi). Fallback 1.00 (нейтральный)."""
    for lo, hi, val in KTR_TABLE:
        if lo <= loc_pct < hi:
            return val
    return Decimal("1.00")


def get_krp(loc_pct: Decimal) -> Decimal:
    """КРП (% к цене) по доле локализации. Fallback 0.00 (без удержания)."""
    for lo, hi, val in KRP_TABLE:
        if lo <= loc_pct < hi:
            return val
    return Decimal("0.00")


def status_label(loc_pct: Decimal) -> str:
    """Статус по КТР: excellent / neutral / weak / critical.

    - excellent — КТР ≤ 0.90 (≥75% локализации)
    - neutral   — КТР ≤ 1.05 (≥55%)
    - weak      — КТР ≤ 1.30 (≥40%)
    - critical  — иначе (КТР > 1.30, локализация <40%)
    """
    ktr = get_ktr(loc_pct)
    if ktr <= Decimal("0.90"):
        return "excellent"
    if ktr <= Decimal("1.05"):
        return "neutral"
    if ktr <= Decimal("1.30"):
        return "weak"
    return "critical"
