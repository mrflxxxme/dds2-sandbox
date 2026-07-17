"""Funnel sync jobs — daily sync, fast backfill, ad anomaly check."""

import asyncio
import logging
import traceback
from datetime import date, timedelta

from sqlalchemy import select, update

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey, SyncLog
from backend.scheduler.helpers import (
    BACKFILL_DAYS,
    get_days_with_incomplete_ads,
    get_missing_dates,
    get_sync_project_ids,
    split_into_windows,
)
from backend.utils.telegram import send_alert
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

# Потолок одного проекта в синке рекламных кампаний. Джоба тикает раз в 30 мин, так что
# 20 мин — безопасный запас без наложения прогонов (max_instances=1). Обязан быть СТРОГО
# больше BUDGET_FETCH_TIME_BUDGET: внутренний цикл сам мягко остановится и вернёт частичные
# бюджеты, а этот таймаут — лишь аварийный предохранитель. Когда оба были по 600с, предохранитель
# срабатывал раньше мягкой остановки и убивал прогон целиком → 0% успеха на проде с 2026-07-11.
AD_CAMPAIGNS_SYNC_TIMEOUT = 1200  # 20 мин

# То же для лёгкого синка «только бюджеты»: тикает раз в 10 мин, потому потолок 8 мин.
# Прежние 300с на проекте 4 уже почти выбирались (реальные прогоны ~242с) — при росте
# кампаний джоба повторила бы судьбу sync_ad_campaigns.
AD_BUDGETS_SYNC_TIMEOUT = 480  # 8 мин

_backfill_locks: dict[int, asyncio.Lock] = {}
_ad_check_lock = asyncio.Lock()


async def _run_and_log(project_id: int, d_from: str, d_to: str, sync_type: str):
    """Run funnel sync and log result. GUARANTEED: never leaves sync_log as RUNNING."""
    from backend.services.funnel.sync import run_funnel_sync

    include_completed = sync_type == "ad_resync"
    log_id = None

    async with AsyncSessionLocal() as db:
        int_key = await db.execute(
            select(IntegrationKey.id)
            .where(
                IntegrationKey.project_id == project_id,
                IntegrationKey.service.in_(["wb", "wb_analytics", "wb_advert"]),
                IntegrationKey.is_active.is_(True),
                IntegrationKey.is_deleted.is_(False),
            )
            .limit(1)
        )
        key_id = int_key.scalar() or None
        sync_log = SyncLog(
            integration_id=key_id,
            service="wb_funnel",
            sync_type=sync_type,
            status="RUNNING",
        )
        db.add(sync_log)
        await db.commit()
        await db.refresh(sync_log)
        log_id = sync_log.id

    result = {"rows": 0, "errors": []}
    status = "ERROR"

    try:
        async with AsyncSessionLocal() as db:
            result = await asyncio.wait_for(
                run_funnel_sync(
                    db,
                    project_id,
                    d_from,
                    d_to,
                    include_completed_campaigns=include_completed,
                ),
                timeout=600,
            )
            status = "OK" if not result.get("errors") else "PARTIAL"
    except TimeoutError:
        result = {"rows": 0, "errors": [f"Timeout: 10min exceeded ({d_from}→{d_to})"]}
        status = "TIMEOUT"
        logger.error(f"Scheduler: project {project_id} [{sync_type}] TIMEOUT {d_from}→{d_to}")
    except asyncio.CancelledError:
        result = {"rows": 0, "errors": ["Task cancelled (worker shutdown or restart)"]}
        status = "ERROR"
        logger.warning(f"Scheduler: project {project_id} [{sync_type}] CANCELLED {d_from}→{d_to}")
        raise
    except Exception as e:
        result = {"rows": 0, "errors": [str(e)[:500]]}
        status = "ERROR"
        logger.error(f"Scheduler: project {project_id} [{sync_type}] ERROR: {e}")
    finally:
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    update(SyncLog)
                    .where(SyncLog.id == log_id)
                    .values(
                        status=status,
                        rows_inserted=result.get("rows", 0),
                        finished_at=utcnow(),
                        error_msg="; ".join(result.get("errors", [])[:3]) or None,
                    )
                )
                await db.commit()
        except Exception as log_err:
            logger.error(f"Failed to update sync_log {log_id}: {log_err}")

    logger.info(
        f"Scheduler: project {project_id} [{sync_type}] {d_from}→{d_to} — "
        f"{result.get('rows', 0)} rows, status: {status}"
    )

    if status in ("ERROR", "TIMEOUT"):
        errors_text = "; ".join(result.get("errors", [])[:3])
        await send_alert(
            f"WB Sync *{status}*\n"
            f"Project: {project_id}\n"
            f"Type: {sync_type}\n"
            f"Period: {d_from} → {d_to}\n"
            f"Error: {errors_text[:300]}",
        )

    return result


