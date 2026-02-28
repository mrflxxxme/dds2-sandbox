"""
Tests for master_logic business rules.
"""

from datetime import datetime
from decimal import Decimal

import pandas as pd
import pytest

from backend.etl.master_logic import (
    make_txn_id,
    make_txn_id_hash,
    make_cp_key,
    get_purpose_tag,
    extract_invoice_id,
    extract_annex_id,
    apply_master_logic,
)


class TestMakeTxnId:
    def test_format(self):
        tid = make_txn_id(
            date=datetime(2024, 1, 15),
            account="40702810400810052145",
            currency="RUB",
            counterparty_account="40702810000000001234",
            counterparty="ООО Ромашка",
            income=0,
            expense=50000,
            purpose="Оплата по договору №1",
        )
        assert "20240115" in tid
        assert "40702810400810052145" in tid
        assert "RUB" in tid

    def test_stability(self):
        """Same inputs should produce same txn_id."""
        args = dict(
            date=datetime(2024, 1, 1),
            account="ACC1",
            currency="RUB",
            counterparty_account="ACC2",
            counterparty="CP",
            income=100,
            expense=0,
            purpose="test",
        )
        assert make_txn_id(**args) == make_txn_id(**args)

    def test_different_amounts_produce_different_ids(self):
        base = dict(
            date=datetime(2024, 1, 1),
            account="ACC1",
            currency="RUB",
            counterparty_account="ACC2",
            counterparty="CP",
            purpose="test",
        )
        id1 = make_txn_id(income=100, expense=0, **base)
        id2 = make_txn_id(income=200, expense=0, **base)
        assert id1 != id2


class TestMakeTxnIdHash:
    def test_returns_hex_string(self):
        h = make_txn_id_hash(
            datetime(2024, 1, 1), "ACC", "RUB", "ACC2", "CP", 100, 0, "test"
        )
        assert len(h) == 40  # SHA1 hex digest
        assert all(c in "0123456789abcdef" for c in h)


class TestMakeCpKey:
    def test_with_inn(self):
        assert make_cp_key("7701234567", "ООО Ромашка") == "7701234567"

    def test_without_inn(self):
        assert make_cp_key(None, "ООО Ромашка") == "ооо ромашка"

    def test_empty_inn_uses_counterparty(self):
        assert make_cp_key("", "ООО Test") == "ооо test"

    def test_both_none(self):
        assert make_cp_key(None, None) is None

    def test_whitespace_inn(self):
        assert make_cp_key("  ", "ООО Test") == "ооо test"


class TestGetPurposeTag:
    def test_commission(self):
        assert get_purpose_tag("Комиссия за обслуживание") == "Комиссия"
        assert get_purpose_tag("SWIFT commission") == "Комиссия"
        assert get_purpose_tag("Тариф банка") == "Комиссия"

    def test_logistics(self):
        assert get_purpose_tag("Invoice INV-001 forwarding") == "Логистика"
        assert get_purpose_tag("Доставка груза по РФ") == "Логистика"
        assert get_purpose_tag("Freight charges") == "Логистика"

    def test_order(self):
        assert get_purpose_tag("ANNEX #5 to contract") == "Заказ"
        assert get_purpose_tag("Приложение №3 к договору") == "Заказ"
        assert get_purpose_tag("According to contract") == "Заказ"

    def test_other(self):
        assert get_purpose_tag("Прочий платёж") == "Другое"
        assert get_purpose_tag(None) == "Другое"
        assert get_purpose_tag("") == "Другое"

    def test_commission_priority_over_logistics(self):
        """Commission regex is checked first."""
        assert get_purpose_tag("Commission for invoice processing") == "Комиссия"


class TestExtractInvoiceId:
    def test_extracts_id(self):
        assert extract_invoice_id("Payment for INVOICE INV-2024-001") == "INV-2024-001"

    def test_case_insensitive(self):
        assert extract_invoice_id("invoice abc123") == "ABC123"

    def test_no_match(self):
        assert extract_invoice_id("Regular payment") is None

    def test_none_input(self):
        assert extract_invoice_id(None) is None


class TestExtractAnnexId:
    def test_extracts_number(self):
        assert extract_annex_id("ANNEX #5 to contract") == "5"

    def test_cyrillic(self):
        assert extract_annex_id("Приложение №3 к договору") == "3"

    def test_appendix(self):
        assert extract_annex_id("APPENDIX 12 dated") == "12"

    def test_no_match(self):
        assert extract_annex_id("No annex here") is None


