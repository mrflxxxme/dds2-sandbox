"""
WB (Wildberries) bank statement parsers: Main, Payout, Cabinet, Multi-account.
"""

import logging
import re
import xml.etree.ElementTree as ET
from io import BytesIO
from typing import Optional

import pandas as pd

from backend.etl.parsers.helpers import (
    NORM_COLS, _clean_str, _find_columns, _parse_date, _to_decimal,
)

logger = logging.getLogger(__name__)


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
    Supports two formats:
    1. With headers: ID заявки на оплату, Сумма, Валюта, Дата создания, Статус оплаты, Комментарий банка
    2. Without headers: columns A-F positionally mapped (request_id, amount, currency, date, status, comment)
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

    # If header matching failed (no headers in the file), re-read without header
    # and use positional mapping: A=request_id, B=amount, C=currency, D=date, E=status, F=comment
    if "request_id" not in col_map or "amount" not in col_map:
        logger.info("WB_CABINET: no header detected, using positional column mapping")
        df = pd.read_excel(BytesIO(data), header=None)
        cols = list(df.columns)
        if len(cols) >= 5:
            col_map = {
                "request_id": cols[0],
                "amount": cols[1],
                "currency": cols[2] if len(cols) > 2 else None,
                "created_at": cols[3] if len(cols) > 3 else None,
                "status": cols[4] if len(cols) > 4 else None,
                "comment": cols[5] if len(cols) > 5 else None,
            }

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
            if "успешно проведена" in lower_s or "оплата" == lower_s.strip():
                status = "RECEIVED"
            elif "поручение" in lower_s:
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


# ─── WB Multi-account (XML SpreadsheetML) parser ─────────────────────────────

