# ruff: noqa: RUF001, RUF002, RUF003
"""
Догон ИСТОРИИ статусов заданий FBS из кабинета WB.

Зачем отдельный источник: публичный Marketplace API истории не отдаёт вовсе —
`POST /api/v3/orders/status` возвращает только текущие значения двух осей без
единой отметки времени. Поэтому наш журнал (`WbFbsOrderEvent`) пишется «в момент
обнаружения» синком и прошлое не знает: у трёх месяцев бэкфилленной истории
поздних вех нет и не будет. Кабинет же отдаёт полную цепочку с точным временем
и городом плеча, вплоть до «Оформлен».

## Как это устроено

  • **Имена статусов храним СЫРЫМИ** (`WbFbsOrderHistory.name`), маппинг в вехи —
    на чтении (`MILESTONE_RULES`). Словарь WB открытый: в живом захвате 30.07
    встретились девять имён, поздние («готов к выдаче», «получен») не попались
    вовсе. Нормализуй мы при записи — новый статус WB требовал бы миграции, а
    неузнанный молча терялся бы.

  • **Веха = ПЕРВОЕ вхождение имени.** Цепочка повторяется на каждом плече
    (несколько «Отсортирован» в разных городах: Сынково → Столбы → Пушкино),
    и «когда впервые отсортировали» — это первый скан, а не последний.

  • **Идемпотентность** — на уникальности `(project_id, order_id, at, name)`:
    повторный догон живого задания дописывает только новые строки.

  • **Очередь догона** идёт по `WbFbsOrder.history_synced_at`: сначала те, кого
    не забирали ни разу (NULL), потом самые старые. Терминальные задания с уже
    финальным статусом повторно не трогаем — их путь закончен.

## Лимиты

Хост отдаёт `x-ratelimit-limit: 150` на минутное окно. Прогон держит паузу
между запросами и свой потолок на прогон, чтобы догон истории (6 к заданий —
это ~45 минут) не выедал окно у остального кабинета. Ошибка одного задания не
роняет прогон: путь остальных от неё не зависит.
"""

