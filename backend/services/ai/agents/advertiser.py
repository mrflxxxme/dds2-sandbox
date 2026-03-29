"""
Advertiser agent — ad campaigns, budgets, DRR optimization.

Has access to marketing tools + dedicated ad campaigns tool.
"""

from backend.services.ai.agents.base import BaseAgent
from backend.services.ai.prompts.advertiser import ADVERTISER_PROMPT
from backend.services.ai.tools.marketing import MARKETING_TOOLS


class AdvertiserAgent(BaseAgent):
    """Advertising specialist: campaigns, budgets, DRR, bid optimization."""

    name = "advertiser"
    description = "Рекламщик — кампании, бюджеты, ДРР, оптимизация ставок"
    temperature = 0.2

    def get_system_prompt(self, brand: str, today: str, memory_context: str) -> str:
        return ADVERTISER_PROMPT.format(brand=brand, today=today, memory_context=memory_context)

    def get_tools(self) -> list[dict]:
        return MARKETING_TOOLS
