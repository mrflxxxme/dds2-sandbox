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
    PalletManifestConflict,
    _advance_pre_distribution_assemblies,
    _build_items_with_stock,
    _build_response,
    _check_transition,
    _gazelka_linked_ids,
    _log_status_change,
    _validate_stock_for_ship,
    advance_pre_distribution_manual,
    apply_goods_weight,
    apply_goods_weight_bulk,
    assign_vehicle,
    assign_vehicle_bulk,
    build_pallet_layout_xlsx,
    cancel_request,
    close_request,
    compute_goods_weight,
    create_assembly_request,
    create_pre_distribution,
    create_prebooking,
    delete_bulk,
    delete_request,
    deliver_request,
    get_assembly_attempts,
    get_assembly_history,
    get_assembly_request,
    get_created_groups,
    get_logistics_analytics,
    get_pre_distribution_vehicles,
    get_vehicle_pre_dist_pool,
    list_assembly_requests,
    list_source_vehicles,
    list_wb_warehouses,
    mark_ready,
    merge_assembly_requests,
    prefetch_list_maps,
    refresh_from_fbo,
    reopen_for_reship,
    return_to_warehouse,
    set_status_bulk,
    ship_bulk,
    ship_joint_supply,
    ship_request,
    start_assembly,
    unassign_vehicle,
    update_assembly_request,
    update_pallet_manifest,
)
