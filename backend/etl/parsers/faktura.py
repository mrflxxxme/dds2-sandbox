# ruff: noqa: RUF001, RUF002
"""
Faktura.ru (WB Bank) XML Spreadsheet 2003 parser.

Format: Excel 2003 XML with namespace `urn:schemas-microsoft-com:office:spreadsheet`.
WB Bank exports single-sheet multi-account statements in this format.

Columns (expected row layout per transaction):
  ПП/Дата | Дата | Корреспондент | ИНН | КПП | Счёт | БИК | Вх.остаток | Дт | Кт | Назначение

NORM_COLS produced: date, bank, account, currency, counterparty, inn,
counterparty_account, purpose, income, expense.
"""

import logging
import xml.etree.ElementTree as ET
from io import BytesIO

import pandas as pd

from backend.etl.parsers.helpers import (
    NORM_COLS,
    _clean_str,
    _parse_date,
    _to_decimal,
)

logger = logging.getLogger(__name__)

_NS = {"ss": "urn:schemas-microsoft-com:office:spreadsheet"}


class FakturaParseError(ValueError):
    """Raised when a Faktura XML Spreadsheet cannot be parsed."""


class FakturaParser:
    """XML Spreadsheet 2003 parser for WB Bank (Faktura.ru) statements."""

    def parse(self, data: bytes, account_no: str) -> tuple[pd.DataFrame, int]:
        """
        Parse bytes of a Faktura XML Spreadsheet.

        Returns: (normalized DataFrame with NORM_COLS, skipped_rows_count)
        """
        try:
            tree = ET.parse(BytesIO(data))  # noqa: S314 — trusted user upload; defusedxml not in requirements
        except ET.ParseError as e:
            raise FakturaParseError(f"Invalid XML: {e}") from e
        except Exception as e:  # pragma: no cover
            raise FakturaParseError(f"Failed to parse bytes: {e}") from e

        root = tree.getroot()

        # Strict namespace check (Excel 2003 XML dialect)
        expected_ns = "urn:schemas-microsoft-com:office:spreadsheet"
        if expected_ns not in (root.tag or ""):
            raise FakturaParseError(f"Wrong XML namespace / format: expected {expected_ns!r}")

        rows, skipped = self._extract_rows(root, account_no)
        df = pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)
        return df, skipped

    # ─── Internal helpers ────────────────────────────────────────────────

    @staticmethod
    def _cell_text(cell: ET.Element) -> str:
        """Extract text content from a <ss:Cell><ss:Data>...</ss:Data></ss:Cell>."""
        data = cell.find("ss:Data", _NS)
        if data is None:
            return ""
        return (data.text or "").strip()

    @classmethod
    def _row_cells_indexed(cls, row: ET.Element) -> list[str]:
        """
        Build a list of cells respecting ss:Index attribute (gaps in Excel XML).

        Excel 2003 XML uses ss:Index to skip empty columns, so plain findall won't
        preserve positional alignment.
        """
        cells_raw = row.findall("ss:Cell", _NS)
        result: list[str] = []
        pos = 0
        for cell in cells_raw:
            idx_attr = cell.attrib.get("{urn:schemas-microsoft-com:office:spreadsheet}Index")
            if idx_attr:
                try:
                    target = int(idx_attr) - 1
                except ValueError:
                    target = pos
                while len(result) < target:
                    result.append("")
                pos = target
            text = cls._cell_text(cell)
            result.append(text)
            pos += 1
        return result

    @classmethod
    def _extract_rows(cls, root: ET.Element, account_no: str) -> tuple[list[dict], int]:
        """Walk worksheet tables and produce normalized transaction rows."""
        rows_out: list[dict] = []
        skipped = 0

        worksheets = root.findall(".//ss:Worksheet", _NS)
        if not worksheets:
            return [], 0

        for ws in worksheets:
            table = ws.find("ss:Table", _NS)
            if table is None:
                continue
            for row in table.findall("ss:Row", _NS):
                cells = cls._row_cells_indexed(row)
                if not cells:
                    continue
                parsed = cls._parse_row(cells, account_no)
                if parsed is None:
                    # Not a data row (header, footer, blank) — skip without counting
                    continue
                if parsed == "skip":
                    skipped += 1
                    continue
                rows_out.append(parsed)

        return rows_out, skipped

    @classmethod
    def _parse_row(cls, cells: list[str], account_no: str) -> dict | str | None:
        """
        Map a row of cells to a normalized transaction dict.

        Expected column order (Faktura.ru WB Bank):
          0  — doc_ref (Плат.пор. №...)
          1  — date (ДД.ММ.ГГГГ)
          2  — counterparty name
          3  — INN
          4  — KPP (ignored)
          5  — counterparty account
          6  — BIK (ignored)
          7  — in_balance (ignored)
          8  — Дт (expense / debit)
          9  — Кт (income / credit)
          10 — purpose

        Returns:
            dict with NORM_COLS keys on success
            "skip" to increment skipped counter
            None if the row is not a data row (silently ignored)
        """
        if len(cells) < 3:
            return None

        doc_ref = cells[0] if len(cells) > 0 else ""
        date_cell = cells[1] if len(cells) > 1 else ""

        # Silently skip header / footer / blank rows
        if not date_cell or not date_cell.strip():
            return None
        # Header detection — если в первой/второй ячейке слово "Дата"
        if "дата" in (doc_ref or "").lower() or "дата" in date_cell.lower():
            return None
        if "итого" in (doc_ref or "").lower():
            return None

        try:
            dt = _parse_date(date_cell)
        except Exception as e:
            logger.warning("Faktura row skipped: date parse error (%s): %s", date_cell, e)
            return "skip"

        counterparty = _clean_str(cells[2]) if len(cells) > 2 else None
        inn = _clean_str(cells[3]) if len(cells) > 3 else None
        counterparty_account = _clean_str(cells[5]) if len(cells) > 5 else None

        expense = _to_decimal(cells[8]) if len(cells) > 8 else _to_decimal(None)
        income = _to_decimal(cells[9]) if len(cells) > 9 else _to_decimal(None)
        purpose = _clean_str(cells[10]) if len(cells) > 10 else None

        return {
            "date": dt,
            "bank": "WB_BANK",
            "account": account_no,
            "currency": "RUB",
            "counterparty": counterparty,
            "inn": inn,
            "counterparty_account": counterparty_account,
            "purpose": purpose,
            "income": income,
            "expense": expense,
        }


# ─── Module-level registration entry point ───────────────────────────────────


def parse_faktura_wb_bank(data: bytes, account_no: str) -> tuple[pd.DataFrame, int]:
    """Functional wrapper used by SOURCE_PARSERS registry."""
    return FakturaParser().parse(data, account_no)
