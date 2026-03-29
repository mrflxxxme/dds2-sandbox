"""
Logistics agent — shipments, delivery tracking, costs.

Monitors: shipment timelines, delivery costs, pallet counts, overdue deliveries.
Knows shipping history and costs per warehouse route.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.logistics import LOGISTICS_PROMPT
from backend.services.ai.tools.shipping import SHIPPING_TOOLS


class LogisticsAgent(BaseAgent):
    """Logistics: shipments, costs, delivery tracking."""

    name = "logistics"
    description = "Логист — отгрузки, сроки доставки, стоимость паллет"
    temperature = 0.1

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return LOGISTICS_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return SHIPPING_TOOLS