# ─── Daily sync ──────────────────────────────────────────────────────────────


async def sync_all_projects_funnel():
    """Daily sync: today + yesterday for all projects."""
    logger.info("⏰ Scheduler: starting daily funnel sync for all projects")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys found, skipping")
        return
    for pid in project_ids:
        try:
            d_from = (date.today() - timedelta(days=1)).isoformat()
            d_to = date.today().isoformat()
            logger.info(f"Scheduler: project {pid} — sync today+yesterday {d_from} → {d_to}")
            await _run_and_log(pid, d_from, d_to, "funnel_auto")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Scheduler: project {pid} sync failed: {e}")


# ─── Fast backfill ───────────────────────────────────────────────────────────


async def fast_backfill_tick():
    """Fill missing days ONLY. Runs every 3 min. Auto-stops when complete."""
    try:
        project_ids = await get_sync_project_ids()
        logger.info(f"⏩ Fast backfill tick: {len(project_ids)} projects")
        if not project_ids:
            _stop_fast_backfill()
            return

        all_filled = True
        for pid in project_ids:
            if pid not in _backfill_locks:
                _backfill_locks[pid] = asyncio.Lock()
            if _backfill_locks[pid].locked():
                logger.info(f"⏩ Backfill: project {pid} — still running, skipping")
                all_filled = False
                continue

            async with _backfill_locks[pid]:
                missing = await get_missing_dates(pid)
                if missing:
                    all_filled = False
                    d_from, d_to = missing[0], missing[-1]
                    logger.info(
                        f"⏩ Backfill: project {pid} — syncing range {d_from}→{d_to} ({len(missing)} days remaining)"
                    )
                    res = await _run_and_log(pid, d_from, d_to, "backfill")
                    if res:
                        logger.info(f"⏩ Backfill: project {pid} — {d_from}→{d_to} done, +{res.get('rows', 0)} rows")
                    await asyncio.sleep(1)
                else:
                    logger.info(f"⏩ Backfill: project {pid} — all {BACKFILL_DAYS} days covered ✅")

        if all_filled:
            logger.info("🎉 Fast backfill complete — all projects fully covered! Stopping.")
            _stop_fast_backfill()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(f"Fast backfill error: {e}\n{traceback.format_exc()}")


def _stop_fast_backfill():
    from backend.scheduler import get_scheduler_instance

    sched = get_scheduler_instance()
    if sched:
        try:
            sched.remove_job("fast_backfill")
            logger.info("Fast backfill job removed from scheduler")
        except Exception as e:
            logger.debug(f"fast_backfill removal: {e}")


# ─── Ad anomaly check ────────────────────────────────────────────────────────


async def ad_anomaly_check():
    """Check for days with incomplete ad data and re-sync them."""
    if _ad_check_lock.locked():
        return
    async with _ad_check_lock:
        try:
            project_ids = await get_sync_project_ids()
            logger.info(f"📊 Ad anomaly check: {len(project_ids)} projects")
            all_ads_ok = True
            for pid in project_ids:
                incomplete_days = await get_days_with_incomplete_ads(pid)
                if incomplete_days:
                    all_ads_ok = False
                    windows = split_into_windows(incomplete_days, max_window=30)
                    logger.info(
                        f"📊 Ad anomaly: project {pid} — {len(incomplete_days)} incomplete days, {len(windows)} windows"
                    )
                    for w_from, w_to in windows:
                        logger.info(f"📊 Ad anomaly: project {pid} — re-syncing window {w_from}→{w_to}")
                        res = await _run_and_log(pid, w_from, w_to, "ad_resync")
                        if res:
                            logger.info(
                                f"📊 Ad anomaly: project {pid} — {w_from}→{w_to} re-synced, +{res.get('rows', 0)} rows"
                            )
                        await asyncio.sleep(5)
                else:
                    logger.info(f"📊 Ad anomaly: project {pid} — all ads complete ✅")
            if all_ads_ok:
                logger.info("🎉 Ad anomaly check complete — all projects have full ad data! Stopping.")
                _stop_ad_anomaly_check()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Ad anomaly check error: {e}\n{traceback.format_exc()}")


