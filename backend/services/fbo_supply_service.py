"""
FBO Supply service: backward-compatible re-export shim.

The actual implementation lives in backend/services/fbo_supply/ package:
  - mappers.py: Pure transformations (status maps, parse dates, create/update from API)
  - sync.py: Sync orchestration (sync_fbo_supplies, enrich, sync_statuses, auto-deliver)
  - service.py: Query operations (list, get_items, list_warehouses)
  - returns.py: Handle unaccepted qty (GOODS/DEFECT/UTILIZED)

All public functions are re-exported here so existing imports
like `from backend.services.fbo_supply_service import X` or
`from backend.services import fbo_supply_service` continue to work.
"""

from backend.services.fbo_supply import (  # noqa: F401
    FBW_BOX_TYPE_MAP,
    FBW_RATE_LIMIT_DELAY,
    FBW_STATUS_MAP,
    FboReturnType,
    _auto_deliver_assembly,
    _auto_deliver_shipment,
    _create_supply_from_fbw_list,
    _map_fbw_box_type,
    _map_fbw_status,
    _parse_wb_date,
    _parse_wb_datetime,
    _update_supply_from_fbw_detail,
    _update_supply_from_fbw_list,
    _upsert_supply_items_fbw,
    enrich_fbo_supplies,
    get_audit_list,
    get_audit_trail,
    get_fbo_summary,
    get_fbo_supply_items,
    get_partial_acceptance_summary,
    get_reassignment_candidates,
    list_fbo_supplies,
    list_warehouses,
    log_action,
    process_fbo_excess,
    process_fbo_return,
    reassign_to_supply,
    restore_linked_from_archive,
    revert_audit_entry,
    set_archive_flag,
    sync_fbo_statuses,
    sync_fbo_supplies,
)
