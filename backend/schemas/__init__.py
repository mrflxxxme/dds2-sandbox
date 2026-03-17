"""
Schemas package — all Pydantic schemas.

Schemas are split into domain-specific modules for maintainability.
All schemas are re-exported here for backward compatibility:
    from backend.schemas import TransactionSchema, OrderSchema  # still works
"""

# Common
# Auth
from backend.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
)
from backend.schemas.common import (
    DeleteResponse,
    MessageResponse,
    StatusResponse,
)

# Cost
from backend.schemas.cost import (
    CostOrderCreate,
    CostOrderItemSchema,
    CostOrderSchema,
    CostUploadResult,
    DutyRuleSchema,
    NomenclatureSchema,
    VatRateUpdate,
)

# Import
from backend.schemas.imports import (
    ImportLogSchema,
    ImportResult,
)

# Integrations
from backend.schemas.integrations import (
    IntegrationKeySchema,
    SyncLogSchema,
)

# Planning
from backend.schemas.planning import (
    CashflowDailyRow,
    CustomsAllocSchema,
    CustomsDTSchema,
    CustomsDTUpdate,
    CustomsTopupSchema,
    FactLinkCreate,
    LeadTimeSchema,
    OrderSchema,
    OrderSummarySchema,
    PaymentFactLinkSchema,
    PlannedIncomeSchema,
    PlannedPaymentSchema,
    WbPayoutSchema,
    WbReconcileRequest,
)

# References
from backend.schemas.refs import (
    AccountSchema,
    CategoryRefCreate,
    CategoryRefSchema,
    CounterpartyCategorySchema,
    OpeningBalanceSchema,
    OverrideSchema,
)

# Reports
from backend.schemas.reports import (
    BalanceDailyRow,
    BalanceRow,
    DashboardBalances,
    DdsMonthRow,
    FxControlRow,
    IncomeByCategoryRow,
    IncomeDailyRow,
)

# WB Tariffs
from backend.schemas.tariff import (
    WbTariffSchema,
    WbTariffUploadResult,
)

# Tax
from backend.schemas.tax import (
    TaxRateBrandData,
    TaxRateMonth,
    TaxRateRegimeChangeRequest,
    TaxRateSaveRequest,
    TaxRatesResponse,
)

# Transactions
from backend.schemas.transactions import (
    BulkCategoryAssignment,
    CategoryAssignByIds,
    CategoryAssignment,
    TransactionFilter,
    TransactionSchema,
    UnassignedGroupRow,
)

__all__ = [
    # Common
    "MessageResponse",
    "StatusResponse",
    "DeleteResponse",
    # Auth
    "LoginRequest",
    "TokenResponse",
    "ChangePasswordRequest",
    # References
    "AccountSchema",
    "CounterpartyCategorySchema",
    "OverrideSchema",
    "OpeningBalanceSchema",
    "CategoryRefSchema",
    "CategoryRefCreate",
    # Transactions
    "TransactionSchema",
    "TransactionFilter",
    "CategoryAssignment",
    "BulkCategoryAssignment",
    "CategoryAssignByIds",
    "UnassignedGroupRow",
    # Import
    "ImportLogSchema",
    "ImportResult",
    # Reports
    "BalanceRow",
    "DdsMonthRow",
    "FxControlRow",
    "BalanceDailyRow",
    "IncomeDailyRow",
    "IncomeByCategoryRow",
    "DashboardBalances",
    # Planning
    "OrderSchema",
    "LeadTimeSchema",
    "PlannedPaymentSchema",
    "PlannedIncomeSchema",
    "CustomsTopupSchema",
    "CustomsAllocSchema",
    "CashflowDailyRow",
    "OrderSummarySchema",
    "PaymentFactLinkSchema",
    "CustomsDTSchema",
    "WbPayoutSchema",
    "FactLinkCreate",
    "CustomsDTUpdate",
    "WbReconcileRequest",
    # Cost
    "NomenclatureSchema",
    "DutyRuleSchema",
    "CostOrderItemSchema",
    "CostOrderSchema",
    "CostUploadResult",
    "VatRateUpdate",
    "CostOrderCreate",
    # Integrations
    "IntegrationKeySchema",
    "SyncLogSchema",
    # Tax
    "TaxRateMonth",
    "TaxRateSaveRequest",
    "TaxRateRegimeChangeRequest",
    "TaxRateBrandData",
    "TaxRatesResponse",
    # WB Tariffs
    "WbTariffSchema",
    "WbTariffUploadResult",
]