def parse_wb_multi(data: bytes) -> tuple[pd.DataFrame, int]:
    """
    Parse WB multi-account statement in XML SpreadsheetML (.xls) format.

    WB exports often save as XML SpreadsheetML. Multiple accounts appear in
    one sheet, separated by "Выписка по счету <account_no>" header rows.

    Returns: (combined_dataframe, total_skipped_rows)
    """
    tree = ET.parse(BytesIO(data))
    root = tree.getroot()
    ns = {'ss': 'urn:schemas-microsoft-com:office:spreadsheet'}

    all_frames: list[pd.DataFrame] = []
    total_skipped = 0

    for ws in root.findall('.//ss:Worksheet', ns):
        table = ws.find('ss:Table', ns)
        if table is None:
            continue

        xml_rows = table.findall('ss:Row', ns)

        # Convert XML rows to list of cell values
        def _row_cells(row) -> list[str]:
            cells = row.findall('ss:Cell', ns)
            return [
                (c.find('ss:Data', ns).text or "").strip()
                if c.find('ss:Data', ns) is not None else ""
                for c in cells
            ]

        # Find all "Выписка по счету" header positions
        account_sections: list[tuple[str, int]] = []
        for i, row in enumerate(xml_rows):
            cells = _row_cells(row)
            if cells and "Выписка по счету" in cells[0]:
                match = re.search(r'Выписка по счету\s+(\d+)', cells[0])
                if match:
                    account_sections.append((match.group(1), i))

        if not account_sections:
            logger.warning("WB_MULTI: no 'Выписка по счету' found")
            continue

        # Process each account section
        for sec_idx, (account_no, start_row) in enumerate(account_sections):
            end_row = (
                account_sections[sec_idx + 1][1]
                if sec_idx + 1 < len(account_sections)
                else len(xml_rows)
            )

            # Find column header row within section
            col_header_idx = None
            for i in range(start_row, min(start_row + 15, end_row)):
                cells = _row_cells(xml_rows[i])
                if any("Дата операции" in c for c in cells):
                    col_header_idx = i
                    break

            if col_header_idx is None:
                logger.warning("WB_MULTI account %s: no column headers found", account_no)
                continue

            # Build column index map from header row
            headers = _row_cells(xml_rows[col_header_idx])
            col_idx: dict[str, int] = {}
            for ci, h in enumerate(headers):
                hl = h.lower().strip()
                if "дата" in hl:
                    col_idx["date"] = ci
                elif hl == "корреспондент" or hl == "наименование":
                    if "counterparty" not in col_idx:
                        col_idx["counterparty"] = ci
                elif "оборот дт" in hl or "дебет" in hl:
                    col_idx["debit"] = ci
                elif "оборот кт" in hl or "кредит" in hl:
                    col_idx["credit"] = ci
                elif "назначение" in hl:
                    col_idx["purpose"] = ci

            if "date" not in col_idx:
                logger.warning("WB_MULTI account %s: no date column", account_no)
                continue

            # Sub-header row
            sub_header_idx = col_header_idx + 1
            sub_headers = _row_cells(xml_rows[sub_header_idx]) if sub_header_idx < end_row else []
            sub_col_idx: dict[str, int] = {}
            for ci, h in enumerate(sub_headers):
                hl = h.lower().strip()
                if "инн" == hl:
                    sub_col_idx["inn"] = ci
                elif "счет" in hl or "счёт" in hl:
                    sub_col_idx["cp_account"] = ci

            # Parse data rows (after sub-header)
            data_start = sub_header_idx + 1
            skipped = 0
            rows_data = []

            i = data_start
            while i < end_row:
                cells = _row_cells(xml_rows[i])
                if not cells:
                    i += 1
                    continue

                if cells[0].startswith("ИТОГО"):
                    break

                date_ci = col_idx.get("date", 1)
                date_val = cells[date_ci] if date_ci < len(cells) else ""
                if not date_val or date_val in ("", "Дата операции"):
                    i += 1
                    continue

                try:
                    dt = _parse_date(date_val)
                except Exception:
                    i += 1
                    skipped += 1
                    continue

                debit_ci = col_idx.get("debit", 4)
                credit_ci = col_idx.get("credit", 5)
                purpose_ci = col_idx.get("purpose", 6)
                cp_ci = col_idx.get("counterparty", 2)

                debit = _to_decimal(cells[debit_ci] if debit_ci < len(cells) else None)
                credit = _to_decimal(cells[credit_ci] if credit_ci < len(cells) else None)
                counterparty = _clean_str(cells[cp_ci] if cp_ci < len(cells) else None)
                purpose = _clean_str(cells[purpose_ci] if purpose_ci < len(cells) else None)

                inn = None
                cp_account = None
                if len(cells) > 5:
                    inn = _clean_str(cells[3] if 3 < len(cells) else None)
                    cp_account = _clean_str(cells[5] if 5 < len(cells) else None)
                    if len(cells) >= 11:
                        debit = _to_decimal(cells[8] if cells[8] else None)
                        credit = _to_decimal(cells[9] if cells[9] else None)
                        purpose = _clean_str(cells[10] if 10 < len(cells) else None)
                        counterparty = _clean_str(cells[2] if 2 < len(cells) else None)

                rows_data.append({
                    "date": dt,
                    "bank": "WB",
                    "account": account_no,
                    "currency": "RUB",
                    "counterparty": counterparty,
                    "inn": inn,
                    "counterparty_account": cp_account,
                    "purpose": purpose,
                    "income": credit,
                    "expense": debit,
                })

                i += 1

            total_skipped += skipped

            if rows_data:
                sheet_df = pd.DataFrame(rows_data, columns=NORM_COLS)
                all_frames.append(sheet_df)
                logger.info("WB_MULTI account '%s': %d rows, %d skipped",
                            account_no, len(rows_data), skipped)

    if all_frames:
        result = pd.concat(all_frames, ignore_index=True)
    else:
        result = pd.DataFrame(columns=NORM_COLS)

    logger.info("WB_MULTI total: %d rows across %d accounts, %d skipped",
                len(result), len(all_frames), total_skipped)
    return result, total_skipped
