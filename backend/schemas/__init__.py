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
    BulkAreaUpdate,
    BulkWeightUpdate,
    CostOrderCreate,
    CostOrderItemSchema,
    CostOrderSchema,
    CostUploadResult,
    DutyExceptionSchema,
    DutyRuleSchema,
    NomenclatureSchema,
    VatRateUpdate,
)

# Design tasks (модуль «Дизайн карточек»)
from backend.schemas.design import (
    DesignAssign,
    DesignBoardResponse,
    DesignCommentOut,
    DesignEventOut,
    DesignMaterialIn,
    DesignMaterialOut,
    DesignMoveIn,
    DesignProductSuggestion,
    DesignStatsOut,
    DesignStatusChange,
    DesignSubmissionFileOut,
    DesignSubmissionOut,
    DesignTaskCreate,
    DesignTaskDetail,
    DesignTaskListItem,
    DesignTaskPermissions,
    DesignTaskUpdate,
    DesignVerdictIn,
    DesignWorkloadRow,
)

# Import
from backend.schemas.imports import (
    ImportLogSchema,
    ImportResult,
)

# Integrations
from backend.schemas.integrations import (
    FakturaStatusSchema,
    IntegrationKeySchema,
    SyncLogSchema,
)

# Localization (ИЛ + ИРП)
from backend.schemas.localization import (
    DistrictBreakdown,
    LocalizationByPeriod,
    LocalizationSkuRow,
    LocalizationSummary,
)

# Monitoring
from backend.schemas.monitoring import (
    MonitoringOverview,
    SchedulerJobInfo,
    SchedulerStatus,
    SyncLogEntry,
    SyncTypeStatus,
)

# Planning
from backend.schemas.planning import (
    BrandPlanSchema,
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
    PlanFactBrandRow,
    PlanFactDayRow,
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
    ProductTagMappingPayload,
    ProductTagSchema,
)

# Payment Requests (заявки на оплату)
from backend.schemas.payment_request import (
    ArchiveShipmentsRequest,
    ArchiveTxnsRequest,
    CancelRequest,
    CounterpartyReconciliation,
    CreateDraftRequest,
    CreateDraftResult,
    CreateDraftsRequest,
    CreateDraftsResponse,
    LinkShipmentsRequest,
    PaymentRequestCreate,
    PaymentRequestDetail,
    PaymentRequestDocumentResponse,
    PaymentRequestEventResponse,
    PaymentRequestListResponse,
    PaymentRequestRow,
    PaymentRequestStatusPoll,
    PaymentRequestUpdate,
    ReconciliationPaymentRow,
    ShippableShipmentRow,
    SubmitRequest,
    UnlinkShipmentsRequest,
)

# Reports
from backend.schemas.reports import (
    BalanceDailyRow,
    BalanceRow,
    CostDnaCategory,
    CostDnaResponse,
    CostDnaTotals,
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
    AutoRuleCreate,
    BulkCategoryAssignment,
    CategoryAssignByIds,
    CategoryAssignment,
    TransactionFilter,
    TransactionSchema,
    UnassignedGroupRow,
)

# Warehouse
from backend.schemas.warehouse import (
    CostPriceUpdate,
    InboundReceiptCreate,
    InboundReceiptItemCreate,
    InboundReceiptItemSchema,
    InboundReceiptSchema,
    InboundReceiptUpdate,
    OutboundShipmentCreate,
    OutboundShipmentItemCreate,
    OutboundShipmentItemSchema,
    OutboundShipmentSchema,
    StockAdjustmentCreate,
    StockAdjustmentSchema,
    StockMovementSchema,
    StockTransferCreate,
    StockTransferItemCreate,
    StockTransferItemSchema,
    StockTransferSchema,
    WarehouseCreate,
    WarehouseReorder,
    WarehouseSchema,
    WarehouseStockSchema,
    WarehouseUpdate,
)

