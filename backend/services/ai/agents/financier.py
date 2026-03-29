"""
Financier agent — BDR, OPIU, DDS, cost price analysis.

Thin wrapper over BaseAgent with finance-specific prompt and tools.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.financier import FINANCIER_PROMPT
from backend.services.ai.tools.finance import FINANCE_TOOLS


class FinancierAgent(BaseAgent):
    """Financial analyst: P&L, cash flow, margins, unit economics."""

    name = "financier"
    description = "Финансовый аналитик — BDR, OPIU, DDS, себестоимость"
    temperature = 0.2

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return FINANCIER_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return FINANCE_TOOLS
