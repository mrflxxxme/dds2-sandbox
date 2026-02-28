"""
ETL parsers: raw bank statements → normalized DataFrame.
Each parser returns a DataFrame with standardized columns:
  date, bank, account, currency, counterparty, inn, counterparty_account,
  purpose, income, expense
"""

import logging
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


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


def _find_columns(df: pd.DataFrame, mapping: dict[str, list[str]]) -> dict[str, str]:
    """
    Find actual column names by partial case-insensitive match.

    Args:
        df: DataFrame with column headers
        mapping: {logical_name: [search_patterns...]}

    Returns:
        {logical_name: actual_column_name}

    Raises:
        KeyError if a required column is not found.
    """
    result = {}
    cols_lower = {str(c).strip().lower(): c for c in df.columns}

    for logical, patterns in mapping.items():
        found = None
        for pattern in patterns:
            pattern_l = pattern.lower()
            for cl, original in cols_lower.items():
                if pattern_l in cl:
                    found = original
                    break
            if found:
                break
        if found is None:
            raise KeyError(
                f"Column '{logical}' not found. "
                f"Searched for {patterns} in columns: {list(df.columns)}"
            )
        result[logical] = found

    return result


# ─── VTB RUB parser (MAIN / TRANSIT) ─────────────────────────────────────────

_VTB_RUB_COLUMNS = {
    "date": ["Дата"],
    "counterparty": ["Контрагент"],
    "inn": ["ИНН"],
    "cp_account": ["Счет контрагента", "Счёт контрагента"],
    "debit": ["Дебет"],
    "credit": ["Кредит"],
    "purpose": ["Назначение"],
}


def parse_vtb_rub(data: bytes, source_type: str, account_no: str) -> tuple[pd.DataFrame, int]:
    """
    Columns: Дата, Номер, Вид операции, Контрагент, ИНН контрагента,
             БИК банка контрагента, Счет контрагента, Дебет RUR, Кредит RUR, Назначение
    Debit  = expense (money out), Credit = income (money in).

    Returns: (DataFrame, skipped_rows_count)
    """
    df = pd.read_excel(BytesIO(data), header=0)
    col_map = _find_columns(df, _VTB_RUB_COLUMNS)

    # drop rows where date is NaN
    df = df.dropna(subset=[col_map["date"]])

    bank = "VTB"
    currency = "RUB"
    skipped = 0

    rows = []
    for idx, row in df.iterrows():
        try:
            dt = _parse_date(row[col_map["date"]])
        except Exception as e:
            logger.warning("VTB_RUB row %s skipped: date parse error: %s", idx, e)
            skipped += 1
            continue

        debit = _to_decimal(row[col_map["debit"]])
        credit = _to_decimal(row[col_map["credit"]])

        rows.append({
            "date": dt,
            "bank": bank,
            "account": account_no,
            "currency": currency,
            "counterparty": _clean_str(row[col_map["counterparty"]]),
            "inn": _clean_str(row[col_map["inn"]]),
            "counterparty_account": _clean_str(row[col_map["cp_account"]]),
            "purpose": _clean_str(row[col_map["purpose"]]),
            "income": credit,
            "expense": debit,
        })

    if skipped:
        logger.info("VTB_RUB parsing: %d rows parsed, %d skipped", len(rows), skipped)

    result = pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)
    return result, skipped


# ─── VTB CNY parser ───────────────────────────────────────────────────────────

_VTB_CNY_COLUMNS = {
    "date": ["Дата"],
    "counterparty": ["Контрагент"],
    "inn": ["ИНН"],
    "cp_account": ["Счет контрагента", "Счёт контрагента"],
    "debit": ["Дебет CNY", "Дебет юан", "Дебет"],
    "credit": ["Кредит CNY", "Кредит юан", "Кредит"],
    "purpose": ["Назначение"],
}


def parse_vtb_cny(data: bytes, account_no: str) -> tuple[pd.DataFrame, int]:
    """
    CNY columns: Дебет CNY, Кредит CNY.
    Returns: (DataFrame, skipped_rows_count)
    """
    df = pd.read_excel(BytesIO(data), header=0)
    col_map = _find_columns(df, _VTB_CNY_COLUMNS)

    df = df.dropna(subset=[col_map["date"]])
    skipped = 0

    rows = []
    for idx, row in df.iterrows():
        try:
            dt = _parse_date(row[col_map["date"]])
        except Exception as e:
            logger.warning("VTB_CNY row %s skipped: date parse error: %s", idx, e)
            skipped += 1
            continue

        debit_cny = _to_decimal(row[col_map["debit"]])
        credit_cny = _to_decimal(row[col_map["credit"]])

        rows.append({
            "date": dt,
            "bank": "VTB",
            "account": account_no,
            "currency": "CNY",
            "counterparty": _clean_str(row[col_map["counterparty"]]),
            "inn": _clean_str(row[col_map["inn"]]),
            "counterparty_account": _clean_str(row[col_map["cp_account"]]),
            "purpose": _clean_str(row[col_map["purpose"]]),
            "income": credit_cny,
            "expense": debit_cny,
        })

    if skipped:
        logger.info("VTB_CNY parsing: %d rows parsed, %d skipped", len(rows), skipped)

    result = pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)
    return result, skipped


# ─── WB (MAIN / PAYOUT) parser ───────────────────────────────────────────────

_WB_COLUMNS = {
    "date": ["Дата операции", "Дата"],
    "counterparty": ["Корреспондент", "Наименование"],
    "inn": ["ИНН"],
    "cp_account": ["Счет"],
    "debit": ["Оборот Дт", "Дебет"],
    "credit": ["Оборот Кт", "Кредит"],
    "purpose": ["Назначение платежа", "Назначение"],
}


