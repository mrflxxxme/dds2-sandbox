# ruff: noqa: RUF001, RUF002, RUF003
"""
Background scheduler for periodic WB funnel sync.

Refactored: scheduler package with job modules.
- helpers.py: project detection, missing dates, windowing
- jobs/funnel.py: daily sync, backfill, ad anomaly
- jobs/wb_finance.py: WB Finance report sync
- jobs/prewarm.py: OPIU/BDR cache prewarm

This module: scheduler lifecycle (start, stop, status, restart).
"""

import contextlib
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.scheduler.jobs.ai_digest import send_daily_digests
from backend.scheduler.jobs.fbo_supplies import (
    enrich_all_projects_fbo_supplies,
    refresh_all_projects_assemblies_from_fbo,
    sync_all_projects_fbo_supplies,
)
from backend.scheduler.jobs.fulfillment_sync import sync_all_fulfillment_warehouses
from backend.scheduler.jobs.funnel import (
    ad_anomaly_check,
    fast_backfill_tick,
    sync_ad_campaigns_all_projects,
    sync_all_projects_funnel,
    sync_budgets_all_projects,
    sync_funnel_hourly,
    sync_nomenclature_all_projects,
)
from backend.scheduler.jobs.health_check import health_monitor
from backend.scheduler.jobs.heartbeat import heartbeat_ping
from backend.scheduler.jobs.prewarm import prewarm_all_reports, prewarm_project  # noqa: F401
from backend.scheduler.jobs.stock_distribution_snapshot import snapshot_all_projects_stock_distribution
from backend.scheduler.jobs.wb_finance import (
    sync_all_projects_wb_finance,
    sync_all_projects_wb_finance_daily,
)
from backend.scheduler.jobs.faktura_statement_sync import sync_all_projects_faktura_statements
from backend.scheduler.jobs.wb_goods_returns_sync import sync_all_projects_wb_returns
from backend.scheduler.jobs.wb_orders_sync import sync_all_projects_wb_orders
from backend.scheduler.jobs.wb_prices_sync import sync_all_projects_wb_prices
from backend.scheduler.jobs.wb_stocks import sync_all_projects_wb_stocks

logger = logging.getLogger("dds.scheduler")

MSK = pytz.timezone("Europe/Moscow")

_scheduler: AsyncIOScheduler | None = None


def get_scheduler_instance() -> AsyncIOScheduler | None:
    """Get the current scheduler instance (used by jobs to self-remove)."""
    return _scheduler


