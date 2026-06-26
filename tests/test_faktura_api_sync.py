"""
Tests for the Faktura.ru JSON API → ETL conversion (statement auto-sync).

Covers the mapping (faktura_ops_to_df) and that it feeds the existing master
logic correctly — especially inter-account «перемещение» detection and txn_id
idempotency. Pure-function tests (no DB).
"""

from decimal import Decimal

from backend.etl.master_logic import apply_master_logic, make_txn_id
from backend.etl.parsers.faktura_api import faktura_ops_to_df

# Two own settlement accounts (ВБ Банк, prod)
ACC_A = "40702810500001001752"
ACC_B = "40702810800000001893"


def _income_op() -> dict:
    """Incoming payment (dc=false) — real shape from the prod statement probe."""
    return {
        "id": 295520708844,
        "absOperationId": "12004964622",
        "dc": False,
        "date": "2026-06-01",
        "payDoc": {
            "payDocRu": {
                "docNumber": "467787",
                "docDate": "2026-06-01",
                "sum": 699658.3,
                "payer": {
                    "name": "РВБ ООО",
                    "inn": "9714053621",
                    "kpp": "507401001",
                    "accountNumber": "40702810825620001712",
                    "bank": {"name": "ВТБ", "bic": "044525411"},
                },
                "payee": {
                    "name": "ПЛЮС ВАЙБ",
                    "inn": "1800027275",
                    "accountNumber": ACC_A,
                    "bank": {"name": "ВБ Банк", "bic": "044525450"},
                },
                "purpose": "Оплата по договору б/н от 29.05.2026 за товар. в т.ч. НДС",
            }
        },
    }


def _expense_op() -> dict:
    """Outgoing payment (dc=true) — we are payer, counterparty is payee."""
    return {
        "id": 1,
        "dc": True,
        "date": "2026-06-02",
        "payDoc": {
            "payDocRu": {
                "sum": 12000.5,
                "payer": {"name": "ПЛЮС ВАЙБ", "inn": "1800027275", "accountNumber": ACC_A},
                "payee": {
                    "name": "ООО Поставщик",
                    "inn": "7701234567",
                    "accountNumber": "40702810000000009999",
                },
                "purpose": "Оплата счёта 42",
            }
        },
    }


class TestFakturaConverter:
    def test_income_direction(self):
        df, skipped = faktura_ops_to_df({ACC_A: [_income_op()]})
        assert skipped == 0
        assert len(df) == 1
        row = df.iloc[0]
        assert row["account"] == ACC_A
        assert row["bank"] == "WB_BANK"
        assert row["currency"] == "RUB"
        assert Decimal(str(row["income"])) == Decimal("699658.30")
        assert Decimal(str(row["expense"])) == Decimal("0")
        # income → counterparty is the PAYER (we are payee)
        assert row["counterparty"] == "РВБ ООО"
        assert row["inn"] == "9714053621"
        assert row["counterparty_account"] == "40702810825620001712"
        assert "Оплата по договору" in row["purpose"]

    def test_expense_direction(self):
        df, _ = faktura_ops_to_df({ACC_A: [_expense_op()]})
        row = df.iloc[0]
        assert Decimal(str(row["expense"])) == Decimal("12000.50")
        assert Decimal(str(row["income"])) == Decimal("0")
        # expense → counterparty is the PAYEE
        assert row["counterparty"] == "ООО Поставщик"
        assert row["counterparty_account"] == "40702810000000009999"

    def test_skips_op_without_date(self):
        df, _ = faktura_ops_to_df({ACC_A: [{"dc": False, "payDoc": {}}]})
        assert len(df) == 0

    def test_unknown_doctype_does_not_crash(self):
        op = {"dc": True, "date": "2026-06-03", "payDoc": {"memoOrder": {"sum": 5}}}
        df, _ = faktura_ops_to_df({ACC_A: [op]})
        assert len(df) == 1
        assert Decimal(str(df.iloc[0]["expense"])) == Decimal("5")
        assert df.iloc[0]["counterparty"] is None  # no payer/payee in this doc

    def test_multiple_accounts_combined(self):
        df, _ = faktura_ops_to_df({ACC_A: [_income_op()], ACC_B: [_expense_op()]})
        assert len(df) == 2
        assert set(df["account"]) == {ACC_A, ACC_B}


class TestInterAccountTransfer:
    def test_transfer_between_own_accounts_is_internal(self):
        """A↔B transfer must be flagged INTERNAL_TRANSFER (excluded from cashflow)
        on BOTH legs — same as the manual import."""
        a_leg = {  # from A's statement: expense to own account B
            "dc": True,
            "date": "2026-06-05",
            "payDoc": {
                "payDocRu": {
                    "sum": 1000000,
                    "payer": {"name": "ПЛЮС ВАЙБ", "accountNumber": ACC_A},
                    "payee": {"name": "ПЛЮС ВАЙБ", "accountNumber": ACC_B},
                    "purpose": "Перемещение между счетами",
                }
            },
        }
        b_leg = {  # from B's statement: income from own account A
            "dc": False,
            "date": "2026-06-05",
            "payDoc": {
                "payDocRu": {
                    "sum": 1000000,
                    "payer": {"name": "ПЛЮС ВАЙБ", "accountNumber": ACC_A},
                    "payee": {"name": "ПЛЮС ВАЙБ", "accountNumber": ACC_B},
                    "purpose": "Перемещение между счетами",
                }
            },
        }
        df, _ = faktura_ops_to_df({ACC_A: [a_leg], ACC_B: [b_leg]})
        assert len(df) == 2

        result = apply_master_logic(df, {ACC_A, ACC_B}, set(), {}, {})
        for _, r in result.iterrows():
            assert r["event_type2"] == "INTERNAL_TRANSFER"
            assert r["is_cashflow2"] == 0
            assert r["status"] == "NO_CASHFLOW"

    def test_external_payment_is_cashflow(self):
        """A payment to a non-own account stays a normal cashflow OPER."""
        df, _ = faktura_ops_to_df({ACC_A: [_expense_op()]})
        result = apply_master_logic(df, {ACC_A, ACC_B}, set(), {}, {})
        r = result.iloc[0]
        assert r["event_type2"] == "OPER"
        assert r["is_cashflow2"] == 1


class TestTxnIdParity:
    def test_converter_rows_yield_stable_txn_id(self):
        """Re-converting the same op yields the same txn_id → idempotent re-sync."""

        def _tid(row):
            return make_txn_id(
                date=row["date"],
                account=row["account"],
                currency=row["currency"],
                counterparty_account=row["counterparty_account"],
                counterparty=row["counterparty"],
                income=row["income"],
                expense=row["expense"],
                purpose=row["purpose"],
            )

        df1, _ = faktura_ops_to_df({ACC_A: [_income_op()]})
        df2, _ = faktura_ops_to_df({ACC_A: [_income_op()]})
        assert _tid(df1.iloc[0]) == _tid(df2.iloc[0])
