# ruff: noqa: RUF001, RUF002
"""
Faktura.ru JSON API → normalized NORM_COLS converter.

Counterpart of ``faktura.py`` (which parses the manual .xls export). This module
maps the live ``/accounts/{id}/motions`` JSON into the SAME normalized rows, so
``make_txn_id`` produces identical ids across the manual file import and the API
auto-sync → re-imports stay idempotent and don't create duplicates.

API operation shape (per ``data.operationInfo[]``):
    {
      "id", "absOperationId",
      "dc": bool,                 # true = debit (money out / we are payer),
                                  # false = credit (money in / we are payee)
      "date": "YYYY-MM-DD",
      "payDoc": { "<docType>": { "sum", "purpose",
                                 "payer": {name, inn, kpp, accountNumber, bank{...}},
                                 "payee": {name, inn, kpp, accountNumber, bank{...}} } }
    }

Direction (mirrors the WB-Bank .xls «Корреспондент» column = the *other* side):
    dc=false (income)  → counterparty = payer,  income = sum
    dc=true  (expense) → counterparty = payee,  expense = sum
"""

import logging

import pandas as pd

from backend.etl.parsers.helpers import (
    NORM_COLS,
    _clean_str,
    _parse_date,
    _to_decimal,
)

logger = logging.getLogger(__name__)


def _inner_doc(op: dict) -> dict:
    """Extract the payment-document body regardless of its type key (payDocRu, ...)."""
    pay_doc = op.get("payDoc")
    if isinstance(pay_doc, dict) and pay_doc:
        first = next(iter(pay_doc.values()), None)
        if isinstance(first, dict):
            return first
    return {}


def _op_to_row(op: dict, account_no: str) -> dict | str | None:
    """Map one API operation → NORM_COLS dict, "skip", or None (not a data row)."""
    if not isinstance(op, dict):
        return None

    date_raw = op.get("date")
    if not date_raw:
        return None
    try:
        dt = _parse_date(date_raw)
    except Exception as e:
        logger.warning("Faktura API op skipped: date parse error (%s): %s", date_raw, e)
        return "skip"

    doc = _inner_doc(op)
    amount_raw = doc.get("sum", op.get("sum"))

    # dc=true → debit (expense, we are payer, the other side is payee)
    is_debit = bool(op.get("dc"))
    other = doc.get("payee" if is_debit else "payer") or {}
    if not isinstance(other, dict):
        other = {}
    bank = other.get("bank") or {}
    if not isinstance(bank, dict):
        bank = {}

    amount = _to_decimal(amount_raw)
    return {
        "date": dt,
        "bank": "WB_BANK",
        "account": account_no,
        "currency": "RUB",
        "counterparty": _clean_str(other.get("name")),
        "inn": _clean_str(other.get("inn")),
        "counterparty_account": _clean_str(other.get("accountNumber")),
        "purpose": _clean_str(doc.get("purpose")),
        "income": _to_decimal(0) if is_debit else amount,
        "expense": amount if is_debit else _to_decimal(0),
    }


def faktura_ops_to_df(account_ops: dict[str, list[dict]]) -> tuple[pd.DataFrame, int]:
    """Convert {account_no: [operations]} from the API into one NORM_COLS frame.

    Returns ``(df, skipped)`` — same contract as the file parsers, so the result
    flows straight into ``etl.service.persist_df``.
    """
    rows: list[dict] = []
    skipped = 0
    for account_no, ops in account_ops.items():
        for op in ops or []:
            parsed = _op_to_row(op, account_no)
            if parsed is None:
                continue
            if isinstance(parsed, str):  # "skip" sentinel — bad date
                skipped += 1
                continue
            rows.append(parsed)

    df = pd.DataFrame(rows, columns=NORM_COLS) if rows else pd.DataFrame(columns=NORM_COLS)
    return df, skipped


def faktura_ops_to_requisites(account_ops: dict[str, list[dict]]) -> dict[str, dict]:
    """Сырые ops → {inn: {account, bik, kpp, bank_name}} для обогащения справочника.

    В transactions БИК/КПП/банк не хранятся (NORM_COLS их не содержит), а в сырых ops
    они есть — берём по контрагенту (payee на дебете / payer на кредите). Заполняем
    первое непустое значение по каждому ИНН.
    """
    out: dict[str, dict] = {}
    for ops in account_ops.values():
        for op in ops or []:
            if not isinstance(op, dict):
                continue
            doc = _inner_doc(op)
            other = doc.get("payee" if bool(op.get("dc")) else "payer") or {}
            if not isinstance(other, dict):
                continue
            inn = _clean_str(other.get("inn"))
            if not inn:
                continue
            bank_raw = other.get("bank")
            bank = bank_raw if isinstance(bank_raw, dict) else {}
            req = out.setdefault(inn, {})
            for key, val in (
                ("account", _clean_str(other.get("accountNumber"))),
                ("kpp", _clean_str(other.get("kpp"))),
                ("bik", _clean_str(bank.get("bic"))),
                ("bank_name", _clean_str(bank.get("name"))),
            ):
                if val and not req.get(key):
                    req[key] = val
    return out
