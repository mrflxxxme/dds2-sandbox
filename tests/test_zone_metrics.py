"""
Tests for zone_metrics (backend/services/funnel/cluster_analysis_service.py).

Зона «Рекомендации» получается вычитанием поиска из итога кампании, поэтому
разность может уйти в минус (источники разные: кластеры — из WB, итог — из нашей
таблицы). Проверяем зажим нулём и что производные считаются от зажатых значений.
"""

from backend.services.funnel.cluster_analysis_service import zone_metrics


def test_basic_derived_metrics():
    z = zone_metrics(1000, 50, 250.0, 5)
    assert z["views"] == 1000
    assert z["clicks"] == 50
    assert z["spend"] == 250.0
    assert z["ctr"] == 5.0       # 50 / 1000
    assert z["cpc"] == 5.0       # 250 / 50
    assert z["cpo"] == 50.0      # 250 / 5


def test_negative_difference_clamped_to_zero():
    """Итог минус поиск ушёл в минус — отдаём нули, а не отрицательные метрики."""
    z = zone_metrics(-100, -10, -50.0, -2)
    assert (z["views"], z["clicks"], z["spend"], z["orders"]) == (0, 0, 0.0, 0)
    assert z["ctr"] == 0.0
    assert z["cpc"] == 0.0
    assert z["cpo"] is None


def test_ctr_uses_clamped_values():
    """Клики отрицательные, показы положительные — CTR не должен стать отрицательным."""
    z = zone_metrics(500, -20, 10.0, 0)
    assert z["clicks"] == 0
    assert z["ctr"] == 0.0
    assert z["cpc"] == 0.0


def test_zero_denominators_do_not_divide():
    z = zone_metrics(0, 0, 0.0, 0)
    assert z["ctr"] == 0.0 and z["cpc"] == 0.0 and z["cpo"] is None


def test_orders_zero_gives_null_cpo():
    z = zone_metrics(100, 10, 30.0, 0)
    assert z["cpo"] is None
    assert z["cpc"] == 3.0
