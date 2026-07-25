# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты распределённого лока WB FBS (backend/services/wb_fbs/locks.py).

Лок закрывает ровно одну гонку: трансляцию остатков запускают ДВЕ точки входа —
кнопка «Передать остатки» (api-контейнер) и джоб раз в 3 минуты (worker).
`asyncio.Lock` тут бесполезен (локи in-process), поэтому `SET NX EX` в Redis,
и берётся он ВНУТРИ `stock_service.push_stocks` — иначе односторонний мьютекс
не исключает ничего.

Без БД и без сети: Redis подменяется фейком.
"""

from unittest.mock import AsyncMock, patch

import pytest

from backend.scheduler.jobs import wb_fbs as fbs_jobs
from backend.services.wb_fbs import locks


class _FakeRedis:
    """Минимальный Redis: `SET NX EX` + `EXISTS` + `EVAL` compare-and-delete."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def eval(self, script, numkeys, key, arg):
        if self.store.get(key) == arg:
            del self.store[key]
            return 1
        return 0


class TestPushLock:
    @pytest.mark.asyncio
    async def test_second_acquire_is_refused(self):
        """Второй прогон по тому же проекту лок не получает → тихо пропустит цикл."""
        fake = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake)):
            first = await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)
            second = await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)

        assert first is not None
        assert second is None
        assert "wb_fbs:push_lock:7" in fake.store

    @pytest.mark.asyncio
    async def test_other_project_is_not_blocked(self):
        """Лок скоуплен по проекту — соседний проект не страдает."""
        fake = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake)):
            assert await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7) is not None
            assert await locks.acquire_lock(locks.PUSH_LOCK_NAME, 8) is not None

    @pytest.mark.asyncio
    async def test_release_allows_next_run(self):
        """После снятия лока следующий прогон стартует."""
        fake = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake)):
            token = await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)
            await locks.release_lock(locks.PUSH_LOCK_NAME, 7, token)
            assert await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7) is not None

    @pytest.mark.asyncio
    async def test_release_with_foreign_token_keeps_lock(self):
        """Прогон, переживший TTL, не снимает лок уже нового владельца."""
        fake = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake)):
            await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)
            await locks.release_lock(locks.PUSH_LOCK_NAME, 7, "чужой-токен")
            assert await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7) is None

    @pytest.mark.asyncio
    async def test_no_redis_does_not_block_push(self):
        """Redis лежит → работаем без лока, а не стоим."""
        with patch("backend.cache.get_redis", AsyncMock(return_value=None)):
            token = await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)
            assert token == locks.NO_REDIS_TOKEN
            await locks.release_lock(locks.PUSH_LOCK_NAME, 7, token)  # no-op, не падает
            assert await locks.is_locked(locks.PUSH_LOCK_NAME, 7) is False

    @pytest.mark.asyncio
    async def test_is_locked_reports_busy_lock(self):
        """Подсказка для кнопки: лок занят → роутер отдаст 409, а не «запущено»."""
        fake = _FakeRedis()
        with patch("backend.cache.get_redis", AsyncMock(return_value=fake)):
            assert await locks.is_locked(locks.PUSH_LOCK_NAME, 7) is False
            token = await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)
            assert await locks.is_locked(locks.PUSH_LOCK_NAME, 7) is True
            await locks.release_lock(locks.PUSH_LOCK_NAME, 7, token)
            assert await locks.is_locked(locks.PUSH_LOCK_NAME, 7) is False

    @pytest.mark.asyncio
    async def test_acquire_uses_module_ttl_by_default(self):
        """TTL берётся из константы, а не из локального дефолта вызывающего."""
        seen: dict = {}

        class _TtlSpy(_FakeRedis):
            async def set(self, key, value, nx=False, ex=None):
                seen["ex"] = ex
                return await super().set(key, value, nx=nx, ex=ex)

        with patch("backend.cache.get_redis", AsyncMock(return_value=_TtlSpy())):
            await locks.acquire_lock(locks.PUSH_LOCK_NAME, 7)

        assert seen["ex"] == locks.PUSH_LOCK_TTL_SEC


class TestLockTtlVsJobBudget:
    """TTL лока ↔ бюджет джоба пуша — связанные константы, живущие в разных модулях.

    Импортировать константу джоба в `locks.py` нельзя (services ← scheduler даст
    цикл), поэтому связь держит этот тест. Старое значение (TTL 180 при бюджете
    джоба 300) протухало ПОСРЕДИ прогона: лок снимался сам, следующий тик входил
    в критическую секцию, и два параллельных PUT по одному складу давали гонку
    «кто последний» на живых остатках.
    """

    def test_ttl_strictly_exceeds_job_timeout(self):
        assert locks.PUSH_LOCK_TTL_SEC > fbs_jobs.STOCK_PUSH_TIMEOUT_SEC, (
            "TTL лока трансляции должен переживать самый долгий прогон джоба"
        )

    def test_ttl_has_margin_over_job_timeout(self):
        """Запас, а не «на секунду больше»: после таймаута ещё финализируются журналы."""
        assert locks.PUSH_LOCK_TTL_SEC >= fbs_jobs.STOCK_PUSH_TIMEOUT_SEC + 60

    def test_job_cycle_budget_fits_under_ttl(self):
        """Внутренний бюджет цикла тоже обязан укладываться в TTL."""
        assert fbs_jobs.STOCK_PUSH_CYCLE_BUDGET_SEC < locks.PUSH_LOCK_TTL_SEC


    def test_manual_run_budget_fits_under_ttl(self):
        """Ручная кнопка ограничена тем же бюджетом, что и джоб.

        Раньше бюджета у ручного пути не было вовсе: `asyncio.create_task`
        без `wait_for`. Прогон с force по нескольким складам переживал TTL,
        лок снимался сам, ближайший тик джоба входил в критическую секцию —
        два PUT по одному складу и `qty_sent` от чужого прогона.
        """
        assert locks.PUSH_RUN_BUDGET_SEC < locks.PUSH_LOCK_TTL_SEC

    def test_manual_and_job_share_one_budget(self):
        """Обе точки входа живут по одному контракту — иначе инвариант держится наполовину."""
        assert locks.PUSH_RUN_BUDGET_SEC == fbs_jobs.STOCK_PUSH_TIMEOUT_SEC


class TestManualPushBudget:
    """Ручной прогон реально обрывается по бюджету, а не просто «есть константа»."""

    @pytest.mark.asyncio
    async def test_overrunning_manual_push_is_cut_off(self, monkeypatch):
        import asyncio

        from backend.routers import wb_fbs as fbs_router

        async def _never_ends(*args, **kwargs):
            await asyncio.sleep(30)
            return []

        failed: dict = {}

        async def _fail_running(db, project_id, *, since, reason):
            failed["project_id"] = project_id
            failed["reason"] = reason
            return 1

        class _Ctx:
            async def __aenter__(self):
                return None

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(fbs_router.stock_service, "push_stocks", _never_ends)
        monkeypatch.setattr(fbs_router.stock_service, "fail_running_pushes", _fail_running)
        monkeypatch.setattr(fbs_router, "AsyncSessionLocal", lambda: _Ctx())
        monkeypatch.setattr(fbs_router, "PUSH_RUN_BUDGET_SEC", 0.05)

        await asyncio.wait_for(fbs_router._push_stocks_bg(7, [], True, 1), timeout=5)

        assert failed["project_id"] == 7, "журналы прерванного прогона обязаны закрыться"
        assert "бюджет" in failed["reason"]
