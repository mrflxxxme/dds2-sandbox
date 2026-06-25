"""
Assembly Request service: backward-compatible re-export shim.

The actual implementation lives in backend/services/assembly/ package:
  - crud.py: CRUD operations and helpers
  - status.py: Status transitions, audit logging, bulk operations
  - analytics.py: FBO sync and logistics analytics

All public functions are re-exported here so existing imports
like `from backend.services.assembly_service import X` or
`from backend.services import assembly_service` continue to work.
"""

from backend.services.assembly import (  # noqa: F401
    _build_items_with_stock,
    _build_response,
    _check_transition,
    _log_status_change,
    _validate_stock_for_ship,
    assign_vehicle,
    assign_vehicle_bulk,
    cancel_request,
    close_request,
    create_assembly_request,
    delete_request,
    deliver_request,
    get_assembly_attempts,
    get_assembly_history,
    get_assembly_request,
    get_created_groups,
    get_logistics_analytics,
    list_assembly_requests,
    list_wb_warehouses,
    mark_ready,
    refresh_from_fbo,
    reopen_for_reship,
    return_to_warehouse,
    ship_bulk,
    ship_request,
    start_assembly,
    unassign_vehicle,
    update_assembly_request,
)
