"""
Tests for Faktura.ru (WB Bank) XML Spreadsheet 2003 parser.
Uses fixture: tests/fixtures/faktura_wb_bank_sample.xml
"""

from decimal import Decimal
from pathlib import Path

import pytest

from backend.etl.parsers.faktura import FakturaParseError, FakturaParser

FIXTURE_DIR = Path(__file__).parent / "fixtures"
SAMPLE_XML_PATH = FIXTURE_DIR / "faktura_wb_bank_sample.xml"


@pytest.fixture
def sample_xml_bytes() -> bytes:
    """Load the anonymized sample Faktura XML file."""
    return SAMPLE_XML_PATH.read_bytes()


@pytest.fixture
def parser() -> FakturaParser:
    return FakturaParser()


# ─── Basic parsing ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_valid_file(parser, sample_xml_bytes):
    """Parsing valid file returns 10 ParsedTransaction rows."""
    df, skipped = parser.parse(sample_xml_bytes, account_no="")
    # Sample has 10 data rows
    assert len(df) == 10, f"Expected 10 rows, got {len(df)}"
    assert skipped >= 0


@pytest.mark.asyncio
async def test_all_inns_extracted(parser, sample_xml_bytes):
    """All 10 rows have inn column (may be None for some)."""
    df, _ = parser.parse(sample_xml_bytes, account_no="")
    assert "inn" in df.columns
    # At least some INNs extracted
    non_null = df["inn"].dropna()
    assert len(non_null) >= 5, f"Expected ≥5 non-null INNs, got {len(non_null)}"


@pytest.mark.asyncio
async def test_amounts_are_decimal(parser, sample_xml_bytes):
    """Income/expense amounts are Decimal-compatible (no floats stored as such)."""
    df, _ = parser.parse(sample_xml_bytes, account_no="")
    assert "income" in df.columns
    assert "expense" in df.columns
    # Verify values parse to Decimal without errors
    for val in df["income"].dropna():
        d = Decimal(str(val))
        assert d >= Decimal("0")
    for val in df["expense"].dropna():
        d = Decimal(str(val))
        assert d >= Decimal("0")


@pytest.mark.asyncio
async def test_dates_parsed(parser, sample_xml_bytes):
    """Date column contains datetime objects."""
    from datetime import datetime

    df, _ = parser.parse(sample_xml_bytes, account_no="")
    assert "date" in df.columns
    for dt in df["date"].dropna():
        assert isinstance(dt, datetime), f"Expected datetime, got {type(dt)}"


@pytest.mark.asyncio
async def test_purpose_extracted(parser, sample_xml_bytes):
    """Purpose (назначение платежа) is extracted for all rows."""
    df, _ = parser.parse(sample_xml_bytes, account_no="")
    assert "purpose" in df.columns
    non_null_purposes = df["purpose"].dropna()
    assert len(non_null_purposes) >= 8


@pytest.mark.asyncio
async def test_norm_cols_present(parser, sample_xml_bytes):
    """All NORM_COLS are present in output DataFrame."""
    from backend.etl.parsers.helpers import NORM_COLS

    df, _ = parser.parse(sample_xml_bytes, account_no="")
    for col in NORM_COLS:
        assert col in df.columns, f"Missing NORM_COLS column: {col}"


# ─── Edge cases ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_empty_xml(parser):
    """Empty Workbook XML returns empty DataFrame."""
    empty_xml = b"""<?xml version="1.0" encoding="utf-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
          xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
  <Worksheet ss:Name="Sheet1">
    <Table>
    </Table>
  </Worksheet>
</Workbook>"""
    df, skipped = parser.parse(empty_xml, account_no="")
    assert len(df) == 0


@pytest.mark.asyncio
async def test_parse_malformed_xml(parser):
    """Malformed XML raises FakturaParseError."""
    malformed = b"<broken xml>"
    with pytest.raises(FakturaParseError):
        parser.parse(malformed, account_no="")


@pytest.mark.asyncio
async def test_parse_wrong_namespace(parser):
    """XML with wrong namespace raises FakturaParseError."""
    wrong_ns = b"""<?xml version="1.0" encoding="utf-8"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:WRONG">
  <Worksheet>
    <Table><Row><Cell><Data>test</Data></Cell></Row></Table>
  </Worksheet>
</Workbook>"""
    with pytest.raises(FakturaParseError) as exc_info:
        parser.parse(wrong_ns, account_no="")
    assert "format" in str(exc_info.value).lower() or "namespace" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_parse_not_xml(parser):
    """Non-XML bytes raise FakturaParseError."""
    with pytest.raises(FakturaParseError):
        parser.parse(b"This is not XML at all, just plain text", account_no="")


# ─── Source type registration ─────────────────────────────────────────────────


def test_faktura_registered_in_source_parsers():
    """FAKTURA_WB_BANK is registered in SOURCE_PARSERS."""
    from backend.etl.parsers import SOURCE_PARSERS

    assert "FAKTURA_WB_BANK" in SOURCE_PARSERS


def test_faktura_parser_callable_from_registry():
    """Calling via SOURCE_PARSERS returns (df, int)."""
    from backend.etl.parsers import SOURCE_PARSERS

    sample_xml = SAMPLE_XML_PATH.read_bytes()
    result = SOURCE_PARSERS["FAKTURA_WB_BANK"](sample_xml, "")
    assert isinstance(result, tuple)
    assert len(result) == 2
    df, skipped = result
    assert len(df) >= 1
