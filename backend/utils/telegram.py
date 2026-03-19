"""
Telegram alert utility — sends error/warning messages to Telegram chat.

Usage:
    from backend.utils.telegram import send_alert
    await send_alert("🔥 Ошибка синхронизации WB", exc=e)

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env
If not configured, alerts silently do nothing.
"""

import asyncio
import logging
import traceback

import httpx

logger = logging.getLogger(__name__)

_bot_token: str | None = None
_chat_id: str | None = None
_proxy: str | None = None
_configured: bool = False


def configure(bot_token: str, chat_id: str, proxy: str = ""):
    """Call once at app startup to enable Telegram alerts."""
    global _bot_token, _chat_id, _proxy, _configured
    if bot_token and chat_id:
        _bot_token = bot_token
        _chat_id = chat_id
        _proxy = proxy or None
        _configured = True
        logger.info("✅ Telegram alerts configured (chat_id=%s, proxy=%s)", chat_id, bool(proxy))
    else:
        logger.info("ℹ️ Telegram alerts disabled (no TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")


async def send_alert(message: str, *, exc: Exception | None = None, silent: bool = False):
    """
    Send alert to Telegram, non-blocking.

    Args:
        message: Alert text (supports Markdown).
        exc: Optional exception to append traceback.
        silent: If True, send without notification sound.
    """
    if not _configured:
        return

    text = f"🚨 *DDS Alert*\n\n{message}"
    if exc:
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        tb_text = "".join(tb[-3:])  # Last 3 frames
        text += f"\n\n```\n{tb_text[:1000]}```"

    url = f"https://api.telegram.org/bot{_bot_token}/sendMessage"
    payload = {
        "chat_id": _chat_id,
        "text": text[:4096],  # Telegram limit
        "parse_mode": "Markdown",
        "disable_notification": silent,
    }

    try:
        async with httpx.AsyncClient(timeout=10, proxy=_proxy) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.warning("Telegram alert failed: %s", resp.text)
    except Exception as e:
        logger.warning("Telegram alert error: %s", e)


def send_alert_sync(message: str, *, exc: Exception | None = None):
    """Sync wrapper for use in sync contexts (e.g., scheduler error handlers)."""
    try:
        loop = asyncio.get_running_loop()
        # We're inside a running event loop — schedule as fire-and-forget
        asyncio.ensure_future(send_alert(message, exc=exc))
    except RuntimeError:
        # No running event loop — skip (alerts are best-effort)
        pass
