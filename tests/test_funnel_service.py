"""
Tests for backend/services/funnel/analysis.py — Funnel analytics.

Tests the _linear_regression_trend pure function and analysis output structure.
"""

import pytest

# ─── _linear_regression_trend ────────────────────────────────────────────────


def test_linear_regression_trend_positive():
    """Positive trend: increasing values should give positive trendPct."""
    from backend.services.funnel.analysis import _linear_regression_trend

    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = _linear_regression_trend(values)
    assert result > 0, f"Expected positive trend, got {result}"


def test_linear_regression_trend_negative():
    """Negative trend: decreasing values should give negative trendPct."""
    from backend.services.funnel.analysis import _linear_regression_trend

    values = [50.0, 40.0, 30.0, 20.0, 10.0]
    result = _linear_regression_trend(values)
    assert result < 0, f"Expected negative trend, got {result}"


def test_linear_regression_trend_flat():
    """Flat: all same values should give 0 trend."""
    from backend.services.funnel.analysis import _linear_regression_trend

    values = [100.0, 100.0, 100.0, 100.0, 100.0]
    result = _linear_regression_trend(values)
    assert result == 0.0


def test_linear_regression_trend_empty():
    """Empty or single value should return 0."""
    from backend.services.funnel.analysis import _linear_regression_trend

    assert _linear_regression_trend([]) == 0
    assert _linear_regression_trend([42.0]) == 0


def test_linear_regression_trend_two_values():
    """Two values should work correctly."""
    from backend.services.funnel.analysis import _linear_regression_trend

    result = _linear_regression_trend([10.0, 20.0])
    assert result > 0


def test_linear_regression_trend_zero_mean():
    """If mean is zero should return 0 (avoid division by zero)."""
    from backend.services.funnel.analysis import _linear_regression_trend

    result = _linear_regression_trend([0.0, 0.0, 0.0])
    assert result == 0


# ─── Cost parsers integration ────────────────────────────────────────────────
# Tests for cost Excel parsers (extracted from cost_service)


def test_detect_and_normalize_divandek():
    """Test divandek format detection and normalization."""
    import io

    import pandas as pd

    from backend.etl.cost_parsers import detect_and_normalize_excel

    data = {
        "Штрихкод": [2000000000001, 2000000000002],
        "Количество": [10, 20],
        "Цена": [50.0, 75.0],
        "Вес 1 шт": [0.5, 0.3],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)

    result = detect_and_normalize_excel(buf.getvalue())
    assert len(result) == 2
    assert list(result.columns) == ["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]
    assert result.iloc[0]["qty"] == 10
    assert result.iloc[0]["price_cny"] == 50.0


def test_detect_and_normalize_carpet():
    """Test carpet (Chinese) format detection and normalization."""
    import io

    import pandas as pd

    from backend.etl.cost_parsers import detect_and_normalize_excel

    data = {
        "条码": [3000000000001, 3000000000002],
        "数量": [5, 15],
        "单价": [100.0, 200.0],
        "净重": [1.5, 2.0],
        "平方数": [3.5, 4.0],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)

    result = detect_and_normalize_excel(buf.getvalue())
    assert len(result) == 2
    assert result.iloc[0]["qty"] == 5
    assert result.iloc[0]["price_cny"] == 100.0


def test_detect_and_normalize_unknown_format():
    """Unknown format should raise ValueError."""
    import io

    import pandas as pd

    from backend.etl.cost_parsers import detect_and_normalize_excel

    data = {"Column1": [1, 2], "Column2": [3, 4]}
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)

    with pytest.raises(ValueError, match="Неизвестный формат"):
        detect_and_normalize_excel(buf.getvalue())


def test_detect_and_normalize_divandek_cn():
    """Test divandek CN (客户编号) format detection."""
    import io

    import pandas as pd

    from backend.etl.cost_parsers import detect_and_normalize_excel

    data = {
        "客户编号": [4000000000001, 4000000000002],
        "数量": [3, 7],
        "单价": [25.5, 30.0],
        "总净重": [6.0, 14.0],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)

    result = detect_and_normalize_excel(buf.getvalue())
    assert len(result) == 2
    # weight_kg should be total_net_weight / qty
    assert result.iloc[0]["weight_kg"] == 2.0  # 6.0 / 3
