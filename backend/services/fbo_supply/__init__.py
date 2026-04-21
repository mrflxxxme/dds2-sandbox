"""
FBO Supply service package.

Re-exports all public functions for backward compatibility.
Import from this package or from backend.services.fbo_supply_service.

Modules:
  - mappers: Pure transformations (status maps, parse dates, create/update from API)
  - sync: Sync orchestration (sync_fbo_supplies, enrich, sync_statuses, auto-deliver)
  - service: Query + link operations (list, get_items, link/unlink, list_warehouses)
"""

from .mappers import (
    FBW_BOX_TYPE_MAP,
    FBW_RATE_LIMIT_DELAY,
    FBW_STATUS_MAP,
    _create_supply_from_fbw_list,
    _map_fbw_box_type,
    _map_fbw_status,
    _parse_wb_date,
    _parse_wb_datetime,
    _update_supply_from_fbw_detail,
    _update_supply_from_fbw_list,
    _upsert_supply_items_fbw,
)
from .service import (
    get_fbo_summary,
    get_fbo_supply_items,
    link_supply_to_shipment,
    list_fbo_supplies,
    list_warehouses,
    unlink_supply_from_shipment,
)
from .sync import (
    _auto_deliver_assembly,
    _auto_deliver_shipment,
    enrich_fbo_supplies,
    sync_fbo_statuses,
    sync_fbo_supplies,
)

__all__ = [
    # mappers
    "FBW_BOX_TYPE_MAP",
    "FBW_RATE_LIMIT_DELAY",
    "FBW_STATUS_MAP",
    "_create_supply_from_fbw_list",
    "_map_fbw_box_type",
    "_map_fbw_status",
    "_parse_wb_date",
    "_parse_wb_datetime",
    "_update_supply_from_fbw_detail",
    "_update_supply_from_fbw_list",
    "_upsert_supply_items_fbw",
    # service
    "get_fbo_summary",
    "get_fbo_supply_items",
    "link_supply_to_shipment",
    "list_fbo_supplies",
    "list_warehouses",
    "unlink_supply_from_shipment",
    # sync
    "_auto_deliver_assembly",
    "_auto_deliver_shipment",
    "enrich_fbo_supplies",
    "sync_fbo_statuses",
    "sync_fbo_supplies",
]
