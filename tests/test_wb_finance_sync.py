"""
Tests for wb_finance_sync — pure functions: _parse_date, _row_to_values.
No DB required.
"""
import pytest
from datetime import date, datetime
from decimal import Decimal

from backend.services.wb_finance_sync import (
    _parse_date,
    _row_to_values,
    _NUMERIC_FIELDS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _parse_date
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseDate:
    """Parse date from WB API responses (various formats)."""

    def test_iso_string(self):
        """Standard ISO date string."""
        assert _parse_date("2024-01-15") == date(2024, 1, 15)

    def test_iso_datetime_string(self):
        """ISO datetime string — should take only date part."""
        assert _parse_date("2024-01-15T12:30:00") == date(2024, 1, 15)

    def test_date_object(self):
        """Already a date object."""
        d = date(2024, 6, 20)
        assert _parse_date(d) is d

    def test_none_returns_today(self):
        """None → today."""
        result = _parse_date(None)
        assert result == date.today()

    def test_empty_string_returns_today(self):
        """Empty string → today."""
        result = _parse_date("")
        assert result == date.today()

    def test_invalid_string_returns_today(self):
        """Invalid format → today."""
        result = _parse_date("not-a-date")
        assert result == date.today()

    def test_wb_api_format(self):
        """WB often returns dates like '2024-01-15T00:00:00Z'."""
        assert _parse_date("2024-01-15T00:00:00Z") == date(2024, 1, 15)

    def test_long_wb_datetime(self):
        """WB datetime with timezone offset."""
        assert _parse_date("2024-03-01T15:30:45+03:00") == date(2024, 3, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests: _row_to_values
# ═══════════════════════════════════════════════════════════════════════════════

class TestRowToValues:
    """Convert WB API row dict to DB insert values."""

    def _make_wb_row(self, **overrides) -> dict:
        """Create a minimal WB API row for testing."""
        row = {
            "rrd_id": 123456789,
            "realizationreport_id": 100,
            "date_from": "2024-01-01",
            "date_to": "2024-01-07",
            "rr_dt": "2024-01-05",
            "sale_dt": "2024-01-04",
            "order_dt": "2024-01-03",
            "sa_name": "TEST-ART-001",
            "nm_id": 54321,
            "brand_name": "TestBrand",
            "subject_name": "Футболка",
            "doc_type_name": "Продажа",
            "supplier_oper_name": "Продажа",
            "bonus_type_name": None,
            "quantity": 1,
            "retail_price_withdisc_rub": 1500.0,
            "retail_amount": 1500.0,
            "ppvz_for_pay": 1200.0,
            "ppvz_sales_commission": 300.0,
            "delivery_rub": 50.0,
            "penalty": 0,
            "additional_payment": 10.0,
            "storage_fee": 5.0,
            "acceptance": 0,
            "deduction": 0,
            "ppvz_reward": 250.0,
            "rebill_logistic_cost": 0,
            "ppvz_vw": 50.0,
            "ppvz_vw_nds": 10.0,
        }
        row.update(overrides)
        return row

    def test_basic_conversion(self):
        """Basic row converts correctly."""
        row = self._make_wb_row()
        values = _row_to_values(row, project_id=42)

        assert values["project_id"] == 42
        assert values["rrd_id"] == 123456789
        assert values["realizationreport_id"] == 100
        assert values["nm_id"] == 54321
        assert values["sa_name"] == "TEST-ART-001"
        assert values["brand_name"] == "TestBrand"
        assert values["quantity"] == 1

    def test_date_fields_parsed(self):
        """Date fields should be parsed to date objects."""
        row = self._make_wb_row()
        values = _row_to_values(row, project_id=1)

        assert values["date_from"] == date(2024, 1, 1)
        assert values["date_to"] == date(2024, 1, 7)
        assert values["rr_dt"] == date(2024, 1, 5)
        assert values["sale_dt"] == date(2024, 1, 4)
        assert values["order_dt"] == date(2024, 1, 3)

    def test_numeric_fields_are_decimal(self):
        """All numeric fields should be converted to Decimal."""
        row = self._make_wb_row()
        values = _row_to_values(row, project_id=1)

        for field in _NUMERIC_FIELDS:
            assert isinstance(values[field], Decimal), f"{field} should be Decimal"

    def test_numeric_values_correct(self):
        """Numeric fields should have correct values."""
        row = self._make_wb_row()
        values = _row_to_values(row, project_id=1)

        assert values["retail_price_withdisc_rub"] == Decimal("1500.0")
        assert values["ppvz_for_pay"] == Decimal("1200.0")
        assert values["ppvz_sales_commission"] == Decimal("300.0")
        assert values["delivery_rub"] == Decimal("50.0")
        assert values["storage_fee"] == Decimal("5.0")

    def test_missing_numeric_fields_default_to_zero(self):
        """Missing numeric fields → Decimal(0)."""
        row = {"rrd_id": 1, "sa_name": "X", "nm_id": 0}
        values = _row_to_values(row, project_id=1)

        for field in _NUMERIC_FIELDS:
            assert values[field] == Decimal("0"), f"{field} should default to 0"

    def test_none_numeric_fields_default_to_zero(self):
        """None numeric fields → Decimal(0)."""
        row = self._make_wb_row(retail_price_withdisc_rub=None, penalty=None)
        values = _row_to_values(row, project_id=1)

        assert values["retail_price_withdisc_rub"] == Decimal("0")
        assert values["penalty"] == Decimal("0")

    def test_string_truncation(self):
        """Long strings should be truncated to max column length."""
        long_name = "A" * 500
        row = self._make_wb_row(sa_name=long_name, brand_name=long_name)
        values = _row_to_values(row, project_id=1)

        assert len(values["sa_name"]) <= 200
        assert len(values["brand_name"]) <= 200

    def test_bonus_type_name_truncation(self):
        """bonus_type_name truncated to 500 chars."""
        long_bonus = "B" * 600
        row = self._make_wb_row(bonus_type_name=long_bonus)
        values = _row_to_values(row, project_id=1)

        assert len(values["bonus_type_name"]) <= 500

    def test_none_strings(self):
        """None string fields → empty string."""
        row = self._make_wb_row(sa_name=None, brand_name=None, subject_name=None)
        values = _row_to_values(row, project_id=1)

        assert values["sa_name"] == ""
        assert values["brand_name"] == ""
        assert values["subject_name"] == ""

    def test_bonus_type_name_none(self):
        """None bonus_type_name stays None (nullable field)."""
        row = self._make_wb_row(bonus_type_name=None)
        values = _row_to_values(row, project_id=1)
        assert values["bonus_type_name"] is None

    def test_bonus_type_name_empty_string(self):
        """Empty string bonus_type_name → None."""
        row = self._make_wb_row(bonus_type_name="")
        values = _row_to_values(row, project_id=1)
        assert values["bonus_type_name"] is None

    def test_missing_realizationreport_id(self):
        """Missing realizationreport_id → 0."""
        row = self._make_wb_row()
        del row["realizationreport_id"]
        values = _row_to_values(row, project_id=1)
        assert values["realizationreport_id"] == 0

    def test_synced_at_is_set(self):
        """synced_at should be set."""
        row = self._make_wb_row()
        values = _row_to_values(row, project_id=1)
        assert "synced_at" in values
        assert values["synced_at"] is not None

    def test_doc_type_name_preserved(self):
        """doc_type_name should be preserved (important for OPIU logic)."""
        row = self._make_wb_row(doc_type_name="Возврат")
        values = _row_to_values(row, project_id=1)
        assert values["doc_type_name"] == "Возврат"

    def test_supplier_oper_name_preserved(self):
        """supplier_oper_name should be preserved (important for BDR/OPIU logic)."""
        for oper in ["Продажа", "Возврат", "Логистика", "Штрафы", "Удержание"]:
            row = self._make_wb_row(supplier_oper_name=oper)
            values = _row_to_values(row, project_id=1)
            assert values["supplier_oper_name"] == oper
