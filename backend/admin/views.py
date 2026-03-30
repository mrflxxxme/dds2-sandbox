"""
SQLAdmin ModelView views for key DDS models.

Rules:
- can_delete = False on ALL models (soft delete only from app)
- Financial models (Transaction, WbFinanceRow) are read-only
- Sensitive fields (encrypted_key, password_hash) excluded from forms AND lists
"""

from sqladmin import ModelView

from backend.models.audit import AuditLog
from backend.models.auth import Project, User
from backend.models.integrations import IntegrationKey, SyncLog, WbAdCampaign, WbFunnelDaily
from backend.models.planning import Order
from backend.models.refs import CategoryRef
from backend.models.transactions import ImportLog, Transaction
from backend.models.wb_finance import WbFinanceSyncLog


class UserAdmin(ModelView, model=User):
    name = "User"
    name_plural = "Users"
    icon = "fa-solid fa-user"

    column_list = [User.id, User.username, User.email, User.role, User.is_active, User.created_at]
    column_searchable_list = [User.username, User.email]
    column_sortable_list = [User.id, User.username, User.role, User.is_active, User.created_at]

    # Never expose password_hash
    form_excluded_columns = [User.password_hash]
    column_details_exclude_list = [User.password_hash]

    can_delete = False


class ProjectAdmin(ModelView, model=Project):
    name = "Project"
    name_plural = "Projects"
    icon = "fa-solid fa-folder"

    column_list = [Project.id, Project.name, Project.slug, Project.owner_id, Project.created_at]
    column_searchable_list = [Project.name, Project.slug]
    column_sortable_list = [Project.id, Project.name, Project.slug, Project.owner_id]

    can_delete = False


class TransactionAdmin(ModelView, model=Transaction):
    name = "Transaction"
    name_plural = "Transactions"
    icon = "fa-solid fa-money-bill-transfer"

    column_list = [
        Transaction.id,
        Transaction.project_id,
        Transaction.date,
        Transaction.counterparty,
        Transaction.income,
        Transaction.expense,
        Transaction.cat_lvl1_2,
        Transaction.is_deleted,
    ]
    column_searchable_list = [Transaction.counterparty, Transaction.txn_id]
    column_sortable_list = [Transaction.id, Transaction.date, Transaction.project_id]
    column_default_sort = (Transaction.date, True)

    # Read-only: financial data
    can_create = False
    can_edit = False
    can_delete = False


class IntegrationKeyAdmin(ModelView, model=IntegrationKey):
    name = "Integration Key"
    name_plural = "Integration Keys"
    icon = "fa-solid fa-key"

    column_list = [
        IntegrationKey.id,
        IntegrationKey.project_id,
        IntegrationKey.service,
        IntegrationKey.label,
        IntegrationKey.is_active,
        IntegrationKey.is_deleted,
    ]
    column_sortable_list = [IntegrationKey.id, IntegrationKey.service, IntegrationKey.project_id]

    # Never expose encrypted_key
    form_excluded_columns = [IntegrationKey.encrypted_key]
    column_details_exclude_list = [IntegrationKey.encrypted_key]

    can_delete = False


class SyncLogAdmin(ModelView, model=SyncLog):
    name = "Sync Log"
    name_plural = "Sync Logs"
    icon = "fa-solid fa-rotate"

    column_list = [
        SyncLog.id,
        SyncLog.integration_id,
        SyncLog.service,
        SyncLog.sync_type,
        SyncLog.status,
        SyncLog.started_at,
        SyncLog.finished_at,
        SyncLog.rows_fetched,
        SyncLog.rows_inserted,
        SyncLog.error_msg,
    ]
    column_searchable_list = [SyncLog.service, SyncLog.sync_type, SyncLog.error_msg]
    column_sortable_list = [SyncLog.id, SyncLog.started_at, SyncLog.status, SyncLog.service, SyncLog.sync_type]
    column_default_sort = (SyncLog.started_at, True)

    # Read-only
    can_create = False
    can_edit = False
    can_delete = False


class ImportLogAdmin(ModelView, model=ImportLog):
    name = "Import Log"
    name_plural = "Import Logs"
    icon = "fa-solid fa-file-import"

    column_list = [
        ImportLog.id,
        ImportLog.project_id,
        ImportLog.filename,
        ImportLog.status,
        ImportLog.imported_at,
        ImportLog.rows_inserted,
    ]
    column_sortable_list = [ImportLog.id, ImportLog.imported_at, ImportLog.project_id]
    column_default_sort = (ImportLog.imported_at, True)

    can_delete = False


