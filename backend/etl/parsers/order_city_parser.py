"""Parser for WB order feed Excel (Лента заказов) — extracts srid → city + okrug mapping."""
import logging
from io import BytesIO

import openpyxl

logger = logging.getLogger("dds.parsers.order_city")


def parse_order_city_excel(data: bytes) -> list[dict]:
    """Parse WB order feed Excel, extract srid→city+okrug mapping.

    WB "Лента заказов" Excel structure:
      Row 1 = merged title "Заказы" (skip)
      Row 2+ = data rows (no separate header row)
      Column O (index 15, 1-based) = округ прибытия (федер. округ)
      Column P (index 16, 1-based) = город прибытия
      Column U (index 21, 1-based) = ID заказа (srid)

    Returns:
        list of {srid: str, city: str, okrug: str | None}
    """
    wb = openpyxl.load_workbook(BytesIO(data), read_only=False, data_only=True)

    # Find sheet 'Заказы'
    sheet_name = None
    for name in wb.sheetnames:
        if "заказ" in name.lower():
            sheet_name = name
            break

    if not sheet_name:
        logger.warning("Sheet 'Заказы' not found in Excel")
        return []

    ws = wb[sheet_name]

    # Fixed column positions (1-based) for WB order feed format
    OKRUG_COL = 15  # Column O = округ прибытия
    CITY_COL = 16   # Column P = город прибытия
    SRID_COL = 21   # Column U = ID заказа

    results = []
    row_count = 0

    # Data starts from row 2 (row 1 is merged title)
    for row_num in range(2, ws.max_row + 1):
        row_count += 1
        srid_val = ws.cell(row=row_num, column=SRID_COL).value
        city_val = ws.cell(row=row_num, column=CITY_COL).value
        okrug_val = ws.cell(row=row_num, column=OKRUG_COL).value

        if not srid_val or not city_val:
            continue

        srid_str = str(srid_val).strip()
        city_str = str(city_val).strip()
        okrug_str = str(okrug_val).strip() if okrug_val else None

        if not srid_str or not city_str or city_str == "-":
            continue

        results.append({"srid": srid_str, "city": city_str, "okrug": okrug_str})

    logger.info(f"Parsed order_city Excel: {len(results)} mappings from {row_count} rows")
    wb.close()
    return results
