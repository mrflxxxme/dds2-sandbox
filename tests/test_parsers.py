"""
Tests for ETL parsers.
"""

from decimal import Decimal

import pytest

from backend.etl.parsers import (
    parse_vtb_rub,
    parse_vtb_cny,
    parse_wb_main,
    parse_statement,
    _find_columns,
    _to_decimal,
    _clean_str,
    _parse_date,
    NORM_COLS,
)


class TestHelpers:
    def test_to_decimal_normal(self):
        assert _to_decimal(100.50) == Decimal("100.50")

    def test_to_decimal_none(self):
        assert _to_decimal(None) == Decimal("0")

    def test_to_decimal_nan(self):
        assert _to_decimal(float("nan")) == Decimal("0")

    def test_to_decimal_string(self):
        assert _to_decimal("1234.56") == Decimal("1234.56")

    def test_to_decimal_invalid(self):
        assert _to_decimal("not_a_number") == Decimal("0")

    def test_clean_str_normal(self):
        assert _clean_str("  hello  ") == "hello"

    def test_clean_str_none(self):
        assert _clean_str(None) is None

    def test_clean_str_empty(self):
        assert _clean_str("   ") is None

    def test_parse_date_dd_mm_yyyy(self):
        dt = _parse_date("01.02.2024")
        assert dt.year == 2024
        assert dt.month == 2
        assert dt.day == 1

    def test_parse_date_iso(self):
        dt = _parse_date("2024-03-15")
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 15

    def test_parse_date_invalid(self):
        with pytest.raises(ValueError, match="Cannot parse date"):
            _parse_date("NOT_A_DATE")


class TestFindColumns:
    def test_finds_exact_match(self):
        import pandas as pd
        df = pd.DataFrame(columns=["Дата", "Контрагент", "Дебет RUR"])
        result = _find_columns(df, {
            "date": ["Дата"],
            "counterparty": ["Контрагент"],
        })
        assert result == {"date": "Дата", "counterparty": "Контрагент"}

    def test_finds_partial_match(self):
        import pandas as pd
        df = pd.DataFrame(columns=["Дата операции", "Сумма дебет", "Назначение платежа"])
        result = _find_columns(df, {
            "date": ["Дата"],
            "purpose": ["Назначение"],
        })
        assert result["date"] == "Дата операции"
        assert result["purpose"] == "Назначение платежа"

    def test_missing_column_raises(self):
        import pandas as pd
        df = pd.DataFrame(columns=["Дата", "Сумма"])
        with pytest.raises(KeyError, match="not found"):
            _find_columns(df, {"counterparty": ["Контрагент"]})


class TestParseVtbRub:
    def test_basic_parsing(self, vtb_rub_excel):
        df, skipped = parse_vtb_rub(vtb_rub_excel, "VTB_RUB_MAIN", "40702810400810052145")

        # "ИТОГО" has a string in date column → logged and skipped
        assert skipped == 1
        assert len(df) == 2
        assert list(df.columns) == NORM_COLS

        # First row: expense
        row0 = df.iloc[0]
        assert row0["bank"] == "VTB"
        assert row0["currency"] == "RUB"
        assert row0["expense"] == Decimal("50000.00")
        assert row0["income"] == Decimal("0")
        assert row0["counterparty"] == "ООО Ромашка"
        # INN may come as float from Excel (e.g. "7701234567.0")
        assert row0["inn"].startswith("7701234567")

        # Second row: income
        row1 = df.iloc[1]
        assert row1["income"] == Decimal("100000.00")
        assert row1["expense"] == Decimal("0")

    def test_skips_bad_dates(self, vtb_rub_excel_bad_dates):
        df, skipped = parse_vtb_rub(vtb_rub_excel_bad_dates, "VTB_RUB_MAIN", "40702810400810052145")

        assert skipped == 1  # "NOT_A_DATE" row skipped
        assert len(df) == 2  # 2 valid rows


class TestParseVtbCny:
    def test_basic_parsing(self, vtb_cny_excel):
        df, skipped = parse_vtb_cny(vtb_cny_excel, "40702156916110000346")

        assert skipped == 0
        assert len(df) == 1
        assert df.iloc[0]["currency"] == "CNY"
        assert df.iloc[0]["expense"] == Decimal("5000.00")
        assert df.iloc[0]["counterparty"] == "SUPPLIER CO LTD"


class TestParseWb:
    def test_basic_parsing(self, wb_excel):
        df, skipped = parse_wb_main(wb_excel, "40702810800000001893")

        # Sub-header row ("Корреспондент") fails date parsing → skipped
        assert len(df) >= 1
        assert df.iloc[0]["bank"] == "WB"
        assert df.iloc[0]["currency"] == "RUB"


class TestParseStatement:
    def test_unknown_source_raises(self):
        with pytest.raises(ValueError, match="Unknown source_type"):
            parse_statement("UNKNOWN_BANK", b"data", "12345")

    def test_dispatches_vtb_rub(self, vtb_rub_excel):
        df, skipped = parse_statement("VTB_RUB_MAIN", vtb_rub_excel, "40702810400810052145")
        assert len(df) == 2
