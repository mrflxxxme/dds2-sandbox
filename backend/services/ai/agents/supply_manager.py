"""
Supply manager agent — stock needs, RF warehouses, assembly requests.

Monitors: WB stock levels, RF warehouse availability, assembly request creation.
Ensures products get from RF warehouses to WB without gaps.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.supply_manager import SUPPLY_MANAGER_PROMPT
from backend.services.ai.tools.supply import SUPPLY_TOOLS


class SupplyManagerAgent(BaseAgent):
    """Supply chain: stock needs, RF availability, assembly requests."""

    name = "supply_manager"
    description = "Снабженец — потребности WB, остатки РФ, заявки на сборку"
    temperature = 0.1

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return SUPPLY_MANAGER_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return SUPPLY_TOOLS
