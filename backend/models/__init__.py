"""
Models package — all SQLAlchemy models.

Models are split into domain-specific modules for maintainability.
All models are re-exported here for backward compatibility:
    from backend.models import User, Transaction, Order  # still works
"""

# Enums
from backend.models.enums import (
    EventType2,
    TransactionStatus,
    PurposeTag,
    DutyBasis,
)

# Auth
from backend.models.auth import (
    User,
    Project,
    ProjectMember,
    ProjectInvite,
)

# References
from backend.models.refs import (
    Account,
    CounterpartyCategory,
    Override,
    OpeningBalance,
    CategoryRef,
    ProjectSetting,
)

# Transactions
from backend.models.transactions import (
    Transaction,
    CategoryChangeLog,
    ImportLog,
)

# Customs
from backend.models.customs import (
    CustomsTopup,
    CustomsAlloc,
    CustomsDT,
)

# Planning
from backend.models.planning import (
    Order,
    LeadTime,
    PlannedPayment,
    PlannedIncome,
    WbPayout,
    PaymentFactLink,
)

# Cost
from backend.models.cost import (
    Nomenclature,
    DutyRule,
    CostOrder,
    CostOrderItem,
)

# Integrations & Funnel
from backend.models.integrations import (
    IntegrationKey,
    SyncLog,
    WbFunnelDaily,
    WbCostOverride,
    WbWarehouseStock,
)

# FX Rates
from backend.models.fx_rates import (
    FxRate,
)

# Tax
from backend.models.tax import (
    TaxRate,
)

# WB Finance (cached report)
from backend.models.wb_finance import (
    WbFinanceRow,
    WbFinanceSyncLog,
)

# Audit
from backend.models.audit import (
    AuditLog,
)

# Order City (WB order feed city mapping)
from backend.models.order_city import (
    OrderCityMap,
)

# Alias for backward compatibility
WbApiKey = IntegrationKey

__all__ = [
    # Enums
    "EventType2", "TransactionStatus", "PurposeTag", "DutyBasis",
    # Auth
    "User", "Project", "ProjectMember", "ProjectInvite",
    # References
    "Account", "CounterpartyCategory", "Override", "OpeningBalance", "CategoryRef", "ProjectSetting",
    # Transactions
    "Transaction", "CategoryChangeLog", "ImportLog",
    # Customs
    "CustomsTopup", "CustomsAlloc", "CustomsDT",
    # Planning
    "Order", "LeadTime", "PlannedPayment", "PlannedIncome", "WbPayout", "PaymentFactLink",
    # Cost
    "Nomenclature", "DutyRule", "CostOrder", "CostOrderItem",
    # Integrations & Funnel
    "IntegrationKey", "SyncLog", "WbFunnelDaily", "WbCostOverride", "WbWarehouseStock",
    # FX Rates
    "FxRate",
    # Tax
    "TaxRate",
    # WB Finance
    "WbFinanceRow", "WbFinanceSyncLog",
    # Audit
    "AuditLog",
    # Aliases
    "WbApiKey",
    # Order City
    "OrderCityMap",
]
