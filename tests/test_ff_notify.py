"""Tests for fulfillment status Telegram notifications.

Covers:
- telegram_service.toggle_ff_notify / list_ff_notify_chats (flag + project isolation)
- fulfillment_notify.notify_ff_events (opted-in only, bot-None no-op, empty no-op,
  send error swallowed)
"""

from unittest.mock import AsyncMock, patch

from backend.models.telegram import TelegramChatBinding
from backend.services import telegram_service
from backend.services.fulfillment_notify import notify_ff_events


async def _make_binding(db, project_id, owner_id, chat_id, *, ff=False, brand=None):
    binding = TelegramChatBinding(
        chat_id=chat_id,
        project_id=project_id,
        brand=brand,
        notify_enabled=True,
        ff_notify_enabled=ff,
        created_by_id=owner_id,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return binding


class TestToggleFfNotify:
    async def test_toggle_on_off(self, db_session, project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-1001)
        assert b.ff_notify_enabled is False

        assert await telegram_service.toggle_ff_notify(db_session, b.id, project.id, True) is True
        await db_session.refresh(b)
        assert b.ff_notify_enabled is True

        assert await telegram_service.toggle_ff_notify(db_session, b.id, project.id, False) is True
        await db_session.refresh(b)
        assert b.ff_notify_enabled is False

    async def test_project_isolation(self, db_session, project, other_project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-1002)
        # toggling under the wrong project must be a no-op (not found)
        assert await telegram_service.toggle_ff_notify(db_session, b.id, other_project.id, True) is False
        await db_session.refresh(b)
        assert b.ff_notify_enabled is False

    async def test_missing_binding(self, db_session, project):
        assert await telegram_service.toggle_ff_notify(db_session, 99_999_999, project.id, True) is False


class TestListFfNotifyChats:
    async def test_only_opted_in_and_project_scoped(self, db_session, project, other_project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-2001, ff=True)
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-2002, ff=False)
        await _make_binding(db_session, other_project.id, other_project.owner_id, chat_id=-2003, ff=True)

        chats = await telegram_service.list_ff_notify_chats(db_session, project.id)
        assert {c.chat_id for c in chats} == {-2001}


class TestNotifyFfEvents:
    async def test_sends_only_to_opted_in(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3001, ff=True)
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3002, ff=False)

        fake_bot = AsyncMock()
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, ["msg-a", "msg-b"])

        sent_chats = {call.kwargs["chat_id"] for call in fake_bot.send_message.call_args_list}
        assert sent_chats == {-3001}
        assert fake_bot.send_message.call_count == 2  # 1 chat × 2 messages

    async def test_noop_when_bot_none(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3003, ff=True)
        with patch("backend.integrations.telegram_bot.bot", new=None):
            await notify_ff_events(db_session, project.id, ["x"])  # must not raise

    async def test_noop_empty_messages(self, db_session, project):
        await notify_ff_events(db_session, project.id, [])  # short-circuits, no bot access

    async def test_send_error_is_swallowed(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3004, ff=True)
        fake_bot = AsyncMock()
        fake_bot.send_message.side_effect = RuntimeError("telegram down")
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, ["x"])  # best-effort: swallowed
