# ruff: noqa: RUF001, RUF002, RUF003
"""
Регресс на прод-инцидент 2026-07-23: разбор скан-счёта зависал на минуты, т.к.
`llm_client.chat` не имел потолка (SDK-дефолт read=600с × ретраи), а nginx рвал
upstream на 120с. Каждый залипший запрос держал DB-сессию → исчерпание пула.

Гарантия: с `timeout=` вызов реджектит БЫСТРО (не ждёт зависший messages.create),
без `timeout=` — обратная совместимость (обычный путь, никаких изменений).
"""

import asyncio
import time

import pytest

from backend.services.ai import llm_client


class _StalledMessages:
    """Эмулирует зависший vision-вызов Anthropic (никогда не отвечает вовремя)."""

    def __init__(self, sleep_s: float) -> None:
        self._sleep_s = sleep_s
        self.calls = 0

    async def create(self, **kwargs: object) -> object:
        self.calls += 1
        await asyncio.sleep(self._sleep_s)
        return object()


class _StalledClient:
    def __init__(self, sleep_s: float) -> None:
        self.messages = _StalledMessages(sleep_s)


class _FastMessages:
    async def create(self, **kwargs: object) -> str:
        return "ok"


class _FastClient:
    messages = _FastMessages()


async def test_chat_timeout_bounds_a_hung_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """timeout=0.3с на вызове, который «висит» 30с → реджект за доли секунды, не 30с."""
    monkeypatch.setattr(llm_client, "get_client", lambda: _StalledClient(sleep_s=30.0))

    t0 = time.monotonic()
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await llm_client.chat(messages=[{"role": "user", "content": "x"}], timeout=0.3)
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, f"chat с timeout висел {elapsed:.1f}с — потолок не сработал"


async def test_chat_without_timeout_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Обратная совместимость: без timeout поведение прежнее — просто возвращает ответ."""
    monkeypatch.setattr(llm_client, "get_client", lambda: _FastClient())

    result = await llm_client.chat(messages=[{"role": "user", "content": "x"}])

    assert result == "ok"