class TestApplyMasterLogic:
    def _make_df(self, rows):
        """Create normalized DataFrame from list of dicts."""
        from backend.etl.parsers import NORM_COLS
        df = pd.DataFrame(rows, columns=NORM_COLS)
        return df

    def test_internal_transfer(self):
        our_accounts = {"ACC1", "ACC2"}
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "Internal",
            "inn": None,
            "counterparty_account": "ACC2",  # ← our account
            "purpose": "Internal transfer",
            "income": 0,
            "expense": 100000,
        }])

        result = apply_master_logic(df, our_accounts, set(), {}, {})

        assert result.iloc[0]["event_type2"] == "INTERNAL_TRANSFER"
        assert result.iloc[0]["is_cashflow2"] == 0
        assert result.iloc[0]["status"] == "NO_CASHFLOW"

    def test_fx_buy(self):
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "Bank",
            "inn": None,
            "counterparty_account": "EXTERNAL",
            "purpose": "Конверсия CNY/RUB курс 12.50",
            "income": 0,
            "expense": 125000,
        }])

        result = apply_master_logic(df, {"ACC1"}, set(), {}, {})

        assert result.iloc[0]["event_type2"] == "FX_BUY"
        assert result.iloc[0]["is_cashflow2"] == 0

    def test_customs_payment(self):
        customs_accounts = {"CUSTOMS_ACC"}
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "Customs",
            "inn": None,
            "counterparty_account": "CUSTOMS_ACC",
            "purpose": "Customs duties payment",
            "income": 0,
            "expense": 50000,
        }])

        result = apply_master_logic(df, {"ACC1"}, customs_accounts, {}, {})

        assert result.iloc[0]["event_type2"] == "CUSTOMS_PAYMENT"
        assert result.iloc[0]["is_cashflow2"] == 1

    def test_oper_default(self):
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "ООО Supplier",
            "inn": "1234567890",
            "counterparty_account": "EXTERNAL_ACC",
            "purpose": "Payment for goods",
            "income": 0,
            "expense": 100000,
        }])

        result = apply_master_logic(df, {"ACC1"}, set(), {}, {})

        assert result.iloc[0]["event_type2"] == "OPER"
        assert result.iloc[0]["is_cashflow2"] == 1
        assert result.iloc[0]["status"] == "UNASSIGNED"

    def test_category_override_priority(self):
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "ООО Test",
            "inn": "111",
            "counterparty_account": "EXT",
            "purpose": "test",
            "income": 0,
            "expense": 1000,
        }])

        result_no_override = apply_master_logic(df, {"ACC1"}, set(), {}, {})
        txn_id = result_no_override.iloc[0]["txn_id"]

        # Both override and cp_category exist — override should win
        overrides = {txn_id: {"cat_lvl1": "Override_L1", "cat_lvl2": "Override_L2"}}
        cp_categories = {"111": {"cat_lvl1": "CP_L1", "cat_lvl2": "CP_L2"}}

        result = apply_master_logic(df, {"ACC1"}, set(), cp_categories, overrides)

        assert result.iloc[0]["cat_lvl1_2"] == "Override_L1"
        assert result.iloc[0]["cat_lvl2_2"] == "Override_L2"
        assert result.iloc[0]["status"] == "OK"

    def test_category_cp_fallback(self):
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "ООО Supplier",
            "inn": "999",
            "counterparty_account": "EXT",
            "purpose": "payment",
            "income": 0,
            "expense": 5000,
        }])

        cp_categories = {"999": {"cat_lvl1": "Поставщики", "cat_lvl2": "Оплата товара"}}
        result = apply_master_logic(df, {"ACC1"}, set(), cp_categories, {})

        assert result.iloc[0]["cat_lvl1_2"] == "Поставщики"
        assert result.iloc[0]["cat_lvl2_2"] == "Оплата товара"
        assert result.iloc[0]["status"] == "OK"

    def test_purpose_tag_extraction(self):
        df = self._make_df([{
            "date": datetime(2024, 1, 1),
            "bank": "VTB",
            "account": "ACC1",
            "currency": "RUB",
            "counterparty": "CP",
            "inn": None,
            "counterparty_account": "EXT",
            "purpose": "Payment for INVOICE INV-777 forwarding ANNEX #12",
            "income": 0,
            "expense": 10000,
        }])

        result = apply_master_logic(df, {"ACC1"}, set(), {}, {})

        # invoice_id should be extracted
        assert result.iloc[0]["invoice_id"] == "INV-777"
        # annex_id should be extracted
        assert result.iloc[0]["annex_id"] == "12"
