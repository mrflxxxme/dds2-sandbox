"""
Claude API wrapper for DDS AI agent.

Thin client around anthropic SDK with retry and rate limit handling.
"""

import logging

import anthropic
import httpx

from backend.config import settings

logger = logging.getLogger("dds.ai")

# Lazy singleton
_client: anthropic.AsyncAnthropic | None = None


def get_client() -> anthropic.AsyncAnthropic:
    """Get or create the Anthropic async client."""
    global _client
    if _client is None:
        kwargs: dict = {"api_key": settings.ANTHROPIC_API_KEY}
        if settings.TELEGRAM_PROXY:
            kwargs["http_client"] = httpx.AsyncClient(proxy=settings.TELEGRAM_PROXY)
            logger.info("Anthropic client using proxy")
        _client = anthropic.AsyncAnthropic(**kwargs)
    return _client


async def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    system: str = "",
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 2048,
    temperature: float = 0.3,
) -> anthropic.types.Message:
    """Send a chat request to Claude with optional tools.

    Returns the raw Message object for tool_use handling.
    """
    client = get_client()

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system
    if tools:
        kwargs["tools"] = tools

    return await client.messages.create(**kwargs)
