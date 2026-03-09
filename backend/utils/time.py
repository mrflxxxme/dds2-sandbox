"""
Unified datetime utility for DDS.

WHY THIS EXISTS:
- datetime.utcnow() is deprecated in Python 3.12+
- datetime.now(timezone.utc) creates offset-aware datetime
  which breaks asyncpg with TIMESTAMP WITHOUT TIME ZONE columns
- This module provides a single source of truth

RULE: All new code MUST use `from backend.utils.time import utcnow`
      instead of datetime.utcnow() or datetime.now(timezone.utc).
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return current UTC time as a naive (timezone-unaware) datetime.

    This is the ONLY approved way to get current time in DDS.

    - Compatible with asyncpg TIMESTAMP WITHOUT TIME ZONE columns
    - Not deprecated (unlike datetime.utcnow())
    - Always UTC (unlike datetime.now() which uses local time)

    Usage:
        from backend.utils.time import utcnow

        # In service code:
        sync_log.finished_at = utcnow()

        # In model defaults:
        created_at = mapped_column(DateTime, default=utcnow)
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
