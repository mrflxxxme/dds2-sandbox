# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты фоновых джобов WB FBS (backend/scheduler/jobs/wb_fbs.py).

Покрываем ровно то, что ломается молча и в проде:
  • проект без активного ключа `wb_marketplace` / без активных складов FBS
    вообще не начинается: ни запроса в WB, ни строки в SyncLog;
  • `asyncio.CancelledError` пробрасывается (иначе рестарт воркера маскируется
    под обычную ошибку синка);
  • внутренний бюджет цикла строго меньше внешнего таймаута — иначе мягкая
    деградация недостижима (грабля синков рекламы, learnings.md).

Распределённый лок трансляции живёт не здесь, а в сервисе
(`services/wb_fbs/locks.py`) — его тесты в `tests/test_wb_fbs_locks.py`.

Все тесты — без БД и без сети: сессия подменяется фейком.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from backend.scheduler.jobs import wb_fbs

# ─── Фейки ──────────────────────────────────────────────────────────────────


class _FakeDB:
    """Сессия-заглушка: копит add()/execute(), flush() раздаёт id как БД."""

    def __init__(self, added: list) -> None:
        self._added = added
        self.executed: list = []

    def add(self, obj) -> None:
        self._added.append(obj)

    async def flush(self) -> None:
        for i, obj in enumerate(self._added, start=1):
            if getattr(obj, "id", None) is None:
                obj.id = i

    async def commit(self) -> None:
        return None

    async def execute(self, stmt):
        self.executed.append(stmt)
        return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _session_factory(added: list):
    """Замена AsyncSessionLocal: каждый вызов — новая фейковая сессия."""
    return lambda: _FakeDB(added)


# ─── Отбор проектов ─────────────────────────────────────────────────────────


class TestProjectSelection:
    def test_project_without_key_is_skipped(self):
        """Проект без ключа wb_marketplace не попадает в прогон."""
        rows = _sel(
            key_rows=[(42, 7)],
            project_ids=[7, 8],
            active_wh={7, 8},
        )
        assert rows == [(7, 42)]

    def test_no_keys_at_all(self):
        assert _sel(key_rows=[], project_ids=[7], active_wh={7}) == []

    def test_project_without_active_warehouse_is_skipped(self):
        """Ключ есть, активных складов FBS нет → не жжём лимиты WB."""
        assert _sel(key_rows=[(42, 7)], project_ids=[7], active_wh=set()) == []

    def test_warehouse_requirement_off_for_directory_sync(self):
        """Синк справочника складов работает и до первой активации склада."""
        rows = _sel(key_rows=[(42, 7)], project_ids=[7], active_wh=set(), require=False)
        assert rows == [(7, 42)]

    def test_global_key_covers_all_projects(self):
        """Глобальный ключ (project_id IS NULL) действует на все проекты."""
        rows = _sel(key_rows=[(99, None)], project_ids=[7, 8], active_wh={7, 8})
        assert rows == [(7, 99), (8, 99)]

    def test_project_key_wins_over_global(self):
        """Проектный ключ приоритетнее глобального — SyncLog ссылается на него."""
        rows = _sel(key_rows=[(99, None), (42, 7)], project_ids=[7, 8], active_wh={7, 8})
        assert rows == [(7, 42), (8, 99)]


def _sel(*, key_rows, project_ids, active_wh, require=True):
    return wb_fbs._select_fbs_projects(
        key_rows,
        project_ids,
        active_wh,
        require_active_warehouse=require,
    )


# ─── Раннер ─────────────────────────────────────────────────────────────────


