"""
Telegram webhook endpoint — receives updates from Telegram servers.
NO JWT auth — validated via X-Telegram-Bot-Api-Secret-Token header.

Note: In production, webhook hits the backend container, but bot/dp
are created in the worker container. We lazily init bot/dp here too
so that the backend can process webhook updates.
"""

import logging

from aiogram.types import Update
from fastapi import APIRouter, HTTPException, Request

from backend.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _ensure_bot():
    """Lazily create bot + dispatcher if not yet initialized (backend container)."""
    from backend.integrations.telegram_bot import bot, dp

    if bot and dp:
        return bot, dp

    # Backend container: create bot+dp on first webhook call
    from backend.integrations.telegram_bot import create_bot

    logger.info("Lazy-initializing Telegram bot for webhook processing")
    return create_bot()


@router.post("/bot/webhook")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook."""
    # Validate secret token
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != settings.TELEGRAM_WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    tg_bot, tg_dp = _ensure_bot()

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": tg_bot})
        await tg_dp.feed_update(tg_bot, update)
    except Exception:
        logger.exception("Error processing Telegram update")

    return {"ok": True}
