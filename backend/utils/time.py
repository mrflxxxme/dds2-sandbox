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

from datetime import date, datetime, timezone


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


def msk_now() -> datetime:
    """Текущий момент в МСК (aware).

    ЕДИНСТВЕННЫЙ правильный способ получить «сейчас по Москве» от naive-UTC utcnow().
    Ловушки, которые уже стреляли:
    - naive.astimezone(MSK) трактует naive как ЛОКАЛЬНОЕ время машины (совпадает с UTC,
      только пока TZ контейнера = UTC);
    - «utcnow().astimezone(MSK) if utcnow().tzinfo else utcnow()» — tzinfo у utcnow()
      всегда None, МСК-ветка мертва → бралась сырая UTC-дата (в 00:00–02:59 МСК — вчера).
    """
    import pytz

    return utcnow().replace(tzinfo=timezone.utc).astimezone(pytz.timezone("Europe/Moscow"))


def msk_today() -> date:
    """Сегодняшняя календарная дата в МСК."""
    return msk_now().date()
