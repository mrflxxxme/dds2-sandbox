# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Scheduler job: ночное обновление карточек товаров → импорт базы знаний.

Ночной прогон (03:45 MSK, после синков отзывов/вопросов). Для каждого проекта:
1. collect_stale_card_nm_ids — nm_id из зеркал (wb_feedbacks/wb_questions/
   wb_product_kb), по которым нет wb_product_cards или карточка старше 7 дней;
2. sync_project_cards — докачать карточки с публичных хостов WB (без ключа,
   вежливый троттлинг внутри сервиса);
3. import_kb_from_cards — обновить записи КБ source='card' (upsert по hash).

Новые nm_id, приехавшие в ночных синках зеркал, таким образом автоматически
получают карточку и записи базы знаний до утреннего прогона автоответов.
Итоги — в SyncLog (sync_type='product_cards_kb'). Каждый проект — в своей
сессии; sync_log финализируется гарантированно (finally).
"""

import asyncio
import logging

from sqlalchemy import select, update

from backend.database import AsyncSessionLocal
from backend.scheduler.helpers import get_sync_project_ids
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")


async def _any_active_key_id(db, project_id: int) -> int | None:
    """id любого активного ключа проекта (FK для SyncLog; None — лог не пишем)."""
    from backend.models import IntegrationKey

    return await db.scalar(
        select(IntegrationKey.id)
        .where(
            IntegrationKey.project_id == project_id,
            IntegrationKey.is_active.is_(True),
            IntegrationKey.is_deleted.is_(False),
        )
        .limit(1)
    )


async def refresh_all_projects_cards_kb():
    """Обновить устаревшие карточки и базу знаний для всех активных проектов."""
    logger.info("WB cards+KB refresh: starting for all projects")
    project_ids = await get_sync_project_ids()
    if not project_ids:
        logger.info("WB cards+KB refresh: no projects with WB keys, skipping")
        return

    from backend.models import SyncLog
    from backend.services.wb_cards_service import (
        collect_stale_card_nm_ids,
        import_kb_from_cards,
        sync_project_cards,
    )

    ok = errors = 0
    for project_id in project_ids:
        log_id = None
        log_status = "ERROR"
        log_error: str | None = None
        rows_fetched = rows_inserted = 0
        try:
            async with AsyncSessionLocal() as db:
                key_id = await _any_active_key_id(db, project_id)
                if key_id is None:
                    logger.debug("WB cards+KB refresh: project %d has no active key, skipping", project_id)
                    continue
                stale = await collect_stale_card_nm_ids(db, project_id)

                sync_log = SyncLog(
                    integration_id=key_id,
                    service="wb",
                    sync_type="product_cards_kb",
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

            if not stale:
                log_status = "OK"
                ok += 1
                continue

            async with AsyncSessionLocal() as db:
                result = await asyncio.wait_for(
                    sync_project_cards(db, project_id, stale),
                    timeout=1800,  # вежливый троттлинг 0.5 сек × nm — с запасом
                )
            async with AsyncSessionLocal() as db:
                kb_result = await asyncio.wait_for(
                    import_kb_from_cards(db, project_id),
                    timeout=600,
                )

            log_status = "OK"
            rows_fetched = int(result.get("cards_total", 0))
            rows_inserted = int(result.get("synced", 0))
            ok += 1
            logger.info(
                "WB cards+KB refresh: project %d — stale=%d, synced=%d, kb created=%d updated=%d",
                project_id, len(stale), result.get("synced", 0),
                kb_result.get("created", 0), kb_result.get("updated", 0),
            )

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = "Timeout exceeded"
            logger.error("WB cards+KB refresh: project %d — TIMEOUT", project_id)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error("WB cards+KB refresh: project %d failed — %s", project_id, str(e), exc_info=True)
            errors += 1
        finally:
            if log_id is not None:
                try:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(SyncLog)
                            .where(SyncLog.id == log_id)
                            .values(
                                status=log_status,
                                rows_fetched=rows_fetched,
                                rows_inserted=rows_inserted,
                                finished_at=utcnow(),
                                error_msg=log_error,
                            )
                        )
                        await db.commit()
                except Exception as log_err:
                    logger.error("WB cards+KB refresh: failed to update sync_log %s — %s", log_id, log_err)

    logger.info("WB cards+KB refresh: done — %d ok, %d errors", ok, errors)
