# ruff: noqa: RUF001, RUF002, RUF003
"""
Распределённые локи домена WB FBS.

Зачем отдельный модуль: трансляцию остатков запускают ДВЕ точки входа —
кнопка «Передать остатки» (api-контейнер) и джоб раз в 3 минуты (worker).
`asyncio.Lock` здесь бесполезен: локи in-process, а контейнера два. Лок обязан
браться там, где начинается сама трансляция (`stock_service.push_stocks`), а не
у одного из вызывающих — односторонний мьютекс не исключает ничего.
"""

import asyncio
import logging
from uuid import uuid4

logger = logging.getLogger("dds.wb_fbs")

#: Имя лока трансляции → ключ `wb_fbs:push_lock:{project_id}`.
PUSH_LOCK_NAME = "push_lock"
#: Имя лока списания проданного из ledger'а → `wb_fbs:writeoff_lock:{project_id}`.
#: Точек входа тоже две: «Передать поставку» (api-контейнер) зовёт списание
#: синхронно, джоб статусов (worker) — раз в 5 минут. Без лока оба читают
#: `written_off_at IS NULL`, оба считают `new_qty = quantity − 1` в Python и
#: пишут литерал — классический lost update: два движения OUTBOUND, а остаток
#: уменьшился на единицу.
WRITEOFF_LOCK_NAME = "writeoff_lock"
#: TTL строго больше бюджета джоба статусов (`ORDER_STATUSES_TIMEOUT_SEC` = 300).
WRITEOFF_LOCK_TTL_SEC = 420
#: Лок синка учётного зеркала сборки FBS → `wb_fbs:assembly_mirror:{project_id}`.
#: Точек входа две в одном worker'е: джоб статусов (5 мин) и джоб поставок
#: (15 мин) — раз в 15 минут пересекаются, и проигравший ловил бы StaleDataError
#: на пересборке состава (`mirror.items = […]`), роняя весь свой тик в rollback.
MIRROR_LOCK_NAME = "assembly_mirror"
#: Синк — чистая работа с БД (без HTTP), 120 с хватает с запасом даже бэкфиллу.
MIRROR_LOCK_TTL_SEC = 120
#: TTL лока обязан быть строго БОЛЬШЕ бюджета джоба пуша
#: (`scheduler/jobs/wb_fbs.py:STOCK_PUSH_TIMEOUT_SEC` = 300 c на проект).
#: Прежние 180 c протухали ПОСРЕДИ прогона: лок снимался сам, следующий тик
#: заходил в критическую секцию, и два PUT по одному складу давали гонку
#: «кто последний» на живых остатках — ровно то, от чего лок и ставился.
#: Запас 120 c — на финализацию журналов и верификацию последнего чанка.
#: Импорт константы джоба сюда не тащим (цикл services↔scheduler) — связь
#: держит тест tests/test_wb_fbs_locks.py::TestLockTtlVsJobBudget.
PUSH_LOCK_TTL_SEC = 420
#: Бюджет ОДНОГО прогона трансляции — общий контракт обеих точек входа.
#: Джоб держит его сам (`asyncio.wait_for(handler, STOCK_PUSH_TIMEOUT_SEC)`),
#: ручная кнопка — через `routers/wb_fbs._push_stocks_bg`. Без него прогон
#: с `force=true` по нескольким складам (все позиции, PUT+верификация на каждую
#: 1000, паузы по 429 до 60 c × 2 ретрая) переживал бы TTL: лок снимается сам,
#: ближайший тик джоба входит в критическую секцию, два PUT по одному складу
#: дают гонку «кто последний», а `qty_sent` фиксирует чужой прогон.
#: Инвариант PUSH_RUN_BUDGET_SEC < PUSH_LOCK_TTL_SEC — tests/test_wb_fbs_locks.py.
PUSH_RUN_BUDGET_SEC = 300
#: Имя лока догона истории из кабинета → `wb_fbs:order_history:{project_id}`.
#: 🔴 Точек входа две: ручка `/orders/history/sync` (api-контейнер) и джоб
#: `wb_fbs_order_history` (worker, каждые 15 мин). Без лока они удваивают темп
#: и выбивают лимит хоста 150/мин — ловилось живьём 30.07, когда два прогона
#: пошли параллельно и посыпались 429. Цена ошибки здесь выше, чем у ключа API:
#: портальная сессия восстанавливается ручным харвестом кук.
ORDER_HISTORY_LOCK_NAME = "order_history"
#: TTL строго больше внешнего таймаута джоба (`ORDER_HISTORY_TIMEOUT_SEC` = 300).
ORDER_HISTORY_LOCK_TTL_SEC = 420

#: Значение-заглушка, когда Redis недоступен: работаем без лока, но помним,
#: что снимать нечего (иначе `release_lock` полезет в мёртвый Redis).
NO_REDIS_TOKEN = "__no_redis__"


def lock_key(name: str, project_id: int) -> str:
    return f"wb_fbs:{name}:{project_id}"


#: Снимаем лок ТОЛЬКО если он всё ещё наш (compare-and-delete). Иначе прогон,
#: переживший TTL, снял бы лок уже следующего владельца.
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


async def acquire_lock(name: str, project_id: int, ttl: int = PUSH_LOCK_TTL_SEC) -> str | None:
    """Взять распределённый лок `SET NX EX`.

    Возвращает токен владения (его же передать в `release_lock`) или `None`,
    если лок занят другим прогоном (ручная кнопка из api-контейнера или
    предыдущий тик джоба). Redis недоступен → работаем без лока: лучше
    оттранслировать остатки без защиты, чем не транслировать вовсе.
    """
    from backend.cache import get_redis

    try:
        r = await get_redis()
        if r is None:
            return NO_REDIS_TOKEN
        token = uuid4().hex
        acquired = await r.set(lock_key(name, project_id), token, nx=True, ex=ttl)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("WB FBS: лок %s недоступен (%s) — прогон без лока", lock_key(name, project_id), e)
        return NO_REDIS_TOKEN
    return token if acquired else None


async def is_locked(name: str, project_id: int) -> bool:
    """Занят ли лок ПРЯМО СЕЙЧАС — только для подсказки в UI (кнопка → 409).

    Настоящее взаимное исключение даёт `acquire_lock` внутри трансляции; здесь
    возможен TOCTOU, и это нормально: худший исход — пользователь увидит «уже
    идёт» и нажмёт кнопку ещё раз.
    """
    from backend.cache import get_redis

    try:
        r = await get_redis()
        if r is None:
            return False
        return bool(await r.exists(lock_key(name, project_id)))
    except asyncio.CancelledError:
        raise
    except Exception:
        return False


async def release_lock(name: str, project_id: int, token: str) -> None:
    """Снять лок, если он всё ещё наш. Провал не критичен — TTL добьёт."""
    if token == NO_REDIS_TOKEN:
        return

    from backend.cache import get_redis

    try:
        r = await get_redis()
        if r is None:
            return
        await r.eval(_RELEASE_LUA, 1, lock_key(name, project_id), token)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("WB FBS: не снят лок %s (%s) — истечёт по TTL", lock_key(name, project_id), e)
