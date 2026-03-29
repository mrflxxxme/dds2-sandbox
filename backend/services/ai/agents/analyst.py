"""
Senior analyst agent — cross-domain analysis, digests, anomaly detection.

Has access to ALL tools (finance + marketing + logistics) and uses a higher
token budget for complex multi-domain answers.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.analyst import ANALYST_PROMPT
from backend.services.ai.tools.all_tools import ALL_TOOLS


class AnalystAgent(BaseAgent):
    """Senior analyst: cross-domain insights, digests, trend detection."""

    name = "analyst"
    description = "Старший аналитик — кросс-доменный анализ, дайджесты, аномалии"
    temperature = 0.3
    max_tokens = 4096
    max_rounds = 8

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return ANALYST_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return ALL_TOOLS
