"""
Tests for ad_finance_sync (разбор ответов WB /adv/v1/upd и /adv/v1/payments).

Проверяем чистые функции разбора: парсинг таймзон, дедуп ключей (иначе CardinalityViolation
на executemany), пропуск строк без обязательных полей.
"""

from datetime import datetime

from backend.services.funnel.ad_finance_sync import _parse_dt, _payment_rows, _upd_rows


def test_parse_dt_with_tz_to_utc():
    # +03:00 МСК → UTC-naive (минус 3 часа)
    assert _parse_dt("2026-07-11T02:58:54.785441+03:00") == datetime(2026, 7, 10, 23, 58, 54, 785441)


def test_parse_dt_with_z():
    assert _parse_dt("2022-02-04T09:06:47Z") == datetime(2022, 2, 4, 9, 6, 47)


def test_parse_dt_space_separator_no_tz():
    # payments иногда без tz, с пробелом — принимаем как есть (naive)
    assert _parse_dt("2025-04-06 01:42:29") == datetime(2025, 4, 6, 1, 42, 29)


def test_parse_dt_bad_values():
    assert _parse_dt(None) is None
    assert _parse_dt("") is None
    assert _parse_dt("не дата") is None


def test_upd_rows_skips_missing_time():
    """Списание без updTime нельзя атрибутировать ко дню — пропускаем."""
    items = [
        {"advertId": 1, "updTime": "2026-07-01T10:00:00+03:00", "updSum": 100, "updNum": 0},
        {"advertId": 2, "updTime": None, "updSum": 50, "updNum": 0},
    ]
    rows = _upd_rows(7, items)
    assert len(rows) == 1
    assert rows[0]["advert_id"] == 1


def test_upd_rows_dedup_full_duplicates():
    """WB может вернуть полные дубли — до executemany их надо схлопнуть (CardinalityViolation)."""
    dup = {"advertId": 5, "updTime": "2026-07-01T10:00:00+03:00", "updSum": 100, "updNum": 0}
    rows = _upd_rows(7, [dup, dict(dup)])
    assert len(rows) == 1


def test_upd_rows_distinct_sum_not_merged():
    """Разные суммы в один момент — это разные списания, схлопывать нельзя."""
    t = "2026-07-01T10:00:00+03:00"
    rows = _upd_rows(7, [
        {"advertId": 5, "updTime": t, "updSum": 100, "updNum": 0},
        {"advertId": 5, "updTime": t, "updSum": 200, "updNum": 0},
    ])
    assert len(rows) == 2


def test_payment_rows_dedup_by_wb_id():
    p = {"id": 999, "date": "2025-04-06 01:42:29", "sum": 20000, "type": 3, "statusId": 1, "cardStatus": "succeeded "}
    rows = _payment_rows(7, [p, dict(p)])
    assert len(rows) == 1
    assert rows[0]["wb_id"] == 999
    assert rows[0]["card_status"] == "succeeded"  # обрезали пробел


def test_payment_rows_skip_missing_id_or_date():
    rows = _payment_rows(7, [
        {"id": None, "date": "2025-04-06 01:42:29", "sum": 1},
        {"id": 1, "date": None, "sum": 1},
    ])
    assert rows == []
