"""
Shared ETL parser helpers: column matching, decimal/string/date normalization.
Used by all per-bank parser modules.
"""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
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
