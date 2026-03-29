"""
Combined tool registry for DDS AI agent.

Aggregates all domain-specific tool lists into a single ALL_TOOLS list
and provides a TOOL_GROUPS dict mapping domain name to its tool list.
"""

from .finance import FINANCE_TOOLS
from .logistics import LOGISTICS_TOOLS
from .marketing import MARKETING_TOOLS

ALL_TOOLS: list[dict] = FINANCE_TOOLS + MARKETING_TOOLS + LOGISTICS_TOOLS

TOOL_GROUPS: dict[str, list[dict]] = {
    "finance": FINANCE_TOOLS,
    "marketing": MARKETING_TOOLS,
    "logistics": LOGISTICS_TOOLS,
}
