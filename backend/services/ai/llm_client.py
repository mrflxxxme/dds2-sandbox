"""
Claude API wrapper for DDS AI agent.

Thin client around anthropic SDK with retry and rate limit handling.
"""

import asyncio
import logging

import anthropic
import httpx

from backend.config import settings

logger = logging.getLogger("dds.ai")

# Lazy singleton
_client: anthropic.AsyncAnthropic | None = None

# Retry config
_MAX_RETRIES = 3
_BASE_DELAY = 2.0  # seconds


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
    tool_choice: dict | None = None,
    enable_cache: bool = True,
    timeout: float | None = None,
) -> anthropic.types.Message:
    """Send a chat request to Claude with optional tools.

    Returns the raw Message object for tool_use handling.
    ``tool_choice`` — e.g. {"type": "any"} to force tool use.
    ``enable_cache`` — if True (default), marks system + tools with
    cache_control=ephemeral for prompt caching (~90% savings on repeat calls
    within 5-minute TTL). Disable for one-shot or rapidly-changing prompts.
    ``timeout`` — если задан, ЖЁСТКИЙ потолок (сек) на всю операцию, включая
    ретраи и backoff. Без него SDK-дефолт ~600с на попытку × ретраи → вызов
    может висеть минуты (прод-инцидент со скан-счётом 2026-07-23: nginx
    proxy_read_timeout=120с срабатывал раньше). Синхронные HTTP-хендлеры,
    отдающие ответ пользователю, обязаны передавать бюджет < 120с.
    Retries on transient errors (429, 5xx) with exponential backoff.
    """
    client = get_client()

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": messages,
    }
    if system:
        if enable_cache:
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            kwargs["system"] = system
    if tools:
        if enable_cache and len(tools) > 0:
            cached_tools = [dict(t) for t in tools]
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}
            kwargs["tools"] = cached_tools
        else:
            kwargs["tools"] = tools
    if tool_choice and tools:
        kwargs["tool_choice"] = tool_choice
    if timeout is not None:
        # Пер-запросный потолок httpx: один зависший вызов не съест весь бюджет
        # (SDK-дефолт read=600с). wait_for ниже добивает суммарный потолок по ретраям.
        kwargs["timeout"] = timeout

    async def _send() -> anthropic.types.Message:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return await client.messages.create(**kwargs)  # type: ignore[return-value,no-any-return]
            except anthropic.RateLimitError as exc:
                last_exc = exc
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    "Anthropic rate limit (429), retry %d/%d in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except anthropic.InternalServerError as exc:
                last_exc = exc
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    "Anthropic server error (%s), retry %d/%d in %.1fs",
                    exc.status_code,
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
            except anthropic.APIConnectionError as exc:
                last_exc = exc
                delay = _BASE_DELAY * (2**attempt)
                logger.warning(
                    "Anthropic connection error, retry %d/%d in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    # timeout — жёсткий потолок ВСЕЙ операции (все попытки + backoff). Отменяет
    # in-flight запрос → быстро освобождает DB-сессию хендлера (иначе залипший
    # разбор счёта держал пул до исчерпания — прод 2026-07-23).
    if timeout is not None:
        return await asyncio.wait_for(_send(), timeout=timeout)
    return await _send()
