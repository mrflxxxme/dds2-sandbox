"""
Tests for ETL parsers.
"""

from decimal import Decimal

import pytest

from backend.etl.parsers import (
    NORM_COLS,
    _clean_str,
    _find_columns,
    _parse_date,
    _to_decimal,
    parse_statement,
    parse_vtb_cny,
    parse_vtb_multi,
    parse_vtb_rub,
    parse_wb_main,
    parse_wb_multi,
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
        result = _find_columns(
            df,
            {
                "date": ["Дата"],
                "counterparty": ["Контрагент"],
            },
        )
        assert result == {"date": "Дата", "counterparty": "Контрагент"}

    def test_finds_partial_match(self):
        import pandas as pd

        df = pd.DataFrame(columns=["Дата операции", "Сумма дебет", "Назначение платежа"])
        result = _find_columns(
            df,
            {
                "date": ["Дата"],
                "purpose": ["Назначение"],
            },
        )
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


class TestParseVtbMulti:
    """Tests for the multi-sheet VTB parser (VTB_MULTI)."""

    def test_parses_all_sheets(self, vtb_multi_excel):
        """Should parse all 3 sheets and return combined DataFrame."""
        df, skipped = parse_vtb_multi(vtb_multi_excel)
        # 1 row from sheet1 (deposit) + 2 rows from sheet2 (RUB) + 1 row from sheet3 (CNY) = 4
        assert len(df) == 4
        assert list(df.columns) == NORM_COLS

    def test_accounts_from_sheet_names(self, vtb_multi_excel):
        """Each row should have the correct account from its sheet."""
        df, _ = parse_vtb_multi(vtb_multi_excel)
        accounts = set(df["account"].tolist())
        assert "42102810316110029573" in accounts  # deposit
        assert "40702810400810052145" in accounts  # RUB main
        assert "40702156916110000346" in accounts  # CNY

    def test_currency_detection(self, vtb_multi_excel):
        """CNY sheet should produce CNY currency rows, RUB sheets → RUB."""
        df, _ = parse_vtb_multi(vtb_multi_excel)
        cny_rows = df[df["account"] == "40702156916110000346"]
        assert len(cny_rows) == 1
        assert cny_rows.iloc[0]["currency"] == "CNY"

        rub_rows = df[df["account"] == "40702810400810052145"]
        assert len(rub_rows) == 2
        assert rub_rows.iloc[0]["currency"] == "RUB"

    def test_amounts_correct(self, vtb_multi_excel):
        """Verify debit/credit parsing for each currency type."""
        from decimal import Decimal

        df, _ = parse_vtb_multi(vtb_multi_excel)

        # CNY row: expense=106000
        cny_row = df[df["account"] == "40702156916110000346"].iloc[0]
        assert cny_row["expense"] == Decimal("106000.00")

        # RUB main row: income=6900000
        rub_rows = df[df["account"] == "40702810400810052145"]
        first_rub = rub_rows.iloc[0]
        assert first_rub["income"] == Decimal("6900000.00")

    def test_dispatches_via_parse_statement(self, vtb_multi_excel):
        """parse_statement('VTB_MULTI', ...) should dispatch correctly."""
        df, skipped = parse_statement("VTB_MULTI", vtb_multi_excel, "ignored")
        assert len(df) == 4


class TestParseWbMulti:
    """Tests for WB multi-account parser (XML SpreadsheetML, multiple accounts in one sheet)."""

    def test_parses_all_accounts(self, wb_multi_xml_xls):
        """Should parse both account sections and return combined DataFrame."""
        df, skipped = parse_wb_multi(wb_multi_xml_xls)
        # 2 rows from account 1 + 1 row from account 2 = 3
        assert len(df) == 3
        assert list(df.columns) == NORM_COLS

    def test_accounts_extracted(self, wb_multi_xml_xls):
        """Each row should have the correct account from its section header."""
        df, _ = parse_wb_multi(wb_multi_xml_xls)
        accounts = set(df["account"].tolist())
        assert "40702810500001001752" in accounts
        assert "40702810800000001893" in accounts

    def test_amounts_correct(self, wb_multi_xml_xls):
        """Verify income/expense parsing."""
        from decimal import Decimal

        df, _ = parse_wb_multi(wb_multi_xml_xls)

        # Account 1, first row: income=500000 (credit/Оборот Кт)
        acc1_rows = df[df["account"] == "40702810500001001752"]
        assert acc1_rows.iloc[0]["income"] == Decimal("500000.00")

        # Account 2, row: income=75000
        acc2_rows = df[df["account"] == "40702810800000001893"]
        assert acc2_rows.iloc[0]["income"] == Decimal("75000.00")

    def test_dispatches_via_parse_statement(self, wb_multi_xml_xls):
        """parse_statement('WB_MULTI', ...) should dispatch correctly."""
        df, skipped = parse_statement("WB_MULTI", wb_multi_xml_xls, "ignored")
        assert len(df) == 3
