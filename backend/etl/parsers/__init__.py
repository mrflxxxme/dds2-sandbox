"""
ETL parsers package — per-bank parser modules.

This __init__.py re-exports everything for backward compatibility:
  from backend.etl.parsers import parse_statement, SOURCE_PARSERS
  from backend.etl.parsers import parse_vtb_rub, parse_wb_main, ...
"""

from backend.etl.parsers.vtb import parse_vtb_rub, parse_vtb_cny, parse_vtb_multi
from backend.etl.parsers.wb import parse_wb_main, parse_wb_payout, parse_wb_payout_cabinet, parse_wb_multi
from backend.etl.parsers.helpers import NORM_COLS

import pandas as pd


# ─── Registry ─────────────────────────────────────────────────────────────────

SOURCE_PARSERS = {
    "VTB_RUB_MAIN": lambda data, acc: parse_vtb_rub(data, "VTB_RUB_MAIN", acc),
    "VTB_RUB_TRANSIT": lambda data, acc: parse_vtb_rub(data, "VTB_RUB_TRANSIT", acc),
    "VTB_CNY": lambda data, acc: parse_vtb_cny(data, acc),
    "VTB_MULTI": lambda data, acc: parse_vtb_multi(data),
    "WB_MAIN": parse_wb_main,
    "WB_PAYOUT": parse_wb_payout,
    "WB_MULTI": lambda data, acc: parse_wb_multi(data),
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


__all__ = [
    "parse_statement",
    "SOURCE_PARSERS",
    "NORM_COLS",
    "parse_vtb_rub",
    "parse_vtb_cny",
    "parse_vtb_multi",
    "parse_wb_main",
    "parse_wb_payout",
    "parse_wb_payout_cabinet",
    "parse_wb_multi",
]
