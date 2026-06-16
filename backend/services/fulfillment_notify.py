"""Best-effort Telegram notifications for fulfillment status changes.

Fires when the FF sync transitions our docs: an assembly to READY or an inbound
receipt to ACCEPTED. Runs in the worker process (where the analytics-bot
singleton lives) and is strictly best-effort — it must never raise into the
sync, so every failure is swallowed and logged. No-op when the analytics bot is
not configured (e.g. local dev without TELEGRAM_BOT_TOKEN_ANALYTICS) or when no
chat of the project opted in (ff_notify_enabled).
"""

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services import telegram_service

logger = logging.getLogger(__name__)


async def notify_ff_events(db: AsyncSession, project_id: int, messages: list[str]) -> None:
    """Send each message to every chat of `project_id` opted into FF notifications.

    `db` is reused for the (read-only) bindings lookup — pass the sync session
    AFTER its commit. Best-effort: resolves opted-in chats once and sends via the
    analytics bot through TELEGRAM_PROXY. Any error is logged, never propagated.
    """
    if not messages:
        return
    try:
        # Bot singleton only exists in the worker process; None elsewhere/locally.
        from backend.integrations.telegram_bot import bot

        if bot is None:
            return

        chats = await telegram_service.list_ff_notify_chats(db, project_id)
        if not chats:
            return

        for binding in chats:
            for text in messages:
                try:
                    await bot.send_message(chat_id=binding.chat_id, text=text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("FF notify send failed (chat=%s): %s", binding.chat_id, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("FF notify error (project=%s): %s", project_id, exc)
