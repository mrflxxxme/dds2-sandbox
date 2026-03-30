"""
Admin panel authentication backend.

Uses sync sessions (sqladmin is sync-only) and verifies
credentials against the User model with bcrypt via pwd_context.
IP whitelist via ADMIN_ALLOWED_IPS setting.
"""

import logging

from sqladmin.authentication import AuthenticationBackend
from sqlalchemy import select
from starlette.requests import Request

from backend.auth import pwd_context
from backend.config import settings
from backend.database import SyncSessionLocal
from backend.models.auth import User

logger = logging.getLogger("dds.admin")


def _get_client_ip(request: Request) -> str:
    """Extract real client IP from X-Forwarded-For or direct connection."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def _is_ip_allowed(request: Request) -> bool:
    """Check if client IP is in ADMIN_ALLOWED_IPS whitelist."""
    allowed = settings.ADMIN_ALLOWED_IPS.strip()
    if not allowed:
        return True  # No restriction when empty
    allowed_ips = {ip.strip() for ip in allowed.split(",") if ip.strip()}
    client_ip = _get_client_ip(request)
    if client_ip in allowed_ips:
        return True
    logger.warning("Admin access denied: IP %s not in whitelist", client_ip)
    return False


class AdminAuth(AuthenticationBackend):
    """Session-based admin authentication for sqladmin with IP whitelist."""

    async def login(self, request: Request) -> bool:
        if not _is_ip_allowed(request):
            return False
        form = await request.form()
        username = form.get("username", "")
        password = form.get("password", "")

        with SyncSessionLocal() as db:  # type: Session
            user = db.execute(
                select(User).where(
                    User.username == username,
                    User.is_active == True,  # noqa: E712
                )
            ).scalar_one_or_none()

            if user is None:
                logger.warning("Admin login failed: user '%s' not found or inactive", username)
                return False

            if not pwd_context.verify(password, user.password_hash):
                logger.warning("Admin login failed: wrong password for '%s'", username)
                return False

            if user.role != "admin":
                logger.warning("Admin login denied: user '%s' has role '%s'", username, user.role)
                return False

            # Store user info in session
            request.session.update(
                {
                    "admin_user_id": user.id,
                    "admin_username": user.username,
                }
            )
            logger.info("Admin login: user '%s' (id=%d)", user.username, user.id)
            return True

    async def logout(self, request: Request) -> bool:
        username = request.session.get("admin_username", "unknown")
        request.session.clear()
        logger.info("Admin logout: user '%s'", username)
        return True

    async def authenticate(self, request: Request) -> bool:
        if not _is_ip_allowed(request):
            return False

        user_id = request.session.get("admin_user_id")
        if not user_id:
            return False

        # Verify user still exists and is active admin
        with SyncSessionLocal() as db:
            user = db.execute(
                select(User).where(
                    User.id == user_id,
                    User.is_active == True,  # noqa: E712
                    User.role == "admin",
                )
            ).scalar_one_or_none()

            if user is None:
                request.session.clear()
                return False

        return True
