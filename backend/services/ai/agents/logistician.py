"""
Logistician agent — stock levels, restocking, warehouses, geography.

Thin wrapper over BaseAgent with logistics-specific prompt and tools.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.logistician import LOGISTICIAN_PROMPT
from backend.services.ai.tools.logistics import LOGISTICS_TOOLS


class LogisticianAgent(BaseAgent):
    """Logistics analyst: inventory, supply planning, warehouse distribution."""

    name = "logistician"
    description = "Логист-аналитик — остатки, поставки, склады, география"
    temperature = 0.2

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return LOGISTICIAN_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return LOGISTICS_TOOLS
