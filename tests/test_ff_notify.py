"""Tests for fulfillment status Telegram notifications.

Covers:
- telegram_service.toggle_ff_notify / list_ff_notify_chats (flag + project isolation)
- fulfillment_notify.notify_ff_events (opted-in only, bot-None no-op, empty no-op,
  send error swallowed)
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from backend.models.telegram import TelegramChatBinding
from backend.services import telegram_service
from backend.services.fulfillment_notify import (
    build_accept_item,
    build_ready_item,
    notify_ff_events,
    refresh_ff_board,
)


async def _make_binding(db, project_id, owner_id, chat_id, *, ff=False, board=False, brand=None):
    binding = TelegramChatBinding(
        chat_id=chat_id,
        project_id=project_id,
        brand=brand,
        notify_enabled=True,
        ff_notify_enabled=ff,
        ff_board_enabled=board,
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
    async def test_sends_only_to_opted_in_as_html(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3001, ff=True)
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3002, ff=False)

        fake_bot = AsyncMock()
        items = [{"text": "msg-a", "url": None}, {"text": "msg-b", "url": None}]
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, items)

        calls = fake_bot.send_message.call_args_list
        assert {c.kwargs["chat_id"] for c in calls} == {-3001}
        assert fake_bot.send_message.call_count == 2  # 1 chat × 2 messages
        assert all(c.kwargs.get("parse_mode") == "HTML" for c in calls)

    async def test_url_attaches_inline_button(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3010, ff=True)
        url = "https://app.vyatkin-wb.ru/p/default/warehouse/5/ff-request/42"
        fake_bot = AsyncMock()
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, [{"text": "t", "url": url}])
        markup = fake_bot.send_message.call_args.kwargs["reply_markup"]
        assert markup is not None
        assert markup.inline_keyboard[0][0].url == url

    async def test_ff_id_attaches_sostav_button(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3012, ff=True)
        fake_bot = AsyncMock()
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, [{"text": "t", "url": None, "ff_id": 99}])
        row = fake_bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0]
        assert row[0].callback_data == "ff_items:99"

    async def test_url_and_ff_id_give_two_buttons(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3013, ff=True)
        url = "https://app.vyatkin-wb.ru/p/default/warehouse/5/ff-request/42"
        fake_bot = AsyncMock()
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, [{"text": "t", "url": url, "ff_id": 42}])
        row = fake_bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0]
        assert row[0].url == url
        assert row[1].callback_data == "ff_items:42"

    async def test_no_url_no_button(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3011, ff=True)
        fake_bot = AsyncMock()
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, [{"text": "t", "url": None}])
        assert fake_bot.send_message.call_args.kwargs["reply_markup"] is None

    async def test_noop_when_bot_none(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3003, ff=True)
        with patch("backend.integrations.telegram_bot.bot", new=None):
            await notify_ff_events(db_session, project.id, [{"text": "x", "url": None}])  # must not raise

    async def test_noop_empty_messages(self, db_session, project):
        await notify_ff_events(db_session, project.id, [])  # short-circuits, no bot access

    async def test_send_error_is_swallowed(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-3004, ff=True)
        fake_bot = AsyncMock()
        fake_bot.send_message.side_effect = RuntimeError("telegram down")
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await notify_ff_events(db_session, project.id, [{"text": "x", "url": None}])  # swallowed


class TestBuildItems:
    def test_ready_item_html_and_url(self):
        item = build_ready_item(
            "default",
            5,
            42,
            "ASM-455",
            "WH-R-1",
            "Владимир",
            97,
            warehouse_name="Газпром",
            wb_number="WB-998877",
        )
        assert "<b>Сборка готова к отгрузке</b>" in item["text"]
        assert "<b>ASM-455</b>" in item["text"]
        assert "<code>WH-R-1</code>" in item["text"]
        assert "Склад ФФ: Газпром" in item["text"]
        assert "<code>WB-998877</code>" in item["text"]
        assert "97 шт" in item["text"]
        assert item["url"] == "https://app.vyatkin-wb.ru/p/default/warehouse/5/ff-request/42"
        assert item["ff_id"] == 42

    def test_ready_item_omits_optional_lines_when_absent(self):
        item = build_ready_item("default", 5, 42, "ASM-455", "WH-R-1", "Владимир", 97)
        assert "Склад ФФ" not in item["text"]
        assert "Поставка ВБ" not in item["text"]

    def test_accept_item_html_and_url(self):
        item = build_accept_item("default", 5, 7, "WH-R-2", "WB Шушары", 339, warehouse_name="Натали")
        assert "<b>Приёмка принята</b>" in item["text"]
        assert "<code>WH-R-2</code>" in item["text"]
        assert "Склад ФФ: Натали" in item["text"]
        assert "339 шт" in item["text"]
        assert item["url"].endswith("/ff-request/7")
        assert item["ff_id"] == 7

    def test_escapes_html_special_chars(self):
        # склад «H&M» не должен ломать HTML-разметку
        item = build_ready_item("default", 5, 1, "ASM-1", "FF-1", "H&M", 10)
        assert "H&amp;M" in item["text"]
        assert "H&M" not in item["text"]

    def test_no_url_when_missing_ids(self):
        assert build_ready_item(None, 5, 42, "A", "F", "D", 1)["url"] is None
        assert build_ready_item("default", None, 42, "A", "F", "D", 1)["url"] is None
        assert build_ready_item("default", 5, None, "A", "F", "D", 1)["url"] is None

    def test_qty_omitted_when_falsy(self):
        item = build_ready_item("default", 5, 1, "A", "F", "D", None)
        assert "шт" not in item["text"]


class TestFfBoardToggle:
    async def test_toggle_on_off_clears_message_id(self, db_session, project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-4001)
        assert b.ff_board_enabled is False

        assert await telegram_service.toggle_ff_board(db_session, b.id, project.id, True) is True
        await db_session.refresh(b)
        assert b.ff_board_enabled is True

        b.ff_board_message_id = 555  # как будто табло уже закреплено
        await db_session.commit()

        assert await telegram_service.toggle_ff_board(db_session, b.id, project.id, False) is True
        await db_session.refresh(b)
        assert b.ff_board_enabled is False
        assert b.ff_board_message_id is None  # на выключении id забывается

    async def test_project_isolation(self, db_session, project, other_project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-4002)
        assert await telegram_service.toggle_ff_board(db_session, b.id, other_project.id, True) is False
        await db_session.refresh(b)
        assert b.ff_board_enabled is False

    async def test_list_board_chats_scoped(self, db_session, project, other_project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-4003, board=True)
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-4004, board=False)
        await _make_binding(db_session, other_project.id, other_project.owner_id, chat_id=-4005, board=True)

        chats = await telegram_service.list_ff_board_chats(db_session, project.id)
        assert {c.chat_id for c in chats} == {-4003}


class TestRefreshFfBoard:
    async def test_creates_and_pins_first_time(self, db_session, project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-4101, board=True)
        fake_bot = AsyncMock()
        fake_bot.send_message.return_value = SimpleNamespace(message_id=777)
        with (
            patch("backend.integrations.telegram_bot.bot", new=fake_bot),
            patch("backend.services.fulfillment_service.build_ff_board_text", new=AsyncMock(return_value="BOARD")),
        ):
            await refresh_ff_board(db_session, project.id)
        fake_bot.send_message.assert_awaited_once()
        fake_bot.pin_chat_message.assert_awaited_once()
        await db_session.refresh(b)
        assert b.ff_board_message_id == 777

    async def test_edits_when_message_exists(self, db_session, project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-4102, board=True)
        b.ff_board_message_id = 123
        await db_session.commit()
        fake_bot = AsyncMock()
        with (
            patch("backend.integrations.telegram_bot.bot", new=fake_bot),
            patch("backend.services.fulfillment_service.build_ff_board_text", new=AsyncMock(return_value="BOARD")),
        ):
            await refresh_ff_board(db_session, project.id)
        fake_bot.edit_message_text.assert_awaited_once()
        fake_bot.send_message.assert_not_awaited()

    async def test_not_modified_is_swallowed(self, db_session, project):
        from aiogram.exceptions import TelegramBadRequest

        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-4103, board=True)
        b.ff_board_message_id = 123
        await db_session.commit()
        fake_bot = AsyncMock()
        fake_bot.edit_message_text.side_effect = TelegramBadRequest(
            method=AsyncMock(), message="Bad Request: message is not modified"
        )
        with (
            patch("backend.integrations.telegram_bot.bot", new=fake_bot),
            patch("backend.services.fulfillment_service.build_ff_board_text", new=AsyncMock(return_value="BOARD")),
        ):
            await refresh_ff_board(db_session, project.id)  # must not raise, must not recreate
        fake_bot.send_message.assert_not_awaited()

    async def test_pin_failure_swallowed_message_still_stored(self, db_session, project):
        b = await _make_binding(db_session, project.id, project.owner_id, chat_id=-4104, board=True)
        fake_bot = AsyncMock()
        fake_bot.send_message.return_value = SimpleNamespace(message_id=888)
        fake_bot.pin_chat_message.side_effect = RuntimeError("not enough rights to pin")
        with (
            patch("backend.integrations.telegram_bot.bot", new=fake_bot),
            patch("backend.services.fulfillment_service.build_ff_board_text", new=AsyncMock(return_value="BOARD")),
        ):
            await refresh_ff_board(db_session, project.id)  # pin fails, but message_id persists
        await db_session.refresh(b)
        assert b.ff_board_message_id == 888

    async def test_noop_when_no_board_chats(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-4105, board=False)
        fake_bot = AsyncMock()
        with patch("backend.integrations.telegram_bot.bot", new=fake_bot):
            await refresh_ff_board(db_session, project.id)
        fake_bot.send_message.assert_not_awaited()
        fake_bot.edit_message_text.assert_not_awaited()

    async def test_noop_when_bot_none(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-4106, board=True)
        with patch("backend.integrations.telegram_bot.bot", new=None):
            await refresh_ff_board(db_session, project.id)  # must not raise

    async def test_no_board_text_no_send(self, db_session, project):
        await _make_binding(db_session, project.id, project.owner_id, chat_id=-4107, board=True)
        fake_bot = AsyncMock()
        with (
            patch("backend.integrations.telegram_bot.bot", new=fake_bot),
            patch("backend.services.fulfillment_service.build_ff_board_text", new=AsyncMock(return_value=None)),
        ):
            await refresh_ff_board(db_session, project.id)
        fake_bot.send_message.assert_not_awaited()
