"""
Agent registry for the multi-agent AI system.

Import all specialized agents and expose them via the AGENTS dict,
keyed by agent name for easy lookup by the router/orchestrator.
"""

from .analyst import AnalystAgent
from .financier import FinancierAgent
from .logistician import LogisticianAgent
from .marketer import MarketerAgent

AGENTS = {
    "financier": FinancierAgent(),
    "marketer": MarketerAgent(),
    "logistician": LogisticianAgent(),
    "analyst": AnalystAgent(),
}

__all__ = [
    "AnalystAgent",
    "FinancierAgent",
    "LogisticianAgent",
    "MarketerAgent",
    "AGENTS",
]