def start_scheduler():
    """Start the background scheduler with cron jobs + fast backfill.

    Runs ONLY in the worker container (DDS_ROLE=worker).
    Single instance is guaranteed by Docker (1 worker container).
    """
    from backend.config import settings

    if not settings.SCHEDULER_ENABLED:
        # Локалка: основной scheduler выключен (WB/Telegram-джобам нужны живые
        # ключи), но FF-синк можно поднять отдельно — опт-ин FULFILLMENT_SYNC_ENABLED.
        if settings.FULFILLMENT_SYNC_ENABLED:
            _start_fulfillment_only_scheduler()
        else:
            logger.info("⏭️ Scheduler disabled (SCHEDULER_ENABLED=false)")
        return

    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=MSK)

    # Daily sync: 00:01, 03:00, 05:00 MSK — today + yesterday
    for hour, minute in [(0, 1), (3, 0), (5, 0)]:
        _scheduler.add_job(
            sync_all_projects_funnel,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=MSK),
            id=f"funnel_sync_{hour:02d}{minute:02d}",
            name=f"WB daily sync at {hour:02d}:{minute:02d} MSK",
            replace_existing=True,
            misfire_grace_time=600,
        )

    # Fast backfill: every 3 min — missing days only, auto-stops
    _scheduler.add_job(
        fast_backfill_tick,
        trigger=IntervalTrigger(seconds=180),
        id="fast_backfill",
        name="Fast backfill (every 3min, auto-stop)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Ad anomaly check: every 3 min — re-sync incomplete ad days, auto-stops
    _scheduler.add_job(
        ad_anomaly_check,
        trigger=IntervalTrigger(seconds=180),
        id="ad_anomaly_check",
        name="Ad anomaly check (every 3min, auto-stop)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Ad campaigns sync: every 30 min at :02 and :32 — names, types, statuses, budgets
    _scheduler.add_job(
        sync_ad_campaigns_all_projects,
        trigger=CronTrigger(minute="2,32", timezone=MSK),
        id="sync_ad_campaigns",
        name="WB Ad Campaigns Sync (every 30min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Budget-only sync: every 10 min — lightweight, active campaigns only
    _scheduler.add_job(
        sync_budgets_all_projects,
        trigger=IntervalTrigger(minutes=10),
        id="sync_budgets",
        name="WB Ad Budgets Sync (every 10min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    # Funnel hourly sync: every hour at :45 — last 2 days
    _scheduler.add_job(
        sync_funnel_hourly,
        trigger=CronTrigger(minute=45, timezone=MSK),
        id="sync_funnel_hourly",
        name="WB Funnel Sync (hourly, last 2 days)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Stock distribution snapshot: daily 23:50 MSK — копит динамику «где товар» вперёд
    _scheduler.add_job(
        snapshot_all_projects_stock_distribution,
        trigger=CronTrigger(hour=23, minute=50, timezone=MSK),
        id="stock_distribution_snapshot",
        name="Assembly stock distribution daily snapshot (23:50 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Nomenclature sync: 2x/day at 08:30 and 20:30 MSK
    for nom_hour in [8, 20]:
        _scheduler.add_job(
            sync_nomenclature_all_projects,
            trigger=CronTrigger(hour=nom_hour, minute=30, timezone=MSK),
            id=f"nomenclature_sync_{nom_hour:02d}",
            name=f"WB Nomenclature Sync ({nom_hour:02d}:30 MSK)",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=600,
        )

    # WB finance report sync — WEEKLY: Mon 03/06/09 (full week reports)
    for job_hour in [3, 6, 9]:
        _scheduler.add_job(
            sync_all_projects_wb_finance,
            trigger=CronTrigger(day_of_week="mon", hour=job_hour, minute=0, timezone=MSK),
            id=f"wb_finance_sync_mon_{job_hour:02d}",
            name=f"WB finance weekly sync (MON {job_hour:02d}:00)",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # WB finance report sync — DAILY: Tue-Sun at 05:00, 08:00, 14:00 MSK
    # Fetches daily reports (period=daily) for current incomplete week.
    # 05:00 MSK — ранний прогон, чтобы данные за вчера появились в БДР к утру
    # (WB обычно публикует финотчёт за прошлый день к 03-05 утра MSK).
    # 08:00 страхует на случай поздней публикации, 14:00 — добор.
    for job_hour in [5, 8, 14]:
        _scheduler.add_job(
            sync_all_projects_wb_finance_daily,
            trigger=CronTrigger(day_of_week="tue-sun", hour=job_hour, minute=0, timezone=MSK),
            id=f"wb_finance_daily_{job_hour:02d}",
            name=f"WB finance daily sync ({job_hour:02d}:00)",
            replace_existing=True,
            misfire_grace_time=3600,
        )

    # Report cache prewarm: every 1h (re-enabled after BDR SQL migration)
    _scheduler.add_job(
        prewarm_all_reports,
        trigger=IntervalTrigger(hours=1),
        id="prewarm_reports",
        name="Report cache prewarm (every 1h)",
        replace_existing=True,
        misfire_grace_time=300,
    )

    # Health monitor: every 6 hours
    _scheduler.add_job(
        health_monitor,
        trigger=IntervalTrigger(hours=6),
        id="health_monitor",
        name="Health monitor (disk, backups, stuck syncs)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Heartbeat: every 2 min — prevents false BackendNoUserTraffic alerts at night
    _scheduler.add_job(
        heartbeat_ping,
        trigger=IntervalTrigger(minutes=2),
        id="heartbeat_ping",
        name="Backend heartbeat ping (every 2min)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # FBO supplies sync + statuses: every 30 min
    _scheduler.add_job(
        sync_all_projects_fbo_supplies,
        trigger=IntervalTrigger(minutes=30),
        id="fbo_supplies_sync",
        name="FBO supplies sync (every 30min)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # FBO supplies enrich: every 3 hours (warehouse_name via detail API)
    _scheduler.add_job(
        enrich_all_projects_fbo_supplies,
        trigger=IntervalTrigger(hours=3),
        id="fbo_supplies_enrich",
        name="FBO supplies enrich (every 3h)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Assembly FBO auto-refresh: every 1 hour — фоновая «Из FBO» для активных
    # сборок (расхождение наполнения обновляется без ручного клика в заявку).
    _scheduler.add_job(
        refresh_all_projects_assemblies_from_fbo,
        trigger=IntervalTrigger(hours=1),
        id="assembly_fbo_autorefresh",
        name="Assembly FBO auto-refresh (every 1h)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # WB warehouse stocks snapshot: daily at 00:00 MSK
    _scheduler.add_job(
        sync_all_projects_wb_stocks,
        trigger=CronTrigger(hour=0, minute=0, timezone=MSK),
        id="wb_stocks_sync",
        name="WB warehouse stocks snapshot (daily 00:00 MSK)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # WB prices snapshot (цены витрины): 2×/день — 09:30 и 21:30 MSK.
    # Питает страницу «Ценообразование» (наценка по артикулам). Цены меняются
    # редко → 2 раза в день достаточно; rate limit Discounts-Prices мягкий.
    _scheduler.add_job(
        sync_all_projects_wb_prices,
        trigger=CronTrigger(hour="9,21", minute=30, timezone=MSK),
        id="wb_prices_sync",
        name="WB prices snapshot (09:30 + 21:30 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Fulfillment (skladbot, wmscelicom, migfull) stocks + requests mirror —
    # тированное расписание (FAST/SLOW по складам + DEFAULT для остальных,
    # см. config + _add_fulfillment_jobs). Пустые FAST/SLOW → один DEFAULT для
    # всех складов = прежнее «раз в час».
    _add_fulfillment_jobs(_scheduler, include_default=True)

    # WB goods returns (возвраты на ПВЗ): дважды в день — 08:00 (утренний срез)
    # и 20:00 MSK (вечерний добор). WB публикует отчёт каждые 30 мин, но для
    # нашего процесса достаточно 2 раза в день. Мониторинг: Prometheus gauge
    # dds_wb_goods_returns_last_success_timestamp + alert WbReturnsSyncStale.
    _scheduler.add_job(
        sync_all_projects_wb_returns,
        trigger=CronTrigger(hour="8,20", minute=0, timezone=MSK),
        id="wb_goods_returns_sync",
        name="WB goods returns sync (08:00 + 20:00 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Faktura.ru (ВБ Банк) statement auto-sync: 4×/day — 06/12/18/23 MSK.
    # Тянет выписку по обоим расчётным счетам и заливает в тот же ETL, что и
    # ручной импорт (/p/<slug>/import). Дедуп по txn_id → перекрытие окна безопасно.
    _scheduler.add_job(
        sync_all_projects_faktura_statements,
        trigger=CronTrigger(hour="6,12,18,23", minute=0, timezone=MSK),
        id="faktura_statement_sync",
        name="Faktura statement sync (06/12/18/23 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # AI morning digest: daily at 7:00 MSK
    _scheduler.add_job(
        send_daily_digests,
        trigger=CronTrigger(hour=7, minute=0, timezone=MSK),
        id="ai_daily_digest",
        name="AI daily digest (07:00 MSK)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    # WB supplier orders sync (для отчёта «Индекс локализации»):
    # 03:30 / 09:30 / 15:30 MSK — 3 раза в день. Тянем последние 30 дней,
    # UPSERT по (project_id, srid). После sync кэш reports:localization*
    # инвалидируется внутри sync_wb_orders.
    _scheduler.add_job(
        sync_all_projects_wb_orders,
        trigger=CronTrigger(hour="3,9,15", minute=30, timezone=MSK),
        id="wb_orders_sync",
        name="WB supplier orders sync (03:30 + 09:30 + 15:30 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Cleanup stale sync_log records left by previous crashes
    import asyncio

    async def _cleanup_stale_syncs():
        try:
            from sqlalchemy import text

            from backend.database import AsyncSessionLocal

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    text("""
                        UPDATE sync_log
                        SET status = 'ERROR',
                            error_msg = COALESCE(error_msg, '') || ' [auto-fix: stale after worker restart]',
                            finished_at = COALESCE(finished_at, NOW())
                        WHERE status IN ('STALE', 'RUNNING')
                          AND started_at < NOW() - interval '30 minutes'
                    """)
                )
                await db.commit()
                if result.rowcount:
                    logger.info("🧹 Cleaned up %d stale/stuck sync_log records", result.rowcount)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Stale sync cleanup failed: %s", e)

    asyncio.get_running_loop().create_task(_cleanup_stale_syncs())

    # Startup catch-up: run finance daily sync 30s after start
    # to recover from missed jobs (e.g. worker restart during scheduled time)
    from datetime import datetime, timedelta

    from apscheduler.triggers.date import DateTrigger

    _scheduler.add_job(
        sync_all_projects_wb_finance_daily,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=30)),
        id="wb_finance_daily_catchup",
        name="WB finance daily catch-up (startup)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Startup catch-up: run weekly finance sync 60s after start.
    # Recovers from missed Mon weekly job (e.g. backend hang incident 2026-04-14).
    # Job itself skips projects whose max_weekly_date already covers prev_sunday,
    # so it's a safe no-op when nothing is missing.
    _scheduler.add_job(
        sync_all_projects_wb_finance,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=60)),
        id="wb_finance_weekly_catchup",
        name="WB finance weekly catch-up (startup)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Startup catch-up: run WB goods-returns sync 90s after start. Cron trigger
    # fires только в 08:00 и 20:00 MSK, поэтому без catch-up после рестарта
    # данные устарели бы до следующего слота (до 12 часов).
    _scheduler.add_job(
        sync_all_projects_wb_returns,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=90)),
        id="wb_goods_returns_catchup",
        name="WB goods returns catch-up (startup)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # Startup catch-up: Faktura statement sync 120s after start (cron fires only
    # 4×/day, so without this a worker restart leaves the statement stale до
    # следующего слота — до 6 часов).
    _scheduler.add_job(
        sync_all_projects_faktura_statements,
        trigger=DateTrigger(run_date=datetime.now(MSK) + timedelta(seconds=120)),
        id="faktura_statement_catchup",
        name="Faktura statement catch-up (startup)",
        replace_existing=True,
        misfire_grace_time=60,
    )

    _scheduler.start()
    logger.info(
        "✅ Scheduler started — daily sync 3x/day + backfill + ad check + wb_finance weekly Mon + wb_finance daily Tue-Sun + prewarm 1h + AI digest 07:00 + finance catch-up"
    )


def _parse_warehouse_ids(raw: str) -> list[int]:
    """CSV id складов → list[int] (дедуп, порядок сохранён); мусор/пусто игнорируется."""
    return list(dict.fromkeys(int(x) for x in raw.split(",") if x.strip().isdigit()))


def _add_fulfillment_jobs(scheduler: AsyncIOScheduler, *, include_default: bool) -> None:
    """Зарегистрировать тированные FF-синк-джобы (общая логика прод/локалка).

    FAST/SLOW — контуры по спискам складов из настроек (из SLOW вычитается
    пересечение с FAST, чтобы склад не синкался дважды). include_default=True
    добавляет DEFAULT-контур для ВСЕХ ОСТАЛЬНЫХ складов (exclude FAST+SLOW) —
    прод; пустые FAST/SLOW → DEFAULT синкает все (= прежнее «раз в час»).
    include_default=False (локалка, FF-only) DEFAULT пропускает: БД там копия
    прода с ~2k замаскированных ключей, синк всех = шторм 401/таймаутов.
    """
    from backend.config import settings

    fast = _parse_warehouse_ids(settings.FULFILLMENT_SYNC_FAST_WAREHOUSE_IDS)
    slow = [w for w in _parse_warehouse_ids(settings.FULFILLMENT_SYNC_SLOW_WAREHOUSE_IDS) if w not in fast]
    prioritized = fast + slow

    if fast:
        m = settings.FULFILLMENT_SYNC_FAST_INTERVAL_MINUTES
        scheduler.add_job(
            sync_all_fulfillment_warehouses,
            trigger=IntervalTrigger(minutes=m),
            id="fulfillment_sync_fast",
            name=f"Fulfillment sync FAST {fast} (every {m}min)",
            kwargs={"warehouse_ids": fast},
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )
    if slow:
        m = settings.FULFILLMENT_SYNC_SLOW_INTERVAL_MINUTES
        scheduler.add_job(
            sync_all_fulfillment_warehouses,
            trigger=IntervalTrigger(minutes=m),
            id="fulfillment_sync_slow",
            name=f"Fulfillment sync SLOW {slow} (every {m}min)",
            kwargs={"warehouse_ids": slow},
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )
    if include_default:
        m = settings.FULFILLMENT_SYNC_INTERVAL_MINUTES
        scheduler.add_job(
            sync_all_fulfillment_warehouses,
            trigger=IntervalTrigger(minutes=m),
            id="fulfillment_sync",
            name=f"Fulfillment sync DEFAULT (every {m}min)",
            kwargs={"exclude_warehouse_ids": prioritized or None},
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )
    logger.info(
        "Fulfillment jobs: fast=%s slow=%s default=%s",
        fast or "—",
        slow or "—",
        ("all-except-prioritized" if prioritized else "all") if include_default else "off",
    )


def _start_fulfillment_only_scheduler():
    """FF-only scheduler (локалка): только FF-джобы, без тяжёлых WB/Telegram.

    Поднимается, когда основной scheduler выключен (SCHEDULER_ENABLED=false) и
    задан FULFILLMENT_SYNC_ENABLED. DEFAULT-контур не добавляется
    (include_default=False) — синкаются только перечисленные FAST/SLOW склады.
    Прод сюда не попадает (там SCHEDULER_ENABLED=true → общий scheduler крутит
    тот же тиринг через _add_fulfillment_jobs(include_default=True)).
    """
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=MSK)
    _add_fulfillment_jobs(_scheduler, include_default=False)
    _scheduler.start()
    logger.info("✅ Fulfillment-only scheduler started (локалка, FF-only)")


def stop_scheduler():
    """Stop the background scheduler gracefully.

    wait=True gives running jobs time to finish (up to stop_grace_period).
    This prevents SIGKILL from docker when jobs are mid-flight.
    """
    global _scheduler
    if _scheduler:
        try:
            _scheduler.shutdown(wait=True)
            logger.info("Scheduler stopped gracefully (all jobs finished)")
        except Exception as e:
            logger.warning("Scheduler shutdown error (forcing): %s", e)
            with contextlib.suppress(Exception):
                _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler_info() -> dict:
    """Get scheduler status and next run times."""
    if not _scheduler:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            }
        )

    return {"running": _scheduler.running, "jobs": jobs}


def restart_backfill_jobs():
    """Restart backfill + ad anomaly jobs (call when new WB API key is added)."""
    global _scheduler
    try:
        if not _scheduler or not _scheduler.running:
            logger.warning("Scheduler not running, cannot restart jobs")
            return

        if not _scheduler.get_job("fast_backfill"):
            _scheduler.add_job(
                fast_backfill_tick,
                IntervalTrigger(minutes=3),
                id="fast_backfill",
                max_instances=1,
                replace_existing=True,
            )
            logger.info("🔄 Restarted fast_backfill job (new API key detected)")

        if not _scheduler.get_job("ad_anomaly_check"):
            _scheduler.add_job(
                ad_anomaly_check,
                IntervalTrigger(minutes=3),
                id="ad_anomaly_check",
                max_instances=1,
                replace_existing=True,
            )
            logger.info("🔄 Restarted ad_anomaly_check job (new API key detected)")
    except Exception as e:
        logger.error(f"Failed to restart backfill jobs: {e}")
