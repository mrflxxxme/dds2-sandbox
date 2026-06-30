# ruff: noqa: RUF001, RUF002, RUF003
"""Smoke: start_scheduler() регистрирует ВСЕ джобы без исключений.

Ловит баги РЕГИСТРАЦИИ джобов, невидимые для pytest/mypy/tsc — start_scheduler
исполняется только в воркере при старте. Реальный инцидент: function-local
`from datetime import ...` затенял модульный → UnboundLocalError на next_run_time
ронял весь scheduler (воркер крэш-луп). Здесь scheduler.start() замокан — джобы
лишь регистрируются, реально не запускаются."""


async def test_start_scheduler_registers_all_jobs(monkeypatch):
    # async → есть running loop (start_scheduler планирует cleanup-таск через get_running_loop)
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    from backend import scheduler as sched_mod
    from backend.config import settings

    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    # не запускаем реальный цикл (нужен лишь факт успешной регистрации add_job)
    monkeypatch.setattr(AsyncIOScheduler, "start", lambda self, *a, **k: None)

    sched_mod.start_scheduler()  # упадёт, если любая add_job-регистрация бросит (как UnboundLocalError)

    inst = sched_mod.get_scheduler_instance()
    assert inst is not None
    job_ids = {j.id for j in inst.get_jobs()}
    assert "cbr_bic_sync" in job_ids  # наш джоб зарегистрирован
