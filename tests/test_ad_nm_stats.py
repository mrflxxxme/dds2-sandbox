"""
Tests for backend/services/funnel/ad_nm_stats.py — РК-статистика кампания × товар.

Покрывает _rows_from_stats: перенос полного набора полей WB, суммирование площадок
(его делает клиент) и устойчивость к служебным ключам ответа fetch_ad_stats
(«_by_campaign», «_skipped_chunks») — на них раньше падал бэкфилл.
"""

from datetime import date

from backend.services.funnel.ad_nm_stats import _rows_from_stats

PROJECT_ID = 1


def _stats(**nm_fields) -> dict:
    return {"2026-07-01": {37158316: {861499954: nm_fields}}}


def test_maps_full_wb_field_set():
    rows = _rows_from_stats(
        PROJECT_ID,
        _stats(views=4729, clicks=284, sum=1033.19, atbs=30, orders=6, shks=6, sum_price=18390),
    )
    assert rows == [
        {
            "project_id": PROJECT_ID,
            "campaign_id": 37158316,
            "nm_id": 861499954,
            "date": date(2026, 7, 1),
            "views": 4729,
            "clicks": 284,
            "spend": 1033.19,
            "atbs": 30,
            "orders": 6,
            "shks": 6,
            "orders_sum": 18390.0,
        }
    ]


def test_missing_fields_default_to_zero():
    (row,) = _rows_from_stats(PROJECT_ID, _stats(views=10, clicks=1, sum=5.5))
    assert (row["atbs"], row["orders"], row["shks"], row["orders_sum"]) == (0, 0, 0, 0.0)


def test_none_values_do_not_crash():
    (row,) = _rows_from_stats(PROJECT_ID, _stats(views=None, clicks=1, sum=None, orders=None))
    assert (row["views"], row["spend"], row["orders"]) == (0, 0.0, 0)


def test_service_keys_are_skipped():
    """Ответ fetch_ad_stats содержит «_by_campaign» и «_skipped_chunks» рядом с датами."""
    stats = _stats(views=1, clicks=1, sum=1.0)
    stats["_by_campaign"] = {"2026-07-01": {37158316: {"sum": 1.0}}}
    stats["_skipped_chunks"] = 0
    rows = _rows_from_stats(PROJECT_ID, stats)
    assert len(rows) == 1
    assert rows[0]["date"] == date(2026, 7, 1)


def test_empty_input():
    assert _rows_from_stats(PROJECT_ID, {}) == []
    assert _rows_from_stats(PROJECT_ID, None) == []
