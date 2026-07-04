"""
Assembly Request service package.

Re-exports all public functions for backward compatibility.
Import from this package or from backend.services.assembly_service.

Modules:
  - crud: CRUD operations and helpers (_build_response, list/get/create/update)
  - status: Status transitions, audit logging, bulk operations
  - analytics: FBO sync and logistics analytics
"""

from .analytics import get_logistics_analytics, refresh_active_assemblies_from_fbo, refresh_from_fbo
from .crud import (
    _build_items_with_stock,
    _build_response,
    _validate_stock_for_ship,
    create_assembly_request,
    get_assembly_attempts,
    get_assembly_request,
    get_created_groups,
    list_assembly_requests,
    list_wb_warehouses,
    prefetch_list_maps,
    update_assembly_request,
)
from .pallets import (
    PalletManifestConflict,
    apply_goods_weight,
    apply_goods_weight_bulk,
    build_pallet_layout_xlsx,
    update_pallet_manifest,
)
from .pre_distribution import (
    _advance_pre_distribution_assemblies,
    advance_pre_distribution_manual,
    create_pre_distribution,
    get_pre_distribution_vehicles,
    get_vehicle_pre_dist_pool,
)
from .prebooking import create_prebooking
from .weight import compute_goods_weight
from .status import (
    _check_transition,
    _gazelka_linked_ids,
    _log_status_change,
    assign_vehicle,
    assign_vehicle_bulk,
    cancel_request,
    close_request,
    delete_bulk,
    delete_request,
    deliver_request,
    get_assembly_history,
    mark_ready,
    reopen_for_reship,
    return_to_warehouse,
    set_status_bulk,
    ship_bulk,
    ship_joint_supply,
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
    "get_assembly_attempts",
    "get_assembly_request",
    "get_created_groups",
    "list_assembly_requests",
    "list_wb_warehouses",
    "prefetch_list_maps",
    "update_assembly_request",
    # status
    "_check_transition",
    "_gazelka_linked_ids",
    "_log_status_change",
    "assign_vehicle",
    "assign_vehicle_bulk",
    "cancel_request",
    "close_request",
    "delete_bulk",
    "delete_request",
    "deliver_request",
    "get_assembly_history",
    "mark_ready",
    "reopen_for_reship",
    "return_to_warehouse",
    "set_status_bulk",
    "ship_bulk",
    "ship_joint_supply",
    "ship_request",
    "start_assembly",
    "unassign_vehicle",
    # analytics
    "get_logistics_analytics",
    "refresh_active_assemblies_from_fbo",
    "refresh_from_fbo",
    # pre-distribution (машина в пути)
    "_advance_pre_distribution_assemblies",
    "advance_pre_distribution_manual",
    "create_pre_distribution",
    "get_pre_distribution_vehicles",
    "get_vehicle_pre_dist_pool",
    # prebooking (предзаявка на моно)
    "create_prebooking",
    # pallets (раскладка по паллетам)
    "PalletManifestConflict",
    "apply_goods_weight",
    "apply_goods_weight_bulk",
    "build_pallet_layout_xlsx",
    "update_pallet_manifest",
    # weight (расчётный вес товаров)
    "compute_goods_weight",
]
