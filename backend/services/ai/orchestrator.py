"""
Multi-agent orchestrator — routes questions to specialized agents.

Flow:
1. Load memory context (Obsidian)
2. Classify intent via Haiku (cheap, fast)
3. Route to 1-2 domain agents (parallel if multiple)
4. If multiple agents → synthesize responses
5. Save insights to memory
6. Return final answer

Replaces the monolithic agent.ask() with orchestrated multi-agent calls.
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.services.ai.agent import _check_rate_limit, _get_history, _save_history
from backend.services.ai.agents import AGENTS
from backend.services.ai.agents.base import AgentResult
from backend.services.ai.llm_client import chat
from backend.services.ai.memory import get_memory_context, save_insights
from backend.services.ai.prompts.orchestrator import ORCHESTRATOR_PROMPT
from backend.services.ai.synthesizer import synthesize

logger = logging.getLogger("dds.ai.orchestrator")

# Default agent when classification fails or is ambiguous
_FALLBACK_AGENT = "analyst"


async def ask(
    db: AsyncSession,
    project_id: int,
    brand: str | None,
    chat_id: int,
    question: str,
    tax_rate: float = 6.0,
) -> str:
    """Main entry point — same signature as old agent.ask() for drop-in replacement.

    1. Check rate limit (reuse existing _check_rate_limit from agent.py)
    2. Load/save chat history (reuse existing _get_history/_save_history)
    3. Load memory context via memory.get_memory_context()
    4. Classify intent via _classify_intent() using Haiku
    5. Run selected agent(s) — use asyncio.gather for parallel
    6. If 2+ agents: synthesize via synthesizer.synthesize()
    7. Save insights via memory.save_insights()
    8. Return final answer
    """
    # 1. Rate limit
    rate_error = await _check_rate_limit(chat_id, project_id)
    if rate_error:
        return rate_error

    # 2. Load chat history
    history = await _get_history(chat_id)
    history.append({"role": "user", "content": question})

    # Build a short summary of recent history for intent classification
    history_summary = _build_history_summary(history)

    # 3. Load memory context + brand notes from DB
    memory_context = await get_memory_context(project_id, brand)
    # Restore brand notes (lost during refactoring from agent.py)
    try:
        from backend.services.telegram_service import list_brand_notes

        if brand:
            note_objs = await list_brand_notes(db, project_id, brand)
            if note_objs:
                notes_text = "\n".join(f"- {n.note}" for n in note_objs)
                memory_context += f"\n\n## Заметки о бренде\n{notes_text}"
    except Exception:  # noqa: S110
        pass  # graceful: brand notes optional

    # 4. Classify intent
    classification = await _classify_intent(question, history_summary)
    agent_names = classification.get("agents", [_FALLBACK_AGENT])
    reason = classification.get("reason", "")

    # Validate agent names — keep only known agents
    valid_names = [name for name in agent_names if name in AGENTS]
    if not valid_names:
        valid_names = [_FALLBACK_AGENT]

    logger.info(
        "Routing question to %s (reason: %s)",
        valid_names,
        reason,
    )

    # 5. Run selected agents (parallel if multiple)
    brand_name = brand or "все бренды"

    # Build history for agents (exclude the last user message — it's in question)
    agent_history = history[:-1] if len(history) > 1 else []

    async def _run_agent(name: str, session: AsyncSession) -> AgentResult:
        agent = AGENTS[name]
        logger.info("Running agent '%s' for project=%s brand=%s", name, project_id, brand_name)
        return await agent.run(
            db=session,
            project_id=project_id,
            brand=brand_name,
            tax_rate=tax_rate,
            question=question,
            memory_context=memory_context,
            history=agent_history,
        )

    async def _run_agent_with_own_session(name: str) -> AgentResult:
        """Run agent with a dedicated DB session (safe for parallel execution)."""
        async with AsyncSessionLocal() as own_db:
            return await _run_agent(name, own_db)

    if len(valid_names) == 1:
        # Single agent: reuse caller's DB session
        try:
            results = [await _run_agent(valid_names[0], db)]
        except Exception as exc:
            logger.error("Single agent '%s' failed: %s", valid_names[0], exc, exc_info=exc)
            results = [AgentResult(answer=f"Агент {valid_names[0]}: ошибка при обработке. Попробуйте позже.")]
    else:
        # Multiple agents: each gets its own DB session to avoid conflicts
        raw_results = await asyncio.gather(
            *[_run_agent_with_own_session(name) for name in valid_names],
            return_exceptions=True,
        )
        # Replace exceptions with error results
        clean_results: list[AgentResult] = []
        for idx, result in enumerate(raw_results):
            if isinstance(result, Exception):
                logger.error(
                    "Agent '%s' failed: %s",
                    valid_names[idx],
                    result,
                    exc_info=result,
                )
                clean_results.append(AgentResult(answer=f"Агент {valid_names[idx]}: ошибка при обработке."))
            else:
                clean_results.append(result)  # type: ignore[arg-type]
        results = clean_results

    # 6. Synthesize if multiple agents
    if len(results) > 1:
        final_answer = await synthesize(
            question=question,
            brand=brand_name,
            agent_results=results,
        )
    else:
        final_answer = results[0].answer

    # 7. Collect and save insights from all agents + synthesizer
    all_insights: list[str] = []
    for result in results:
        all_insights.extend(result.insights)
    # Parse insights from synthesized answer (<!-- INSIGHTS: ... -->)
    all_insights.extend(_parse_synthesis_insights(final_answer))
    if all_insights:
        await save_insights(project_id, brand, all_insights)

    # 8. Save history and return
    history.append({"role": "assistant", "content": final_answer})
    await _save_history(chat_id, history)

    total_tokens = sum(r.tokens_used for r in results)
    all_tools = []
    for r in results:
        all_tools.extend(r.tools_used)
    logger.info(
        "Orchestrator complete: agents=%s tools=%s tokens=%d",
        valid_names,
        all_tools,
        total_tokens,
    )

    return final_answer


async def _classify_intent(question: str, history_summary: str) -> dict:
    """Use Haiku to classify intent and select agents.

    Returns: {"agents": ["financier"], "reason": "..."}
    Uses ORCHESTRATOR_PROMPT, model=claude-haiku-4-5-20251001, max_tokens=200.
    Parse JSON from response. On parse error, fallback to analyst.
    """
    user_message = question
    if history_summary:
        user_message = f"Контекст диалога: {history_summary}\n\nВопрос: {question}"

    text = ""
    try:
        response = await chat(
            messages=[{"role": "user", "content": user_message}],
            system=ORCHESTRATOR_PROMPT,
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0.0,
        )

        # Extract text from response
        for block in response.content:
            if block.type == "text":
                text += block.text

        text = text.strip()
        logger.debug("Intent classification raw: %s", text)

        # Parse JSON — handle cases where LLM wraps in markdown code block
        if text.startswith("```"):
            # Strip ```json ... ```
            lines = text.splitlines()
            json_lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(json_lines).strip()

        result = json.loads(text)

        # Validate structure
        if isinstance(result, dict) and "agents" in result and isinstance(result["agents"], list):
            return result

        logger.warning("Unexpected classification format: %s", result)

    except json.JSONDecodeError as exc:
        logger.warning("Failed to parse intent JSON: %s (raw: %.200s)", exc, text)
    except Exception as exc:
        logger.warning("Intent classification failed: %s", exc, exc_info=True)

    return {"agents": [_FALLBACK_AGENT], "reason": "fallback — classification error"}


def _parse_synthesis_insights(text: str) -> list[str]:
    """Extract insights from synthesizer output (<!-- INSIGHTS: ... --> blocks)."""
    import re

    pattern = re.compile(r"<!--\s*INSIGHTS?:\s*(.*?)\s*-->", re.DOTALL | re.IGNORECASE)
    insights: list[str] = []
    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        for line in raw.splitlines():
            line = line.strip().lstrip("•-– ")
            if line:
                insights.append(line)
    return insights


def _build_history_summary(history: list[dict]) -> str:
    """Build a brief summary of recent conversation for intent classification.

    Takes last 4 messages (2 turns) and truncates each to 300 chars.
    """
    recent = history[-4:]
    parts: list[str] = []
    for msg in recent:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if isinstance(content, str):
            truncated = content[:300] + ("..." if len(content) > 300 else "")
            parts.append(f"{role}: {truncated}")
    return " | ".join(parts)
