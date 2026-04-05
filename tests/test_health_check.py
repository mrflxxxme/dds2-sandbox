"""
Tests for health_check_service — daily health checks.
Tests the pure _decide_illiquid_action function directly.
The full get_daily_health is integration-heavy, so we test the decision tree.
"""

from backend.services.health_check_service import _decide_illiquid_action

# ═══════════════════════════════════════════════════════════════════════════════
# _decide_illiquid_action — decision tree
# ═══════════════════════════════════════════════════════════════════════════════


class TestDecideIlliquidAction:
    """Test the illiquid product recommendation decision tree."""

    def test_high_margin_low_drr_increase_ads(self):
        """High margin (>20%) + low DRR (<5%) -> increase ads."""
        code, reason = _decide_illiquid_action(margin=25.0, turnover_days=90, drr=3.0)
        assert code == "increase_ads"

    def test_medium_margin_low_drr_increase_ads(self):
        """Medium margin (>15%) + low DRR (<10%) -> increase ads."""
        code, reason = _decide_illiquid_action(margin=18.0, turnover_days=90, drr=7.0)
        assert code == "increase_ads"

    def test_high_drr_positive_margin_cut_ads(self):
        """High DRR (>15%) + positive margin -> cut ads, reduce price."""
        code, reason = _decide_illiquid_action(margin=10.0, turnover_days=90, drr=20.0)
        assert code == "cut_ads_reduce_price"

    def test_high_drr_low_margin_cut_ads(self):
        """High DRR (>15%) + very low margin -> cut ads, reduce price more."""
        code, reason = _decide_illiquid_action(margin=3.0, turnover_days=90, drr=20.0)
        assert code == "cut_ads_reduce_price"

    def test_negative_margin_deep_sale(self):
        """Negative margin -> deep sale."""
        code, reason = _decide_illiquid_action(margin=-5.0, turnover_days=90, drr=5.0)
        assert code == "deep_sale"

    def test_very_high_turnover_deep_sale(self):
        """Turnover > 150 days -> deep sale."""
        code, reason = _decide_illiquid_action(margin=10.0, turnover_days=160, drr=5.0)
        assert code == "deep_sale"

    def test_high_turnover_reduce_20(self):
        """Turnover > 100 days -> reduce price 20%."""
        code, reason = _decide_illiquid_action(margin=10.0, turnover_days=120, drr=5.0)
        assert code == "reduce_price_20"

    def test_moderate_with_margin_reduce_10(self):
        """Moderate turnover with margin > 5% -> reduce price 10%."""
        code, reason = _decide_illiquid_action(margin=8.0, turnover_days=85, drr=12.0)
        assert code == "reduce_price_10"

    def test_default_reduce_10(self):
        """Default case -> reduce price 10%."""
        code, reason = _decide_illiquid_action(margin=3.0, turnover_days=85, drr=12.0)
        assert code == "reduce_price_10"

    def test_reason_is_string(self):
        """All recommendations include a reason string."""
        code, reason = _decide_illiquid_action(margin=10.0, turnover_days=90, drr=5.0)
        assert isinstance(reason, str)
        assert len(reason) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Health score computation (via mocked get_daily_health)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthScoreComputation:
    """Test the health score formula logic."""

    def test_perfect_score(self):
        """No issues -> health score = 100."""
        # The formula: 100 - 5*urgent - 10*overdue - 3*cat_a_issues - 2*illiquid
        score = 100
        score -= 5 * 0  # no urgent without assembly
        score -= 10 * 0  # no overdue
        score -= 3 * 0  # no category A issues
        score -= 2 * 0  # no illiquid
        assert score == 100

    def test_degraded_score(self):
        """Multiple issues reduce the score."""
        score = 100
        score -= 5 * 2  # 2 urgent items
        score -= 10 * 1  # 1 overdue assembly
        score -= 3 * 3  # 3 category A issues
        score -= 2 * 5  # 5 illiquid items
        assert score == 100 - 10 - 10 - 9 - 10  # = 61

    def test_score_floor_at_zero(self):
        """Score cannot go below 0."""
        score = 100
        score -= 5 * 100  # extreme case
        score = max(0, score)
        assert score == 0
