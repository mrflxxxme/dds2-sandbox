"""
Parser for WB tariff xlsx files.

Expected columns: Категория, Предмет, Склад WB %
Only Предмет (subject_name) and Склад WB % (commission_rate) are used.
"""

import io
import logging
from decimal import Decimal

import pandas as pd

logger = logging.getLogger("dds.etl.tariff")


def parse_tariff_xlsx(data: bytes) -> list[dict]:
    """Parse WB tariff xlsx and return list of {subject_name, commission_rate}.

    Flexible column detection — matches Russian headers.
    Deduplicates by subject_name (keeps last occurrence).
    Validates rates are 0-100.
    """
    df = pd.read_excel(io.BytesIO(data), engine="openpyxl")

    if df.empty:
        raise ValueError("Файл пустой")

    # ── Flexible column mapping ──
    col_subject = None
    col_rate = None

    for c in df.columns:
        cl = str(c).lower().strip()
        if cl in ("предмет", "subject", "предмет товара"):
            col_subject = c
        elif "склад wb" in cl or "склад вб" in cl or cl in ("комиссия", "commission", "комиссия %", "тариф"):
            col_rate = c

    if not col_subject:
        raise ValueError(
            "Не найдена колонка 'Предмет'. "  # noqa: RUF001
            "Ожидаемые колонки: Категория, Предмет, Склад WB %"
        )
    if not col_rate:
        raise ValueError(
            "Не найдена колонка с тарифом (Склад WB %). "  # noqa: RUF001
            "Ожидаемые колонки: Категория, Предмет, Склад WB %"
        )

    # ── Clean data ──
    df = df.rename(columns={col_subject: "subject_name", col_rate: "commission_rate"})
    df = df[["subject_name", "commission_rate"]].copy()

    # Drop rows with empty subject
    df = df.dropna(subset=["subject_name"])
    df["subject_name"] = df["subject_name"].astype(str).str.strip()
    df = df[df["subject_name"] != ""]

    # Parse rate as numeric
    df["commission_rate"] = pd.to_numeric(df["commission_rate"], errors="coerce")
    bad_rates = df["commission_rate"].isna()
    if bad_rates.any():
        bad_subjects = df.loc[bad_rates, "subject_name"].head(5).tolist()
        logger.warning(f"Пропущены строки с нечисловым тарифом: {bad_subjects}")  # noqa: RUF001
        df = df[~bad_rates]

    # Validate range 0-100
    out_of_range = (df["commission_rate"] < 0) | (df["commission_rate"] > 100)
    if out_of_range.any():
        bad = df.loc[out_of_range, "subject_name"].head(5).tolist()
        raise ValueError(f"Тариф вне диапазона 0-100% для: {bad}")

    if df.empty:
        raise ValueError("Нет валидных строк после обработки")

    # Deduplicate by subject_name (keep last)
    df = df.drop_duplicates(subset=["subject_name"], keep="last")

    # Convert to list of dicts
    result = []
    for _, row in df.iterrows():
        result.append(
            {
                "subject_name": row["subject_name"],
                "commission_rate": Decimal(str(round(float(row["commission_rate"]), 4))),
            }
        )

    logger.info(f"Parsed {len(result)} tariffs from xlsx")
    return result