def _stop_ad_anomaly_check():
    from backend.scheduler import get_scheduler_instance

    sched = get_scheduler_instance()
    if sched:
        try:
            sched.remove_job("ad_anomaly_check")
            logger.info("Ad anomaly check job removed from scheduler")
        except Exception as e:
            logger.debug(f"ad_anomaly_check removal: {e}")


# ─── Ad campaigns sync (hourly) ─────────────────────────────────────────────


async def sync_ad_campaigns_all_projects():
    """Sync ad campaigns (names, types, budgets) for all projects with WB keys."""
    from backend.services.funnel.ad_campaigns_service import sync_ad_campaigns

    logger.info("⏰ Scheduler: starting hourly ad campaigns sync")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for ad campaigns sync")
        return

    for pid in project_ids:
        log_id = None
        status = "ERROR"
        synced = 0
        error_msg = None

        try:
            # Create sync_log entry
            async with AsyncSessionLocal() as db:
                int_key = await db.execute(
                    select(IntegrationKey.id)
                    .where(
                        IntegrationKey.project_id == pid,
                        IntegrationKey.service.in_(["wb", "wb_advert", "wb_analytics"]),
                        IntegrationKey.is_active.is_(True),
                        IntegrationKey.is_deleted.is_(False),
                    )
                    .limit(1)
                )
                key_id = int_key.scalar() or None
                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb_funnel",
                    sync_type="ad_campaigns",
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.commit()
                await db.refresh(sync_log)
                log_id = sync_log.id

            async with AsyncSessionLocal() as db:
                # fetch_budgets=False: бюджеты активных кампаний каждые 10 мин обновляет
                # sync_budgets_all_projects. Тянуть их ещё и здесь = две джобы дерутся за
                # rate-лимитированный /adv/v1/budget (прод 2026-07-17: прогон бюджетов при
                # наложении 228с → 426с и срез по лимиту). Новинки синк всё же спросит сам.
                result = await asyncio.wait_for(
                    sync_ad_campaigns(db, pid, fetch_budgets=False),
                    timeout=AD_CAMPAIGNS_SYNC_TIMEOUT,
                )
                synced = result.get("synced", 0)
                status = "OK"
                logger.info(f"Ad campaigns sync: project {pid} — {synced} campaigns")
        except TimeoutError:
            status = "TIMEOUT"
            error_msg = f"Timeout {AD_CAMPAIGNS_SYNC_TIMEOUT // 60}min exceeded"
            logger.error(f"Ad campaigns sync TIMEOUT for project {pid}")
        except asyncio.CancelledError:
            status = "ERROR"
            error_msg = "Task cancelled (worker shutdown or restart)"
            logger.warning(f"Ad campaigns sync CANCELLED for project {pid}")
            raise  # propagate cancellation — never swallow it to keep looping
        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"Ad campaigns sync failed for project {pid}: {e}")
        finally:
            if log_id:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=status,
                                rows_inserted=synced,
                                finished_at=utcnow(),
                                error_msg=error_msg,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error(f"Failed to update sync_log {log_id}: {log_err}")


# ─── Утренняя дозагрузка «кнопочных» рекламных источников ────────────────────
# Зона Поиск (ad_search), История затрат (ad_upd) и Пополнения счёта
# (ad_payments) не входят в штатное расписание — в разделе «Сырые данные» они
# помечены «Только по кнопке». Чтобы к 9:00 МСК всё было свежим, гоняем их рано
# утром (после ad_nm 04:20). Окна небольшие — синк идемпотентен (UPSERT /
# DO NOTHING), это самолечащийся top-up, а не бэкфилл истории.


