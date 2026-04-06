"""
Supply Chain service package — factory orders and vehicle delivery management.

Usage:
    from backend.services.supply_chain import factory_orders, vehicle_delivery
"""

from backend.services.supply_chain.factory_orders import (
    add_items,
    create_factory_order,
    delete_factory_order,
    get_factory_order,
    get_factory_orders,
    split_to_vehicles,
    update_factory_order,
)
from backend.services.supply_chain.vehicle_delivery import (
    add_items_to_vehicle,
    create_vehicle,
    get_available_items,
    get_supply_chain_overview,
    get_vehicle,
    get_vehicle_history,
    list_vehicles,
    remove_item_from_vehicle,
    update_vehicle_status,
)

__all__ = [
    # Factory Orders
    "get_factory_orders",
    "get_factory_order",
    "create_factory_order",
    "update_factory_order",
    "delete_factory_order",
    "add_items",
    "split_to_vehicles",
    # Vehicles
    "list_vehicles",
    "get_vehicle",
    "create_vehicle",
    "add_items_to_vehicle",
    "remove_item_from_vehicle",
    "get_available_items",
    "update_vehicle_status",
    "get_vehicle_history",
    "get_supply_chain_overview",
]
