"""Best-effort Telegram notifications for fulfillment status changes.

Fires when the FF sync transitions our docs: an assembly to READY or an inbound
receipt to ACCEPTED. Runs in the worker process (where the analytics-bot
singleton lives) and is strictly best-effort — it must never raise into the
sync, so every failure is swallowed and logged. No-op when the analytics bot is
not configured (e.g. local dev without TELEGRAM_BOT_TOKEN_ANALYTICS) or when no
chat of the project opted in (ff_notify_enabled).

Messages are HTML-formatted (parse_mode=HTML): headline + key values in <b>, the
FF number in <code> (tap-to-copy), the FF warehouse and (for assemblies) the WB
supply number, plus two inline buttons — "Открыть заявку" (deep-link to the FF
request card in DDS) and "Состав" (callback that lists line items in-chat).
"""

import asyncio
import html
import logging
from collections.abc import Awaitable
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest, AssemblyRequestItem
from backend.models.wb_fbo import WbFboSupply
from backend.services import telegram_service

if TYPE_CHECKING:
    from aiogram import Bot
    from aiogram.types import LinkPreviewOptions

    from backend.models.telegram import TelegramChatBinding

logger = logging.getLogger(__name__)

# Prod app origin (same host the bot webhook uses). Notifications only ever fire
# from the prod worker (bot is None locally), so the prod domain is correct.
_APP_BASE_URL = "https://app.vyatkin-wb.ru"


def _ff_request_url(slug: str | None, warehouse_id: int | None, ff_id: object) -> str | None:
    """Deep link to the FF-request card in DDS, or None if we can't build it."""
    if not (slug and warehouse_id and ff_id):
        return None
    return f"{_APP_BASE_URL}/p/{slug}/warehouse/{warehouse_id}/ff-request/{ff_id}"


def build_ready_item(
    slug: str | None,
    warehouse_id: int | None,
    ff_id: object,
    assembly_number: object,
    ff_number: object,
    dest: object,
    qty: object,
    *,
    warehouse_name: object = None,
    wb_number: object = None,
) -> dict[str, object]:
    """HTML message + deep-link for an assembly that just went READY."""
    e = html.escape
    lines = [
        "✅ <b>Сборка готова к отгрузке</b>",
        "",
        f"🏷 Заявка: <b>{e(str(assembly_number))}</b>",
        f"🏭 ФФ: <code>{e(str(ff_number))}</code>",
    ]
    if warehouse_name:
        lines.append(f"🏢 Склад ФФ: {e(str(warehouse_name))}")
    if wb_number:
        lines.append(f"🚚 Поставка ВБ: <code>{e(str(wb_number))}</code>")
    lines.append(f"🏬 Склад сдачи: {e(str(dest)) if dest else '—'}")
    if qty:
        lines.append(f"📦 Кол-во: <b>{e(str(qty))} шт</b>")
    return {"text": "\n".join(lines), "url": _ff_request_url(slug, warehouse_id, ff_id), "ff_id": ff_id}


def build_accept_item(
    slug: str | None,
    warehouse_id: int | None,
    ff_id: object,
    ff_number: object,
    dest: object,
    qty: object,
    *,
    warehouse_name: object = None,
) -> dict[str, object]:
    """HTML message + deep-link for an inbound receipt that was just accepted."""
    e = html.escape
    lines = [
        "📦 <b>Приёмка принята</b>",
        "",
        f"🏭 ФФ: <code>{e(str(ff_number))}</code>",
    ]
    if warehouse_name:
        lines.append(f"🏢 Склад ФФ: {e(str(warehouse_name))}")
    lines.append(f"🏬 Склад: {e(str(dest)) if dest else '—'}")
    if qty:
        lines.append(f"📥 Принято: <b>{e(str(qty))} шт</b>")
    return {"text": "\n".join(lines), "url": _ff_request_url(slug, warehouse_id, ff_id), "ff_id": ff_id}


