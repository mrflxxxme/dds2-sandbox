"""
Services package — business logic layer.

Routers handle HTTP concerns (request/response, validation).
Services handle business logic (data processing, API calls, calculations).
"""

from backend.services import (
    funnel,
    refs_service,
    transactions_service,
    integrations_service,
)

# Packages (decomposed from monolith files)
from backend.services import reports as reports_service  # noqa: F401
from backend.services import planning as planning_service  # noqa: F401
from backend.services import cost as cost_service  # noqa: F401

# Backward compatibility alias
funnel_service = funnel

__all__ = [
    "cost_service",
    "funnel",
    "funnel_service",
    "planning_service",
    "reports_service",
    "refs_service",
    "transactions_service",
    "integrations_service",
]