class TestRunner:
    @pytest.mark.asyncio
    async def test_no_projects_no_work(self):
        """Нет подходящих проектов → ни сессии, ни хендлера."""
        handler = AsyncMock(return_value=(0, 0))
        with patch.object(wb_fbs, "_get_fbs_project_ids", AsyncMock(return_value=[])):
            await wb_fbs._run_fbs_job(
                sync_type="new_orders",
                title="test",
                handler=handler,
                timeout=wb_fbs.NEW_ORDERS_TIMEOUT_SEC,
                cycle_budget=wb_fbs.NEW_ORDERS_CYCLE_BUDGET_SEC,
            )
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_disabled_flag_stops_everything(self):
        """WB_FBS_ENABLED=false — проекты даже не выбираются."""
        from backend.config import settings

        picker = AsyncMock(return_value=[(7, 42)])
        handler = AsyncMock(return_value=(0, 0))
        with (
            patch.object(settings, "WB_FBS_ENABLED", False),
            patch.object(wb_fbs, "_get_fbs_project_ids", picker),
        ):
            await wb_fbs._run_fbs_job(
                sync_type="new_orders",
                title="test",
                handler=handler,
                timeout=wb_fbs.NEW_ORDERS_TIMEOUT_SEC,
                cycle_budget=wb_fbs.NEW_ORDERS_CYCLE_BUDGET_SEC,
            )
        picker.assert_not_awaited()
        handler.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_happy_path_writes_synclog(self):
        """Успешный прогон: хендлер вызван, SyncLog создан и закрыт."""
        added: list = []
        handler = AsyncMock(return_value=(3, 3))
        with (
            patch.object(wb_fbs, "AsyncSessionLocal", _session_factory(added)),
            patch.object(wb_fbs, "_get_fbs_project_ids", AsyncMock(return_value=[(7, 42)])),
        ):
            await wb_fbs._run_fbs_job(
                sync_type="new_orders",
                title="test",
                handler=handler,
                timeout=wb_fbs.NEW_ORDERS_TIMEOUT_SEC,
                cycle_budget=wb_fbs.NEW_ORDERS_CYCLE_BUDGET_SEC,
            )

        handler.assert_awaited_once()
        assert len(added) == 1
        log = added[0]
        assert log.service == wb_fbs.SYNC_SERVICE
        assert log.sync_type == "new_orders"
        assert log.integration_id == 42

    @pytest.mark.asyncio
    async def test_handler_failure_does_not_stop_other_projects(self):
        """Падение одного проекта не роняет цикл."""
        added: list = []
        calls: list[int] = []

        async def handler(db, project_id):
            calls.append(project_id)
            if project_id == 7:
                raise RuntimeError("WB 500")
            return 1, 1

        with (
            patch.object(wb_fbs, "AsyncSessionLocal", _session_factory(added)),
            patch.object(wb_fbs, "_get_fbs_project_ids", AsyncMock(return_value=[(7, 42), (8, 43)])),
        ):
            await wb_fbs._run_fbs_job(
                sync_type="supplies",
                title="test",
                handler=handler,
                timeout=wb_fbs.SUPPLIES_TIMEOUT_SEC,
                cycle_budget=wb_fbs.SUPPLIES_CYCLE_BUDGET_SEC,
            )

        assert calls == [7, 8]

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self):
        """Рестарт воркера должен пробрасываться, а не тонуть в except Exception."""
        added: list = []

        async def handler(db, project_id):
            raise asyncio.CancelledError()

        with (
            patch.object(wb_fbs, "AsyncSessionLocal", _session_factory(added)),
            patch.object(wb_fbs, "_get_fbs_project_ids", AsyncMock(return_value=[(7, 42)])),
            pytest.raises(asyncio.CancelledError),
        ):
            await wb_fbs._run_fbs_job(
                sync_type="stock_push",
                title="test",
                handler=handler,
                timeout=wb_fbs.STOCK_PUSH_TIMEOUT_SEC,
                cycle_budget=wb_fbs.STOCK_PUSH_CYCLE_BUDGET_SEC,
            )


# ─── Бюджеты времени ────────────────────────────────────────────────────────