import asyncio
import logging
import time
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.integrations.wb_portal_client import (
    WbPortalError,
    WbPortalRateLimited,
    WbSessionExpired,
)
from backend.models.wb_fbs import FBS_TERMINAL_STATUSES, WbFbsOrder, WbFbsOrderHistory
from backend.services.wb_fbs.contour import contour_condition
from backend.services.wb_fbs.locks import (
    ORDER_HISTORY_LOCK_NAME,
    ORDER_HISTORY_LOCK_TTL_SEC,
    acquire_lock,
    release_lock,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


class HistorySyncBusy(Exception):
    """Догон уже идёт (лок занят). Второй прогон удвоил бы темп и выбил лимит."""

#: Размер пачки. Считался из потребности, а не из головы: живых заданий на
#: проде ~5.9 к, по лестнице протухания это ~18 к запросов в сутки. Пачка 400
#: при каденсе джоба 15 минут даёт 38 к/сутки — двукратный запас. Прошлые
#: «200 раз в полчаса» (9.6 к) очередь НЕ разгребали, и новые заказы голодали
#: бы за спиной у перезаборов.
DEFAULT_BATCH = 400

#: Пауза между запросами. 0.3 с + ~0.2 с ответ ≈ 120 запросов в минуту при
#: лимите хоста 150: запас на параллельные вызовы кабинета из других частей
#: системы (FBW-транспорт живёт на той же сессии).
REQUEST_PAUSE_SEC = 0.3

#: Сколько заданий подряд должны упасть по 401, чтобы счесть сессию мёртвой.
#: 🔴 Одиночный 401 на маркетплейс-хосте бывает и при ЖИВОЙ сессии (ровно так
#: вёл себя неверный `origin`), а `mark_wb_portal_expired` гасит ключ для ВСЕХ
#: потребителей, включая FBW-транспорт. Цена ложного срабатывания — простой
#: до ручного харвеста кук.
SESSION_DEAD_AFTER = 3

#: Внутренний бюджет прогона. 🔴 Обязан быть строго МЕНЬШЕ внешнего таймаута
#: джоба (`ORDER_HISTORY_TIMEOUT_SEC`), иначе мягкая деградация недостижима:
#: внешний `wait_for` убьёт корутину раньше, чем она успеет вернуть частичный
#: результат (та же грабля, что у синков рекламы — см. learnings).
DEFAULT_BUDGET_SEC = 200.0

#: Потолок строк истории на одно задание. Внешние данные: без него один
#: аномальный ответ упирается в лимит asyncpg 32767 параметров на INSERT.
_MAX_STATUSES = 500

#: Веха → правило распознавания сырого имени статуса WB.
#: Порядок ВАЖЕН: правила проверяются сверху вниз, первое совпадение выигрывает.
#: Сравнение по подстроке в нижнем регистре — WB меняет формулировки («Продавец
#: собрал заказ: скоро передаст в доставку»), и точное равенство ломалось бы на
#: любой правке текста.
MILESTONE_CONFIRM = "confirm"
MILESTONE_ASSEMBLED = "assembled"
MILESTONE_SORTED = "sorted"
MILESTONE_READY = "ready"
MILESTONE_SOLD = "sold"
MILESTONE_CANCELLED = "cancelled"

MILESTONE_RULES: tuple[tuple[str, str], ...] = (
    # 🔴 «собрал» ДО «собирает»: обе строки начинаются одинаково, и правило
    # «собирает» с проверкой по подстроке перехватило бы обе.
    ("продавец собрал", MILESTONE_ASSEMBLED),
    ("продавец собирает", MILESTONE_CONFIRM),
    # «Отсортирован» — приёмка на СЦ. Рядом живут «Отгружено сортировочным
    # центром» и «В пути в сортировочный центр»: они про другое плечо и под
    # это правило не подпадают (корень «отсортиров», не «сортировоч»).
    ("отсортирован", MILESTONE_SORTED),
    ("готов к выдаче", MILESTONE_READY),
    ("ожидает получения", MILESTONE_READY),
    ("в пункте выдачи", MILESTONE_READY),
    # Курьерская ветка (обнаружена догоном 30.07): у неё нет ПВЗ, и последнее
    # плечо начинается моментом передачи курьеру. Смысл вехи тот же —
    # «товар доступен покупателю», поэтому этап до вручения считается одинаково
    # для обоих способов доставки.
    ("передан курьеру", MILESTONE_READY),
    ("получен покупателем", MILESTONE_SOLD),
    ("вручен", MILESTONE_SOLD),
    ("вручён", MILESTONE_SOLD),
    ("выдан", MILESTONE_SOLD),
    ("отменен", MILESTONE_CANCELLED),
    ("отменён", MILESTONE_CANCELLED),
    ("отмена", MILESTONE_CANCELLED),
)

#: Имена, которые мы осознанно НЕ считаем вехами (промежуточные плечи логистики).
#: Нужны, чтобы «неузнанное имя» в логе означало действительно новое имя WB,
#: а не привычный шум.
KNOWN_NON_MILESTONE: tuple[str, ...] = (
    "оформлен",  # веха есть точнее — created_at_wb самого задания
    "отгружено сортировочным центром",
    "отгружен распределительным центром",
    "в пути в сортировочный центр",
    "поступил в сортировочный центр",
    "в пути",
    "доставлен сц/рц",
    "курьер был назначен",  # назначение ≠ передача: веха — «передан курьеру»
)


def classify(name: str) -> str | None:
    """Сырое имя статуса WB → веха (или None, если это промежуточное плечо)."""
    low = (name or "").strip().lower()
    for needle, milestone in MILESTONE_RULES:
        if needle in low:
            return milestone
    return None


def needles_for(milestone: str) -> tuple[str, ...]:
    """Подстроки-признаки вехи — единый источник для Python и для SQL.

    Аналитика этапов достаёт вехи из истории прямо в SQL (`LIKE`), и своя копия
    списка там означала бы, что новое правило применилось в одном месте и не
    применилось в другом.
    """
    return tuple(needle for needle, key in MILESTONE_RULES if key == milestone)


def is_known(name: str) -> bool:
    """Знаем ли мы это имя вообще — веха или осознанно пропускаемое плечо."""
    low = (name or "").strip().lower()
    return classify(name) is not None or any(k in low for k in KNOWN_NON_MILESTONE)


def _parse_dt(value: Any) -> datetime | None:
    """ISO-метка WB (`2026-07-30T10:47:53.721896Z`) → наивный UTC.

    Все даты домена наивно-UTC, поэтому зону снимаем сразу: смешение aware и
    naive в одной колонке даёт `TypeError` при первом же вычитании.
    """
    if not value or not isinstance(value, str):
        return None
    raw = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # Микросекунды WB бывают длиннее шести знаков — Python такое не берёт.
        trimmed = re.sub(r"(\.\d{6})\d+", r"\1", raw)
        try:
            parsed = datetime.fromisoformat(trimmed)
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


#: Ступени протухания истории по возрасту заказа. 🔴 Плоский порог не годится:
#: `supplier_status` ЗАСТЫВАЕТ на `complete` навсегда (канон домена), поэтому
#: «не терминальный» ≠ «ещё едет». С одним порогом в 6 ч все 6 к заданий зеркала
#: считались бы живыми и требовали 24 к запросов в сутки при пропускной
#: способности джоба 9.6 к — очередь не сходилась бы никогда, а новые заказы
#: голодали бы за спиной у вечных перезаборов.
_REFRESH_LADDER: tuple[tuple[int, timedelta], ...] = (
    (3, timedelta(hours=6)),  # первые трое суток путь меняется быстро
    (14, timedelta(hours=24)),
    (45, timedelta(hours=72)),
)

#: Старше этого — не трогаем вовсе: за полтора месяца путь либо закончился,
#: либо WB о нём уже ничего не расскажет.
_TRACK_MAX_DAYS = 45


def _finished_expr(project_id: int) -> Any:
    """Путь задания ЗАВЕРШЁН по данным истории — перезабирать больше нечего.

    Признак берётся из вех (`ready` / `sold` / `cancelled`), а НЕ из
    `is_final`: WB это поле не проставляет вообще (0 из 36 806 строк живого
    зеркала), и доверять ему было бы тихой ошибкой.
    """
    needles = (
        needles_for(MILESTONE_READY)
        + needles_for(MILESTONE_SOLD)
        + needles_for(MILESTONE_CANCELLED)
    )
    has_terminal_leg = (
        select(WbFbsOrderHistory.order_id)
        .where(
            # 🔴 project_id обязателен не только по Iron rule: без него подзапрос
            # не ложится на уникальный индекс (project_id, order_id, at, name),
            # планировщик сканирует историю ВСЕХ проектов и держится за это,
            # пока хеш влезает в work_mem. С ним — Index Only Scan (243→73 мс).
            WbFbsOrderHistory.project_id == project_id,
            WbFbsOrderHistory.order_id == WbFbsOrder.id,
            or_(*[func.lower(WbFbsOrderHistory.name).like(f"%{n}%") for n in needles]),
        )
        .exists()
    )
    # `cancel`/`cancel_carrier` — единственные ДЕЙСТВИТЕЛЬНО терминальные
    # значения `supplier_status` (в отличие от `complete`, который застывает
    # навсегда): у отменённого задания пути больше не будет.
    return or_(has_terminal_leg, WbFbsOrder.supplier_status.in_(FBS_TERMINAL_STATUSES))


async def _pick_orders(db: AsyncSession, project_id: int, limit: int) -> list[tuple[int, int]]:
    """Очередь догона: `(pk, wb_order_id)` — сначала ни разу не забиравшиеся.

    Берём задание, если оно (а) не забиралось ни разу, либо (б) путь ещё не
    завершён, заказ моложе `_TRACK_MAX_DAYS`, и с прошлого захода прошло больше
    ступени `_REFRESH_LADDER` для его возраста.
    """
    now = utcnow()
    age_days = func.extract("epoch", now - WbFbsOrder.created_at_wb) / 86400.0

    # Порог протухания как ступенчатая функция возраста заказа.
    ladder: Any = None
    for days, gap in reversed(_REFRESH_LADDER):
        branch = now - gap
        ladder = branch if ladder is None else case((age_days <= days, branch), else_=ladder)
    stale_before = ladder

    rows = (
        await db.execute(
            select(WbFbsOrder.id, WbFbsOrder.wb_order_id)
            .where(
                WbFbsOrder.project_id == project_id,
                contour_condition(WbFbsOrder.raw),
                or_(
                    WbFbsOrder.history_synced_at.is_(None),
                    and_(
                        WbFbsOrder.history_synced_at < stale_before,
                        age_days <= _TRACK_MAX_DAYS,
                        ~_finished_expr(project_id),
                    ),
                ),
            )
            # 🔴 `nullsfirst()` обязателен: в Postgres ASC по умолчанию даёт
            # NULLS **LAST**, и задания, которых не касались ни разу, уходили бы
            # в хвост очереди — первичный догон начинался бы с обновления уже
            # забранных. Второй ключ (`id`) делает порядок детерминированным.
            .order_by(WbFbsOrder.history_synced_at.asc().nullsfirst(), WbFbsOrder.id.asc())
            .limit(limit)
        )
    ).all()
    return [(int(pk), int(wb_id)) for pk, wb_id in rows]


async def _store(db: AsyncSession, project_id: int, order_pk: int, payload: dict) -> int:
    """Записать историю одного задания. Возвращает число НОВЫХ строк."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[datetime, str]] = set()
    raw_statuses = payload.get("statuses")
    for item in (raw_statuses if isinstance(raw_statuses, list) else [])[:_MAX_STATUSES]:
        if not isinstance(item, dict):
            continue  # внешние данные: WB может отдать что угодно
        at = _parse_dt(item.get("date"))
        name = (item.get("name") or "").strip()
        if at is None or not name:
            continue
        key = (at, name[:120])
        if key in seen:
            continue  # WB иногда дублирует строку плеча — ключ уникальности тот же
        seen.add(key)
        if not is_known(name):
            logger.warning(
                "FBS история: неизвестный статус WB %r (задание pk=%s) — "
                "проверь MILESTONE_RULES, веха могла потеряться",
                name,
                order_pk,
            )
        rows.append(
            {
                "project_id": project_id,
                "order_id": order_pk,
                "at": at,
                "name": name[:120],
                "place": (item.get("place") or "").strip()[:120] or None,
                "is_final": bool(item.get("isFinal")),
                "created_at": utcnow(),
            }
        )

    inserted = 0
    if rows:
        stmt = (
            pg_insert(WbFbsOrderHistory)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_wb_fbs_order_history")
            .returning(WbFbsOrderHistory.id)
        )
        inserted = len((await db.execute(stmt)).all())

    await db.execute(
        sa_update(WbFbsOrder)
        .where(WbFbsOrder.id == order_pk, WbFbsOrder.project_id == project_id)
        .values(
            history_synced_at=utcnow(),
            # coalesce, а не присваивание: на повторном заходе WB может не
            # отдать поле, и обещанная дата — единственный источник колонки
            # SLA в географии — обнулилась бы.
            delivery_date_plan=func.coalesce(
                literal(_parse_dt(payload.get("deliveryDate")), type_=WbFbsOrder.delivery_date_plan.type),
                WbFbsOrder.delivery_date_plan,
            ),
        )
    )
    return inserted


async def sync_order_history(
    db: AsyncSession,
    project_id: int,
    *,
    client: Any,
    limit: int = DEFAULT_BATCH,
    budget_sec: float = DEFAULT_BUDGET_SEC,
) -> dict[str, int]:
    """Догнать историю статусов пачки заданий. Возвращает счётчики прогона.

    Клиент передаётся снаружи (а не создаётся здесь), чтобы прогон и джоб
    делили одну портальную сессию: её обновление стоит ручного харвеста, и
    плодить соединения на каждое задание нельзя.

    Ошибка одного задания прогон не роняет — путь остальных от неё не зависит;
    а вот протухшая сессия роняет сразу: без неё не ответит НИ ОДНО задание, и
    молотить лимит впустую бессмысленно.
    """
    # 🔴 Лок берётся ВНУТРИ сервиса, а не у вызывающего: точек входа две (ручка
    # в api-контейнере и джоб в worker'е), и обе обязаны упереться в один замок.
    token = await acquire_lock(
        ORDER_HISTORY_LOCK_NAME, project_id, ttl=ORDER_HISTORY_LOCK_TTL_SEC
    )
    if token is None:
        raise HistorySyncBusy("Догон истории уже идёт — дождитесь окончания прогона")
    try:
        return await _run_sync(db, project_id, client=client, limit=limit, budget_sec=budget_sec)
    finally:
        await release_lock(ORDER_HISTORY_LOCK_NAME, project_id, token)


async def _run_sync(
    db: AsyncSession,
    project_id: int,
    *,
    client: Any,
    limit: int,
    budget_sec: float,
) -> dict[str, int]:
    """Тело прогона под уже взятым локом."""
    queue = await _pick_orders(db, project_id, limit)
    # 🔴 Закрыть транзакцию ДО первого похода в WB. SQLAlchemy открывает её на
    # первом же SELECT, а дальше цикл ждёт сеть — серверный коннект pgbouncer
    # висел бы `idle in transaction` всё время прогона и выедал пул (клин
    # локалки 2026-07-16, см. learnings). Паттерн — `bulk_create_ff_requests`.
    await db.commit()
    stats = {"asked": 0, "orders": 0, "rows": 0, "failed": 0}
    if not queue:
        return stats

    deadline = time.monotonic() + budget_sec
    session_fails = 0
    for idx, (order_pk, wb_order_id) in enumerate(queue):
        if time.monotonic() >= deadline:
            logger.info(
                "FBS история: бюджет %.0f c исчерпан — обработано %s из %s, остальное возьмёт "
                "следующий прогон (очередь идёт от самых давних)",
                budget_sec,
                stats["orders"],
                len(queue),
            )
            break
        if idx:
            await asyncio.sleep(REQUEST_PAUSE_SEC)
        stats["asked"] += 1
        try:
            payload = await client.fetch_order_history(wb_order_id)
        except WbPortalRateLimited as e:
            # Лимит хоста: задание не испорчено, испорчен темп. Ждём и пробуем
            # ЕЩЁ раз — иначе пачка «сгорала» бы в failed, а задания оставались
            # без истории до следующего прогона (ловилось живьём 30.07, когда
            # два прогона случайно пошли параллельно и удвоили темп).
            pause = min(max(int(getattr(e, "retry_after", 0) or 0), 5), 60)
            # 🔴 Дедлайн проверяется ПЕРЕД паузой: она бывает до минуты, и
            # итерация, начатая под конец бюджета, пробивала бы внешний таймаут
            # джоба — ровно тот сценарий, ради которого бюджет и заведён.
            if time.monotonic() + pause >= deadline:
                logger.info("FBS история: лимит кабинета у границы бюджета — прогон закрыт")
                await db.commit()
                break
            logger.warning("FBS история: лимит кабинета, пауза %s c", pause)
            # Пауза до минуты — транзакцию через неё держать нельзя.
            await db.commit()
            await asyncio.sleep(pause)
            try:
                payload = await client.fetch_order_history(wb_order_id)
            except WbPortalError as retry_error:
                stats["failed"] += 1
                logger.warning("FBS история: задание %s — %s", wb_order_id, retry_error)
                continue
        except WbSessionExpired:
            # Одиночный 401 сессию НЕ хоронит: на маркетплейс-хосте он бывает и
            # при живой сессии. Хороним только серию подряд — тогда это
            # действительно протухшие куки, и джоб пометит ключ EXPIRED.
            session_fails += 1
            stats["failed"] += 1
            if session_fails < SESSION_DEAD_AFTER:
                logger.warning(
                    "FBS история: 401 на задании %s (%s/%s) — продолжаем",
                    wb_order_id,
                    session_fails,
                    SESSION_DEAD_AFTER,
                )
                continue
            logger.warning(
                "FBS история: %s подряд 401 — сессия кабинета мертва, прогон остановлен "
                "(забрано %s из %s)",
                session_fails,
                stats["orders"],
                len(queue),
            )
            await db.commit()
            raise
        except WbPortalError as e:
            stats["failed"] += 1
            logger.warning("FBS история: задание %s — %s", wb_order_id, e)
            continue

        try:
            stats["rows"] += await _store(db, project_id, order_pk, payload)
        except (AttributeError, TypeError, ValueError) as e:
            # Формат ответа WB — не наша ответственность: битое задание
            # пропускаем, прогон продолжается (раньше падало всё).
            stats["failed"] += 1
            logger.warning("FBS история: задание %s — формат ответа: %s", wb_order_id, e)
            await db.rollback()
            continue
        stats["orders"] += 1
        session_fails = 0  # успех — серия прервана
        # Коммит на КАЖДОМ задании: следующий шаг цикла — снова поход в сеть,
        # и открытая транзакция дожила бы до него. Заодно обрыв не обнуляет
        # работу — на прогоне в 5600 заданий это уже спасало (OOM 30.07).
        await db.commit()

    await db.commit()
    logger.info(
        "FBS история: project=%s забрано %s/%s заданий, новых строк %s, ошибок %s",
        project_id,
        stats["orders"],
        stats["asked"],
        stats["rows"],
        stats["failed"],
    )
    return stats


async def history_coverage(db: AsyncSession, project_id: int) -> dict[str, Any]:
    """Покрытие догона: сколько заданий уже с историей и сколько строк.

    Питает подпись на экране аналитики — без неё пустой этап не отличить от
    «ещё не догнали».
    """
    orders_total, orders_done = (
        await db.execute(
            select(
                func.count(),
                func.count().filter(WbFbsOrder.history_synced_at.isnot(None)),
            ).where(
                WbFbsOrder.project_id == project_id,
                contour_condition(WbFbsOrder.raw),
            )
        )
    ).one()
    # Контур: строки истории своей метки не несут, поэтому фильтруем через
    # задание — иначе при переключении sandbox↔prod счётчики смешаются.
    rows, first_at = (
        await db.execute(
            select(func.count(), func.min(WbFbsOrderHistory.at))
            .join(WbFbsOrder, WbFbsOrder.id == WbFbsOrderHistory.order_id)
            .where(
                WbFbsOrderHistory.project_id == project_id,
                contour_condition(WbFbsOrder.raw),
            )
        )
    ).one()
    return {
        "orders_total": int(orders_total or 0),
        "orders_covered": int(orders_done or 0),
        "rows": int(rows or 0),
        "since": first_at,
    }