async def _run_ad_topup_job(sync_type: str, runner):
    """Общий каркас утренней дозагрузки рекламного источника по всем проектам.

    runner(db, pid) -> dict со `status` ('ok'/'error') и `rows`. На каждый проект
    пишет sync_log (honest-статус: status='error' из sync-сервиса → sync_log
    ERROR, не «загружено»), изолирует ошибки проекта, пробрасывает CancelledError.
    """
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for %s", sync_type)
        return

    for pid in project_ids:
        log_id = None
        status = "ERROR"
        rows = 0
        error_msg = None
        try:
            async with AsyncSessionLocal() as db:
                key_id = (
                    await db.execute(
                        select(IntegrationKey.id)
                        .where(
                            IntegrationKey.project_id == pid,
                            IntegrationKey.service.in_(["wb_advert", "wb", "wb_analytics"]),
                            IntegrationKey.is_active.is_(True),
                            IntegrationKey.is_deleted.is_(False),
                        )
                        .limit(1)
                    )
                ).scalar() or None
                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb_funnel",
                    sync_type=sync_type,
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.commit()
                await db.refresh(sync_log)
                log_id = sync_log.id

            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(runner(db, pid), timeout=600)
            rows = int((result or {}).get("rows") or 0)
            if (result or {}).get("status") == "error":
                error_msg = str(result.get("error") or "источник вернул ошибку")[:500]
                logger.warning("%s: project %s — error: %s", sync_type, pid, error_msg)
            else:
                status = "OK"
                logger.info("%s: project %s — %d rows", sync_type, pid, rows)
        except TimeoutError:
            status = "TIMEOUT"
            error_msg = "Timeout 10min exceeded"
            logger.error("%s TIMEOUT for project %s", sync_type, pid)
        except asyncio.CancelledError:
            status = "ERROR"
            error_msg = "Task cancelled (worker shutdown or restart)"
            logger.warning("%s CANCELLED for project %s", sync_type, pid)
            raise  # propagate cancellation — never swallow it to keep looping
        except Exception as e:
            error_msg = str(e)[:500]
            logger.error("%s failed for project %s: %s", sync_type, pid, e)
        finally:
            if log_id:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=status,
                                rows_inserted=rows,
                                finished_at=utcnow(),
                                error_msg=error_msg,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error("Failed to update sync_log %s: %s", log_id, log_err)


async def sync_all_projects_ad_upd():
    """Утро: история затрат за рекламу (adv/v1/upd) за последние дни."""
    from backend.services.funnel.ad_finance_sync import sync_ad_upd

    d_to = utcnow().date()
    d_from = d_to - timedelta(days=3)  # small self-heal window; списание неизменяемо

    async def runner(db, pid):
        return await sync_ad_upd(db, pid, d_from.isoformat(), d_to.isoformat())

    logger.info("⏰ Scheduler: starting morning ad_upd top-up")
    await _run_ad_topup_job("ad_upd", runner)


async def sync_all_projects_ad_payments():
    """Утро: пополнения счёта WB Продвижение (adv/v1/payments) за 30 дней."""
    from backend.services.funnel.ad_finance_sync import sync_ad_payments

    d_to = utcnow().date()
    d_from = d_to - timedelta(days=30)  # пополнения редки → окно шире

    async def runner(db, pid):
        return await sync_ad_payments(db, pid, d_from.isoformat(), d_to.isoformat())

    logger.info("⏰ Scheduler: starting morning ad_payments top-up")
    await _run_ad_topup_job("ad_payments", runner)


async def sync_all_projects_ad_search():
    """Утро: посуточная статистика зоны «Поиск» по активным CPM-кампаниям (батч).

    Только кампании с недавним расходом (spend>0 за окно) — у завершённых/спящих
    статистика поиска неизменна. Тянем все их (advertId,nmId) пары батчами по 100
    в одном запросе normquery/stats: запросов ~десяток вместо «кампания-на-запрос»
    → обходим жёсткий per-seller rate-limit WB. Кнопка «Дозагрузить» — тот же bulk.
    """
    from backend.models.integrations import WbAdCampaign, WbAdCampaignDaily
    from backend.services.funnel.ad_search_stats import sync_ad_search_daily_bulk

    d_to = utcnow().date()
    d_from = d_to - timedelta(days=3)

    async def runner(db, pid):
        active = (
            select(WbAdCampaignDaily.campaign_id)
            .where(
                WbAdCampaignDaily.project_id == pid,
                WbAdCampaignDaily.date >= d_from,
                WbAdCampaignDaily.spend > 0,
            )
            .distinct()
        )
        cids = (
            await db.execute(
                select(WbAdCampaign.campaign_id).where(
                    WbAdCampaign.project_id == pid,
                    WbAdCampaign.campaign_type == "cpm",
                    WbAdCampaign.campaign_id.in_(active),
                ).limit(2000)
            )
        ).scalars().all()
        return await sync_ad_search_daily_bulk(db, pid, list(cids), d_from.isoformat(), d_to.isoformat())

    logger.info("⏰ Scheduler: starting morning ad_search top-up")
    await _run_ad_topup_job("ad_search", runner)


