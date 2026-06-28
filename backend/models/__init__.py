"""
Models package — all SQLAlchemy models.

Models are split into domain-specific modules for maintainability.
All models are re-exported here for backward compatibility:
    from backend.models import User, Transaction, Order  # still works
"""

# AI Chat
from backend.models.ai_chat import (
    AiConversation,
    AiMessage,
)

# Enums
# Audit
from backend.models.audit import (
    AuditLog,
)

# Auth
from backend.models.auth import (
    Project,
    ProjectInvite,
    ProjectMember,
    User,
)

# Category Rules
from backend.models.category_rules import (
    CategoryRule,
)

# Cost
from backend.models.cost import (
    CostOpeningBalance,
    CostOrder,
    CostOrderItem,
    DutyException,
    DutyRule,
    Nomenclature,
)

# Counterparties & Loans
from backend.models.counterparty import (
    Counterparty,
    CounterpartyDocument,
    CounterpartyType,
    DocType,
)

# Customs
from backend.models.customs import (
    CustomsAlloc,
    CustomsDT,
    CustomsTopup,
)
from backend.models.enums import (
    DutyBasis,
    EventType2,
    FactoryOrderStatus,
    PurposeTag,
    SupplierCountry,
    TransactionStatus,
    VehicleStatus,
)

# Fulfillment (external FF providers: skladbot, migfull)
from backend.models.fulfillment import (
    FfRequestKind,
    FulfillmentBoxOverride,
    FulfillmentGuidBarcode,
    FulfillmentProvider,
    FulfillmentRequest,
    FulfillmentStatusEvent,
    FulfillmentStock,
)

# FX Rates
from backend.models.fx_rates import (
    FxRate,
)

# Integrations & Funnel
from backend.models.integrations import (
    IntegrationKey,
    SyncLog,
    WbAdCampaign,
    WbAdCampaignEvent,
    WbCostOverride,
    WbFunnelDaily,
    WbStockSnapshot,
    WbWarehouseStock,
)
from backend.models.loan import (
    Loan,
    LoanDirection,
    LoanPayment,
    LoanPaymentType,
    LoanStatus,
)

# Order City (WB order feed city mapping)
from backend.models.order_city import (
    OrderCityMap,
)

# Payment Requests (заявки на оплату перевозчику)
from backend.models.payment_request import (
    PAYMENT_REQUEST_TRANSITIONS,
    PaymentCategory,
    PaymentRequest,
    PaymentRequestDocType,
    PaymentRequestDocument,
    PaymentRequestEvent,
    PaymentRequestSource,
    PaymentRequestStatus,
    PaymentShipmentArchive,
)

# Planning
from backend.models.planning import (
    BrandPlan,
    LeadTime,
    Order,
    PaymentFactLink,
    PlannedIncome,
    PlannedPayment,
    WbPayout,
)

# References
from backend.models.refs import (
    Account,
    CategoryOverride,  # noqa: F401
    CategoryRef,
    CounterpartyCategory,
    ImtAlias,  # noqa: F401
    OpeningBalance,
    Override,
    ProductStatusMap,  # noqa: F401
    ProductSubcategory,  # noqa: F401
    ProductSubcategoryMap,  # noqa: F401
    ProductTag,  # noqa: F401
    ProductTagMap,  # noqa: F401
    ProjectSetting,
    SizeAlias,  # noqa: F401
    SizeOverride,  # noqa: F401
)

# Supply Chain
from backend.models.supply_chain import (
    FactoryOrder,
    FactoryOrderHistory,
    FactoryOrderItem,
    Supplier,
    SupplyProject,
    VehicleDocument,
    VehicleStatusHistory,
)

# Tax
from backend.models.tax import (
    TaxRate,
)

# Telegram bot
from backend.models.telegram import (
    BrandNote,
    TelegramBotUser,
    TelegramChatBinding,
)

# Transactions
from backend.models.transactions import (
    CategoryChangeLog,
    ImportLog,
    Transaction,
)

# Warehouse
from backend.models.warehouse import (
    DefectMarkOperation,
    DefectMarkOperationItem,
    DefectMarkStatus,
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    MovementType,
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    StockAdjustment,
    StockMovement,
    StockTransfer,
    StockTransferItem,
    TransferStatus,
    Warehouse,
    WarehouseDeliveryTime,
    WarehouseStock,
    WarehouseType,
)

# Gazelka (gazelka.space) — отправка заявки логиста перевозчику
from backend.models.gazelka import (
    GazelkaOrder,
    GazelkaOrderStatus,
)

# migfull-портал (plusvb.migfull.app) — создание заявки на отгрузку у ФФ «Натали»
from backend.models.migfull_portal import (
    MigfullShipmentOrder,
    MigfullShipmentStatus,
)

