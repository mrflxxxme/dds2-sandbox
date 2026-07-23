"""
Warehouse service — backward-compatible re-export facade.

Split into:
  - warehouse_crud.py          — warehouses CRUD, delivery times
  - warehouse_stock_engine.py  — stock balance engine, movements, adjustments, summaries
  - warehouse_inbound.py       — inbound receipts
  - warehouse_outbound.py      — outbound shipments, stock transfers
"""

# ─── CRUD ──────────────────────────────────────────────────────────────────
from backend.services.warehouse_crud import (  # noqa: F401
    add_extra_counterparty,
    create_warehouse,
    delete_warehouse,
    get_delivery_times,
    get_warehouse,
    link_counterparty,
    list_warehouses,
    remove_extra_counterparty,
    reorder_warehouses,
    update_delivery_times,
    update_warehouse,
)

# ─── Inbound ──────────────────────────────────────────────────────────────
from backend.services.warehouse_inbound import (  # noqa: F401
    accept_receipt,
    cancel_receipt,
    create_receipt,
    get_receipt,
    list_receipts,
    update_receipt,
)

# ─── Outbound ─────────────────────────────────────────────────────────────
from backend.services.warehouse_outbound import (  # noqa: F401
    cancel_shipment,
    cancel_transfer,
    complete_transfer,
    create_shipment,
    create_transfer,
    deliver_shipment,
    get_shipment,
    get_transfer,
    list_shipments,
    list_transfers,
    send_transfer,
    ship_shipment,
)

# ─── Stock Engine ──────────────────────────────────────────────────────────
from backend.services.warehouse_stock_engine import (  # noqa: F401
    _get_reserved_map,
    _next_number,
    _resolve_barcode,
    _update_stock,
    create_adjustment,
    get_stock_movements,
    get_stock_summary,
    get_unified_stock_summary,
    get_warehouse_stock,
    update_cost_price,
)
