"""
Cost Excel parsers — detect and normalize different supplier invoice formats.

Supports:
- Дивандек RU (штрихкод columns)
- Ковры CN (条码 columns)
- Дивандек CN (客户编号 columns)

Each normalizer converts a supplier-specific Excel format to a standard schema:
    barcode, qty, price_cny, weight_kg, area_m2, volume_m3

Extracted from services/cost_service.py for maintainability.
"""

import io

import pandas as pd

from backend.etl.cost_parser_helpers import (
    normalize_carpet,
    normalize_divandek,
    normalize_divandek_cn,
    normalize_divandek_cn_ru,
    normalize_textile,
)

# Re-export normalizers so existing imports keep working
__all__ = [
    "normalize_divandek",
    "normalize_carpet",
    "normalize_divandek_cn",
    "normalize_divandek_cn_ru",
    "normalize_textile",
    "detect_and_normalize_excel",
]


def detect_and_normalize_excel(data: bytes) -> pd.DataFrame:
    """Detect Excel format by columns and normalize to standard schema."""
    import logging

    logger = logging.getLogger("dds.cost.parser")

    df = pd.read_excel(io.BytesIO(data))
    logger.info(f"Raw columns from Excel: {list(df.columns)}")

    # Deduplicate column names — some supplier files have duplicate headers
    seen = {}
    new_cols = []
    for c in df.columns:
        name = str(c).strip()
        if name in seen:
            seen[name] += 1
            new_cols.append(f"{name}_{seen[name]}")
        else:
            seen[name] = 0
            new_cols.append(name)
    df.columns = new_cols

    logger.info(f"Deduped columns: {list(df.columns)}")

    cols = list(new_cols)
    cols_lower = [c.lower() for c in cols]

    if "штрихкод" in cols or "штрихкод" in cols_lower:
        return normalize_divandek(df)
    elif "条码" in cols:
        return normalize_carpet(df)
    elif "客户编号" in cols or "客户编号" in cols_lower:
        return normalize_divandek_cn(df)
    elif "номер клиента" in cols_lower:
        return normalize_divandek_cn_ru(df)
    elif "货号" in cols:
        return normalize_textile(df)
    else:
        raise ValueError(f"Неизвестный формат файла. Колонки: {cols}")
