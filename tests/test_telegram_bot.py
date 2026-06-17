"""
Tests for backend/integrations/telegram_bot.py — Telegram bot handlers & helpers.

Covers:
- Pure functions: _strip_html, _fix_html, _split_message
- Command handlers: /start, /help, /setup, /brand, /notify, /note, /notes, /delnote
- Text handler: handle_text (AI routing, group mention filtering)
- Callback queries: cb_setup_project, cb_setup_brand
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════════
# Pure functions
# ═══════════════════════════════════════════════════════════════════════════════


class TestStripHtml:
    def test_removes_simple_tags(self):
        from backend.integrations.telegram_bot import _strip_html

        assert _strip_html("<b>bold</b>") == "bold"

    def test_removes_nested_tags(self):
        from backend.integrations.telegram_bot import _strip_html

        assert _strip_html("<b><i>text</i></b>") == "text"

    def test_no_tags(self):
        from backend.integrations.telegram_bot import _strip_html

        assert _strip_html("plain text") == "plain text"

    def test_empty_string(self):
        from backend.integrations.telegram_bot import _strip_html

        assert _strip_html("") == ""

    def test_removes_tag_with_attributes(self):
        from backend.integrations.telegram_bot import _strip_html

        assert _strip_html('<a href="http://example.com">link</a>') == "link"

    def test_preserves_entities(self):
        from backend.integrations.telegram_bot import _strip_html

        assert _strip_html("A &amp; B") == "A &amp; B"


class TestFixHtml:
    def test_escapes_stray_ampersand(self):
        from backend.integrations.telegram_bot import _fix_html

        result = _fix_html("A & B")
        assert result == "A &amp; B"

    def test_keeps_valid_entities(self):
        from backend.integrations.telegram_bot import _fix_html

        result = _fix_html("&amp; &lt; &gt; &quot; &#123;")
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result

    def test_keeps_allowed_tags(self):
        from backend.integrations.telegram_bot import _fix_html

        for tag in ("b", "i", "u", "s", "code", "pre", "blockquote"):
            result = _fix_html(f"<{tag}>text</{tag}>")
            assert f"<{tag}>" in result
            assert f"</{tag}>" in result

    def test_escapes_unknown_tags(self):
        from backend.integrations.telegram_bot import _fix_html

        result = _fix_html("<div>text</div>")
        assert "<div>" not in result
        assert "&lt;div&gt;" in result

    def test_preserves_unclosed_lt(self):
        from backend.integrations.telegram_bot import _fix_html

        # "< 100" has no closing ">" so regex doesn't match — left as-is
        result = _fix_html("Price < 100")
        assert "Price" in result

    def test_escapes_unknown_tag_with_gt(self):
        from backend.integrations.telegram_bot import _fix_html

        result = _fix_html("Price <span>100</span>")
        assert "<span>" not in result
        assert "&lt;span&gt;" in result

    def test_mixed_valid_and_invalid(self):
        from backend.integrations.telegram_bot import _fix_html

        result = _fix_html("<b>bold</b> <span>bad</span>")
        assert "<b>bold</b>" in result
        assert "<span>" not in result


class TestSplitMessage:
    def test_short_message_no_split(self):
        from backend.integrations.telegram_bot import _split_message

        text = "Hello world"
        assert _split_message(text) == [text]

    def test_exact_limit(self):
        from backend.integrations.telegram_bot import _split_message

        text = "a" * 4096
        assert _split_message(text) == [text]

    def test_splits_on_newline(self):
        from backend.integrations.telegram_bot import _split_message

        line = "x" * 50 + "\n"
        text = line * 100  # 5100 chars
        chunks = _split_message(text, 4096)
        assert len(chunks) >= 2
        for chunk in chunks:
            assert len(chunk) <= 4096

    def test_splits_long_line_at_limit(self):
        from backend.integrations.telegram_bot import _split_message

        text = "x" * 8000  # No newlines
        chunks = _split_message(text, 4096)
        assert len(chunks) == 2
        assert chunks[0] == "x" * 4096
        assert chunks[1] == "x" * 3904

    def test_empty_string(self):
        from backend.integrations.telegram_bot import _split_message

        assert _split_message("") == [""]

    def test_custom_limit(self):
        from backend.integrations.telegram_bot import _split_message

        text = "abc\ndef\nghi"
        chunks = _split_message(text, 5)
        assert all(len(c) <= 5 for c in chunks)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: mock Message factory
# ═══════════════════════════════════════════════════════════════════════════════


def _make_message(text="", chat_type="group", chat_id=-100123, user_id=42):
    """Create a mock aiogram Message."""
    msg = AsyncMock()
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = chat_type
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.reply = AsyncMock()
    msg.reply_to_message = None
    return msg


def _make_callback(data="", chat_type="group", chat_id=-100123, user_id=42):
    """Create a mock aiogram CallbackQuery."""
    cb = AsyncMock()
    cb.data = data
    cb.from_user = MagicMock()
    cb.from_user.id = user_id
    cb.message = _make_message(chat_type=chat_type, chat_id=chat_id)
    cb.answer = AsyncMock()
    return cb


# ═══════════════════════════════════════════════════════════════════════════════
# Command handlers
# ═══════════════════════════════════════════════════════════════════════════════


class TestCmdStart:
    @pytest.mark.asyncio
    async def test_start_no_token(self):
        from backend.integrations.telegram_bot import cmd_start

        msg = _make_message("/start", chat_type="private")
        await cmd_start(msg)
        msg.reply.assert_called_once()
        text = msg.reply.call_args[0][0]
        assert "DDS Analytics Bot" in text

    @pytest.mark.asyncio
    async def test_start_deeplink_private_valid(self):
        from backend.integrations.telegram_bot import cmd_start_deeplink

        msg = _make_message("/start abc123token", chat_type="private")

        with (
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
        ):
            svc.verify_deep_link_token = AsyncMock(return_value=7)
            svc.link_telegram_user = AsyncMock()
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await cmd_start_deeplink(msg)
            svc.verify_deep_link_token.assert_called_once_with("abc123token")
            svc.link_telegram_user.assert_called_once()
            msg.reply.assert_called_once()
            assert "привязан" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_start_deeplink_invalid_token(self):
        from backend.integrations.telegram_bot import cmd_start_deeplink

        msg = _make_message("/start badtoken", chat_type="private")

        with patch("backend.integrations.telegram_bot.telegram_service") as svc:
            svc.verify_deep_link_token = AsyncMock(return_value=None)
            await cmd_start_deeplink(msg)
            msg.reply.assert_called_once()
            assert "истекла" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_start_deeplink_group_rejected(self):
        from backend.integrations.telegram_bot import cmd_start_deeplink

        msg = _make_message("/start token123", chat_type="group")
        await cmd_start_deeplink(msg)
        msg.reply.assert_called_once()
        assert "личных" in msg.reply.call_args[0][0]


class TestCmdHelp:
    @pytest.mark.asyncio
    async def test_help_returns_commands(self):
        from backend.integrations.telegram_bot import cmd_help

        msg = _make_message("/help")
        await cmd_help(msg)
        msg.reply.assert_called_once()
        text = msg.reply.call_args[0][0]
        assert "/setup" in text
        assert "/brand" in text
        assert "/notify" in text


class TestCmdSetup:
    @pytest.mark.asyncio
    async def test_setup_private_rejected(self):
        from backend.integrations.telegram_bot import cmd_setup

        msg = _make_message("/setup", chat_type="private")
        await cmd_setup(msg)
        msg.reply.assert_called_once()
        assert "групповых" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_setup_unlinked_user(self):
        from backend.integrations.telegram_bot import cmd_setup

        msg = _make_message("/setup", chat_type="supergroup")

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_dds_user_by_telegram = AsyncMock(return_value=None)

            await cmd_setup(msg)
            msg.reply.assert_called_once()
            assert "авторизуйтесь" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_setup_shows_projects(self):
        from backend.integrations.telegram_bot import cmd_setup

        msg = _make_message("/setup", chat_type="supergroup")

        tg_user = MagicMock()
        tg_user.user_id = 7
        project = MagicMock()
        project.name = "Мой магазин"
        project.id = 4

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_dds_user_by_telegram = AsyncMock(return_value=tg_user)
            svc.get_user_projects = AsyncMock(return_value=[project])

            await cmd_setup(msg)
            msg.reply.assert_called_once()
            assert "Выберите проект" in msg.reply.call_args[0][0]
            # Check inline keyboard was passed
            assert msg.reply.call_args[1].get("reply_markup") is not None


class TestCmdBrand:
    @pytest.mark.asyncio
    async def test_brand_private_rejected(self):
        from backend.integrations.telegram_bot import cmd_brand

        msg = _make_message("/brand", chat_type="private")
        await cmd_brand(msg)
        assert "групповых" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_brand_no_binding(self):
        from backend.integrations.telegram_bot import cmd_brand

        msg = _make_message("/brand", chat_type="supergroup")

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=None)

            await cmd_brand(msg)
            assert "/setup" in msg.reply.call_args[0][0]


class TestCmdNotify:
    @pytest.mark.asyncio
    async def test_notify_private_rejected(self):
        from backend.integrations.telegram_bot import cmd_notify

        msg = _make_message("/notify on", chat_type="private")
        await cmd_notify(msg)
        assert "групповых" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_notify_bad_args(self):
        from backend.integrations.telegram_bot import cmd_notify

        msg = _make_message("/notify", chat_type="supergroup")
        await cmd_notify(msg)
        assert "on" in msg.reply.call_args[0][0] and "off" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_notify_on(self):
        from backend.integrations.telegram_bot import cmd_notify

        msg = _make_message("/notify on", chat_type="supergroup")
        binding = MagicMock()
        binding.id = 1
        binding.project_id = 4

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            svc.toggle_notify = AsyncMock()

            await cmd_notify(msg)
            svc.toggle_notify.assert_called_once_with(session_cls.return_value.__aenter__.return_value, 1, 4, True)
            assert "включён" in msg.reply.call_args[0][0]


class TestCmdNote:
    @pytest.mark.asyncio
    async def test_note_private_rejected(self):
        from backend.integrations.telegram_bot import cmd_note

        msg = _make_message("/note test", chat_type="private")
        await cmd_note(msg)
        assert "групповых" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_note_no_text(self):
        from backend.integrations.telegram_bot import cmd_note

        msg = _make_message("/note", chat_type="supergroup")
        await cmd_note(msg)
        assert "текст" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_note_no_brand(self):
        from backend.integrations.telegram_bot import cmd_note

        msg = _make_message("/note some note", chat_type="supergroup")
        binding = MagicMock()
        binding.brand = None

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)

            await cmd_note(msg)
            assert "бренде" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_note_success(self):
        from backend.integrations.telegram_bot import cmd_note

        msg = _make_message("/note важная заметка", chat_type="supergroup")
        binding = MagicMock()
        binding.project_id = 4
        binding.brand = "TestBrand"

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            svc.add_brand_note = AsyncMock()

            await cmd_note(msg)
            svc.add_brand_note.assert_called_once()
            assert "добавлена" in msg.reply.call_args[0][0]


class TestCmdNotes:
    @pytest.mark.asyncio
    async def test_notes_empty(self):
        from backend.integrations.telegram_bot import cmd_notes

        msg = _make_message("/notes", chat_type="supergroup")
        binding = MagicMock()
        binding.project_id = 4
        binding.brand = "TestBrand"

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            svc.list_brand_notes = AsyncMock(return_value=[])

            await cmd_notes(msg)
            assert "нет" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_notes_lists(self):
        from backend.integrations.telegram_bot import cmd_notes

        msg = _make_message("/notes", chat_type="supergroup")
        binding = MagicMock()
        binding.project_id = 4
        binding.brand = "TestBrand"
        note1 = MagicMock()
        note1.note = "First note"
        note2 = MagicMock()
        note2.note = "Second note"

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            svc.list_brand_notes = AsyncMock(return_value=[note1, note2])

            await cmd_notes(msg)
            text = msg.reply.call_args[0][0]
            assert "First note" in text
            assert "Second note" in text


class TestCmdDelnote:
    @pytest.mark.asyncio
    async def test_delnote_bad_arg(self):
        from backend.integrations.telegram_bot import cmd_delnote

        msg = _make_message("/delnote abc", chat_type="supergroup")
        await cmd_delnote(msg)
        assert "номер" in msg.reply.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_delnote_out_of_range(self):
        from backend.integrations.telegram_bot import cmd_delnote

        msg = _make_message("/delnote 5", chat_type="supergroup")
        binding = MagicMock()
        binding.project_id = 4
        binding.brand = "TestBrand"
        note1 = MagicMock()
        note1.id = 10

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            svc.list_brand_notes = AsyncMock(return_value=[note1])

            await cmd_delnote(msg)
            assert "#5" in msg.reply.call_args[0][0]

    @pytest.mark.asyncio
    async def test_delnote_success(self):
        from backend.integrations.telegram_bot import cmd_delnote

        msg = _make_message("/delnote 1", chat_type="supergroup")
        binding = MagicMock()
        binding.project_id = 4
        binding.brand = "TestBrand"
        note1 = MagicMock()
        note1.id = 10

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            svc.list_brand_notes = AsyncMock(return_value=[note1])
            svc.delete_brand_note = AsyncMock()

            await cmd_delnote(msg)
            svc.delete_brand_note.assert_called_once()
            assert "удалена" in msg.reply.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════════════════
# Callback queries
# ═══════════════════════════════════════════════════════════════════════════════


class TestCallbacks:
    @pytest.mark.asyncio
    async def test_cb_setup_project_shows_brands(self):
        from backend.integrations.telegram_bot import cb_setup_project

        cb = _make_callback("setup:p:4")

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_project_brands = AsyncMock(return_value=["BrandA", "BrandB"])

            await cb_setup_project(cb)
            cb.message.edit_text.assert_called_once()
            assert "бренд" in cb.message.edit_text.call_args[0][0].lower()
            cb.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_cb_setup_brand_saves_binding(self):
        from backend.integrations.telegram_bot import cb_setup_brand

        cb = _make_callback("setup:b:4:TestBrand")

        tg_user = MagicMock()
        tg_user.user_id = 7

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_dds_user_by_telegram = AsyncMock(return_value=tg_user)
            svc.bind_chat = AsyncMock()

            await cb_setup_brand(cb)
            svc.bind_chat.assert_called_once()
            cb.message.edit_text.assert_called_once()
            assert "TestBrand" in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cb_setup_brand_all_brands(self):
        from backend.integrations.telegram_bot import cb_setup_brand

        cb = _make_callback("setup:b:4:")

        tg_user = MagicMock()
        tg_user.user_id = 7

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_dds_user_by_telegram = AsyncMock(return_value=tg_user)
            svc.bind_chat = AsyncMock()

            await cb_setup_brand(cb)
            # brand=None should be passed when empty
            call_args = svc.bind_chat.call_args
            assert call_args[0][3] is None  # brand arg
            assert "все бренды" in cb.message.edit_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cb_ff_items_shows_goods(self):
        from backend.integrations.telegram_bot import cb_ff_items

        cb = _make_callback("ff_items:42")
        binding = MagicMock()
        binding.project_id = 4

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
            patch("backend.integrations.telegram_bot.fulfillment_service") as ff,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            ff.get_ff_request_goods = AsyncMock(return_value="📦 <b>Состав</b>")

            await cb_ff_items(cb)
            ff.get_ff_request_goods.assert_awaited_once()
            _, pid, fid = ff.get_ff_request_goods.call_args.args
            assert (pid, fid) == (4, 42)
            cb.message.answer.assert_called_once()
            assert cb.message.answer.call_args.kwargs.get("parse_mode") == "HTML"
            cb.answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_cb_ff_items_bad_id_alerts(self):
        from backend.integrations.telegram_bot import cb_ff_items

        cb = _make_callback("ff_items:not-an-int")
        await cb_ff_items(cb)
        cb.answer.assert_called_once()
        assert cb.answer.call_args.kwargs.get("show_alert") is True
        cb.message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_cb_ff_items_unbound_alerts(self):
        from backend.integrations.telegram_bot import cb_ff_items

        cb = _make_callback("ff_items:42")
        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=None)

            await cb_ff_items(cb)
            cb.answer.assert_called_once()
            assert cb.answer.call_args.kwargs.get("show_alert") is True
            cb.message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_cb_ff_items_no_goods_alerts(self):
        from backend.integrations.telegram_bot import cb_ff_items

        cb = _make_callback("ff_items:42")
        binding = MagicMock()
        binding.project_id = 4

        with (
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
            patch("backend.integrations.telegram_bot.fulfillment_service") as ff,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)
            ff.get_ff_request_goods = AsyncMock(return_value=None)

            await cb_ff_items(cb)
            cb.message.answer.assert_not_called()
            cb.answer.assert_called_once()
            assert cb.answer.call_args.kwargs.get("show_alert") is True


# ═══════════════════════════════════════════════════════════════════════════════
# Text handler (AI routing)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHandleText:
    @pytest.mark.asyncio
    async def test_group_no_mention_ignored(self):
        """In group chat, message without @mention should be ignored."""
        from backend.integrations.telegram_bot import handle_text

        msg = _make_message("просто сообщение", chat_type="supergroup")

        with patch("backend.integrations.telegram_bot.bot", None):
            await handle_text(msg)
            msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_mention_triggers_ai(self):
        """In group chat, @mention should trigger AI."""
        from backend.integrations.telegram_bot import handle_text

        msg = _make_message("@testbot как продажи?", chat_type="supergroup")

        mock_bot = AsyncMock()
        me = MagicMock()
        me.username = "testbot"
        me.id = 999
        mock_bot.me = AsyncMock(return_value=me)
        mock_bot.send_chat_action = AsyncMock()

        binding = MagicMock()
        binding.project_id = 4
        binding.brand = "TestBrand"

        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.tax_rate = 6
        mock_db.get = AsyncMock(return_value=mock_project)

        with (
            patch("backend.integrations.telegram_bot.bot", mock_bot),
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
            patch("backend.integrations.telegram_bot._send_html", new_callable=AsyncMock) as send_html,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)

            with patch("backend.services.ai.orchestrator.ask", new_callable=AsyncMock) as mock_ask:
                mock_ask.return_value = "Продажи за сегодня: 100K"
                await handle_text(msg)
                mock_ask.assert_called_once()
                send_html.assert_called_once()

    @pytest.mark.asyncio
    async def test_unbound_chat_ignored(self):
        """Message in unbound chat (no binding) should be silently ignored."""
        from backend.integrations.telegram_bot import handle_text

        msg = _make_message("hello", chat_type="private")

        with (
            patch("backend.integrations.telegram_bot.bot", None),
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=None)

            await handle_text(msg)
            msg.reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_ai_error_sends_fallback(self):
        """When AI agent raises, user gets error message."""
        from backend.integrations.telegram_bot import handle_text

        msg = _make_message("вопрос", chat_type="private")

        binding = MagicMock()
        binding.project_id = 4
        binding.brand = None

        mock_db = AsyncMock()
        mock_project = MagicMock()
        mock_project.tax_rate = 6
        mock_db.get = AsyncMock(return_value=mock_project)

        with (
            patch("backend.integrations.telegram_bot.bot", None),
            patch("backend.integrations.telegram_bot.AsyncSessionLocal") as session_cls,
            patch("backend.integrations.telegram_bot.telegram_service") as svc,
            patch("backend.utils.telegram.send_alert", new_callable=AsyncMock),
        ):
            session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_db)
            session_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            svc.get_chat_binding = AsyncMock(return_value=binding)

            with patch("backend.services.ai.orchestrator.ask", new_callable=AsyncMock) as mock_ask:
                mock_ask.side_effect = RuntimeError("LLM timeout")
                await handle_text(msg)
                msg.reply.assert_called()
                assert "недоступен" in msg.reply.call_args[0][0]


# ═══════════════════════════════════════════════════════════════════════════════
# _send_html
# ═══════════════════════════════════════════════════════════════════════════════


class TestSendHtml:
    @pytest.mark.asyncio
    async def test_send_html_success(self):
        from backend.integrations.telegram_bot import _send_html

        msg = _make_message()
        await _send_html(msg, "<b>hello</b>")
        msg.reply.assert_called_once()
        assert msg.reply.call_args[1]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_html_fallback_plain(self):
        from backend.integrations.telegram_bot import _send_html

        msg = _make_message()
        # First call (HTML) fails, second (plain) succeeds
        msg.reply.side_effect = [Exception("Bad HTML"), None]

        with patch("backend.integrations.telegram_bot.bot", None):
            await _send_html(msg, "<b>hello</b>")
            assert msg.reply.call_count == 2
            # Second call should be plain text (no parse_mode)
            second_call = msg.reply.call_args_list[1]
            assert "parse_mode" not in second_call[1]

    @pytest.mark.asyncio
    async def test_send_html_long_message_split(self):
        from backend.integrations.telegram_bot import _send_html

        msg = _make_message()
        long_text = "line\n" * 1000  # ~5000 chars
        await _send_html(msg, long_text)
        assert msg.reply.call_count >= 2
