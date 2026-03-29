"""
Marketer agent — sales funnel, advertising, CTR, DRR analysis.

Thin wrapper over BaseAgent with marketing-specific prompt and tools.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.marketer import MARKETER_PROMPT
from backend.services.ai.tools.marketing import MARKETING_TOOLS


class MarketerAgent(BaseAgent):
    """Marketing analyst: funnel, ads, conversions, product rankings."""

    name = "marketer"
    description = "Маркетинг-аналитик — воронка, реклама, CTR, ДРР"
    temperature = 0.3

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return MARKETER_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return MARKETING_TOOLS
