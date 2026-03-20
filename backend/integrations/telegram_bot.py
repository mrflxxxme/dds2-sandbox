"""
Telegram analytics bot — aiogram 3 setup + command handlers.

Bot lifecycle:
- Worker container creates bot + dispatcher on startup
- In production: webhook mode (Telegram POSTs to /api/v1/bot/webhook)
- In local dev: polling mode (bot polls Telegram API)
"""

import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.services import telegram_service

logger = logging.getLogger(__name__)

# Module-level singletons
bot: Bot | None = None
dp: Dispatcher | None = None
router = Router()


def create_bot() -> tuple[Bot, Dispatcher]:
    """Create and configure bot + dispatcher. Called once at worker startup."""
    global bot, dp

    session = None
    if settings.TELEGRAM_PROXY:
        from aiogram.client.session.aiohttp import AiohttpSession

        session = AiohttpSession(proxy=settings.TELEGRAM_PROXY)
        logger.info("Telegram bot using proxy: %s", settings.TELEGRAM_PROXY.split("@")[-1])

    bot = Bot(token=settings.TELEGRAM_BOT_TOKEN_ANALYTICS, session=session)
    dp = Dispatcher()
    dp.include_router(router)
    return bot, dp


async def set_bot_commands():
    """Register bot commands in Telegram UI."""
    if not bot:
        return
    await bot.set_my_commands(
        [
            BotCommand(command="help", description="Список команд"),
            BotCommand(command="setup", description="Привязать чат к проекту"),
            BotCommand(command="brand", description="Сменить бренд"),
            BotCommand(command="notify", description="Вкл/выкл дайджест"),
            BotCommand(command="note", description="Добавить заметку по бренду"),
            BotCommand(command="notes", description="Показать заметки"),
            BotCommand(command="delnote", description="Удалить заметку"),
        ]
    )


# ─── /start TOKEN — deep link auth (private chat only) ──────────────────────


