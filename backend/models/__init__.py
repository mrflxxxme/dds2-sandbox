"""
Models package — all SQLAlchemy models.

Models are split into domain-specific modules for maintainability.
All models are re-exported here for backward compatibility:
    from backend.models import User, Transaction, Order  # still works
"""

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
    CostOrder,
    CostOrderItem,
    DutyRule,
    Nomenclature,
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
    PurposeTag,
    TransactionStatus,
)

# FX Rates
from backend.models.fx_rates import (
    FxRate,
)

# Integrations & Funnel
from backend.models.integrations import (
    IntegrationKey,
    SyncLog,
    WbCostOverride,
    WbFunnelDaily,
    WbStockSnapshot,
    WbWarehouseStock,
)

# Order City (WB order feed city mapping)
from backend.models.order_city import (
    OrderCityMap,
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
    CategoryRef,
    CounterpartyCategory,
    OpeningBalance,
    Override,
    ProjectSetting,
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

# WB FBO Supplies
from backend.models.wb_fbo import (
    WbFboSupply,
    WbFboSupplyItem,
    WbSupplyStatus,
)

# WB Finance (cached report)
from backend.models.wb_finance import (
    WbFinanceRow,
    WbFinanceSyncLog,
)

# WB Tariffs (commission rates by subject)
from backend.models.wb_tariff import (
    WbTariff,
)

# Alias for backward compatibility
WbApiKey = IntegrationKey

__all__ = [
    # Enums
    "EventType2",
    "TransactionStatus",
    "PurposeTag",
    "DutyBasis",
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
    "CostOrder",
    "CostOrderItem",
    # Integrations & Funnel
    "IntegrationKey",
    "SyncLog",
    "WbFunnelDaily",
    "WbCostOverride",
    "WbStockSnapshot",
    "WbWarehouseStock",
    # FX Rates
    "FxRate",
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
    # WB FBO Supplies
    "WbFboSupply",
    "WbFboSupplyItem",
    "WbSupplyStatus",
]
