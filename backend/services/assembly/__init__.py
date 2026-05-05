"""
Assembly Request service package.

Re-exports all public functions for backward compatibility.
Import from this package or from backend.services.assembly_service.

Modules:
  - crud: CRUD operations and helpers (_build_response, list/get/create/update)
  - status: Status transitions, audit logging, bulk operations
  - analytics: FBO sync and logistics analytics
"""

from .analytics import get_logistics_analytics, refresh_from_fbo
from .crud import (
    _build_items_with_stock,
    _build_response,
    _validate_stock_for_ship,
    create_assembly_request,
    get_assembly_request,
    list_assembly_requests,
    list_wb_warehouses,
    update_assembly_request,
)
from .status import (
    _check_transition,
    _log_status_change,
    assign_vehicle,
    assign_vehicle_bulk,
    cancel_request,
    delete_request,
    deliver_request,
    get_assembly_history,
    mark_ready,
    ship_bulk,
    ship_request,
    start_assembly,
    unassign_vehicle,
)

__all__ = [
    # crud
    "_build_items_with_stock",
    "_build_response",
    "_validate_stock_for_ship",
    "create_assembly_request",
    "get_assembly_request",
    "list_assembly_requests",
    "list_wb_warehouses",
    "update_assembly_request",
    # status
    "_check_transition",
    "_log_status_change",
    "assign_vehicle",
    "assign_vehicle_bulk",
    "cancel_request",
    "delete_request",
    "deliver_request",
    "get_assembly_history",
    "mark_ready",
    "ship_bulk",
    "ship_request",
    "start_assembly",
    "unassign_vehicle",
    # analytics
    "get_logistics_analytics",
    "refresh_from_fbo",
]
