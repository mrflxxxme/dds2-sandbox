"""
Base agent class for multi-agent AI system.

Each specialized agent (financier, marketer, etc.) inherits from BaseAgent
and implements its own system prompt and tool set. The run() loop handles
Claude tool-calling rounds, similar to the monolithic agent.py but scoped
per agent.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.executor import execute_tool
from backend.services.ai.llm_client import chat
from backend.utils.time import utcnow

logger = logging.getLogger("dds.ai.agents")


@dataclass
class AgentResult:
    """Result returned by an agent after execution."""

    answer: str
    tools_used: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    tokens_used: int = 0


class BaseAgent(ABC):
    """Abstract base for all domain agents.

    Each agent is stateless: no instance state is kept between calls.
    All per-request context is passed via ``run()`` parameters.
    """

    name: str = "base"
    description: str = "Abstract base agent"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.2
    max_tokens: int = 2048
    max_rounds: int = 5

    # ── Abstract interface ────────────────────────────────────────────

    @abstractmethod
    def get_system_prompt(
        self,
        brand: str,
        today: str,
        memory_context: str,
    ) -> str:
        """Build the system prompt for this agent.

        Args:
            brand: Brand name or "все бренды".
            today: Current date as ``YYYY-MM-DD``.
            memory_context: Prior insights / notes relevant to the conversation.
        """

    @abstractmethod
    def get_tools(self) -> list[dict]:
        """Return the tool definitions available to this agent.

        Each dict follows the Anthropic tool JSON schema format.
        """

    # ── Main execution loop ───────────────────────────────────────────

    async def run(
        self,
        db: AsyncSession,
        project_id: int,
        brand: str,
        tax_rate: float,
        question: str,
        memory_context: str = "",
        history: list[dict] | None = None,
    ) -> AgentResult:
        """Execute the agent's tool-calling loop and return a result.

        The loop sends messages to Claude, executes any requested tools via
        ``executor.execute_tool``, and feeds results back until Claude
        produces a final text answer or the round limit is reached.

        ``history`` — prior chat messages for multi-turn context.
        """
        today = utcnow().strftime("%Y-%m-%d")
        system = self.get_system_prompt(brand=brand, today=today, memory_context=memory_context)
        tools = self.get_tools()

        # Build messages: history + current question
        if history:
            messages: list[dict] = list(history)
            # Ensure last message is the current question
            if not messages or messages[-1].get("content") != question:
                messages.append({"role": "user", "content": question})
        else:
            messages = [{"role": "user", "content": question}]

        tools_used: list[str] = []
        total_tokens = 0

        for _round in range(self.max_rounds):
            # First round: force tool use so agent doesn't answer from memory
            tool_choice = {"type": "any"} if _round == 0 and tools else None

            kwargs: dict = {
                "messages": messages,
                "tools": tools or None,
                "system": system,
                "model": self.model,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
            }
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

            try:
                response = await chat(**kwargs)
            except Exception as exc:
                logger.error("Agent '%s' chat() failed (round %d): %s", self.name, _round + 1, exc)
                return AgentResult(
                    answer="Ошибка при обращении к AI. Попробуйте позже.",
                    tools_used=tools_used,
                    tokens_used=total_tokens,
                )

            total_tokens += _count_tokens(response)

            # ── Final answer ──────────────────────────────────────
            if response.stop_reason == "end_turn":
                answer = _extract_text(response)
                insights = _extract_insights(answer)
                return AgentResult(
                    answer=answer,
                    tools_used=tools_used,
                    insights=insights,
                    tokens_used=total_tokens,
                )

            # ── Tool calls ────────────────────────────────────────
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
                        logger.info(
                            "Agent '%s' calling tool '%s' (round %d)",
                            self.name,
                            block.name,
                            _round + 1,
                        )

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

            # ── Unexpected stop reason ────────────────────────────
            text = _extract_text(response)
            if text:
                return AgentResult(
                    answer=text,
                    tools_used=tools_used,
                    insights=_extract_insights(text),
                    tokens_used=total_tokens,
                )
            break

        # Loop exhausted — force a final text answer without tools
        logger.warning(
            "Agent '%s' exhausted %d rounds, forcing final answer",
            self.name,
            self.max_rounds,
        )
        try:
            messages.append(
                {
                    "role": "user",
                    "content": "Суммируй все полученные данные и дай финальный ответ. Не вызывай инструменты.",
                }
            )
            final_response = await chat(
                messages=messages,
                system=system,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            total_tokens += _count_tokens(final_response)
            answer = _extract_text(final_response)
            if answer:
                return AgentResult(
                    answer=answer,
                    tools_used=tools_used,
                    insights=_extract_insights(answer),
                    tokens_used=total_tokens,
                )
        except Exception:
            logger.exception("Agent '%s' failed to produce final answer", self.name)

        return AgentResult(
            answer="Не удалось получить ответ. Попробуйте переформулировать вопрос.",
            tools_used=tools_used,
            tokens_used=total_tokens,
        )


# ── Helpers (module-private) ──────────────────────────────────────────────


def _extract_text(response: object) -> str:
    """Extract concatenated text from Claude response content blocks."""
    parts: list[str] = []
    for block in response.content:  # type: ignore[attr-defined]
        if block.type == "text":
            parts.append(block.text)
    return "\n".join(parts) if parts else ""


def _count_tokens(response: object) -> int:
    """Return approximate total tokens from the response usage metadata."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "output_tokens", 0) or 0)


def _extract_insights(text: str) -> list[str]:
    """Extract insight markers from agent output.

    Agents can tag important findings with ``[INSIGHT] ...`` lines.
    These are collected for the memory system.
    """
    insights: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[INSIGHT]"):
            insight = stripped[len("[INSIGHT]") :].strip()
            if insight:
                insights.append(insight)
    return insights
