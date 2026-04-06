"""
Model mixins — reusable base classes for models.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from backend.utils.time import utcnow


class SoftDeleteMixin:
    """
    Mixin for soft delete support.

    Adds `is_deleted` flag and `deleted_at` timestamp.
    Usage:
        class Order(Base, SoftDeleteMixin):
            ...

    Querying active records:
        query.where(Order.is_deleted == False)
    """

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, server_default="false")
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    def soft_delete(self) -> None:
        """Mark record as deleted without physical removal."""
        self.is_deleted = True
        self.deleted_at = utcnow()

    def restore(self) -> None:
        """Restore a soft-deleted record."""
        self.is_deleted = False
        self.deleted_at = None


class TimestampMixin:
    """
    Mixin for created_at / updated_at timestamps.

    Usage:
        class SomeModel(Base, TimestampMixin):
            ...
    """

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        default=utcnow,
        onupdate=utcnow,
        nullable=True,
    )
