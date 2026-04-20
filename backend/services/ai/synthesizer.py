"""
Response synthesizer — merges multiple agent responses into one.

When the orchestrator routes a question to 2 domain agents, their
individual AgentResult responses are combined here into a single
cohesive answer using Claude Sonnet and the SYNTHESIZER_PROMPT.
"""

import logging

from backend.services.ai.agents.base import AgentResult
from backend.services.ai.llm_client import chat
from backend.services.ai.prompts.synthesizer import SYNTHESIZER_PROMPT

logger = logging.getLogger("dds.ai.synthesizer")


async def synthesize(
    question: str,
    brand: str,
    agent_results: list[AgentResult],
) -> str:
    """Combine responses from multiple domain agents.

    Builds a user message containing each agent's answer, then asks
    Claude Sonnet to merge them following SYNTHESIZER_PROMPT rules
    (no duplicate numbers, cross-domain links, prioritised action plan).

    Returns merged HTML response suitable for Telegram.
    """
    # Build the combined input for the synthesizer
    parts: list[str] = []
    for idx, result in enumerate(agent_results, 1):
        parts.append(f"--- Ответ эксперта {idx} ---\n{result.answer}")
    combined = "\n\n".join(parts)

    # Escape curly braces in user input to prevent .format() injection / KeyError
    safe_question = question.replace("{", "{{").replace("}", "}}")
    safe_brand = brand.replace("{", "{{").replace("}", "}}") if brand else brand
    system = SYNTHESIZER_PROMPT.format(question=safe_question, brand=safe_brand)
    messages = [{"role": "user", "content": combined}]

    try:
        response = await chat(
            messages=messages,
            system=system,
            model="claude-sonnet-4-20250514",
            max_tokens=2048,
            temperature=0.1,
            enable_cache=False,
        )

        # Extract text from response
        text_parts: list[str] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)

        if text_parts:
            return "\n".join(text_parts)

    except Exception as exc:
        logger.error("Synthesizer failed: %s", exc, exc_info=True)

    # Fallback: concatenate agent answers with a separator
    logger.warning("Falling back to simple concatenation of agent answers")
    fallback_parts: list[str] = []
    for result in agent_results:
        if result.answer:
            fallback_parts.append(result.answer)
    return "\n\n---\n\n".join(fallback_parts)
