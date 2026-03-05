"""
Services package — business logic layer.

Routers handle HTTP concerns (request/response, validation).
Services handle business logic (data processing, API calls, calculations).
"""

from backend.services import (
    cost_service,
    funnel,
    planning_service,
    reports_service,
    refs_service,
    transactions_service,
    integrations_service,
)

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