class TestTimeBudgets:
    """Внутренний бюджет цикла ОБЯЗАН быть строго меньше внешнего таймаута.

    Равные значения = внешний `wait_for` убивает корутину раньше, чем цикл
    успеет остановиться сам → вместо частичного результата стабильный ноль
    (месяц нулевых синков рекламы в проде, learnings.md).
    """

    @pytest.mark.parametrize(
        ("timeout", "budget"),
        [
            (wb_fbs.STOCK_PUSH_TIMEOUT_SEC, wb_fbs.STOCK_PUSH_CYCLE_BUDGET_SEC),
            (wb_fbs.NEW_ORDERS_TIMEOUT_SEC, wb_fbs.NEW_ORDERS_CYCLE_BUDGET_SEC),
            (wb_fbs.ORDER_STATUSES_TIMEOUT_SEC, wb_fbs.ORDER_STATUSES_CYCLE_BUDGET_SEC),
            (wb_fbs.SUPPLIES_TIMEOUT_SEC, wb_fbs.SUPPLIES_CYCLE_BUDGET_SEC),
            (wb_fbs.WAREHOUSES_TIMEOUT_SEC, wb_fbs.WAREHOUSES_CYCLE_BUDGET_SEC),
        ],
    )
    def test_cycle_budget_strictly_less_than_timeout(self, timeout, budget):
        assert budget < timeout
        assert budget > wb_fbs.MIN_PROJECT_TIMEOUT_SEC

    def test_request_budget_fits_into_job_timeout(self):
        """Бюджет ОДНОГО запроса к WB меньше внешнего таймаута джоба.

        Тот же инвариант уровнем ниже: без него один `_request` под 429 занимал
        `3×TIMEOUT + 2×RETRY_AFTER_MAX` ≈ 210 c при таймауте новых заданий 120 c —
        корутину убивали раньше, чем клиент успевал отдать «лимит WB исчерпан»,
        и в SyncLog вместо причины ложился безликий Timeout.
        """
        from backend.integrations import wb_fbs_api

        assert wb_fbs_api.REQUEST_TOTAL_BUDGET_SEC < wb_fbs.NEW_ORDERS_TIMEOUT_SEC
        assert wb_fbs_api.REQUEST_TOTAL_BUDGET_SEC < wb_fbs.WAREHOUSES_TIMEOUT_SEC
        # Одна пауза по 429 не должна съедать бюджет целиком — иначе ретрая
        # не остаётся вовсе и смысл счётчика попыток теряется.
        assert wb_fbs_api.RETRY_AFTER_MAX < wb_fbs_api.REQUEST_TOTAL_BUDGET_SEC / 2

    @pytest.mark.asyncio
    async def test_budget_exhausted_stops_cycle(self):
        """Исчерпанный бюджет обрывает цикл, а не гонит остаток проектов."""
        added: list = []
        calls: list[int] = []

        async def handler(db, project_id):
            calls.append(project_id)
            return 1, 1

        # Бюджет 0 c → проверка стоит ПЕРЕД взятием проекта, поэтому цикл
        # обрывается сразу: ни одного похода в WB вместо «зависнем и умрём».
        with (
            patch.object(wb_fbs, "AsyncSessionLocal", _session_factory(added)),
            patch.object(wb_fbs, "_get_fbs_project_ids", AsyncMock(return_value=[(7, 42), (8, 43)])),
        ):
            await wb_fbs._run_fbs_job(
                sync_type="new_orders",
                title="test",
                handler=handler,
                timeout=wb_fbs.NEW_ORDERS_TIMEOUT_SEC,
                cycle_budget=0,
            )

        assert calls == []


# ─── Регистрация в расписании ───────────────────────────────────────────────


async def test_jobs_registered_in_scheduler(monkeypatch):
    """Все пять джобов зарегистрированы с max_instances=1 и coalesce=True."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from backend import scheduler as sched_mod
    from backend.config import settings

    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    # async-тест → есть running loop (start_scheduler планирует cleanup-таск),
    # реальный цикл не запускаем: нужен лишь факт регистрации add_job.
    monkeypatch.setattr(AsyncIOScheduler, "start", lambda self, *a, **k: None)

    sched_mod.start_scheduler()

    inst = sched_mod.get_scheduler_instance()
    assert inst is not None
    expected = {
        "wb_fbs_stock_push",
        "wb_fbs_orders_sync",
        "wb_fbs_order_statuses",
        "wb_fbs_supplies_sync",
        "wb_fbs_warehouses_sync",
    }
    jobs = {j.id: j for j in inst.get_jobs()}
    assert expected <= set(jobs)
    for job_id in expected:
        assert jobs[job_id].max_instances == 1
        assert jobs[job_id].coalesce is True
