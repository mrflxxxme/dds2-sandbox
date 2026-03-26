"""
Tests for fx_service — pure functions and rate calculation logic.
No DB required for pure functions.
Tests extract_rate_from_purpose and find_rate_for_date.
"""

from datetime import date

import pytest

# ─── Imports ─────────────────────────────────────────────────────────────────
from backend.services.fx_service import (
    extract_rate_from_purpose,
    find_rate_for_date,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Tests: extract_rate_from_purpose
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractRateFromPurpose:
    """Extract CNY/RUB exchange rate from VTB transaction purpose text."""

    @pytest.mark.asyncio
    async def test_standard_vtb_format(self):
        """Standard VTB format: 'по курсу Банка CNY/RUB 11.472'."""
        rate = await extract_rate_from_purpose("Перевод CNY по курсу Банка CNY/RUB 11.472 от 15.01.2024")
        assert rate == 11.472

    @pytest.mark.asyncio
    async def test_comma_decimal(self):
        """Russian format with comma: CNY/RUB 11,472."""
        rate = await extract_rate_from_purpose("Конверсия CNY/RUB 11,472 — покупка валюты")
        assert rate == 11.472

    @pytest.mark.asyncio
    async def test_high_rate(self):
        """High exchange rate."""
        rate = await extract_rate_from_purpose("курс CNY/RUB 15.123")
        assert rate == 15.123

    @pytest.mark.asyncio
    async def test_low_rate(self):
        """Low exchange rate."""
        rate = await extract_rate_from_purpose("CNY/RUB 8.50 конверсия")
        assert rate == 8.50

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        """Should match case-insensitively."""
        rate = await extract_rate_from_purpose("cny/rub 12.345")
        assert rate == 12.345

    @pytest.mark.asyncio
    async def test_no_rate_in_text(self):
        """No rate pattern → None."""
        rate = await extract_rate_from_purpose("Оплата по договору №1 за товар")
        assert rate is None

    @pytest.mark.asyncio
    async def test_empty_string(self):
        """Empty string → None."""
        rate = await extract_rate_from_purpose("")
        assert rate is None

    @pytest.mark.asyncio
    async def test_none_input(self):
        """None input → None."""
        rate = await extract_rate_from_purpose(None)
        assert rate is None

    @pytest.mark.asyncio
    async def test_rate_with_extra_spaces(self):
        """Extra spaces between CNY/RUB and number."""
        rate = await extract_rate_from_purpose("CNY/RUB   11.999")
        assert rate == 11.999

    @pytest.mark.asyncio
    async def test_real_vtb_purpose(self):
        """Real VTB purpose text from production."""
        purpose = (
            "Покупка иностранной валюты по поручению №18 от 05.02.2026 "
            "по курсу Банка CNY/RUB 11.0514 от 05.02.2026 сумма 106000.00 CNY"
        )
        rate = await extract_rate_from_purpose(purpose)
        assert rate == 11.0514


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: find_rate_for_date
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindRateForDate:
    """Find closest exchange rate from a rates_map dict."""

    def test_exact_match(self):
        """Exact date → return that rate."""
        rates = {
            date(2024, 1, 10): 11.5,
            date(2024, 1, 15): 11.8,
            date(2024, 1, 20): 12.0,
        }
        assert find_rate_for_date(rates, date(2024, 1, 15)) == 11.8

    def test_closest_previous(self):
        """No exact match → return closest previous date's rate."""
        rates = {
            date(2024, 1, 10): 11.5,
            date(2024, 1, 20): 12.0,
        }
        # Jan 15 is between 10 and 20 → should pick Jan 10 (previous)
        assert find_rate_for_date(rates, date(2024, 1, 15)) == 11.5

    def test_closest_future(self):
        """No previous dates → return closest future date's rate."""
        rates = {
            date(2024, 1, 15): 11.8,
            date(2024, 1, 20): 12.0,
        }
        # Jan 5 has no previous → should pick Jan 15 (future)
        assert find_rate_for_date(rates, date(2024, 1, 5)) == 11.8

    def test_empty_map(self):
        """Empty rates map → None."""
        assert find_rate_for_date({}, date(2024, 1, 15)) is None

    def test_single_rate_exact(self):
        """Single rate, exact match."""
        rates = {date(2024, 1, 10): 11.5}
        assert find_rate_for_date(rates, date(2024, 1, 10)) == 11.5

    def test_single_rate_before(self):
        """Single rate, target date after → use that rate (previous)."""
        rates = {date(2024, 1, 10): 11.5}
        assert find_rate_for_date(rates, date(2024, 1, 20)) == 11.5

    def test_single_rate_after(self):
        """Single rate, target date before → use that rate (future)."""
        rates = {date(2024, 1, 20): 12.0}
        assert find_rate_for_date(rates, date(2024, 1, 10)) == 12.0

    def test_boundary_date(self):
        """Exact boundary date → should match."""
        rates = {
            date(2024, 1, 1): 10.0,
            date(2024, 12, 31): 15.0,
        }
        assert find_rate_for_date(rates, date(2024, 1, 1)) == 10.0
        assert find_rate_for_date(rates, date(2024, 12, 31)) == 15.0

    def test_prefers_previous_over_future(self):
        """When equidistant, should prefer previous (by algorithm design)."""
        rates = {
            date(2024, 1, 10): 11.0,
            date(2024, 1, 20): 12.0,
        }
        # Jan 15 is equidistant from both — algorithm picks previous
        assert find_rate_for_date(rates, date(2024, 1, 15)) == 11.0

    def test_many_rates(self):
        """Multiple rates → find correct closest previous."""
        rates = {
            date(2024, 1, 1): 10.0,
            date(2024, 1, 5): 10.5,
            date(2024, 1, 10): 11.0,
            date(2024, 1, 15): 11.5,
            date(2024, 1, 20): 12.0,
        }
        # Jan 12 → closest previous is Jan 10 = 11.0
        assert find_rate_for_date(rates, date(2024, 1, 12)) == 11.0
        # Jan 18 → closest previous is Jan 15 = 11.5
        assert find_rate_for_date(rates, date(2024, 1, 18)) == 11.5


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: RATE_RE regex pattern
# ═══════════════════════════════════════════════════════════════════════════════


class TestRateRegex:
    """Verify the RATE_RE regex pattern directly."""

    @pytest.mark.asyncio
    async def test_multiple_rates_picks_first(self):
        """If multiple rates in text, should pick the first."""
        result = await extract_rate_from_purpose("CNY/RUB 11.5 конвертировано по CNY/RUB 12.0")
        assert result == 11.5

    @pytest.mark.asyncio
    async def test_no_match_usd(self):
        """USD/RUB pattern should NOT match — only CNY/RUB."""
        result = await extract_rate_from_purpose("USD/RUB 97.50")
        assert result is None
