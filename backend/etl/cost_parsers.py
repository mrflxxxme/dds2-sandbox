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


def normalize_divandek(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize дивандек format Excel to standard columns."""
    col_map = {}
    for c in df.columns:
        cl = str(c).lower().strip()
        if "штрихкод" in cl or "barc" in cl:
            col_map[c] = "barcode"
        elif cl in ["количество", "кол-во", "кол"]:
            col_map[c] = "qty"
        elif "цена" in cl:
            col_map[c] = "price_cny"
        elif "вес 1 шт" in cl or "вес1" in cl:
            col_map[c] = "weight_kg"
        elif "объём" in cl and "одной" in cl:
            col_map[c] = "volume_box_m3"
        elif "кол-во в коробке" in cl:
            col_map[c] = "qty_per_box"
        elif "размер" in cl:
            col_map[c] = "size"

    df = df.rename(columns=col_map)

    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()
        df = df.reset_index(drop=True)

    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    df["volume_m3"] = df["volume_m3"].fillna(0)
    df["weight_kg"] = pd.to_numeric(df.get("weight_kg", pd.Series(dtype="float")), errors="coerce").fillna(0)
    df["area_m2"] = 0
    df["barcode"] = pd.to_numeric(df.get("barcode", pd.Series(dtype="str")), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", pd.Series(dtype="float")), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", pd.Series(dtype="float")), errors="coerce").fillna(0)

    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def normalize_carpet(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize ковры format (Chinese headers) Excel to standard columns."""
    import datetime as dt

    col_map = {}
    for c in df.columns:
        cl = str(c).strip()
        if cl == "条码" and "barcode" not in col_map.values():
            col_map[c] = "barcode"
        elif cl in ("数量", "总数量"):
            col_map[c] = "qty"
        elif cl == "单价":
            col_map[c] = "price_cny"
        elif cl == "净重":
            col_map[c] = "weight_kg_per_unit"
        elif cl == "平方数":
            col_map[c] = "area_m2"
        elif cl == "单箱体积":
            col_map[c] = "volume_box_m3"
        elif cl == "内包":
            col_map[c] = "qty_per_box"
        elif cl == "尺寸":
            col_map[c] = "size"
    df = df.rename(columns=col_map)

    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()
        df = df.reset_index(drop=True)

    def _fix_numeric(series):
        def _fix_val(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return 0.0
            if isinstance(v, (dt.datetime, dt.date)):
                return float(f"{v.day}.{v.month}")
            s = str(v).strip()
            if not s or s == "nan":
                return 0.0
            s = s.replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return 0.0
        return series.apply(_fix_val)

    df["barcode"] = pd.to_numeric(df.get("barcode", pd.Series(dtype="str")), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", pd.Series(dtype="float")), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = _fix_numeric(df.get("price_cny", pd.Series([0])))
    df["weight_kg"] = _fix_numeric(df.get("weight_kg_per_unit", pd.Series([0])))

    # Area: prefer direct 平方数 (area_m2) column, fallback to computing from size
    if "area_m2" in df.columns:
        df["area_m2"] = _fix_numeric(df["area_m2"])
    elif "size" in df.columns:
        def _area_from_size(s):
            try:
                s = str(s).strip()
                if "*" in s:
                    parts = s.split("*")
                    return float(parts[0]) / 100 * float(parts[1]) / 100
            except Exception:
                pass
            return 0.0
        df["area_m2"] = df["size"].apply(_area_from_size)
    else:
        df["area_m2"] = 0

    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    df["volume_m3"] = df["volume_m3"].fillna(0)
    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def normalize_divandek_cn(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize дивандек format with Chinese headers (客户编码 = barcode)."""
    col_map = {
        "客户编号": "barcode",
        "数量": "qty",
        "单价": "price_cny",
        "总净重": "total_net_weight",
        "单箱体积": "volume_box_m3",
        "装箱数": "qty_per_box",
        "尺寸": "size",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Filter rows with valid numeric barcodes
    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()
        df = df.reset_index(drop=True)

    # Barcode cleanup
    df["barcode"] = pd.to_numeric(df.get("barcode", pd.Series(dtype="str")), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()

    df["qty"] = pd.to_numeric(df.get("qty", pd.Series(dtype="float")), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", pd.Series(dtype="float")), errors="coerce").fillna(0)

    # Weight per unit = total_net_weight / qty
    if "total_net_weight" in df.columns:
        total_weight = pd.to_numeric(df["total_net_weight"], errors="coerce").fillna(0)
    else:
        total_weight = pd.Series([0] * len(df))
    qty_safe = df["qty"].replace(0, 1)
    df["weight_kg"] = total_weight / qty_safe

    # Area from size (e.g. "*240 + 50*7" or "180*200")
    if "size" in df.columns:
        def _area_from_size(s):
            try:
                s = str(s).strip()
                if "*" in s:
                    # Take the first two numeric dimensions
                    parts = [p.strip() for p in s.replace("+", "*").split("*") if p.strip()]
                    nums = []
                    for p in parts:
                        try:
                            nums.append(float(p))
                        except ValueError:
                            pass
                    if len(nums) >= 2:
                        return nums[0] / 100 * nums[1] / 100
            except Exception:
                pass
            return 0.0
        df["area_m2"] = df["size"].apply(_area_from_size)
    else:
        df["area_m2"] = 0

    # Volume per unit = volume_box_m3 / qty_per_box
    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    df["volume_m3"] = df["volume_m3"].fillna(0)
    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def normalize_divandek_cn_ru(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize дивандек CN format with Russian-translated headers.

    Columns: форма/款式, размер, количество, Количество упаковки,
    Количество ящиков, цена за единицу товара, Итого,
    Вес брутто в коробке, Общий чистый вес, Общий вес брутто,
    Один том в коробке, объем, Номер клиента
    """
    col_map = {}
    for c in df.columns:
        cl = str(c).lower().strip()
        if cl == "номер клиента":
            col_map[c] = "barcode"
        elif cl == "количество":
            col_map[c] = "qty"
        elif "цена за единицу" in cl:
            col_map[c] = "price_cny"
        elif "общий нетто" in cl or cl == "общий чистый вес":
            col_map[c] = "total_net_weight"
        elif "общий брутто" in cl or cl == "общий вес брутто":
            col_map[c] = "total_gross_weight"
        elif "объём 1 кор" in cl or cl == "один том в коробке":
            col_map[c] = "volume_box_m3"
        elif "в коробке" in cl or cl == "количество упаковки":
            col_map[c] = "qty_per_box"
        elif "общий объём" in cl or cl == "объем":
            col_map[c] = "total_volume"
        elif cl == "1 шт вес":
            col_map[c] = "weight_per_unit"
        elif cl == "размер" or cl == "款式":
            col_map[c] = "size"

    df = df.rename(columns=col_map)

    # Drop any duplicate columns (keep first)
    df = df.loc[:, ~df.columns.duplicated()]

    # Filter rows with valid barcodes
    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()
        df = df.reset_index(drop=True)

    df["barcode"] = pd.to_numeric(df.get("barcode", pd.Series(dtype="str")), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()

    df["qty"] = pd.to_numeric(df.get("qty", pd.Series(dtype="float")), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", pd.Series(dtype="float")), errors="coerce").fillna(0)

    # Weight per unit
    qty_safe = df["qty"].replace(0, 1)
    if "weight_per_unit" in df.columns:
        df["weight_kg"] = pd.to_numeric(df["weight_per_unit"], errors="coerce").fillna(0)
    elif "total_net_weight" in df.columns:
        total_weight = pd.to_numeric(df["total_net_weight"], errors="coerce").fillna(0)
        df["weight_kg"] = total_weight / qty_safe
    else:
        df["weight_kg"] = 0

    # Area from size
    if "size" in df.columns:
        def _area_from_size(s):
            try:
                s = str(s).strip()
                if "*" in s:
                    parts = [p.strip() for p in s.replace("+", "*").split("*") if p.strip()]
                    nums = []
                    for p in parts:
                        try:
                            nums.append(float(p))
                        except ValueError:
                            pass
                    if len(nums) >= 2:
                        return nums[0] / 100 * nums[1] / 100
            except Exception:
                pass
            return 0.0
        df["area_m2"] = df["size"].apply(_area_from_size)
    else:
        df["area_m2"] = 0

    # Volume per unit
    if "total_volume" in df.columns:
        vol = pd.to_numeric(df["total_volume"], errors="coerce").fillna(0)
        df["volume_m3"] = vol / qty_safe
    elif "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    df["volume_m3"] = df["volume_m3"].fillna(0)
    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def normalize_textile(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize textile format with dual-header rows.

    Excel has Chinese headers (row 0 in Excel) and Russian sub-headers (row 1).
    pandas reads Chinese as column names, Russian sub-headers become row 0.
    Unnamed columns get their real name from the Russian sub-header row.
    """
    # Check if first row is sub-headers (contains non-numeric text like 'Баркод', 'Артикул')
    first_row = df.iloc[0] if len(df) > 0 else None
    has_subheaders = False
    if first_row is not None:
        str_vals = [str(v).strip().lower() for v in first_row.values if pd.notna(v)]
        # If first row has known sub-header words, it's a sub-header row
        subheader_words = {"баркод", "артикул", "размер", "цвет", "заказ", "сумма", "нетто", "длина"}
        if len(set(str_vals) & subheader_words) >= 2:
            has_subheaders = True

    if has_subheaders:
        # Use first row values to rename Unnamed columns
        for i, c in enumerate(df.columns):
            if str(c).startswith("Unnamed") and i < len(first_row):
                val = str(first_row.iloc[i]).strip()
                if val and val != "nan":
                    df = df.rename(columns={c: val})
        # Drop the sub-header row
        df = df.iloc[1:].copy()
        df = df.reset_index(drop=True)

    # Now map columns to standard names
    col_map = {}
    # Two-pass barcode detection: first look for explicit barcode columns
    barcode_col = None
    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("баркод", "шк", "штрихкод", "barcode"):
            barcode_col = c
            break
    # Fallback: use 货号 only if no explicit barcode column found
    if barcode_col is None:
        for c in df.columns:
            if str(c).strip() == "货号":
                barcode_col = c
                break
    if barcode_col:
        col_map[barcode_col] = "barcode"

    for c in df.columns:
        cl = str(c).strip().lower()
        if cl in ("订单数量数量", "订单数量", "数量", "总数量", "заказ"):
            col_map[c] = "qty"
        elif cl in ("单价",) or "цена" in cl:
            col_map[c] = "price_cny"
        elif cl in ("单件净重", "нетто"):
            col_map[c] = "weight_per_unit"
        elif cl in ("单件毛重",):
            col_map[c] = "gross_weight_per_unit"
        elif cl in ("单件体积",) or cl == "объём коробки":
            col_map[c] = "volume_m3"
        elif cl in ("总体积",) or cl == "объём общий":
            col_map[c] = "total_volume"
        elif cl in ("装箱数",):
            col_map[c] = "qty_per_box"
        elif cl in ("尺寸", "размер"):
            col_map[c] = "size"

    df = df.rename(columns=col_map)

    # Drop duplicate columns and remaining unnamed
    df = df.loc[:, ~df.columns.duplicated()]
    df = df.drop(columns=[c for c in df.columns if str(c).startswith("Unnamed")], errors="ignore")

    # Filter rows with valid barcode
    if "barcode" in df.columns:
        df = df[df["barcode"].notna() & (df["barcode"].astype(str).str.strip() != "")].copy()
        df = df.reset_index(drop=True)

    df["barcode"] = df.get("barcode", pd.Series(dtype="str")).astype(str).str.strip()
    df = df[~df["barcode"].isin(["", "nan", "None"])].copy()
    df = df.reset_index(drop=True)

    # Try to convert barcode to int (remove .0)
    try:
        df["barcode"] = pd.to_numeric(df["barcode"], errors="coerce").fillna(0).astype(int).astype(str)
        df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()
    except Exception:
        pass

    df["qty"] = pd.to_numeric(df.get("qty", pd.Series(dtype="float")), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", pd.Series(dtype="float")), errors="coerce").fillna(0)

    # Weight per unit
    if "weight_per_unit" in df.columns:
        df["weight_kg"] = pd.to_numeric(df["weight_per_unit"], errors="coerce").fillna(0)
    else:
        df["weight_kg"] = 0

    # Area from size (e.g. "30x60" → convert cm² to m²)
    if "size" in df.columns:
        def _area_from_size(s):
            try:
                s = str(s).strip()
                for sep in ("*", "x", "X", "х", "Х"):
                    if sep in s:
                        parts = s.split(sep)
                        if len(parts) >= 2:
                            return float(parts[0]) / 100 * float(parts[1]) / 100
            except Exception:
                pass
            return 0.0
        df["area_m2"] = df["size"].apply(_area_from_size)
    else:
        df["area_m2"] = 0

    # Volume per unit
    if "volume_m3" in df.columns:
        df["volume_m3"] = pd.to_numeric(df["volume_m3"], errors="coerce").fillna(0)
    else:
        df["volume_m3"] = 0

    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


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
