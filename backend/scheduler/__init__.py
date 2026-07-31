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
from datetime import datetime, timedelta

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
from backend.scheduler.jobs.ab_tests import ab_tests_tick_all_projects
from backend.scheduler.jobs.fulfillment_sync import sync_all_fulfillment_warehouses
from backend.scheduler.jobs.gazelka_sync import sync_all_projects_gazelka_states
from backend.scheduler.jobs.funnel import (
    ad_anomaly_check,
    ad_nm_backfill_tick,
    ads_schedule_tick,
    fast_backfill_tick,
    snapshot_ad_intraday_all_projects,
    sync_ad_campaigns_all_projects,
    sync_all_projects_ad_payments,
    sync_all_projects_ad_search,
    sync_all_projects_ad_upd,
    sync_all_projects_funnel,
    sync_budgets_all_projects,
    sync_funnel_hourly,
    sync_nomenclature_all_projects,
)
from backend.scheduler.jobs.draft_category_snapshot import snapshot_all_projects_draft_categories
from backend.scheduler.jobs.draft_staleness_watch import check_all_projects_draft_staleness
from backend.scheduler.jobs.health_check import health_monitor
from backend.scheduler.jobs.heartbeat import heartbeat_ping
from backend.scheduler.jobs.prewarm import prewarm_all_reports, prewarm_project  # noqa: F401
from backend.scheduler.jobs.ff_storage_snapshot import snapshot_all_projects_ff_storage
from backend.scheduler.jobs.stock_distribution_snapshot import snapshot_all_projects_stock_distribution
from backend.scheduler.jobs.stock_mismatch_snapshot import snapshot_all_projects_stock_mismatch
from backend.scheduler.jobs.supply_discrepancy import check_all_projects_supply_discrepancies
from backend.scheduler.jobs.wb_finance import (
    sync_all_projects_wb_finance,
    sync_all_projects_wb_finance_daily,
)
from backend.scheduler.jobs.cbr_bic_sync import sync_cbr_bic_directory
from backend.scheduler.jobs.faktura_statement_sync import sync_all_projects_faktura_statements
from backend.scheduler.jobs.vtb_statement_sync import sync_all_projects_vtb_statements
from backend.scheduler.jobs.wb_goods_returns_sync import sync_all_projects_wb_returns
from backend.scheduler.jobs.wb_reviews_sync import sync_all_projects_wb_feedbacks
from backend.scheduler.jobs.wb_orders_sync import sync_all_projects_wb_orders
from backend.scheduler.jobs.wb_prices_sync import sync_all_projects_wb_prices
from backend.scheduler.jobs.measurements_digest import send_measurement_digests
from backend.scheduler.jobs.problem_digest import problem_digest_asap_tick, send_problem_digests
from backend.scheduler.jobs.wb_measurements import sync_all_projects_wb_measurements
from backend.scheduler.jobs.wb_fbs import (
    push_all_projects_fbs_stocks,
    sync_all_projects_fbs_new_orders,
    sync_all_projects_fbs_order_statuses,
    sync_all_projects_fbs_recent_orders,
    sync_all_projects_fbs_order_history,
    sync_all_projects_fbs_supplies,
    sync_all_projects_fbs_warehouses,
)
from backend.scheduler.jobs.wb_stocks import sync_all_projects_wb_remains, sync_all_projects_wb_stocks
from backend.scheduler.jobs.wb_supply_states import sync_all_projects_wb_supply_states

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

    # Ads schedule: every 15 min — пауза/запуск кампаний по расписанию.
    # Тик в :01 — сразу после штатного долива бюджетов ВБ в 00:00 МСК.
    _scheduler.add_job(
        ads_schedule_tick,
        trigger=CronTrigger(minute="1,16,31,46", timezone=MSK),
        id="ads_schedule",
        name="WB Ads Schedule pause/start (every 15min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # Ad intraday snapshots: тик каждые 10 мин (:7,:17,…) — накопительные показы/клики/расход
    # из официальной статистики WB (wb_ad_campaign_daily) для интрадей-графика «место принятия
    # решения». Фактическая частота на проект — настройка ads_snapshot_interval_min (30/60): гейт
    # внутри snapshot_ad_intraday пропускает тик, если с прошлого снимка прошло меньше интервала.
    _scheduler.add_job(
        snapshot_ad_intraday_all_projects,
        trigger=CronTrigger(minute="7,17,27,37,47,57", timezone=MSK),
        id="snapshot_ad_intraday",
        name="WB Ad Intraday Snapshots (every 10min tick)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # АБ-тесты главного фото: тик каждые 5 мин — дельты счётчиков, ротация по границе
    # круга (по времени round_minutes, досрочно по показам), финиш с возвратом исходного.
    _scheduler.add_job(
        ab_tests_tick_all_projects,
        trigger=IntervalTrigger(minutes=5),
        id="ab_tests_tick",
        name="WB AB photo tests tick (every 5min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )

    # РК-статистика по товарам: раз в сутки ночью. Первый проход после релиза
    # видит пустую wb_ad_nm_daily и пересобирает всю доступную историю сам.
    _scheduler.add_job(
        ad_nm_backfill_tick,
        trigger=CronTrigger(hour=4, minute=20, timezone=MSK),
        id="ad_nm_backfill",
        name="WB Ads: РК-статистика по товарам (догон истории)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Утренняя дозагрузка «кнопочных» рекламных источников — чтобы к 9:00 MSK
    # раздел «Сырые данные» был свежим по каждому блоку. Стартуем после ad_nm
    # (04:20), с шагом 10 мин, чтобы не бить в rate limit WB одновременно. Окна
    # маленькие, синк идемпотентен. misfire_grace_time=1h — прощаем рестарт
    # воркера рядом со слотом (доберётся, а не потеряется до завтра).
    for job_fn, minute, job_id, job_name in [
        (sync_all_projects_ad_upd, 25, "ad_upd_morning", "WB Ads: история затрат (утро 04:25)"),
        (sync_all_projects_ad_search, 35, "ad_search_morning", "WB Ads: зона Поиск (утро 04:35)"),
        (sync_all_projects_ad_payments, 45, "ad_payments_morning", "WB Ads: пополнения счёта (утро 04:45)"),
    ]:
        _scheduler.add_job(
            job_fn,
            trigger=CronTrigger(hour=4, minute=minute, timezone=MSK),
            id=job_id,
            name=job_name,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=3600,
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

    # FF storage snapshot: daily 23:40 MSK — посуточное начисление хранения ФФ
    # (штуки→короба→паллеты на момент среза; до дневного среза распределения).
    _scheduler.add_job(
        snapshot_all_projects_ff_storage,
        trigger=CronTrigger(hour=23, minute=40, timezone=MSK),
        id="ff_storage_snapshot",
        name="FF billing storage daily snapshot (23:40 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Stock mismatch snapshot: daily 10:50 MSK — копит динамику расхождения
    # «наш склад vs ФФ-зеркало» вперёд (после утреннего цикла FF-синка).
    _scheduler.add_job(
        snapshot_all_projects_stock_mismatch,
        trigger=CronTrigger(hour=10, minute=50, timezone=MSK),
        id="stock_mismatch_snapshot",
        name="Assembly stock mismatch daily snapshot (10:50 MSK)",
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

    # Gazelka order states sync: every 15 min — «Машина назначена» из кабинета перевозчика,
    # авто-занос WB-пропуска (водитель/ТС), авто-шип по статусу «В маршруте» (тариф→стоимость).
    _scheduler.add_job(
        sync_all_projects_gazelka_states,
        trigger=IntervalTrigger(minutes=15),
        id="gazelka_states_sync",
        name="Gazelka order states sync (every 15min)",
        replace_existing=True,
        max_instances=1,
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

    # WB supply live-states sync: every 30 min — фоновая «Обновить» WB-панели +
    # добор авто-заноса пропуска (F3+: как только дата забронирована и пропуск
    # полный — заносим в кабинет сам). Тянет listSupplies + справочник статусов и
    # обновляет wb_supply_state локальных связей заявка↔WB-поставка (bulk, один
    # клиент на проект; scope — только активные заявки, звонков немного).
    _scheduler.add_job(
        sync_all_projects_wb_supply_states,
        trigger=IntervalTrigger(minutes=30),
        id="wb_supply_states_sync",
        name="WB supply states sync (every 30 min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # Расхождение WB-поставок → TG: every 2 hours (машина назначена/в пути,
    # дата/паллеты/пропуск не сходятся; повтор пока проблема не исправлена).
    _scheduler.add_job(
        check_all_projects_supply_discrepancies,
        trigger=IntervalTrigger(hours=2),
        id="supply_discrepancy_notify",
        name="Supply discrepancy notify (every 2h)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # Сторож черновиков сборки → TG: every hour (черновик без синка/правки >6ч
    # или остатки WB старше 6ч; анти-спам — не чаще 1 алерта в 12ч на объект).
    _scheduler.add_job(
        check_all_projects_draft_staleness,
        trigger=IntervalTrigger(hours=1),
        id="draft_staleness_watch",
        name="Draft staleness watch (hourly)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # Почасовой срез черновиков сборки по категориям (вкладка «Динамика черновика»):
    # сдвиг :40 — после синка WB remains (:20), в стороне от прочих :00-джобов.
    _scheduler.add_job(
        snapshot_all_projects_draft_categories,
        trigger=CronTrigger(minute=40, timezone=MSK),
        id="draft_category_snapshot",
        name="Assembly draft category snapshot (hourly :40 MSK)",
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

    # WB остатки как в кабинете (analytics warehouse_remains): каждый час.
    # Сдвиг :20 — от прочих WB-джобов, идущих в :00 (rate limit).
    _scheduler.add_job(
        sync_all_projects_wb_remains,
        trigger=CronTrigger(minute=20, timezone=MSK),
        id="wb_remains_sync",
        name="WB warehouse remains — cabinet-accurate stock (hourly :20 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )

    # ── WB FBS (продажи со склада продавца) ──
    # Все шесть джобов: max_instances=1 + coalesce=True — пропущенные тики
    # схлопываются в ОДИН, а не выстраиваются в очередь (после паузы воркера
    # иначе в WB улетела бы пачка одинаковых PUT и выжгла бакет лимитов).
    # Проекты без ключа wb_marketplace / без активных складов FBS джобы
    # отсеивают сами. Гонка с ручной кнопкой — Redis-лок внутри самой
    # трансляции (services/wb_fbs/locks.py), а не здесь: кнопка живёт в
    # api-контейнере и джоб её лок не увидел бы.
    _scheduler.add_job(
        push_all_projects_fbs_stocks,
        trigger=IntervalTrigger(minutes=settings.WB_FBS_STOCK_PUSH_INTERVAL_MINUTES),
        id="wb_fbs_stock_push",
        name=f"WB FBS stock push (every {settings.WB_FBS_STOCK_PUSH_INTERVAL_MINUTES}min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    _scheduler.add_job(
        sync_all_projects_fbs_new_orders,
        trigger=IntervalTrigger(minutes=settings.WB_FBS_ORDERS_SYNC_INTERVAL_MINUTES),
        id="wb_fbs_orders_sync",
        name=f"WB FBS new orders sync (every {settings.WB_FBS_ORDERS_SYNC_INTERVAL_MINUTES}min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    # Догон недавнего окна периодным методом: `/orders/new` отдаёт только то,
    # что ещё не положено в поставку, и задание, собранное между двумя
    # опросами, исчезает оттуда навсегда. Каденс реже «новых» — у `GET /orders`
    # своя цена (окно + пагинация), а утечку закрывает и 10-минутный проход.
    _scheduler.add_job(
        sync_all_projects_fbs_recent_orders,
        trigger=IntervalTrigger(minutes=settings.WB_FBS_RECENT_ORDERS_INTERVAL_MINUTES),
        id="wb_fbs_recent_orders",
        name=f"WB FBS recent orders catch-up (every {settings.WB_FBS_RECENT_ORDERS_INTERVAL_MINUTES}min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    _scheduler.add_job(
        sync_all_projects_fbs_order_statuses,
        trigger=IntervalTrigger(minutes=5),
        id="wb_fbs_order_statuses",
        name="WB FBS order statuses + writeoff (every 5min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # История статусов из КАБИНЕТА: точные вехи для аналитики этапов. Пачка 400
    # каждые 15 мин = 38 к запросов в сутки при потребности ~18 к (живые задания
    # по лестнице протухания) — двукратный запас. Лимит хоста 150/мин при этом
    # выбирается лишь на ~80%: прогон держит паузу между запросами.
    _scheduler.add_job(
        sync_all_projects_fbs_order_history,
        trigger=IntervalTrigger(minutes=settings.WB_FBS_ORDER_HISTORY_INTERVAL_MINUTES),
        id="wb_fbs_order_history_sync",
        name="WB FBS order history sync (every 15min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    _scheduler.add_job(
        sync_all_projects_fbs_supplies,
        trigger=IntervalTrigger(minutes=15),
        id="wb_fbs_supplies_sync",
        name="WB FBS supplies sync (every 15min)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )

    # Справочник складов продавца: раз в сутки 04:50 MSK — свободный слот между
    # рекламными дозагрузками (04:20–04:45) и утренними WB-джобами.
    _scheduler.add_job(
        sync_all_projects_fbs_warehouses,
        trigger=CronTrigger(hour=4, minute=50, timezone=MSK),
        id="wb_fbs_warehouses_sync",
        name="WB FBS seller warehouses sync (daily 04:50 MSK)",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # WB замеры складов + удержания за габариты: раз в сутки в 01:00 MSK
    # (сдвиг от stocks 00:00, чтобы не бить в rate limit одновременно).
    _scheduler.add_job(
        sync_all_projects_wb_measurements,
        trigger=CronTrigger(hour=1, minute=0, timezone=MSK),
        id="wb_measurements_sync",
        name="WB measurements + dimension penalties (daily 01:00 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Ежедневная сводка замеров WB в Telegram: 09:00 MSK — во все чаты с включённым
    # тумблером «Замеры». Джоб сам досинкивает короткое окно для свежести «сегодня».
    _scheduler.add_job(
        send_measurement_digests,
        trigger=CronTrigger(hour=9, minute=0, timezone=MSK),
        id="measurements_digest",
        name="WB measurements daily digest (09:00 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Сводка «Проблемные товары» в Telegram: 09:30 MSK — проекты с включённой
    # настройкой ads_problem_digest (выручка + текст + xlsx по брендам; пн — доп. недельный файл).
    _scheduler.add_job(
        send_problem_digests,
        trigger=CronTrigger(hour=9, minute=30, timezone=MSK),
        id="problem_digest",
        name="Ads problem products digest (09:30 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # «Прислать сводку сейчас»: раз в минуту исполняем маркеры от send-now
    # (API-контейнер не достаёт до Telegram — шлём отсюда). Почти всегда no-op.
    _scheduler.add_job(
        problem_digest_asap_tick,
        trigger=IntervalTrigger(seconds=60),
        id="problem_digest_asap",
        name="Ads problem digest asap sender (every 1min)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
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

    # WB customer feedbacks (отзывы): ночной прогон 03:15 MSK. Первый проход для
    # проекта делает full backfill (тянет и архив WB), дальше — только активные.
    _scheduler.add_job(
        sync_all_projects_wb_feedbacks,
        trigger=CronTrigger(hour=3, minute=15, timezone=MSK),
        id="wb_feedbacks_sync",
        name="WB feedbacks sync (03:15 MSK)",
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

    # VTB «Интеграционный Банк-Клиент» (H2H) statement auto-sync: 3×/day — 07/13/19 MSK.
    # Активна только для проектов с ключом IntegrationKey(service="vtb_ibk"); пока ключа
    # нет — no-op. Заливает в тот же ETL, что и ручной импорт ВТБ. Дедуп по txn_id.
    _scheduler.add_job(
        sync_all_projects_vtb_statements,
        trigger=CronTrigger(hour="7,13,19", minute=15, timezone=MSK),
        id="vtb_statement_sync",
        name="VTB ИБК statement sync (07/13/19 MSK)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Справочник БИК ЦБ (ED807): раз в неделю (Пн 04:00 MSK) + один прогон сразу на старте
    # (next_run_time) — наполнить cbr_bic после деплоя, чтобы к/с банка получателя резолвился
    # для любого банка РФ (resolve_bank_db). Без БД-справочника платёжки в не-крупные банки
    # отбивались банком «Неверно указан к/с».
    _scheduler.add_job(
        sync_cbr_bic_directory,
        trigger=CronTrigger(day_of_week="mon", hour=4, minute=0, timezone=MSK),
        id="cbr_bic_sync",
        name="CBR BIC directory sync (weekly Mon 04:00 MSK + on startup)",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
        next_run_time=datetime.now(MSK),
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