# WB FBO Supplies
from backend.schemas.wb_fbo import (
    FboSyncResultSchema,
    WbFboSupplyItemSchema,
    WbFboSupplyListResponse,
    WbFboSupplySchema,
    WbFboSupplyWithItemsSchema,
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
    "ProductTagSchema",
    "ProductTagMappingPayload",
    # Payment Requests
    "PaymentRequestCreate",
    "PaymentRequestUpdate",
    "PaymentRequestRow",
    "PaymentRequestDetail",
    "PaymentRequestListResponse",
    "PaymentRequestDocumentResponse",
    "PaymentRequestEventResponse",
    "PaymentRequestStatusPoll",
    "ShippableShipmentRow",
    "SubmitRequest",
    "CancelRequest",
    "CreateDraftRequest",
    "CreateDraftsRequest",
    "CreateDraftResult",
    "CreateDraftsResponse",
    "ArchiveShipmentsRequest",
    "ArchiveTxnsRequest",
    "ReconciliationPaymentRow",
    "CounterpartyReconciliation",
    "LinkShipmentsRequest",
    "UnlinkShipmentsRequest",
    # Transactions
    "AutoRuleCreate",
    "TransactionSchema",
    "TransactionFilter",
    "CategoryAssignment",
    "BulkCategoryAssignment",
    "CategoryAssignByIds",
    "UnassignedGroupRow",
    # Design tasks (модуль «Дизайн карточек»)
    "DesignAssign",
    "DesignBoardResponse",
    "DesignCommentOut",
    "DesignEventOut",
    "DesignMaterialIn",
    "DesignMaterialOut",
    "DesignMoveIn",
    "DesignProductSuggestion",
    "DesignStatsOut",
    "DesignStatusChange",
    "DesignSubmissionFileOut",
    "DesignSubmissionOut",
    "DesignTaskCreate",
    "DesignTaskDetail",
    "DesignTaskListItem",
    "DesignTaskPermissions",
    "DesignTaskUpdate",
    "DesignVerdictIn",
    "DesignWorkloadRow",
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
    "CostDnaCategory",
    "CostDnaTotals",
    "CostDnaResponse",
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
    "BrandPlanSchema",
    "PlanFactDayRow",
    "PlanFactBrandRow",
    # Cost
    "NomenclatureSchema",
    "DutyRuleSchema",
    "DutyExceptionSchema",
    "BulkAreaUpdate",
    "BulkWeightUpdate",
    "CostOrderItemSchema",
    "CostOrderSchema",
    "CostUploadResult",
    "VatRateUpdate",
    "CostOrderCreate",
    # Integrations
    "FakturaStatusSchema",
    "IntegrationKeySchema",
    "SyncLogSchema",
    # Localization
    "LocalizationByPeriod",
    "LocalizationSkuRow",
    "LocalizationSummary",
    "DistrictBreakdown",
    # Monitoring
    "MonitoringOverview",
    "SchedulerJobInfo",
    "SchedulerStatus",
    "SyncLogEntry",
    "SyncTypeStatus",
    # Tax
    "TaxRateMonth",
    "TaxRateSaveRequest",
    "TaxRateRegimeChangeRequest",
    "TaxRateBrandData",
    "TaxRatesResponse",
    # WB Tariffs
    "WbTariffSchema",
    "WbTariffUploadResult",
    # Warehouse
    "WarehouseCreate",
    "WarehouseUpdate",
    "WarehouseSchema",
    "WarehouseReorder",
    "InboundReceiptCreate",
    "InboundReceiptUpdate",
    "InboundReceiptSchema",
    "InboundReceiptItemCreate",
    "InboundReceiptItemSchema",
    "OutboundShipmentCreate",
    "OutboundShipmentSchema",
    "OutboundShipmentItemCreate",
    "OutboundShipmentItemSchema",
    "StockTransferCreate",
    "StockTransferSchema",
    "StockTransferItemCreate",
    "StockTransferItemSchema",
    "StockMovementSchema",
    "WarehouseStockSchema",
    "CostPriceUpdate",
    "StockAdjustmentCreate",
    "StockAdjustmentSchema",
    # WB FBO Supplies
    "WbFboSupplySchema",
    "WbFboSupplyWithItemsSchema",
    "WbFboSupplyItemSchema",
    "WbFboSupplyListResponse",
    "FboSyncResultSchema",
]