def _parse_wb(data: bytes, account_no: str, bank_name: str = "WB") -> tuple[pd.DataFrame, int]:
    """
    WB format has header row 0 as column names, row 1 as sub-headers.
    Returns: (DataFrame, skipped_rows_count)
    """
    df = pd.read_excel(BytesIO(data), header=0, skiprows=0)
    # row 0 is the real header, row 1 has sub-headers → skip row 1
    if len(df) > 0 and str(df.iloc[0, 2]).strip() in ("Наименование", "Корреспондент"):
        df = df.iloc[1:].reset_index(drop=True)

    col_map = _find_columns(df, _WB_COLUMNS)
    skipped = 0

    rows = []
    for idx, row in df.iterrows():
        date_val = row[col_map["date"]]
        if date_val is None or (isinstance(date_val, float) and pd.isna(date_val)):
            continue
        try:
            dt = _parse_date(date_val)
        except Exception as e:
            logger.warning("WB row %s skipped: date parse error: %s", idx, e)
            skipped += 1
            continue

        debit = _to_decimal(row[col_map["debit"]])
        credit = _to_decimal(row[col_map["credit"]])

        rows.append({
            "date": dt,
            "bank": bank_name,
            "account": account_no,
            "currency": "RUB",
            "counterparty": _clean_str(row[col_map["counterparty"]]),
            "inn": _clean_str(row[col_map["inn"]]),
            "counterparty_account": _clean_str(row[col_map["cp_account"]]),
            "purpose": _clean_str(row[col_map["purpose"]]),
            "income": credit,
            "expense": debit,
        })

    if skipped:
        logger.info("WB parsing: %d rows parsed, %d skipped", len(rows), skipped)

    result = pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)
    return result, skipped


def parse_wb_main(data: bytes, account_no: str) -> tuple[pd.DataFrame, int]:
    return _parse_wb(data, account_no, "WB")


def parse_wb_payout(data: bytes, account_no: str) -> tuple[pd.DataFrame, int]:
    return _parse_wb(data, account_no, "WB")


# ─── WB Payout Cabinet parser ─────────────────────────────────────────────────

def parse_wb_payout_cabinet(data: bytes) -> list[dict]:
    """
    Parse WB seller cabinet payout Excel.
    Columns: ID заявки на оплату, Сумма, Валюта, Дата создания, Статус оплаты, Комментарий банка
    Returns list of dicts (not DataFrame — this is not a bank statement).
    """
    df = pd.read_excel(BytesIO(data), header=0)

    # Flexible column matching
    col_map: dict[str, str] = {}
    for c in df.columns:
        cl = str(c).strip().lower()
        if "id" in cl and "заявк" in cl:
            col_map["request_id"] = c
        elif cl.startswith("сумм"):
            col_map["amount"] = c
        elif "валют" in cl:
            col_map["currency"] = c
        elif "дата" in cl:
            col_map["created_at"] = c
        elif "статус" in cl:
            col_map["status"] = c
        elif "коммент" in cl:
            col_map["comment"] = c

    results = []
    skipped = 0
    for idx, row in df.iterrows():
        request_id = _clean_str(row.get(col_map.get("request_id")))
        if not request_id:
            continue

        # Parse amount: "800 373.93" → Decimal("800373.93")
        raw_amount = str(row.get(col_map.get("amount"), "0"))
        raw_amount = raw_amount.replace("\xa0", "").replace(" ", "").replace(",", ".")
        amount = _to_decimal(raw_amount)
        if amount <= 0:
            continue

        # Parse date
        raw_date = row.get(col_map.get("created_at"))
        try:
            created_at = _parse_date(raw_date)
        except Exception as e:
            logger.warning("WB_CABINET row %s skipped: date parse error: %s", idx, e)
            skipped += 1
            continue

        raw_status = _clean_str(row.get(col_map.get("status")))

        # Derive status enum
        status = "PENDING"
        if raw_status:
            lower_s = raw_status.lower()
            if "успешно проведена" in lower_s:
                status = "TRANSIT"
            elif "обрабатывается" in lower_s:
                status = "PROCESSING"

        results.append({
            "request_id": request_id,
            "amount_rub": amount,
            "currency": "RUB",
            "created_at": created_at,
            "wb_status_raw": raw_status,
            "status": status,
            "bank_comment": _clean_str(row.get(col_map.get("comment"))),
        })

    if skipped:
        logger.info("WB_CABINET parsing: %d rows parsed, %d skipped", len(results), skipped)

    return results


# ─── Registry ─────────────────────────────────────────────────────────────────

SOURCE_PARSERS = {
    "VTB_RUB_MAIN": lambda data, acc: parse_vtb_rub(data, "VTB_RUB_MAIN", acc),
    "VTB_RUB_TRANSIT": lambda data, acc: parse_vtb_rub(data, "VTB_RUB_TRANSIT", acc),
    "VTB_CNY": lambda data, acc: parse_vtb_cny(data, acc),
    "WB_MAIN": parse_wb_main,
    "WB_PAYOUT": parse_wb_payout,
}


def parse_statement(source_type: str, data: bytes, account_no: str) -> tuple[pd.DataFrame, int]:
    """
    Parse a bank statement file.
    Returns: (normalized_dataframe, skipped_rows_count)
    """
    parser = SOURCE_PARSERS.get(source_type)
    if not parser:
        raise ValueError(f"Unknown source_type: {source_type}")
    return parser(data, account_no)
