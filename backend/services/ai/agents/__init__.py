"""
Agent registry for the multi-agent AI system.

Import all specialized agents and expose them via the AGENTS dict,
keyed by agent name for easy lookup by the router/orchestrator.
"""

from .advertiser import AdvertiserAgent
from .analyst import AnalystAgent
from .financier import FinancierAgent
from .logistics import LogisticsAgent
from .marketer import MarketerAgent
from .supply_manager import SupplyManagerAgent

# Keep old logistician as alias for backward compat
from .logistician import LogisticianAgent

AGENTS = {
    "financier": FinancierAgent(),
    "marketer": MarketerAgent(),
    "advertiser": AdvertiserAgent(),
    "supply_manager": SupplyManagerAgent(),
    "logistics": LogisticsAgent(),
    "logistician": LogisticianAgent(),  # backward compat / fallback
    "analyst": AnalystAgent(),
}

__all__ = [
    "AdvertiserAgent",
    "AnalystAgent",
    "FinancierAgent",
    "LogisticsAgent",
    "LogisticianAgent",
    "MarketerAgent",
    "SupplyManagerAgent",
    "AGENTS",
]
