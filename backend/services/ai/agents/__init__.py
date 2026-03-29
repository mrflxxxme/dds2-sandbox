"""
Agent registry for the multi-agent AI system.

Import all specialized agents and expose them via the AGENTS dict,
keyed by agent name for easy lookup by the router/orchestrator.
"""

from .advertiser import AdvertiserAgent
from .analyst import AnalystAgent
from .financier import FinancierAgent
from .logistician import LogisticianAgent
from .marketer import MarketerAgent

AGENTS = {
    "financier": FinancierAgent(),
    "marketer": MarketerAgent(),
    "advertiser": AdvertiserAgent(),
    "logistician": LogisticianAgent(),
    "analyst": AnalystAgent(),
}

__all__ = [
    "AdvertiserAgent",
    "AnalystAgent",
    "FinancierAgent",
    "LogisticianAgent",
    "MarketerAgent",
    "AGENTS",
]