# WB FBO Supplies
from backend.models.wb_fbo import (
    FboAuditAction,
    FboSupplyAudit,
    WbFboSupply,
    WbFboSupplyItem,
    WbSupplyStatus,
)

# WB Finance (cached report)
from backend.models.wb_finance import (
    WbFinanceRow,
    WbFinanceSyncLog,
)

# WB Orders (per-srid feed from /api/v1/supplier/orders)
from backend.models.wb_order import (
    WbOrder,
)

# WB Order Cancel Stats
from backend.models.wb_order_cancel import (
    WbOrderCancelDaily,
)

# WB Tariffs (commission rates by subject)
from backend.models.wb_tariff import (
    WbTariff,
)

# Alias for backward compatibility
WbApiKey = IntegrationKey

__all__ = [
    # Counterparties & Loans
    "Counterparty",
    "CounterpartyDocument",
    "CounterpartyType",
    "DocType",
    "Loan",
    "LoanPayment",
    "LoanDirection",
    "LoanStatus",
    "LoanPaymentType",
    # Enums
    "EventType2",
    "TransactionStatus",
    "PurposeTag",
    "DutyBasis",
    "FactoryOrderStatus",
    "SupplierCountry",
    "VehicleStatus",
    # Auth
    "User",
    "Project",
    "ProjectMember",
    "ProjectInvite",
    # References
    "Account",
    "CounterpartyCategory",
    "Override",
    "OpeningBalance",
    "CategoryRef",
    "ProjectSetting",
    # Category Rules
    "CategoryRule",
    # Transactions
    "Transaction",
    "CategoryChangeLog",
    "ImportLog",
    # Customs
    "CustomsTopup",
    "CustomsAlloc",
    "CustomsDT",
    # Planning
    "Order",
    "LeadTime",
    "PlannedPayment",
    "PlannedIncome",
    "WbPayout",
    "PaymentFactLink",
    "BrandPlan",
    # Cost
    "Nomenclature",
    "DutyRule",
    "DutyException",
    "CostOrder",
    "CostOrderItem",
    "CostOpeningBalance",
    # Integrations & Funnel
    "IntegrationKey",
    "SyncLog",
    "WbFunnelDaily",
    "WbAdCampaign",
    "WbAdCampaignEvent",
    "WbCostOverride",
    "WbStockSnapshot",
    "WbWarehouseStock",
    # FX Rates
    "FxRate",
    # Fulfillment
    "FulfillmentProvider",
    "FulfillmentBoxOverride",
    "FulfillmentGuidBarcode",
    "FulfillmentRequest",
    "FulfillmentStatusEvent",
    "FulfillmentStock",
    "FfRequestKind",
    # Tax
    "TaxRate",
    # WB Finance
    "WbFinanceRow",
    "WbFinanceSyncLog",
    # Audit
    "AuditLog",
    # Aliases
    "WbApiKey",
    # Order City
    "OrderCityMap",
    # Payment Requests
    "PaymentCategory",
    "PaymentRequest",
    "PaymentRequestDocument",
    "PaymentRequestEvent",
    "PaymentRequestStatus",
    "PaymentRequestSource",
    "PaymentRequestDocType",
    "PaymentShipmentArchive",
    "PAYMENT_REQUEST_TRANSITIONS",
    # WB Tariffs
    "WbTariff",
    # Telegram bot
    "TelegramBotUser",
    "TelegramChatBinding",
    "BrandNote",
    # Warehouse
    "Warehouse",
    "WarehouseType",
    "InboundReceipt",
    "InboundReceiptItem",
    "InboundStatus",
    "OutboundShipment",
    "OutboundShipmentItem",
    "OutboundStatus",
    "StockTransfer",
    "StockTransferItem",
    "TransferStatus",
    "StockMovement",
    "MovementType",
    "WarehouseStock",
    "StockAdjustment",
    "WarehouseDeliveryTime",
    "DefectMarkOperation",
    "DefectMarkOperationItem",
    "DefectMarkStatus",
    # WB Order Cancel Stats
    "WbOrderCancelDaily",
    "WbOrder",
    # WB FBO Supplies
    "FboAuditAction",
    "FboSupplyAudit",
    "WbFboSupply",
    "WbFboSupplyItem",
    "WbSupplyStatus",
    # Gazelka (gazelka.space)
    "GazelkaOrder",
    "GazelkaOrderStatus",
    # migfull-портал (plusvb.migfull.app)
    "MigfullShipmentOrder",
    "MigfullShipmentStatus",
    # Supply Chain
    "FactoryOrder",
    "FactoryOrderHistory",
    "FactoryOrderItem",
    "Supplier",
    "SupplyProject",
    "VehicleDocument",
    "VehicleStatusHistory",
    # AI Chat
    "AiConversation",
    "AiMessage",
]
