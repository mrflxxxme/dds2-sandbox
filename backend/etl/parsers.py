"""
ETL parsers: raw bank statements → normalized DataFrame.
Each parser returns a DataFrame with standardized columns:
  date, bank, account, currency, counterparty, inn, counterparty_account,
  purpose, income, expense
"""

import re
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Optional

import pandas as pd


# ─── Column mapping & normalization helpers ──────────────────────────────────

def _to_decimal(val) -> Decimal:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return Decimal("0")
    try:
        return Decimal(str(val)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return Decimal("0")


def _clean_str(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    return str(val).strip() or None


def _parse_date(val) -> datetime:
    if isinstance(val, datetime):
        return val.replace(hour=0, minute=0, second=0, microsecond=0)
    if isinstance(val, str):
        for fmt in ["%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"]:
            try:
                return datetime.strptime(val.strip(), fmt)
            except ValueError:
                continue
    try:
        return pd.to_datetime(val).to_pydatetime().replace(hour=0, minute=0, second=0, microsecond=0)
    except Exception:
        raise ValueError(f"Cannot parse date: {val!r}")


NORM_COLS = [
    "date", "bank", "account", "currency",
    "counterparty", "inn", "counterparty_account",
    "purpose", "income", "expense",
]


# ─── VTB RUB parser (MAIN / TRANSIT) ─────────────────────────────────────────

def parse_vtb_rub(data: bytes, source_type: str, account_no: str) -> pd.DataFrame:
    """
    Columns: Дата, Номер, Вид операции, Контрагент, ИНН контрагента,
             БИК банка контрагента, Счет контрагента, Дебет RUR, Кредит RUR, Назначение
    Debit  = expense (money out), Credit = income (money in).
    """
    df = pd.read_excel(BytesIO(data), header=0)
    # drop rows where Дата is NaN
    df = df.dropna(subset=[df.columns[0]])

    bank = "VTB"
    currency = "RUB"

    rows = []
    for _, row in df.iterrows():
        try:
            dt = _parse_date(row.iloc[0])
        except Exception:
            continue

        # Debit = column index 7, Credit = column index 8
        debit = _to_decimal(row.iloc[7])
        credit = _to_decimal(row.iloc[8])

        rows.append({
            "date": dt,
            "bank": bank,
            "account": account_no,
            "currency": currency,
            "counterparty": _clean_str(row.iloc[3]),
            "inn": _clean_str(row.iloc[4]),
            "counterparty_account": _clean_str(row.iloc[6]),
            "purpose": _clean_str(row.iloc[9]),
            "income": credit,
            "expense": debit,
        })

    return pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)


# ─── VTB CNY parser ───────────────────────────────────────────────────────────

def parse_vtb_cny(data: bytes, account_no: str) -> pd.DataFrame:
    """
    Extra columns for RUB equivalent: Дебет RUR, Кредит RUR (ignored for CNY transactions).
    CNY columns: Дебет CNY (index 7), Кредит CNY (index 8)
    """
    df = pd.read_excel(BytesIO(data), header=0)
    df = df.dropna(subset=[df.columns[0]])

    rows = []
    for _, row in df.iterrows():
        try:
            dt = _parse_date(row.iloc[0])
        except Exception:
            continue

        debit_cny = _to_decimal(row.iloc[7])
        credit_cny = _to_decimal(row.iloc[8])

        rows.append({
            "date": dt,
            "bank": "VTB",
            "account": account_no,
            "currency": "CNY",
            "counterparty": _clean_str(row.iloc[3]),
            "inn": _clean_str(row.iloc[4]),
            "counterparty_account": _clean_str(row.iloc[6]),
            "purpose": _clean_str(row.iloc[11]),
            "income": credit_cny,
            "expense": debit_cny,
        })

    return pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)


# ─── WB (MAIN / PAYOUT) parser ───────────────────────────────────────────────

def _parse_wb(data: bytes, account_no: str, bank_name: str = "WB") -> pd.DataFrame:
    """
    WB format has header row 0 as column names, row 1 as sub-headers (Наименование/ИНН/КПП/...).
    Actual data starts at row 2.
    Columns: Документ, Дата операции, Корреспондент, (INN), (KPP), (Счет), (BIK),
             Вх.остаток, Оборот Дт, Оборот Кт, Назначение платежа
    """
    df = pd.read_excel(BytesIO(data), header=0, skiprows=0)
    # row 0 is the real header, row 1 has sub-headers → skip row 1
    if str(df.iloc[0, 2]).strip() in ("Наименование", "Корреспондент"):
        df = df.iloc[1:].reset_index(drop=True)

    rows = []
    for _, row in df.iterrows():
        date_val = row.iloc[1]
        if date_val is None or (isinstance(date_val, float) and pd.isna(date_val)):
            continue
        try:
            dt = _parse_date(date_val)
        except Exception:
            continue

        debit = _to_decimal(row.iloc[8])
        credit = _to_decimal(row.iloc[9])

        # counterparty sub-cols: name=2, inn=3, счет=5
        rows.append({
            "date": dt,
            "bank": bank_name,
            "account": account_no,
            "currency": "RUB",
            "counterparty": _clean_str(row.iloc[2]),
            "inn": _clean_str(row.iloc[3]),
            "counterparty_account": _clean_str(row.iloc[5]),
            "purpose": _clean_str(row.iloc[10]),
            "income": credit,
            "expense": debit,
        })

    return pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)


def parse_wb_main(data: bytes, account_no: str) -> pd.DataFrame:
    return _parse_wb(data, account_no, "WB")


def parse_wb_payout(data: bytes, account_no: str) -> pd.DataFrame:
    return _parse_wb(data, account_no, "WB")


# ─── Registry ─────────────────────────────────────────────────────────────────

SOURCE_PARSERS = {
    "VTB_RUB_MAIN": lambda data, acc: parse_vtb_rub(data, "VTB_RUB_MAIN", acc),
    "VTB_RUB_TRANSIT": lambda data, acc: parse_vtb_rub(data, "VTB_RUB_TRANSIT", acc),
    "VTB_CNY": lambda data, acc: parse_vtb_cny(data, acc),
    "WB_MAIN": parse_wb_main,
    "WB_PAYOUT": parse_wb_payout,
}


def parse_statement(source_type: str, data: bytes, account_no: str) -> pd.DataFrame:
    parser = SOURCE_PARSERS.get(source_type)
    if not parser:
        raise ValueError(f"Unknown source_type: {source_type}")
    return parser(data, account_no)
