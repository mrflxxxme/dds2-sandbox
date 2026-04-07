"""
Web-chat AI orchestrator — single powerful agent with all tools.

Same quality as Telegram bot (monolithic prompt, all tools, full context)
but with Markdown formatting and DB-backed conversation history.

Flow:
1. Load conversation history from DB
2. Load memory context + brand notes
3. Build comprehensive system prompt
4. Run tool-calling loop (up to MAX_ROUNDS)
5. Save insights to memory
6. Return final answer
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.ai_chat import AiMessage
from backend.services.ai.executor import execute_tool
from backend.services.ai.llm_client import chat
from backend.services.ai.memory import get_memory_context, save_insights
from backend.services.ai.prompts.web_chat import WEB_CHAT_PROMPT
from backend.services.ai.tools import ALL_TOOLS
from backend.services.ai.tools.common import normalize_brand
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ai.orchestrator")

# Quality > token savings
MAX_ROUNDS = 8
MAX_TOKENS = 4096
TEMPERATURE = 0.3
MODEL = "claude-sonnet-4-20250514"
HISTORY_LIMIT = 20  # last 20 messages for context


async def ask(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    chat_id: int,
    question: str,
    tax_rate: float = 6.0,
) -> str:
    """Main entry point — user question -> AI answer.

    1. Check rate limit
    2. Load conversation history from DB
    3. Build system prompt with brand notes + memory
    4. Run tool-calling loop
    5. Save insights
    6. Return answer
    """
    from backend.services.ai.agent import _check_rate_limit

    # 1. Rate limit
    rate_error = await _check_rate_limit(chat_id, project_id)
    if rate_error:
        return rate_error

    # 2. Load conversation history from DB (persistent, not Redis)
    db_history = await _load_db_history(db, chat_id, limit=HISTORY_LIMIT)

    # 3. Build system prompt
    brand_for_tools = normalize_brand(brand)
    brand_name = brand or "все бренды"

    # Memory context
    memory_context = await get_memory_context(project_id, brand_for_tools)

    # Brand notes from DB
    brand_notes = "(нет заметок)"
    try:
        from backend.services.telegram_service import list_brand_notes

        if brand_for_tools:
            note_objs = await list_brand_notes(db, project_id, brand_for_tools)
            if note_objs:
                brand_notes = "\n".join(f"- {n.note}" for n in note_objs)
    except Exception:  # noqa: S110
        pass  # brand notes are optional, don't fail the whole request

    today = utcnow().strftime("%Y-%m-%d")
    system = WEB_CHAT_PROMPT.format(
        brand=brand_name,
        today=today,
        brand_notes=brand_notes,
        memory_context=memory_context or "(нет предыдущих инсайтов)",
    )

    # 4. Build messages for Claude
    messages: list[dict] = list(db_history)
    messages.append({"role": "user", "content": question})

    # 5. Tool-calling loop
    tools_used: list[str] = []
    total_tokens = 0

    for _round in range(MAX_ROUNDS):
        try:
            response = await chat(
                messages=messages,
                tools=ALL_TOOLS,
                system=system,
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
            )
        except Exception as exc:
            logger.error("Chat API failed (round %d): %s", _round + 1, exc)
            return "Ошибка при обращении к AI. Попробуйте позже."

        total_tokens += _count_tokens(response)

        # Final answer
        if response.stop_reason == "end_turn":
            answer = _extract_text(response)
            logger.info(
                "Web chat complete: tools=%s tokens=%d rounds=%d",
                tools_used,
                total_tokens,
                _round + 1,
            )

            # Save insights asynchronously
            insights = _extract_insights(answer)
            if insights:
                await save_insights(project_id, brand_for_tools, insights)

            return answer

        # Tool calls — execute sequentially (async session safety)
        if response.stop_reason == "tool_use":
            assistant_content: list[dict] = []
            tool_results: list[dict] = []

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
                    tools_used.append(block.name)
                    logger.info("Tool call '%s' (round %d)", block.name, _round + 1)

                    try:
                        result = await execute_tool(
                            db,
                            project_id,
                            brand_for_tools,
                            tax_rate,
                            block.name,
                            block.input,
                        )
                    except Exception as exc:
                        logger.error("Tool '%s' failed: %s", block.name, exc)
                        result = json.dumps({"error": str(exc)})

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

        # Unexpected stop reason — try to extract text
        text = _extract_text(response)
        if text:
            return text
        break

    # Loop exhausted — force final answer without tools
    logger.warning("Web chat exhausted %d rounds, forcing final answer", MAX_ROUNDS)
    try:
        messages.append(
            {"role": "user", "content": "Суммируй все полученные данные и дай финальный ответ. Не вызывай инструменты."}
        )
        final_response = await chat(
            messages=messages,
            system=system,
            model=MODEL,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
        )
        answer = _extract_text(final_response)
        if answer:
            return answer
    except Exception:
        logger.exception("Failed to produce final answer")

    return "Не удалось получить ответ. Попробуйте переформулировать вопрос."


# ── DB History ───────────────────────────────────────────────────────────


async def _load_db_history(
    db: AsyncSession,
    conversation_id: int,
    limit: int = 20,
) -> list[dict]:
    """Load recent messages from DB for conversation context.

    Returns list of {"role": "user"|"assistant", "content": "..."} dicts.
    Only loads user/assistant text messages (no tool calls in history).
    """
    result = await db.execute(
        select(AiMessage)
        .where(AiMessage.conversation_id == conversation_id)
        .order_by(AiMessage.created_at.desc())
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()  # oldest first

    messages: list[dict] = []
    for msg in rows:
        if msg.role in ("user", "assistant") and msg.content:
            messages.append({"role": msg.role, "content": msg.content})

    return messages


# ── Helpers ──────────────────────────────────────────────────────────────


def _extract_text(response: object) -> str:
    """Extract concatenated text from Claude response content blocks."""
    parts: list[str] = []
    for block in response.content:  # type: ignore[attr-defined]
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts) if parts else ""


def _count_tokens(response: object) -> int:
    """Return total tokens from the response usage metadata."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "output_tokens", 0) or 0)


def _extract_insights(text: str) -> list[str]:
    """Extract [INSIGHT] markers from agent output."""
    insights: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[INSIGHT]"):
            insight = stripped[len("[INSIGHT]") :].strip()
            if insight:
                insights.append(insight)
    return insights
