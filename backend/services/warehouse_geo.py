"""Geo-mapping: region → nearest WB warehouse using Haversine distance.

Logic only — all static data is in warehouse_geo_data.py.
"""

import math
from typing import Optional

from backend.services.warehouse_geo_data import (
    WAREHOUSE_COORDS,
    WB_API_ID_TO_STOCK_NAME,
    ACCEPTANCE_TO_STOCK_NAME,
    REGION_COORDS,
    CITY_COORDS,
    KZ_WAREHOUSES,
    KZ_OKRUGS,
)

# Re-export data constants for backward compatibility
__all__ = [
    "WAREHOUSE_COORDS", "WB_API_ID_TO_STOCK_NAME", "ACCEPTANCE_TO_STOCK_NAME",
    "REGION_COORDS", "CITY_COORDS", "KZ_WAREHOUSES", "KZ_OKRUGS",
    "haversine", "find_nearest_warehouse", "find_nearest_warehouse_by_city",
    "get_all_warehouse_names", "normalize_acceptance_wh_name",
    "is_storage_warehouse", "get_country_filtered_warehouses",
]

# Prefixes/suffixes to exclude from acceptance API results
SC_PREFIX = "СЦ "
SKIP_SUFFIXES = ("СГТ", "Питание", "Горючее")


def normalize_acceptance_wh_name(name: str) -> str:
    """Convert warehouse name from Acceptance API format to Stocks API format."""
    return ACCEPTANCE_TO_STOCK_NAME.get(name, name)


def is_storage_warehouse(entry: dict) -> bool:
    """Check if acceptance API entry is a proper storage warehouse."""
    if entry.get("isSortingCenter"):
        return False
    wh = entry.get("warehouseName", "")
    if not wh:
        return False
    if wh.startswith(SC_PREFIX):
        return False
    if any(wh.endswith(s) for s in SKIP_SUFFIXES):
        return False
    return True


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in km between two points on Earth."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def find_nearest_warehouse(
    region_name: str,
    open_warehouses: list[str],
) -> Optional[str]:
    """Find nearest open WB warehouse for the given buyer region."""
    region_coord = REGION_COORDS.get(region_name)
    if not region_coord:
        return None

    best_wh = None
    best_dist = float("inf")

    for wh_name in open_warehouses:
        wh_coord = WAREHOUSE_COORDS.get(wh_name)
        if not wh_coord:
            continue
        dist = haversine(region_coord[0], region_coord[1], wh_coord[0], wh_coord[1])
        if dist < best_dist:
            best_dist = dist
            best_wh = wh_name

    return best_wh


def get_all_warehouse_names() -> list[str]:
    """Return all known WB warehouse names."""
    return list(WAREHOUSE_COORDS.keys())


def find_nearest_warehouse_by_city(
    city: str,
    open_warehouses: list[str],
) -> Optional[str]:
    """Find nearest open WB warehouse for the given delivery city."""
    city_coord = CITY_COORDS.get(city)
    if not city_coord:
        return None

    best_wh = None
    best_dist = float("inf")

    for wh_name in open_warehouses:
        wh_coord = WAREHOUSE_COORDS.get(wh_name)
        if not wh_coord:
            continue
        dist = haversine(city_coord[0], city_coord[1], wh_coord[0], wh_coord[1])
        if dist < best_dist:
            best_dist = dist
            best_wh = wh_name

    return best_wh


def get_country_filtered_warehouses(
    okrug: str | None,
    open_warehouses: list[str],
) -> list[str]:
    """Filter open warehouses by country based on order's okrug."""
    if okrug and okrug in KZ_OKRUGS:
        return [w for w in open_warehouses if w in KZ_WAREHOUSES]
    else:
        return [w for w in open_warehouses if w not in KZ_WAREHOUSES]
