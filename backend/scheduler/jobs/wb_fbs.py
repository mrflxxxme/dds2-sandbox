# ruff: noqa: RUF001, RUF002, RUF003
"""
Фоновые джобы домена WB FBS (продажи со склада продавца).

Шесть джобов, все регистрируются с `max_instances=1, coalesce=True`:
  • push_all_projects_fbs_stocks          — трансляция остатков на склады продавца WB;
  • sync_all_projects_fbs_new_orders      — новые сборочные задания;
  • sync_all_projects_fbs_recent_orders   — догон недавнего окна (`GET /orders`);
  • sync_all_projects_fbs_order_statuses  — статусы не-терминальных заданий + списание;
  • sync_all_projects_fbs_supplies        — зеркало поставок FBS;
  • sync_all_projects_fbs_warehouses      — справочник складов продавца (раз в сутки).

Инварианты домена:
  • Работаем ТОЛЬКО с проектами, у которых есть активный ключ ПОД ТЕКУЩИЙ РЕЖИМ
    контура (`client_factory.service_for_mode()`: `wb_marketplace`, а в песочнице
    `wb_marketplace_sandbox`) и (кроме синка справочника складов) хотя бы один
    активный склад FBS. Иначе жжём лимиты WB и плодим ERROR в SyncLog на пустом
    месте. Литерал вместо `service_for_mode()` уже давал молчаливый простой:
    в режиме sandbox ручные кнопки работали, а все пять джобов выходили на
    «нет проектов» — фон мёртв, а в SyncLog ни строки.
  • Гонка «ручная кнопка из api-контейнера ‖ джоб из worker» НЕ лечится
    `asyncio.Lock` — локи in-process, а контейнера два. Распределённый лок Redis
    `SET NX EX` (`wb_fbs:push_lock:{project_id}`) берётся ВНУТРИ самой
    трансляции (`services/wb_fbs/locks.py` + `stock_service.push_stocks`), а не
    здесь: лок у одного из двух вызывающих не исключает ничего.
  • Внутренний бюджет цикла строго МЕНЬШЕ внешнего `asyncio.wait_for`: иначе
    внешний таймаут убивает корутину раньше, чем цикл успеет сам остановиться
    и вернуть частичный результат (см. .claude/rules/learnings.md — синки
    рекламы месяц отдавали 0% успеха именно из-за равных бюджетов).
  • `DELETE /api/v3/stocks` в джобах НЕ используется никогда: лимит 10/мин и
    необратимость. Обнуление позиции = `PUT` с `amount = 0`.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.models import FbsWarehouseMode, IntegrationKey, Project, SyncLog, WbFbsWarehouse
from backend.utils.time import utcnow

logger = logging.getLogger("dds.scheduler")

# ─── Константы домена ───────────────────────────────────────────────────────

#: `IntegrationKey.service` ключа категории «Маркетплейс» (боевой контур).
#: Дефолт/фолбэк: реальный сервис берётся по режиму — см. `_fbs_key_service`.
FBS_KEY_SERVICE = "wb_marketplace"
#: `SyncLog.service` всех джобов домена.
SYNC_SERVICE = "wb_fbs"

#: Лимит проектов на прогон — страховка от неограниченной выборки.
MAX_PROJECTS_PER_CYCLE = 500
#: Пол таймаута на проект: не даём остатку бюджета выродиться в «2 секунды».
MIN_PROJECT_TIMEOUT_SEC = 30.0

# ─── Бюджеты времени ────────────────────────────────────────────────────────
# Пара на каждый джоб: (внешний `asyncio.wait_for` на один проект,
# внутренний бюджет ВСЕГО цикла по проектам). Инвариант CYCLE_BUDGET < TIMEOUT
# закреплён тестом tests/test_wb_fbs_jobs.py::TestTimeBudgets.

# ВНИМАНИЕ: TTL распределённого лока трансляции
# (`services/wb_fbs/locks.py:PUSH_LOCK_TTL_SEC`) обязан быть строго больше
# STOCK_PUSH_TIMEOUT_SEC — иначе лок протухнет посреди прогона и следующий тик
# войдёт в критическую секцию. Связь держит tests/test_wb_fbs_locks.py.
STOCK_PUSH_TIMEOUT_SEC = 300
STOCK_PUSH_CYCLE_BUDGET_SEC = 240

NEW_ORDERS_TIMEOUT_SEC = 120
NEW_ORDERS_CYCLE_BUDGET_SEC = 90

# Догон недавнего окна дороже «новых»: у периодного метода пагинация (до 1000
# заданий на страницу), а окно после простоя воркера бывает и в пару суток —
# потому потолок выше. Оба бюджета строго больше `REQUEST_TOTAL_BUDGET_SEC`
# одного запроса к WB и связаны тестом TestTimeBudgets.
RECENT_ORDERS_TIMEOUT_SEC = 180
RECENT_ORDERS_CYCLE_BUDGET_SEC = 150

ORDER_STATUSES_TIMEOUT_SEC = 300
ORDER_STATUSES_CYCLE_BUDGET_SEC = 240

#: Догон истории: пачка 400 заданий × (пауза 0.3 c + ~0.2 c ответ) ≈ 200 c.
#: Внутренний бюджет сервиса (`DEFAULT_BUDGET_SEC = 200`) строго меньше этого
#: таймаута — иначе прогон убивают раньше, чем он успевает вернуть частичный
#: результат (грабля синков рекламы, см. learnings).
ORDER_HISTORY_TIMEOUT_SEC = 300
ORDER_HISTORY_CYCLE_BUDGET_SEC = 260

SUPPLIES_TIMEOUT_SEC = 300
SUPPLIES_CYCLE_BUDGET_SEC = 240

WAREHOUSES_TIMEOUT_SEC = 120
WAREHOUSES_CYCLE_BUDGET_SEC = 90


# ─── Выбор проектов ─────────────────────────────────────────────────────────


def _fbs_key_service() -> str:
    """`IntegrationKey.service` под текущий режим контура.

    Тот же резолвер, что у клиента и роутера (`client_factory.service_for_mode`),
    иначе фон и UI расходятся: в песочнице кнопки ходят по ключу
    `wb_marketplace_sandbox`, а джобы искали бы боевой и не находили ничего.
    Импорт локальный: `scheduler` не тянет `services` на уровне модуля.
    """
    from backend.services.wb_fbs.client_factory import service_for_mode

    return service_for_mode()


def _select_fbs_projects(
    key_rows: list[tuple[int, int | None]],
    project_ids: list[int],
    active_warehouse_project_ids: set[int],
    *,
    require_active_warehouse: bool,
) -> list[tuple[int, int]]:
    """Чистый отбор проектов под FBS-синк → `[(project_id, integration_key_id)]`.

    `key_rows` — `[(key_id, project_id | None)]` активных ключей текущего режима;
    `project_id is None` = глобальный ключ (действует на все проекты).
    Проектный ключ приоритетнее глобального.
    """
    if not key_rows:
        return []

    global_key_id = next((kid for kid, pid in key_rows if pid is None), None)
    per_project = {pid: kid for kid, pid in key_rows if pid is not None}

    out: list[tuple[int, int]] = []
    for pid in project_ids:
        key_id = per_project.get(pid, global_key_id)
        if key_id is None:
            continue
        if require_active_warehouse and pid not in active_warehouse_project_ids:
            continue
        out.append((pid, key_id))
    return out


async def _get_fbs_project_ids(
    *, require_active_warehouse: bool = True, require_translate_mode: bool = False
) -> list[tuple[int, int]]:
    """Проекты под FBS-синк: `[(project_id, integration_key_id)]`.

    Ключ интеграции нужен для `SyncLog.integration_id` — и он обязан быть ключом
    ТОГО контура, в который реально пойдёт прогон (иначе журнал ссылается на
    боевой ключ, которым в песочнице никто не ходит). Проекты без активного
    ключа текущего режима (и без активных складов FBS, когда это требуется)
    отсекаются здесь — джоб их даже не начинает.

    `require_translate_mode` — только для пуша остатков: склад в режиме
    наблюдения (`observe`) в WB не пишет никогда, и гонять по нему прогон
    значит жечь лимиты и плодить пустые строки в SyncLog. Сам гейт стоит в
    сервисе (`stock_service._push_stocks_locked`) — здесь лишь отбор.
    """
    service = _fbs_key_service()
    async with AsyncSessionLocal() as db:
        # order_by — детерминизм при нескольких ключах на проект (побеждает последний).
        key_res = await db.execute(
            select(IntegrationKey.id, IntegrationKey.project_id)
            .where(
                IntegrationKey.service == service,
                IntegrationKey.is_active == True,  # noqa: E712
                IntegrationKey.is_deleted == False,  # noqa: E712
            )
            .order_by(IntegrationKey.id)
            .limit(MAX_PROJECTS_PER_CYCLE)
        )
        key_rows: list[tuple[int, int | None]] = [(r[0], r[1]) for r in key_res]
        if not key_rows:
            return []

        wh_ids: set[int] = set()
        if require_active_warehouse:
            wh_query = select(WbFbsWarehouse.project_id).where(WbFbsWarehouse.is_active == True)  # noqa: E712
            if require_translate_mode:
                wh_query = wh_query.where(WbFbsWarehouse.mode == FbsWarehouseMode.TRANSLATE.value)
            wh_res = await db.execute(wh_query.distinct().limit(MAX_PROJECTS_PER_CYCLE))
            wh_ids = {r[0] for r in wh_res if r[0]}
            if not wh_ids:
                return []

        # Отсекаем НЕ подходящие проекты в SQL, а не после LIMIT: иначе на базе
        # с тысячами проектов (локалка после sync-prod) лимит съедал бы выборку
        # раньше, чем в неё попал единственный проект с ключом FBS.
        proj_stmt = select(Project.id).where(Project.is_deleted == False)  # noqa: E712
        if not any(pid is None for _, pid in key_rows):
            scoped = {pid for _, pid in key_rows if pid is not None}
            proj_stmt = proj_stmt.where(Project.id.in_(scoped))
        if require_active_warehouse:
            proj_stmt = proj_stmt.where(Project.id.in_(wh_ids))

        proj_res = await db.execute(proj_stmt.order_by(Project.id).limit(MAX_PROJECTS_PER_CYCLE))
        project_ids = [r[0] for r in proj_res if r[0]]

    return _select_fbs_projects(
        key_rows,
        project_ids,
        wh_ids,
        require_active_warehouse=require_active_warehouse,
    )


# ─── Общий раннер ───────────────────────────────────────────────────────────

#: handler(db, project_id) → (rows_fetched, rows_inserted) для SyncLog.
FbsHandler = Callable[[AsyncSession, int], Awaitable[tuple[int, int]]]


async def _finalize_sync_log(
    log_id: int,
    *,
    status: str,
    rows_fetched: int,
    rows_inserted: int,
    error: str | None,
) -> None:
    """Закрыть SyncLog в ОТДЕЛЬНОЙ сессии — строка не должна остаться RUNNING."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                update(SyncLog)
                .where(SyncLog.id == log_id)
                .values(
                    status=status,
                    rows_fetched=rows_fetched,
                    rows_inserted=rows_inserted,
                    finished_at=utcnow(),
                    error_msg=error,
                )
            )
            await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as log_err:
        logger.error("WB FBS: не удалось закрыть sync_log %s — %s", log_id, log_err)


