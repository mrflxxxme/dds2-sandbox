"""
JWT authentication utilities.
Provides password hashing, JWT token creation/verification, and user lookup.
"""

import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import User

logger = logging.getLogger("dds.auth")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a bcrypt hash."""
    return pwd_context.verify(plain, hashed)


def validate_password_strength(password: str) -> None:
    """Validate password meets minimum requirements."""
    if len(password) < settings.MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Пароль должен быть не менее {settings.MIN_PASSWORD_LENGTH} символов",
        )


def create_access_token(user_id: int, username: str) -> str:
    """Create a short-lived JWT access token (30 min default)."""
    expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": expire,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


async def create_refresh_token(user_id: int) -> str:
    """Create a long-lived refresh token stored in Redis (30 days default)."""
    import uuid
    from backend.cache import get_redis

    token = uuid.uuid4().hex + secrets.token_urlsafe(32)
    redis = await get_redis()
    if redis:
        key = f"refresh:{token}"
        ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400  # days → seconds
        await redis.setex(key, ttl, str(user_id))
        logger.info(f"Refresh token created for user_id={user_id}, ttl={settings.REFRESH_TOKEN_EXPIRE_DAYS}d")
    else:
        logger.warning("Redis unavailable — refresh token not stored")
    return token


async def verify_refresh_token(token: str) -> int | None:
    """Verify a refresh token and return user_id if valid. Returns None if invalid/expired."""
    from backend.cache import get_redis

    redis = await get_redis()
    if not redis:
        return None

    key = f"refresh:{token}"
    user_id = await redis.get(key)
    if user_id is None:
        return None

    return int(user_id)


async def revoke_refresh_token(token: str) -> None:
    """Revoke (delete) a refresh token from Redis."""
    from backend.cache import get_redis

    redis = await get_redis()
    if redis:
        await redis.delete(f"refresh:{token}")


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """FastAPI dependency: extract and verify current user from JWT token."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub", 0))
    except (JWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    result = await db.execute(select(User).where(User.id == user_id, User.is_active == True))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    return user


async def ensure_default_admin(db: AsyncSession):
    """Create default admin user with random password if no users exist.
    The generated password is logged to stdout on first run.
    """
    result = await db.execute(select(User).limit(1))
    if result.scalar_one_or_none() is None:
        # Generate a random password instead of using 'admin'
        default_password = secrets.token_urlsafe(12)
        admin = User(
            username="admin",
            password_hash=hash_password(default_password),
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        logger.warning(
            "\n" + "=" * 60 +
            f"\n  DEFAULT ADMIN CREATED"
            f"\n  Username: admin"
            f"\n  Password: {default_password}"
            f"\n  ⚠️  CHANGE THIS PASSWORD IMMEDIATELY!"
            "\n" + "=" * 60
        )
