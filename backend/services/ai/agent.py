"""
AI agent orchestration — chat loop with Claude function calling.

Handles:
- System prompt construction with brand notes
- Chat history from Redis (last 10 messages, TTL 1h)
- Tool call loop (max 3 rounds)
- Rate limiting (20/hour per chat, 100/day per project)
"""

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import get_redis
from backend.services.ai.executor import execute_tool
from backend.services.ai.llm_client import chat
from backend.services.ai.tools import TOOLS
from backend.services.telegram_service import list_brand_notes

logger = logging.getLogger("dds.ai")

MAX_ROUNDS = 3
HISTORY_TTL = 3600  # 1 hour
HISTORY_MAX = 10  # last 10 messages
RATE_LIMIT_HOUR = 20
RATE_LIMIT_DAY = 100

SYSTEM_PROMPT = """Ты AI-аналитик для продавца на Wildberries.
Бренд: {brand}.
Отвечай только по данным этого бренда.

Правила:
- Кратко (до 500 символов), используй эмодзи
- Деньги: формат 1.47М р, 210К р
- Не придумывай данные - только из tools
- Если данных нет - скажи честно
- Рекомендации: конкретные действия, не общие советы
- Формат: Telegram MarkdownV2 (экранируй спецсимволы: . ! - ( ) = {{ }})
- Сегодня: {today}

Заметки по бренду (что знаешь о клиенте):
{brand_notes}"""


async def _check_rate_limit(chat_id: int, project_id: int) -> str | None:
    """Check rate limits. Returns error message if exceeded, None if OK."""
    redis = await get_redis()
    if not redis:
        return None

    # Per-chat: 20/hour
    chat_key = f"ai:rate:chat:{chat_id}"
    chat_count = await redis.incr(chat_key)
    if chat_count == 1:
        await redis.expire(chat_key, 3600)
    if chat_count > RATE_LIMIT_HOUR:
        ttl = await redis.ttl(chat_key)
        return f"Лимит запросов (20/час). Доступен через {ttl // 60 + 1} мин."

    # Per-project: 100/day
    proj_key = f"ai:rate:proj:{project_id}"
    proj_count = await redis.incr(proj_key)
    if proj_count == 1:
        await redis.expire(proj_key, 86400)
    if proj_count > RATE_LIMIT_DAY:
        return "Дневной лимит запросов (100) исчерпан."

    return None


async def _get_history(chat_id: int) -> list[dict]:
    """Load chat history from Redis."""
    redis = await get_redis()
    if not redis:
        return []
    raw = await redis.get(f"bot:chat:{chat_id}:history")
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return []
    return []


async def _save_history(chat_id: int, history: list[dict]):
    """Save chat history to Redis (keep last N messages)."""
    redis = await get_redis()
    if not redis:
        return
    # Keep only user+assistant messages, trim to last HISTORY_MAX
    trimmed = history[-(HISTORY_MAX * 2) :]
    await redis.setex(
        f"bot:chat:{chat_id}:history",
        HISTORY_TTL,
        json.dumps(trimmed, ensure_ascii=False),
    )


async def ask(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    chat_id: int,
    question: str,
    tax_rate: float = 6.0,
) -> str:
    """Main entry point: user question -> AI answer.

    1. Check rate limit
    2. Load brand notes + chat history
    3. Send to Claude with tools
    4. Execute tool calls (max 3 rounds)
    5. Return final text answer
    """
    # 1. Rate limit
    rate_error = await _check_rate_limit(chat_id, project_id)
    if rate_error:
        return rate_error

    # 2. Brand notes
    brand_name = brand or "все бренды"
    notes = []
    if brand:
        note_objs = await list_brand_notes(db, project_id, brand)
        notes = [n.note for n in note_objs]
    notes_text = "\n".join(f"- {n}" for n in notes) if notes else "(нет заметок)"

    from backend.utils.time import utcnow

    today = utcnow().strftime("%Y-%m-%d")

    system = SYSTEM_PROMPT.format(brand=brand_name, brand_notes=notes_text, today=today)

    # 3. Load history
    history = await _get_history(chat_id)
    history.append({"role": "user", "content": question})

    messages = list(history)

    # 4. Tool call loop
    for _round in range(MAX_ROUNDS):
        response = await chat(messages=messages, tools=TOOLS, system=system)

        # Check stop reason
        if response.stop_reason == "end_turn":
            # Extract text from content blocks
            text = _extract_text(response)
            history.append({"role": "assistant", "content": text})
            await _save_history(chat_id, history)
            return text

        if response.stop_reason == "tool_use":
            # Build assistant message with all content blocks
            assistant_content = []
            tool_results = []

            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    # Execute tool
                    result = await execute_tool(
                        db,
                        project_id,
                        brand,
                        tax_rate,
                        block.name,
                        block.input,
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )

            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason
        text = _extract_text(response)
        if text:
            history.append({"role": "assistant", "content": text})
            await _save_history(chat_id, history)
            return text
        break

    # Fallback if loop exhausted
    history.append({"role": "assistant", "content": "Не удалось получить ответ. Попробуйте переформулировать вопрос."})
    await _save_history(chat_id, history)
    return "Не удалось получить ответ. Попробуйте переформулировать вопрос."


def _extract_text(response) -> str:
    """Extract text content from Claude response."""
    parts = []
    for block in response.content:
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts) if parts else ""


async def generate_digest(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    tax_rate: float = 6.0,
) -> str:
    """Generate a morning digest for the given project/brand.

    Uses Claude Haiku for cost efficiency.
    """
    from backend.utils.time import utcnow

    today = utcnow()
    yesterday = today.strftime("%Y-%m-%d")

    brand_name = brand or "все бренды"
    notes = []
    if brand:
        note_objs = await list_brand_notes(db, project_id, brand)
        notes = [n.note for n in note_objs]
    notes_text = "\n".join(f"- {n}" for n in notes) if notes else "(нет заметок)"

    system = f"""Ты AI-аналитик для продавца на Wildberries.
Бренд: {brand_name}.
Сгенерируй краткий утренний дайджест по данным за вчера.

Формат:
- Заголовок с датой и брендом
- Ключевые метрики (заказы, выручка, маржа, ДРР) с процентом изменения
- Топ товар дня
- Предупреждение если есть проблемы (высокий ДРР, низкие остатки)
- Кратко, с эмодзи, до 500 символов
- Формат: Telegram MarkdownV2

Заметки по бренду: {notes_text}
Сегодня: {today.strftime('%Y-%m-%d')}"""

    question = f"Дай утренний дайджест за {yesterday}. Используй get_funnel_data и get_top_products."

    messages = [{"role": "user", "content": question}]

    for _round in range(MAX_ROUNDS):
        response = await chat(
            messages=messages,
            tools=TOOLS,
            system=system,
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
        )

        if response.stop_reason == "end_turn":
            return _extract_text(response)

        if response.stop_reason == "tool_use":
            assistant_content = []
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    result = await execute_tool(db, project_id, brand, tax_rate, block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                    )
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})
            continue

        text = _extract_text(response)
        if text:
            return text
        break

    return "Не удалось сгенерировать дайджест."