async def _run_fbs_job(
    *,
    sync_type: str,
    title: str,
    handler: FbsHandler,
    timeout: int,
    cycle_budget: int,
    require_active_warehouse: bool = True,
    require_translate_mode: bool = False,
) -> None:
    """Прогнать `handler` по всем FBS-проектам: SyncLog + таймауты.

    Каждый проект живёт в своей короткой сессии (SyncLog — в отдельной, чтобы
    падение синка не откатило журнал). Бюджет цикла проверяется ПЕРЕД взятием
    следующего проекта и урезает таймаут — так прогон деградирует до частичного
    результата сам, не дожидаясь внешнего убийства.
    """
    from backend.config import settings

    if not settings.WB_FBS_ENABLED:
        logger.debug("%s: WB_FBS_ENABLED=false — пропуск", title)
        return

    projects = await _get_fbs_project_ids(
        require_active_warehouse=require_active_warehouse,
        require_translate_mode=require_translate_mode,
    )
    if not projects:
        # Ключ называем ТЕКУЩИЙ: иначе в песочнице лог советует искать боевой.
        logger.debug("%s: нет проектов с активным ключом %s — пропуск", title, _fbs_key_service())
        return

    started = time.monotonic()
    ok = 0
    errors = 0
    processed = 0

    for project_id, key_id in projects:
        elapsed = time.monotonic() - started
        if elapsed >= cycle_budget:
            logger.warning(
                "%s: бюджет цикла %d c исчерпан — обработано %d из %d проектов",
                title,
                cycle_budget,
                processed,
                len(projects),
            )
            break
        # Внешний таймаут проекта: не больше остатка бюджета цикла (и не больше
        # потолка джоба), но и не меньше пола — иначе хвост проектов гарантированно
        # падал бы по таймауту вместо честной работы.
        project_timeout = max(MIN_PROJECT_TIMEOUT_SEC, min(float(timeout), cycle_budget - elapsed))

        processed += 1
        log_id: int | None = None
        log_status = "ERROR"
        log_error: str | None = None
        rows_fetched = 0
        rows_inserted = 0
        try:
            async with AsyncSessionLocal() as db:
                sync_log = SyncLog(
                    integration_id=key_id,
                    service=SYNC_SERVICE,
                    sync_type=sync_type,
                    started_at=utcnow(),
                    status="RUNNING",
                )
                db.add(sync_log)
                await db.flush()
                log_id = sync_log.id
                await db.commit()

            async with AsyncSessionLocal() as db:
                rows_fetched, rows_inserted = await asyncio.wait_for(
                    handler(db, project_id),
                    timeout=project_timeout,
                )

            log_status = "OK"
            ok += 1
            logger.info("%s: проект %d — %d/%d", title, project_id, rows_fetched, rows_inserted)

        except asyncio.CancelledError:
            log_error = "Task cancelled (worker shutdown or restart)"
            raise
        except TimeoutError:
            log_error = f"Timeout {project_timeout:.0f}s exceeded"
            logger.error("%s: проект %d — TIMEOUT (%.0fs)", title, project_id, project_timeout)
            errors += 1
        except Exception as e:
            log_error = str(e)[:1000]
            logger.error("%s: проект %d упал — %s", title, project_id, str(e), exc_info=True)
            errors += 1
        finally:
            if log_id is not None:
                await _finalize_sync_log(
                    log_id,
                    status=log_status,
                    rows_fetched=rows_fetched,
                    rows_inserted=rows_inserted,
                    error=log_error,
                )

    logger.info("%s: готово — %d ok, %d errors", title, ok, errors)


