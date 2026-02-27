"""
Master Logic: applies business rules to normalized transactions.
Produces: txn_id, cp_key, net, is_internal, is_fx, event_type2,
          is_cashflow2, purpose_tag, invoice_id, annex_id,
          cat_lvl1_2, cat_lvl2_2, status
"""

import hashlib
import re
from decimal import Decimal
from typing import Optional

import pandas as pd

# ─── Regex patterns ──────────────────────────────────────────────────────────

RE_FX = re.compile(
    r"конверси|fx|курс|cny.*rub|rub.*cny|покупка валют|продажа валют|currency",
    re.IGNORECASE,
)
RE_COMMISSION = re.compile(
    r"комисси|commission|swift|тариф|bank charge",
    re.IGNORECASE,
)
RE_LOGISTICS = re.compile(
    r"invoice|forwarding|freight|transport|доставк",
    re.IGNORECASE,
)
RE_ORDER = re.compile(
    r"annex|appendix|приложен|according to|contract|по договор",
    re.IGNORECASE,
)
RE_INVOICE_ID = re.compile(r"INVOICE\s*([A-Z0-9\-]+)", re.IGNORECASE)
RE_ANNEX_ID = re.compile(r"(?:ANNEX|APPENDIX|ПРИЛОЖЕН(?:ИЕ)?)\s*[№#]?\s*([0-9]+)", re.IGNORECASE)


# ─── txn_id ──────────────────────────────────────────────────────────────────

def make_txn_id(date, account, currency, counterparty_account, counterparty,
                income, expense, purpose) -> str:
    date_str = pd.Timestamp(date).strftime("%Y%m%d") if date is not None else "00000000"
    cp_acc = str(counterparty_account or counterparty or "").strip()[:50]
    purpose_part = str(purpose or "").strip().lower()[:80]
    inc = str(round(float(income or 0), 2))
    exp = str(round(float(expense or 0), 2))

    key = f"{date_str}|{account}|{currency}|{cp_acc}|{inc}|{exp}|{purpose_part}"
    # Return the key directly (readable, matches existing data format)
    return key


def make_txn_id_hash(date, account, currency, counterparty_account, counterparty,
                     income, expense, purpose) -> str:
    """SHA1-based stable ID for deduplication."""
    raw = make_txn_id(date, account, currency, counterparty_account, counterparty,
                      income, expense, purpose)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ─── cp_key ──────────────────────────────────────────────────────────────────

def make_cp_key(inn: Optional[str], counterparty: Optional[str]) -> Optional[str]:
    if inn and str(inn).strip():
        return str(inn).strip()
    if counterparty and str(counterparty).strip():
        return str(counterparty).strip().lower()
    return None


# ─── Purpose tag ─────────────────────────────────────────────────────────────

def get_purpose_tag(purpose: Optional[str]) -> str:
    if not purpose:
        return "Другое"
    p = str(purpose)
    if RE_COMMISSION.search(p):
        return "Комиссия"
    if RE_LOGISTICS.search(p):
        return "Логистика"
    if RE_ORDER.search(p):
        return "Заказ"
    return "Другое"


def extract_invoice_id(purpose: Optional[str]) -> Optional[str]:
    if not purpose:
        return None
    m = RE_INVOICE_ID.search(str(purpose))
    return m.group(1).upper() if m else None


def extract_annex_id(purpose: Optional[str]) -> Optional[str]:
    if not purpose:
        return None
    m = RE_ANNEX_ID.search(str(purpose))
    return m.group(1) if m else None


# ─── Master Logic pipeline ───────────────────────────────────────────────────

def apply_master_logic(
    df: pd.DataFrame,
    our_accounts: set[str],       # account numbers where is_our_account=True
    customs_payee_accounts: set[str],  # account numbers where is_customs_payee=True
    cp_categories: dict[str, dict],    # {cp_key: {cat_lvl1, cat_lvl2}}
    overrides: dict[str, dict],        # {txn_id: {cat_lvl1, cat_lvl2}}
) -> pd.DataFrame:
    """
    Input df must have NORM_COLS.
    Returns enriched df with all master logic columns.
    """
    result = df.copy()

    # ── txn_id ──
    result["txn_id"] = result.apply(
        lambda r: make_txn_id(
            r["date"], r["account"], r["currency"],
            r.get("counterparty_account"), r.get("counterparty"),
            r.get("income", 0), r.get("expense", 0), r.get("purpose"),
        ),
        axis=1,
    )

    # ── cp_key ──
    result["cp_key"] = result.apply(
        lambda r: make_cp_key(r.get("inn"), r.get("counterparty")), axis=1
    )

    # ── net ──
    result["income"] = pd.to_numeric(result["income"], errors="coerce").fillna(0)
    result["expense"] = pd.to_numeric(result["expense"], errors="coerce").fillna(0)
    result["net"] = result["income"] - result["expense"]

    # ── is_internal ──
    result["is_internal"] = result["counterparty_account"].apply(
        lambda a: bool(a and str(a).strip() in our_accounts)
    ).astype(int)

    # ── is_fx ──
    result["is_fx"] = result["purpose"].apply(
        lambda p: bool(p and RE_FX.search(str(p)))
    ).astype(int)

    # ── CUSTOMS_PAYMENT ──
    result["is_customs"] = result["counterparty_account"].apply(
        lambda a: bool(a and str(a).strip() in customs_payee_accounts)
    ).astype(int)

    # ── event_type2 (priority: internal > fx > customs > oper) ──
    def _event_type2(row):
        if row["is_internal"]:
            return "INTERNAL_TRANSFER"
        if row["is_fx"]:
            return "FX_BUY"
        if row["is_customs"]:
            return "CUSTOMS_PAYMENT"
        return "OPER"

    result["event_type2"] = result.apply(_event_type2, axis=1)

    # ── is_cashflow2 ──
    result["is_cashflow2"] = result["event_type2"].apply(
        lambda e: 0 if e in ("INTERNAL_TRANSFER", "FX_BUY") else 1
    )

    # ── SRC_IMP enrichment ──
    result["purpose_tag"] = result["purpose"].apply(get_purpose_tag)
    result["invoice_id"] = result["purpose"].apply(extract_invoice_id)
    result["annex_id"] = result["purpose"].apply(extract_annex_id)

    # ── Categories (priority: override > cp_category > empty) ──
    def _get_categories(row):
        txn_id = row["txn_id"]
        cp_key = row["cp_key"]

        if txn_id in overrides:
            ov = overrides[txn_id]
            return ov.get("cat_lvl1"), ov.get("cat_lvl2")
        if cp_key and cp_key in cp_categories:
            cp = cp_categories[cp_key]
            return cp.get("cat_lvl1"), cp.get("cat_lvl2")
        return None, None

    cats = result.apply(_get_categories, axis=1, result_type="expand")
    result["cat_lvl1_2"] = cats[0]
    result["cat_lvl2_2"] = cats[1]

    # ── status ──
    def _status(row):
        if row["is_cashflow2"] == 0:
            return "NO_CASHFLOW"
        if not row["cat_lvl1_2"]:
            return "UNASSIGNED"
        return "OK"

    result["status"] = result.apply(_status, axis=1)
    result["account_text"] = result["account"]

    # Drop helper column
    result = result.drop(columns=["is_customs"], errors="ignore")

    return result
