"""
SQLAdmin integration for DDS2.

Mounts an admin panel at /admin with session-based authentication.
Only users with role='admin' can access the panel.
"""

from sqladmin import Admin

from backend.admin.auth import AdminAuth
from backend.config import settings
from backend.database import sync_engine


def setup_admin(app) -> Admin:
    """Create and configure the SQLAdmin instance, register all model views."""
    authentication_backend = AdminAuth(secret_key=settings.SECRET_KEY)
    admin = Admin(
        app,
        sync_engine,
        authentication_backend=authentication_backend,
        title="DDS Admin",
    )

    from backend.admin.views import (
        AuditLogAdmin,
        CategoryRefAdmin,
        ImportLogAdmin,
        IntegrationKeyAdmin,
        OrderAdmin,
        ProjectAdmin,
        SyncLogAdmin,
        TransactionAdmin,
        UserAdmin,
        WbAdCampaignAdmin,
        WbFinanceSyncLogAdmin,
        WbFunnelDailyAdmin,
    )

    admin.add_view(UserAdmin)
    admin.add_view(ProjectAdmin)
    admin.add_view(TransactionAdmin)
    admin.add_view(IntegrationKeyAdmin)
    admin.add_view(SyncLogAdmin)
    admin.add_view(WbFinanceSyncLogAdmin)
    admin.add_view(WbAdCampaignAdmin)
    admin.add_view(ImportLogAdmin)
    admin.add_view(CategoryRefAdmin)
    admin.add_view(AuditLogAdmin)
    admin.add_view(WbFunnelDailyAdmin)
    admin.add_view(OrderAdmin)

    return admin
