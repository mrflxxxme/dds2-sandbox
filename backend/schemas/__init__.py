"""
Schemas package — all Pydantic schemas.

Schemas are split into domain-specific modules for maintainability.
All schemas are re-exported here for backward compatibility:
    from backend.schemas import TransactionSchema, OrderSchema  # still works
"""

# Common
from backend.schemas.common import (
    MessageResponse,
    StatusResponse,
    DeleteResponse,
)

# Auth
from backend.schemas.auth import (
    LoginRequest,
    TokenResponse,
    ChangePasswordRequest,
)

# References
from backend.schemas.refs import (
    AccountSchema,
    CounterpartyCategorySchema,
    OverrideSchema,
    OpeningBalanceSchema,
    CategoryRefSchema,
    CategoryRefCreate,
)

# Transactions
from backend.schemas.transactions import (
    TransactionSchema,
    TransactionFilter,
    CategoryAssignment,
    BulkCategoryAssignment,
    CategoryAssignByIds,
    UnassignedGroupRow,
)

# Import
from backend.schemas.imports import (
    ImportLogSchema,
    ImportResult,
)

# Reports
from backend.schemas.reports import (
    BalanceRow,
    DdsMonthRow,
    FxControlRow,
    BalanceDailyRow,
    IncomeDailyRow,
    IncomeByCategoryRow,
    DashboardBalances,
)

# Planning
from backend.schemas.planning import (
    OrderSchema,
    LeadTimeSchema,
    PlannedPaymentSchema,
    PlannedIncomeSchema,
    CustomsTopupSchema,
    CustomsAllocSchema,
    CashflowDailyRow,
    OrderSummarySchema,
    PaymentFactLinkSchema,
    CustomsDTSchema,
    WbPayoutSchema,
    FactLinkCreate,
    CustomsDTUpdate,
    WbReconcileRequest,
)

# Cost
from backend.schemas.cost import (
    NomenclatureSchema,
    DutyRuleSchema,
    CostOrderItemSchema,
    CostOrderSchema,
    CostUploadResult,
    VatRateUpdate,
    CostOrderCreate,
)

# Integrations
from backend.schemas.integrations import (
    IntegrationKeySchema,
    SyncLogSchema,
)

# Tax
from backend.schemas.tax import (
    TaxRateMonth,
    TaxRateSaveRequest,
    TaxRateRegimeChangeRequest,
    TaxRateBrandData,
    TaxRatesResponse,
)

__all__ = [
    # Common
    "MessageResponse", "StatusResponse", "DeleteResponse",
    # Auth
    "LoginRequest", "TokenResponse", "ChangePasswordRequest",
    # References
    "AccountSchema", "CounterpartyCategorySchema", "OverrideSchema",
    "OpeningBalanceSchema", "CategoryRefSchema", "CategoryRefCreate",
    # Transactions
    "TransactionSchema", "TransactionFilter", "CategoryAssignment",
    "BulkCategoryAssignment", "CategoryAssignByIds", "UnassignedGroupRow",
    # Import
    "ImportLogSchema", "ImportResult",
    # Reports
    "BalanceRow", "DdsMonthRow", "FxControlRow", "BalanceDailyRow",
    "IncomeDailyRow", "IncomeByCategoryRow", "DashboardBalances",
    # Planning
    "OrderSchema", "LeadTimeSchema", "PlannedPaymentSchema",
    "PlannedIncomeSchema", "CustomsTopupSchema", "CustomsAllocSchema",
    "CashflowDailyRow", "OrderSummarySchema", "PaymentFactLinkSchema",
    "CustomsDTSchema", "WbPayoutSchema",
    "FactLinkCreate", "CustomsDTUpdate", "WbReconcileRequest",
    # Cost
    "NomenclatureSchema", "DutyRuleSchema", "CostOrderItemSchema",
    "CostOrderSchema", "CostUploadResult", "VatRateUpdate", "CostOrderCreate",
    # Integrations
    "IntegrationKeySchema", "SyncLogSchema",
    # Tax
    "TaxRateMonth", "TaxRateSaveRequest", "TaxRateRegimeChangeRequest",
    "TaxRateBrandData", "TaxRatesResponse",
]
