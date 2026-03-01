"""
Models package — all SQLAlchemy models.

All models are defined in _all_models.py and re-exported here.
This ensures backward compatibility: `from backend.models import User` still works.
"""

from backend.models._all_models import (
    # Enums
    EventType2,
    TransactionStatus,
    PurposeTag,
    DutyBasis,
    # Auth
    User,
    Project,
    ProjectMember,
    ProjectInvite,
    # References
    Account,
    CounterpartyCategory,
    Override,
    OpeningBalance,
    CategoryRef,
    # Transactions
    Transaction,
    CategoryChangeLog,
    ImportLog,
    # Customs
    CustomsTopup,
    CustomsAlloc,
    CustomsDT,
    # Planning
    Order,
    LeadTime,
    PlannedPayment,
    PlannedIncome,
    WbPayout,
    PaymentFactLink,
    # Cost
    Nomenclature,
    DutyRule,
    CostOrder,
    CostOrderItem,
    # Integrations
    IntegrationKey,
    SyncLog,
    # Funnel
    WbFunnelDaily,
    WbCostOverride,
)

# Alias for backward compatibility
WbApiKey = IntegrationKey

__all__ = [
    "EventType2", "TransactionStatus", "PurposeTag", "DutyBasis",
    "User", "Project", "ProjectMember", "ProjectInvite",
    "Account", "CounterpartyCategory", "Override", "OpeningBalance", "CategoryRef",
    "Transaction", "CategoryChangeLog", "ImportLog",
    "CustomsTopup", "CustomsAlloc", "CustomsDT",
    "Order", "LeadTime", "PlannedPayment", "PlannedIncome", "WbPayout", "PaymentFactLink",
    "Nomenclature", "DutyRule", "CostOrder", "CostOrderItem",
    "IntegrationKey", "SyncLog",
    "WbFunnelDaily", "WbCostOverride",
    "WbApiKey",
]