@router.message(CommandStart(deep_link=True))
async def cmd_start_deeplink(message: Message):
    """Handle /start with deep link token for auth."""
    logger.info(
        "Deep link /start from tg_user=%s, chat=%s, text=%s", message.from_user.id, message.chat.id, message.text
    )

    if message.chat.type != "private":
        await message.reply("Авторизация работает только в личных сообщениях с ботом.")
        return

    token = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else ""
    if not token:
        await message.reply("Используйте ссылку из DDS для авторизации.")
        return

    logger.info("Verifying deep link token: %s...", token[:8])
    user_id = await telegram_service.verify_deep_link_token(token)
    if not user_id:
        logger.warning("Deep link token invalid or expired: %s...", token[:8])
        await message.reply("Ссылка истекла или недействительна. Сгенерируйте новую в DDS → Настройки → Telegram.")
        return

    logger.info("Deep link verified: tg_user=%s → dds_user=%s", message.from_user.id, user_id)
    async with AsyncSessionLocal() as db:
        await telegram_service.link_telegram_user(db, message.from_user.id, user_id)

    await message.reply(
        "Telegram привязан к вашему аккаунту DDS.\n\n"
        "Теперь добавьте бота в групповой чат и используйте /setup для привязки к проекту."
    )
    logger.info("Deep link binding complete for tg_user=%s", message.from_user.id)


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start without token."""
    await message.reply(
        "DDS Analytics Bot\n\n"
        "Для начала работы:\n"
        "1. Откройте DDS → Настройки → Telegram\n"
        "2. Нажмите «Привязать Telegram»\n"
        "3. Перейдите по ссылке\n\n"
        "Используйте /help для списка команд."
    )


# ─── /help ───────────────────────────────────────────────────────────────────


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.reply(
        "Команды бота:\n\n"
        "/setup — привязать чат к проекту + бренду\n"
        "/brand — сменить бренд\n"
        "/notify on|off — вкл/выкл утренний дайджест\n"
        "/note текст — добавить заметку по бренду\n"
        "/notes — показать заметки\n"
        "/delnote N — удалить заметку #N\n"
        "/help — эта справка"
    )


# ─── /setup — bind chat to project + brand ───────────────────────────────────


@router.message(Command("setup"))
async def cmd_setup(message: Message):
    """Start chat binding flow: show project selection."""
    if message.chat.type == "private":
        await message.reply("Команда /setup работает только в групповых чатах.")
        return

    async with AsyncSessionLocal() as db:
        tg_user = await telegram_service.get_dds_user_by_telegram(db, message.from_user.id)
        if not tg_user:
            await message.reply("Сначала авторизуйтесь: откройте DDS → Настройки → Telegram → «Привязать».")
            return

        projects = await telegram_service.get_user_projects(db, tg_user.user_id)
        if not projects:
            await message.reply("У вас нет проектов в DDS.")
            return

    buttons = [[InlineKeyboardButton(text=p.name, callback_data=f"setup:p:{p.id}")] for p in projects]
    await message.reply("Выберите проект:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("setup:p:"))
async def cb_setup_project(callback: CallbackQuery):
    """User selected a project — show brand selection."""
    project_id = int(callback.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        brands = await telegram_service.get_project_brands(db, project_id)

    buttons = [[InlineKeyboardButton(text="Все бренды", callback_data=f"setup:b:{project_id}:")]]
    for brand in brands:
        cb_data = f"setup:b:{project_id}:{brand[:50]}"
        buttons.append([InlineKeyboardButton(text=brand, callback_data=cb_data)])

    await callback.message.edit_text("Выберите бренд:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    await callback.answer()


@router.callback_query(F.data.startswith("setup:b:"))
async def cb_setup_brand(callback: CallbackQuery):
    """User selected a brand — save binding."""
    parts = callback.data.split(":", 3)
    project_id = int(parts[2])
    brand = parts[3] if len(parts) > 3 and parts[3] else None

    tg_user_id = callback.from_user.id
    async with AsyncSessionLocal() as db:
        tg_user = await telegram_service.get_dds_user_by_telegram(db, tg_user_id)
        if not tg_user:
            await callback.answer("Ошибка: аккаунт не привязан.", show_alert=True)
            return

        await telegram_service.bind_chat(db, callback.message.chat.id, project_id, brand, tg_user.user_id)

    brand_text = brand or "все бренды"
    msg = "Чат привязан к проекту. Бренд: " + brand_text + "."
    await callback.message.edit_text(msg)
    await callback.answer()


# ─── /brand — change brand ───────────────────────────────────────────────────


@router.message(Command("brand"))
async def cmd_brand(message: Message):
    """Change brand for current chat binding."""
    if message.chat.type == "private":
        await message.reply("Команда /brand работает только в групповых чатах.")
        return

    async with AsyncSessionLocal() as db:
        binding = await telegram_service.get_chat_binding(db, message.chat.id)
        if not binding:
            await message.reply("Чат не привязан. Используйте /setup.")
            return

        brands = await telegram_service.get_project_brands(db, binding.project_id)

    buttons = [[InlineKeyboardButton(text="Все бренды", callback_data=f"setup:b:{binding.project_id}:")]]
    for brand in brands:
        cb_data = f"setup:b:{binding.project_id}:{brand[:50]}"
        buttons.append([InlineKeyboardButton(text=brand, callback_data=cb_data)])

    await message.reply("Выберите бренд:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


# ─── /notify on|off ──────────────────────────────────────────────────────────


@router.message(Command("notify"))
async def cmd_notify(message: Message):
    """Toggle daily digest notifications."""
    if message.chat.type == "private":
        await message.reply("Команда /notify работает только в групповых чатах.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        await message.reply("Использование: /notify on или /notify off")
        return

    enabled = args[1].lower() == "on"

    async with AsyncSessionLocal() as db:
        binding = await telegram_service.get_chat_binding(db, message.chat.id)
        if not binding:
            await message.reply("Чат не привязан. Используйте /setup.")
            return
        await telegram_service.toggle_notify(db, binding.id, binding.project_id, enabled)

    status = "включён" if enabled else "выключен"
    await message.reply(f"Утренний дайджест {status}.")


# ─── /note, /notes, /delnote ─────────────────────────────────────────────────


@router.message(Command("note"))
async def cmd_note(message: Message):
    """Add a brand note."""
    if message.chat.type == "private":
        await message.reply("Команда /note работает только в групповых чатах.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.reply("Использование: /note текст заметки")
        return

    async with AsyncSessionLocal() as db:
        binding = await telegram_service.get_chat_binding(db, message.chat.id)
        if not binding:
            await message.reply("Чат не привязан. Используйте /setup.")
            return
        if not binding.brand:
            await message.reply("Заметки доступны только при выбранном бренде. Используйте /brand.")
            return

        await telegram_service.add_brand_note(db, binding.project_id, binding.brand, args[1].strip())

    await message.reply("Заметка добавлена.")


@router.message(Command("notes"))
async def cmd_notes(message: Message):
    """List brand notes."""
    if message.chat.type == "private":
        await message.reply("Команда /notes работает только в групповых чатах.")
        return

    async with AsyncSessionLocal() as db:
        binding = await telegram_service.get_chat_binding(db, message.chat.id)
        if not binding:
            await message.reply("Чат не привязан. Используйте /setup.")
            return
        if not binding.brand:
            await message.reply("Заметки доступны только при выбранном бренде. Используйте /brand.")
            return

        notes = await telegram_service.list_brand_notes(db, binding.project_id, binding.brand)

    if not notes:
        await message.reply("Заметок пока нет. Добавьте: /note текст")
        return

    lines = [f"{i + 1}. {n.note}" for i, n in enumerate(notes)]
    await message.reply(f"Заметки ({binding.brand}):\n\n" + "\n".join(lines))


@router.message(Command("delnote"))
async def cmd_delnote(message: Message):
    """Delete a brand note by number."""
    if message.chat.type == "private":
        await message.reply("Команда /delnote работает только в групповых чатах.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip().isdigit():
        await message.reply("Использование: /delnote N (номер заметки из /notes)")
        return

    note_num = int(args[1].strip())

    async with AsyncSessionLocal() as db:
        binding = await telegram_service.get_chat_binding(db, message.chat.id)
        if not binding:
            await message.reply("Чат не привязан. Используйте /setup.")
            return
        if not binding.brand:
            await message.reply("Заметки доступны только при выбранном бренде.")
            return

        notes = await telegram_service.list_brand_notes(db, binding.project_id, binding.brand)
        if note_num < 1 or note_num > len(notes):
            await message.reply(f"Нет заметки #{note_num}. Всего заметок: {len(notes)}")
            return

        await telegram_service.delete_brand_note(db, notes[note_num - 1].id, binding.project_id)

    await message.reply("Заметка #" + str(note_num) + " удалена.")


# ─── Text messages → AI agent ──────────────────────────────────────────────


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message):
    """Route text messages to AI agent for bound chats.

    In groups: responds only to @mentions or replies to bot messages.
    In private chats: responds to all text (if chat is bound).
    """
    logger.info("handle_text: chat=%s type=%s text=%s", message.chat.id, message.chat.type, (message.text or "")[:80])

    # In group chats: only respond to @mentions or replies to bot
    if message.chat.type in ("group", "supergroup"):
        bot_username = None
        bot_id = None
        if bot:
            try:
                me = await bot.me()
                bot_username = me.username
                bot_id = me.id
            except Exception:
                logger.warning("Failed to get bot.me()")

        is_mention = bot_username and f"@{bot_username}" in (message.text or "")
        is_reply_to_bot = (
            message.reply_to_message
            and message.reply_to_message.from_user
            and bot_id
            and message.reply_to_message.from_user.id == bot_id
        )
        logger.info(
            "handle_text: bot=%s bot_username=%s is_mention=%s is_reply=%s",
            bot is not None,
            bot_username,
            is_mention,
            is_reply_to_bot,
        )
        if not is_mention and not is_reply_to_bot:
            return  # Ignore messages not directed at bot

    async with AsyncSessionLocal() as db:
        binding = await telegram_service.get_chat_binding(db, message.chat.id)
        if not binding:
            logger.info("handle_text: no binding for chat=%s", message.chat.id)
            return  # Ignore text in unbound chats

        # Get project tax_rate
        from backend.models.auth import Project

        project = await db.get(Project, binding.project_id)
        tax_rate = float(project.tax_rate or 6) if project else 6.0

        # Send typing action
        try:
            from aiogram.enums import ChatAction

            if bot:
                await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        except Exception:
            logger.debug("Failed to send typing action")

        # Strip bot mention from question text
        question = message.text or ""
        if bot:
            try:
                me = await bot.me()
                question = question.replace(f"@{me.username}", "").strip()
            except Exception:
                logger.debug("Failed to strip bot mention")

        try:
            from backend.services.ai.agent import ask

            logger.info(
                "AI ask: chat=%s project=%s brand=%s q=%s",
                message.chat.id,
                binding.project_id,
                binding.brand,
                question[:80],
            )
            answer = await ask(
                db=db,
                project_id=binding.project_id,
                brand=binding.brand,
                chat_id=message.chat.id,
                question=question,
                tax_rate=tax_rate,
            )
            logger.info("AI answer ready: chat=%s len=%d", message.chat.id, len(answer))
            try:
                await message.reply(answer, parse_mode="HTML")
            except Exception:
                # Fallback: plain text if HTML parsing fails or message deleted
                try:
                    await message.reply(answer)
                except Exception:
                    await bot.send_message(chat_id=message.chat.id, text=answer)
        except Exception as exc:
            logger.exception("AI agent error: %s", exc)
            from backend.utils.telegram import send_alert

            await send_alert(f"🤖 Bot AI error: {type(exc).__name__}: {exc}")
            try:
                await message.reply("Сервис временно недоступен, попробуйте через минуту.")
            except Exception:
                await bot.send_message(
                    chat_id=message.chat.id, text="Сервис временно недоступен, попробуйте через минуту."
                )