# ─── Хендлеры (сервисы грузим лениво: домен зовётся только из джоба) ────────


async def _handle_stock_push(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """Трансляция остатков на все активные склады продавца WB проекта.

    Таймаут снимает корутину, не дав ей дописать журнал прогона: `_run_plans`
    честно пробрасывает `CancelledError`, и строка `WbFbsStockPush` остаётся в
    статусе RUNNING навсегда — «лог трансляции» показывает вечное «Выполняется».
    Ручная кнопка от этого защищена (`fail_running_pushes` в роутере), фоновый
    путь был — нет. Дочиняем здесь: помечаем как ERROR всё, что этот прогон
    начал и не закрыл.
    """
    from backend.services.wb_fbs.stock_service import fail_running_pushes, push_stocks

    started = utcnow()
    try:
        push_ids = await push_stocks(db, project_id, trigger="auto")
    except (TimeoutError, asyncio.CancelledError):
        # Отдельная сессия: текущая уже отменена вместе с задачей, писать в неё
        # нечего. `shield` доводит запись до конца, даже когда таск снимают.
        async with AsyncSessionLocal() as log_db:
            await asyncio.shield(
                fail_running_pushes(log_db, project_id, since=started, reason="Таймаут фонового прогона")
            )
        raise
    return len(push_ids), len(push_ids)


async def _handle_new_orders(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """Новые сборочные задания (`GET /api/v3/orders/new`)."""
    from backend.services.wb_fbs.orders_service import sync_new_orders

    count = await sync_new_orders(db, project_id)
    return count, count


async def _handle_recent_orders(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """Догон недавнего окна (`GET /api/v3/orders` за последние сутки-двое).

    `GET /orders/new` отдаёт только задания, ещё не положенные в поставку:
    собранное между двумя опросами исчезает оттуда навсегда и в зеркало не
    попадает никогда. Периодный метод закрывает эту утечку.
    """
    from backend.services.wb_fbs.orders_service import sync_orders_recent

    count = await sync_orders_recent(db, project_id)
    return count, count


async def _run_assembly_mirror(db: AsyncSession, project_id: int) -> int:
    """Учётное зеркало сборки FBS (заявки kind=fbs) — best-effort.

    Сбой зеркала не должен ронять родительский синк (поставки/статусы уже
    записаны и закоммичены сервисами) — логируем и продолжаем; сессию
    откатываем, чтобы не оставить её в failed-состоянии.
    """
    from backend.services.wb_fbs.assembly_mirror import sync_fbs_assembly_mirror

    try:
        return await sync_fbs_assembly_mirror(db, project_id)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error("WB FBS assembly mirror: проект %d упал — %s", project_id, e, exc_info=True)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001 — сессия могла быть уже закрыта
            pass
        return 0


async def _handle_order_statuses(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """Статусы не-терминальных заданий + списание ушедших в `complete`.

    Списание живёт здесь, а не отдельным джобом: `complete` появляется ровно в
    момент обновления статусов, и списывать логично тем же проходом
    (`writeoff_completed_orders` идемпотентен по `written_off_at`).

    После списания — догон учётного зеркала сборки FBS (kind=fbs): статусы
    заявок обязаны догонять статусы заданий без отдельного расписания.
    """
    from backend.services.wb_fbs.orders_service import sync_order_statuses, writeoff_completed_orders

    updated = await sync_order_statuses(db, project_id)
    written_off = await writeoff_completed_orders(db, project_id)
    await _run_assembly_mirror(db, project_id)
    return updated, written_off


async def _handle_supplies(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """Зеркало поставок FBS + учётное зеркало сборки (заявки kind=fbs)."""
    from backend.services.wb_fbs.supplies_service import sync_supplies

    count = await sync_supplies(db, project_id)
    await _run_assembly_mirror(db, project_id)
    return count, count


async def _handle_order_history(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """История статусов заданий из КАБИНЕТА — точные вехи для аналитики этапов.

    Ходит НЕ в публичный Marketplace API (истории там нет вовсе), а в кабинет
    портальной сессией. Отсюда два отличия от соседних хендлеров:
      • сессии может не быть вовсе — тогда прогон честно падает в ERROR с
        текстом про кабинет, а не молчит: без неё домен просто не наполняется;
      • `WbSessionExpired` НЕ гасим — протухшую сессию обновляют руками
        (харвест кук), и она обязана быть видна в SyncLog, а не выглядеть
        случайным таймаутом.
    """
    from backend.integrations.wb_portal_client import WbSessionExpired
    from backend.services import integrations_service
    from backend.services.wb_fbs.order_history import sync_order_history

    client = await integrations_service.get_wb_portal_client(db, project_id)
    try:
        stats = await sync_order_history(db, project_id, client=client)
    except WbSessionExpired:
        # 🔴 Помечаем сессию EXPIRED, как это делает FBW-транспорт: иначе
        # протухание видно только строкой в SyncLog, а UI продолжает считать
        # доступ живым и не просит его обновить. Куки кабинета WB ротирует
        # примерно раз в неделю, так что это штатное событие, а не авария.
        # Сервис поднимает это исключение только после SESSION_DEAD_AFTER
        # подряд неудач — одиночный 401 сессию не хоронит.
        await integrations_service.mark_wb_portal_expired(db, project_id)
        raise
    finally:
        await client.aclose()
    return stats["asked"], stats["rows"]


async def _handle_warehouses(db: AsyncSession, project_id: int) -> tuple[int, int]:
    """Справочник складов продавца WB (+ флаги isProcessing/isDeleting)."""
    from backend.services.wb_fbs.warehouse_service import sync_warehouses

    count = await sync_warehouses(db, project_id)
    return count, count


# ─── Публичные джобы ────────────────────────────────────────────────────────


async def push_all_projects_fbs_stocks() -> None:
    """Транслировать остатки FBS на склады продавца WB (каденс — 3 мин).

    Взаимное исключение с кнопкой «Передать остатки» (api-контейнер) обеспечивает
    распределённый лок ВНУТРИ `push_stocks` — здесь его брать нельзя, иначе джоб
    заблокировал бы сам себя.

    Единственный джоб, требующий склада в режиме `translate`: в режиме
    наблюдения писать в кабинет нельзя, и прогон был бы гарантированно пустым.
    """
    await _run_fbs_job(
        sync_type="stock_push",
        title="WB FBS stock push",
        handler=_handle_stock_push,
        timeout=STOCK_PUSH_TIMEOUT_SEC,
        cycle_budget=STOCK_PUSH_CYCLE_BUDGET_SEC,
        require_translate_mode=True,
    )


async def sync_all_projects_fbs_new_orders() -> None:
    """Забрать новые сборочные задания FBS (каденс — 2 мин)."""
    await _run_fbs_job(
        sync_type="new_orders",
        title="WB FBS new orders",
        handler=_handle_new_orders,
        timeout=NEW_ORDERS_TIMEOUT_SEC,
        cycle_budget=NEW_ORDERS_CYCLE_BUDGET_SEC,
        # Заказы приходят независимо от того, включили мы трансляцию остатков
        # или нет: это чтение кабинета, а не наша отдача. Гейт по активному
        # складу оставлен ТОЛЬКО у пуша остатков — иначе до включения тумблера
        # зеркало заданий стоит мёртвым и статусы протухают (поймано вживую:
        # 22 задания уехали в поставку, а у нас все висели «Новое»).
        require_active_warehouse=False,
    )


async def sync_all_projects_fbs_recent_orders() -> None:
    """Догнать задания за недавнее окно периодным методом (каденс — 10 мин).

    Отдельный джоб, а не расширение синка новых: у периодного метода свой
    лимит запросов и своя цена (окно + пагинация), гонять его каждые 2 минуты
    незачем — утечку закрывает и десятиминутный проход.
    """
    await _run_fbs_job(
        sync_type="orders_recent",
        title="WB FBS recent orders",
        handler=_handle_recent_orders,
        timeout=RECENT_ORDERS_TIMEOUT_SEC,
        cycle_budget=RECENT_ORDERS_CYCLE_BUDGET_SEC,
        # Ровно та же причина, что у синка новых заданий: заказы приходят
        # независимо от того, включили мы трансляцию остатков или нет.
        require_active_warehouse=False,
    )


async def sync_all_projects_fbs_order_statuses() -> None:
    """Обновить статусы заданий FBS и списать ушедшие в доставку (каденс — 5 мин)."""
    await _run_fbs_job(
        sync_type="order_statuses",
        title="WB FBS order statuses",
        handler=_handle_order_statuses,
        timeout=ORDER_STATUSES_TIMEOUT_SEC,
        cycle_budget=ORDER_STATUSES_CYCLE_BUDGET_SEC,
        # Статусы обязаны обновляться всегда — на них завязаны стикеры
        # (WB выдаёт их только для confirm/complete) и вычет открытых заказов.
        require_active_warehouse=False,
    )


async def sync_all_projects_fbs_supplies() -> None:
    """Синхронизировать поставки FBS (каденс — 15 мин)."""
    await _run_fbs_job(
        sync_type="supplies",
        title="WB FBS supplies",
        handler=_handle_supplies,
        timeout=SUPPLIES_TIMEOUT_SEC,
        cycle_budget=SUPPLIES_CYCLE_BUDGET_SEC,
        # Поставки создаются в кабинете и без нашей трансляции.
        require_active_warehouse=False,
    )


async def sync_all_projects_fbs_order_history() -> None:
    """Догнать историю статусов заданий из кабинета WB (каденс — 15 мин)."""
    await _run_fbs_job(
        sync_type="order_history",
        title="WB FBS order history",
        handler=_handle_order_history,
        timeout=ORDER_HISTORY_TIMEOUT_SEC,
        cycle_budget=ORDER_HISTORY_CYCLE_BUDGET_SEC,
        # История читается из кабинета и от трансляции остатков не зависит.
        require_active_warehouse=False,
    )


async def sync_all_projects_fbs_warehouses() -> None:
    """Обновить справочник складов продавца WB (раз в сутки).

    Единственный джоб БЕЗ требования активного склада FBS: это он и наполняет
    справочник, из которого потом включают склады. Цена — 1 запрос в сутки на
    проект с ключом «Маркетплейс».
    """
    await _run_fbs_job(
        sync_type="warehouses",
        title="WB FBS warehouses",
        handler=_handle_warehouses,
        timeout=WAREHOUSES_TIMEOUT_SEC,
        cycle_budget=WAREHOUSES_CYCLE_BUDGET_SEC,
        require_active_warehouse=False,
    )