# ─── Nomenclature sync (2x/day) ──────────────────────────────────────────────


async def sync_nomenclature_all_projects():
    """Sync nomenclature (barcodes, brands, categories) for all projects with WB keys."""
    from backend.services.integrations_service import sync_wb_nomenclature

    logger.info("⏰ Scheduler: starting nomenclature sync")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for nomenclature sync")
        return

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_wb_nomenclature(db, pid),
                    timeout=300,
                )
                logger.info(
                    f"Nomenclature sync: project {pid} — " f"inserted={result.rows_inserted}, status={result.status}"
                )
        except TimeoutError:
            logger.error(f"Nomenclature sync TIMEOUT for project {pid}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Nomenclature sync failed for project {pid}: {e}")


# ─── Budget-only sync (every 10 min) ─────────────────────────────────────────


async def sync_budgets_all_projects():
    """Sync ONLY budgets of active campaigns for all projects. Lightweight, every 10 min."""
    from backend.services.funnel.ad_campaigns_service import sync_ad_budgets_only

    logger.info("⏰ Scheduler: starting budget-only sync for all projects")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for budget sync")
        return

    for pid in project_ids:
        log_id = None
        status = "ERROR"
        updated = 0
        events = 0
        error_msg = None

        try:
            async with AsyncSessionLocal() as db:
                int_key = await db.execute(
                    select(IntegrationKey.id)
                    .where(
                        IntegrationKey.project_id == pid,
                        IntegrationKey.service.in_(["wb", "wb_advert"]),
                        IntegrationKey.is_active.is_(True),
                        IntegrationKey.is_deleted.is_(False),
                    )
                    .limit(1)
                )
                key_id = int_key.scalar() or None
                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb_funnel",
                    sync_type="ad_budgets",
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.commit()
                await db.refresh(sync_log)
                log_id = sync_log.id

            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_ad_budgets_only(db, pid),
                    timeout=AD_BUDGETS_SYNC_TIMEOUT,
                )
                updated = result.get("updated", 0)
                events = result.get("events", 0)
                status = "OK"
                logger.info(f"Budget sync: project {pid} — updated={updated}, events={events}")
        except TimeoutError:
            status = "TIMEOUT"
            error_msg = f"Timeout {AD_BUDGETS_SYNC_TIMEOUT // 60}min exceeded"
            logger.error(f"Budget sync TIMEOUT for project {pid}")
        except asyncio.CancelledError:
            status = "ERROR"
            error_msg = "Task cancelled (worker shutdown or restart)"
            logger.warning(f"Budget sync CANCELLED for project {pid}")
            raise
        except Exception as e:
            error_msg = str(e)[:500]
            logger.error(f"Budget sync failed for project {pid}: {e}")
        finally:
            if log_id:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=status,
                                rows_inserted=updated,
                                finished_at=utcnow(),
                                error_msg=error_msg,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error(f"Failed to update sync_log {log_id}: {log_err}")


# ─── Ad intraday snapshots (every 10 min) ────────────────────────────────────