async def notify_ff_events(db: AsyncSession, project_id: int, items: list[dict[str, object]]) -> None:
    """Send each item (HTML text + optional deep-link button) to opted-in chats.

    `db` is reused for the (read-only) bindings lookup — pass the sync session
    AFTER its commit. Best-effort: resolves opted-in chats once and sends via the
    analytics bot through TELEGRAM_PROXY. Any error is logged, never propagated.
    """
    if not items:
        return
    try:
        # Bot singleton only exists in the worker process; None elsewhere/locally.
        from backend.integrations.telegram_bot import bot

        if bot is None:
            return

        chats = await telegram_service.list_ff_notify_chats(db, project_id)
        if not chats:
            return

        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

        no_preview = LinkPreviewOptions(is_disabled=True)
        for binding in chats:
            for item in items:
                text = str(item.get("text") or "")
                url = item.get("url")
                ff_id = item.get("ff_id")
                buttons: list[InlineKeyboardButton] = []
                if url:
                    buttons.append(InlineKeyboardButton(text="🔗 Открыть заявку", url=str(url)))
                if ff_id:
                    # callback_data ≤64 байт: "ff_items:" (9 симв.) + int id
                    buttons.append(InlineKeyboardButton(text="📦 Состав", callback_data=f"ff_items:{ff_id}"))
                markup = InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None
                try:
                    await bot.send_message(
                        chat_id=binding.chat_id,
                        text=text,
                        parse_mode="HTML",
                        link_preview_options=no_preview,
                        reply_markup=markup,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("FF notify send failed (chat=%s): %s", binding.chat_id, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("FF notify error (project=%s): %s", project_id, exc)


async def _send_or_edit_board(
    bot: "Bot",
    binding: "TelegramChatBinding",
    text: str,
    no_preview: "LinkPreviewOptions",
    bad_request_exc: type[BaseException],
) -> None:
    """Edit the existing pinned board, or create + pin a new one and store its id.

    Mutates binding.ff_board_message_id when a new message is created (caller
    commits). Falls through to re-create when the stored message was deleted or
    can no longer be edited.
    """
    if binding.ff_board_message_id:
        try:
            await bot.edit_message_text(
                chat_id=binding.chat_id,
                message_id=binding.ff_board_message_id,
                text=text,
                parse_mode="HTML",
                link_preview_options=no_preview,
            )
            return
        except bad_request_exc as exc:
            msg = str(exc).lower()
            if "not modified" in msg:
                return  # состояние не изменилось — нормальный no-op
            if not ("not found" in msg or "can't be edited" in msg or "message to edit" in msg):
                raise
            # сообщение удалили/устарело — создаём и закрепляем заново ниже

    sent = await bot.send_message(
        chat_id=binding.chat_id,
        text=text,
        parse_mode="HTML",
        link_preview_options=no_preview,
    )
    binding.ff_board_message_id = sent.message_id
    try:
        await bot.pin_chat_message(chat_id=binding.chat_id, message_id=sent.message_id, disable_notification=True)
    except Exception as exc:  # боту не дали прав админа — табло работает и без закрепа
        logger.info("FF board pin skipped (chat=%s): %s", binding.chat_id, exc)


async def refresh_ff_board(db: AsyncSession, project_id: int) -> None:
    """Best-effort: пересобрать и обновить закреплённое табло заявок ФФ в каждом
    чате проекта, где включён ff_board_enabled. Редактирует уже закреплённое
    сообщение (создаёт + пинит при первом запуске). Никогда не бросает в синк.
    """
    try:
        from backend.integrations.telegram_bot import bot

        if bot is None:
            return

        chats = await telegram_service.list_ff_board_chats(db, project_id)
        if not chats:
            return

        from aiogram.exceptions import TelegramBadRequest
        from aiogram.types import LinkPreviewOptions

        from backend.services.fulfillment_service import build_ff_board_text

        no_preview = LinkPreviewOptions(is_disabled=True)
        # Текст зависит от привязанного склада (None = все склады). Кэшируем по
        # warehouse_id, чтобы несколько чатов одного склада не пересобирали табло.
        texts: dict[int | None, str | None] = {}
        for binding in chats:
            wh_id = binding.ff_board_warehouse_id
            if wh_id not in texts:
                texts[wh_id] = await build_ff_board_text(db, project_id, wh_id)
            text = texts[wh_id]
            if not text:
                continue  # для этого склада активных сборок нет — табло не трогаем
            try:
                await _send_or_edit_board(bot, binding, text, no_preview, TelegramBadRequest)
                await db.commit()  # сохранить ff_board_message_id, если создали новое
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("FF board update failed (chat=%s): %s", binding.chat_id, exc)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("FF board error (project=%s): %s", project_id, exc)


# ─── Уведомление в чат склада о новых заявках на сборку ──────────────────────

_ASM_CREATED_ROWS_LIMIT = 20  # строк в сводном bulk-сообщении (+ «…ещё M»)


def _fmt_qty_n(n: int) -> str:
    """1416 → «1 416» (пробел-разделитель тысяч для читаемости)."""
    return f"{n:,}".replace(",", " ")


def build_assembly_created_text(items: list[dict]) -> str:
    """HTML-сообщение «новая заявка(и) на сборку» для одного склада ФФ.

    items — [{number, qty, dest, eta}] заявок ЭТОГО склада. Один элемент —
    карточка заявки; несколько (bulk-распределение) — сводка со списком и итогом,
    чтобы не спамить чат отдельными сообщениями.
    """
    e = html.escape
    total = sum(it["qty"] for it in items)
    if len(items) == 1:
        it = items[0]
        lines = ["🆕 <b>Новая заявка на сборку</b>", "", f"🏷 <code>{e(it['number'])}</code>"]
        if it["dest"]:
            lines.append(f"🏬 Назначение: <b>{e(str(it['dest']))}</b>")
        lines.append(f"📦 Кол-во: <b>{_fmt_qty_n(it['qty'])} шт</b>")
        if it["eta"]:
            lines.append(f"🗓 План готовности: {it['eta']:%d.%m}")
        return "\n".join(lines)

    lines = [f"🆕 <b>{len(items)} новых заявок на сборку</b>", ""]
    shown = items[:_ASM_CREATED_ROWS_LIMIT]
    body = []
    for it in shown:
        dest = f" · <b>{e(str(it['dest']))}</b>" if it["dest"] else ""
        body.append(f"<code>{e(it['number'])}</code>{dest} · {_fmt_qty_n(it['qty'])} шт")
    if len(items) > _ASM_CREATED_ROWS_LIMIT:
        body.append(f"…ещё {len(items) - _ASM_CREATED_ROWS_LIMIT}")
    lines.append("<blockquote expandable>" + "\n".join(body) + "</blockquote>")
    lines.append(f"📦 Итого: <b>{_fmt_qty_n(total)} шт</b>")
    return "\n".join(lines)


async def notify_assembly_created(db: AsyncSession, project_id: int, request_ids: list[int]) -> None:
    """Best-effort: уведомить чат склада ФФ о новых заявках на сборку.

    Заявки группируются по складу — одно сводное сообщение на склад (без спама при
    bulk-распределении). Шлём только в чаты, привязанные к этому складу
    (ff_board_warehouse_id). Работает из web-процесса (httpx через analytics-бота).
    Никогда не бросает в создающую транзакцию — вызывать ПОСЛЕ commit.
    """
    if not request_ids:
        return
    try:
        # Дешёвый guard: без чатов, привязанных к складам, тяжёлый select не нужен
        # (горячий путь — фича включена не у всех проектов). Таблица крошечная.
        bindings = await telegram_service.list_warehouse_linked_bindings(db, project_id)
        if not bindings:
            return
        wh_to_chats: dict[int, list[int]] = {}
        for b in bindings:
            if b.ff_board_warehouse_id is None:  # отфильтровано в запросе, но сужаем тип
                continue
            wh_to_chats.setdefault(b.ff_board_warehouse_id, []).append(b.chat_id)

        result = await db.execute(
            select(
                AssemblyRequest.warehouse_id,
                AssemblyRequest.number,
                AssemblyRequest.estimated_ready_date,
                AssemblyRequest.wb_warehouse_name_manual,
                WbFboSupply.warehouse_name,
                func.coalesce(func.sum(AssemblyRequestItem.quantity), 0),
            )
            .outerjoin(
                WbFboSupply,
                and_(
                    WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id,
                    WbFboSupply.project_id == project_id,
                ),
            )
            .outerjoin(AssemblyRequestItem, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
            .where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.id.in_(request_ids),
                AssemblyRequest.warehouse_id.in_(list(wh_to_chats)),
                AssemblyRequest.is_deleted == False,
            )
            .group_by(AssemblyRequest.id, WbFboSupply.warehouse_name)
        )
        by_wh: dict[int, list[dict]] = {}
        for wh_id, number, eta, manual_dest, fbo_dest, qty in result.all():
            by_wh.setdefault(wh_id, []).append(
                {
                    "number": str(number),
                    "eta": eta,
                    "dest": fbo_dest or manual_dest or None,
                    "qty": int(qty or 0),
                }
            )

        # Шлём ПАРАЛЛЕЛЬНО — латенси ответа ограничена одним таймаутом, а не суммой
        # по складам/чатам (важно при bulk-распределении; отправка best-effort).
        sends: list[Awaitable[bool]] = []
        for wh_id, items in by_wh.items():
            text = build_assembly_created_text(items)
            sends.extend(
                telegram_service.send_analytics_message(chat_id, text) for chat_id in wh_to_chats.get(wh_id, [])
            )
        if sends:
            await asyncio.gather(*sends, return_exceptions=True)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("assembly-created notify error (project=%s): %s", project_id, exc)
