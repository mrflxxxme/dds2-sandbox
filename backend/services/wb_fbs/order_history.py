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

from sqlalchemy import and_, case, func, or_, select
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
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

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

#: Внутренний бюджет прогона. 🔴 Обязан быть строго МЕНЬШЕ внешнего таймаута
#: джоба (`ORDER_HISTORY_TIMEOUT_SEC`), иначе мягкая деградация недостижима:
#: внешний `wait_for` убьёт корутину раньше, чем она успеет вернуть частичный
#: результат (та же грабля, что у синков рекламы — см. learnings).
DEFAULT_BUDGET_SEC = 200.0

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


def _finished_expr() -> Any:
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
    stale_before = case(
        (age_days <= _REFRESH_LADDER[0][0], now - _REFRESH_LADDER[0][1]),
        else_=ladder,
    )

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
                        ~_finished_expr(),
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
    for item in payload.get("statuses") or []:
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
        .where(WbFbsOrder.id == order_pk)
        .values(
            history_synced_at=utcnow(),
            delivery_date_plan=_parse_dt(payload.get("deliveryDate")),
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
    queue = await _pick_orders(db, project_id, limit)
    stats = {"asked": 0, "orders": 0, "rows": 0, "failed": 0}
    if not queue:
        return stats

    deadline = time.monotonic() + budget_sec
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
            logger.warning("FBS история: лимит кабинета, пауза %s c", pause)
            await asyncio.sleep(pause)
            try:
                payload = await client.fetch_order_history(wb_order_id)
            except WbPortalError as retry_error:
                stats["failed"] += 1
                logger.warning("FBS история: задание %s — %s", wb_order_id, retry_error)
                continue
        except WbSessionExpired:
            logger.warning(
                "FBS история: сессия кабинета протухла на задании %s — прогон остановлен "
                "(забрано %s из %s)",
                wb_order_id,
                stats["orders"],
                len(queue),
            )
            await db.commit()
            raise
        except WbPortalError as e:
            stats["failed"] += 1
            logger.warning("FBS история: задание %s — %s", wb_order_id, e)
            continue

        stats["rows"] += await _store(db, project_id, order_pk, payload)
        stats["orders"] += 1
        # Коммитим пачкой: прогон длинный, и обрыв не должен обнулять работу.
        if stats["orders"] % 25 == 0:
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
    rows, first_at = (
        await db.execute(
            select(func.count(), func.min(WbFbsOrderHistory.at)).where(
                WbFbsOrderHistory.project_id == project_id
            )
        )
    ).one()
    return {
        "orders_total": int(orders_total or 0),
        "orders_covered": int(orders_done or 0),
        "rows": int(rows or 0),
        "since": first_at,
    }