async def snapshot_ad_intraday_all_projects():
    """Снять накопительные внутридневные счётчики активных кампаний по всем проектам.

    Источник — кабинетная сессия рекламы (wb_portal_session); проекты без сессии функция
    сама тихо пропускает. Дельты между снимками = интрадей-график показы/клики/CTR."""
    from backend.services.funnel.ad_campaigns_service import snapshot_ad_intraday

    logger.info("⏰ Scheduler: starting ad intraday snapshot for all projects")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        return

    # Нулевой исход — норма (нет сессии / гейт интервала), поэтому по проекту не шумим:
    # копим причины и отдаём ОДНУ сводку на тик. Без неё «снимков нет» неотличимо от
    # «джоба работает» — ровно на этом сгорел день расследования 2026-07-16.
    outcomes: dict[str, int] = {}
    details: dict[str, str] = {}
    total_snaps = 0

    for pid in project_ids:
        try:
            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(snapshot_ad_intraday(db, pid), timeout=120)
            snaps = result.get("snapshots") or 0
            total_snaps += snaps
            if snaps:
                outcomes["ok"] = outcomes.get("ok", 0) + 1
                logger.info(f"Ad intraday snapshot: project {pid} — {snaps} snapshots")
                continue
            # Кампании активны, но WB не отдал строк — отдельный сигнал, не «нет кампаний».
            reason = result.get("skipped") or result.get("error") or "no_rows_from_wb"
            outcomes[reason] = outcomes.get(reason, 0) + 1
            if result.get("detail"):
                details[reason] = result["detail"]
        except TimeoutError:
            outcomes["timeout"] = outcomes.get("timeout", 0) + 1
            logger.error(f"Ad intraday snapshot TIMEOUT for project {pid}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            outcomes["failed"] = outcomes.get("failed", 0) + 1
            logger.error(f"Ad intraday snapshot failed for project {pid}: {e}")

    breakdown = ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
    tail = "".join(f"; {k}: {v}" for k, v in sorted(details.items()))
    logger.info(
        f"Ad intraday snapshot done: {total_snaps} snapshots over "
        f"{len(project_ids)} projects ({breakdown}){tail}"
    )


# ─── Funnel hourly sync (last 2 days) ────────────────────────────────────────


async def sync_funnel_hourly():
    """Hourly funnel sync: last 2 days for all projects."""
    logger.info("⏰ Scheduler: starting hourly funnel sync (last 2 days)")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("Scheduler: no projects with WB keys for hourly funnel sync")
        return

    for pid in project_ids:
        try:
            d_from = (date.today() - timedelta(days=1)).isoformat()
            d_to = date.today().isoformat()
            logger.info(f"Hourly funnel: project {pid} — {d_from} → {d_to}")
            await _run_and_log(pid, d_from, d_to, "funnel_hourly")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Hourly funnel sync failed for project {pid}: {e}")


# ─── Ads autopay (реальные деньги) ───────────────────────────────────────────

_autopay_lock = asyncio.Lock()


async def ads_autopay_tick():
    """Автопополнение бюджетов кампаний по настройкам ads_autopay.

    Каждые 15 мин; реально пополняет только кампании, у которых текущий час МСК
    совпал с настроенным и сегодня ещё не пополняли (идемпотентность — по
    журналу ads_autopay_log). РЕАЛЬНОЕ СПИСАНИЕ ДЕНЕГ через WB API.
    """
    if _autopay_lock.locked():
        return
    async with _autopay_lock:
        from backend.services.funnel.ads_manager import run_autopay_tick
        from backend.services.funnel.wb_api_client import get_wb_key

        try:
            project_ids = await get_sync_project_ids()
            for pid in project_ids:
                async with AsyncSessionLocal() as db:
                    api_key = (
                        await get_wb_key(db, pid, "wb_advert")
                        or await get_wb_key(db, pid, "wb_analytics")
                        or await get_wb_key(db, pid, "wb")
                    )
                    if not api_key:
                        continue
                    res = await run_autopay_tick(db, pid, api_key)
                    if res.get("checked"):
                        logger.info(
                            f"💰 Ads autopay: project {pid} — checked {res['checked']}, deposits {res['deposits']}"
                        )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Ads autopay error: {e}\n{traceback.format_exc()}")


_ad_nm_lock = asyncio.Lock()


async def ad_nm_backfill_tick():
    """Догон РК-статистики в разбивке по товарам (wb_ad_nm_daily).

    Раз в сутки ночью. Первый запуск после релиза видит пустую таблицу и тянет
    всю доступную глубину — так история пересобирается на проде сама, без ручного
    запуска. Дальше догоняет только новые дни (плюс перезалив свежих суток).

    Идёт окнами по 31 дню с паузами: WB жёстко лимитирует /adv/v3/fullstats.
    """
    if _ad_nm_lock.locked():
        logger.info("Ad nm backfill: предыдущий проход ещё идёт, пропускаем")
        return
    async with _ad_nm_lock:
        from backend.services.funnel.ad_nm_stats import catch_up_ad_nm_daily

        try:
            for pid in await get_sync_project_ids():
                async with AsyncSessionLocal() as db:
                    res = await catch_up_ad_nm_daily(db, pid)
                if res.get("skipped"):
                    logger.info(f"📊 Ad nm backfill: project {pid} — {res['skipped']}")
                    continue
                logger.info(
                    f"📊 Ad nm backfill: project {pid} — {res.get('rows', 0)} строк, "
                    f"{res.get('date_from')}→{res.get('date_to')}, "
                    f"пропущено чанков: {res.get('skipped_chunks', 0)}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Ad nm backfill error: {e}\n{traceback.format_exc()}")