class CategoryRefAdmin(ModelView, model=CategoryRef):
    name = "Category Ref"
    name_plural = "Category Refs"
    icon = "fa-solid fa-tags"

    column_list = [
        CategoryRef.id,
        CategoryRef.project_id,
        CategoryRef.direction,
        CategoryRef.cat_lvl1,
        CategoryRef.cat_lvl2,
        CategoryRef.is_deleted,
    ]
    column_searchable_list = [CategoryRef.cat_lvl1, CategoryRef.cat_lvl2]
    column_sortable_list = [CategoryRef.id, CategoryRef.project_id, CategoryRef.direction]

    can_delete = False


class AuditLogAdmin(ModelView, model=AuditLog):
    name = "Audit Log"
    name_plural = "Audit Logs"
    icon = "fa-solid fa-clipboard-list"

    column_list = [
        AuditLog.id,
        AuditLog.user_id,
        AuditLog.method,
        AuditLog.endpoint,
        AuditLog.status_code,
        AuditLog.created_at,
    ]
    column_sortable_list = [AuditLog.id, AuditLog.created_at, AuditLog.user_id]
    column_default_sort = (AuditLog.created_at, True)

    # Read-only
    can_create = False
    can_edit = False
    can_delete = False


class WbFunnelDailyAdmin(ModelView, model=WbFunnelDaily):
    name = "WB Funnel Daily"
    name_plural = "WB Funnel Daily"
    icon = "fa-solid fa-chart-line"

    column_list = [
        WbFunnelDaily.id,
        WbFunnelDaily.project_id,
        WbFunnelDaily.date,
        WbFunnelDaily.nm_id,
        WbFunnelDaily.vendor_code,
    ]
    column_sortable_list = [WbFunnelDaily.id, WbFunnelDaily.date, WbFunnelDaily.project_id]
    column_default_sort = (WbFunnelDaily.date, True)

    # Read-only
    can_create = False
    can_edit = False
    can_delete = False


class OrderAdmin(ModelView, model=Order):
    name = "Order"
    name_plural = "Orders"
    icon = "fa-solid fa-truck"

    column_list = [
        Order.id,
        Order.project_id,
        Order.order_no,
        Order.order_name,
        Order.category,
        Order.is_deleted,
    ]
    column_searchable_list = [Order.order_name]
    column_sortable_list = [Order.id, Order.project_id, Order.order_no]

    can_delete = False


class WbFinanceSyncLogAdmin(ModelView, model=WbFinanceSyncLog):
    name = "WB Finance Sync"
    name_plural = "WB Finance Syncs"
    icon = "fa-solid fa-coins"

    column_list = [
        WbFinanceSyncLog.id,
        WbFinanceSyncLog.project_id,
        WbFinanceSyncLog.date_from,
        WbFinanceSyncLog.date_to,
        WbFinanceSyncLog.rows_synced,
        WbFinanceSyncLog.status,
        WbFinanceSyncLog.error_msg,
        WbFinanceSyncLog.synced_at,
    ]
    column_searchable_list = [WbFinanceSyncLog.error_msg]
    column_sortable_list = [
        WbFinanceSyncLog.id,
        WbFinanceSyncLog.synced_at,
        WbFinanceSyncLog.project_id,
        WbFinanceSyncLog.status,
    ]
    column_default_sort = (WbFinanceSyncLog.synced_at, True)

    # Read-only
    can_create = False
    can_edit = False
    can_delete = False


class WbAdCampaignAdmin(ModelView, model=WbAdCampaign):
    name = "WB Ad Campaign"
    name_plural = "WB Ad Campaigns"
    icon = "fa-solid fa-bullhorn"

    column_list = [
        WbAdCampaign.id,
        WbAdCampaign.project_id,
        WbAdCampaign.campaign_id,
        WbAdCampaign.name,
        WbAdCampaign.campaign_type,
        WbAdCampaign.status,
        WbAdCampaign.budget,
    ]
    column_searchable_list = [WbAdCampaign.name]
    column_sortable_list = [WbAdCampaign.id, WbAdCampaign.project_id, WbAdCampaign.campaign_id, WbAdCampaign.status]

    # Read-only
    can_create = False
    can_edit = False
    can_delete = False
