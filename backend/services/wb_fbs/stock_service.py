# ruff: noqa: RUF001, RUF002, RUF003
"""
Формула FBS-остатка и трансляция остатков в кабинет WB.

Что здесь считается — для пары (наш склад, номенклатура):

    qty_ledger  = WarehouseStock.quantity          # ГОДНЫЙ; брак (defect_quantity)
                                                   # и in_transit намеренно не входят
    qty_mirror  = Σ max(0, qty_good × units_per_box − недоступное у ФФ)
                                                   # зеркало WMS провайдера, только то,
                                                   # что реально отгрузим: см. `_net_mirror`
    reserved    = резерв активных заявок на сборку (PENDING/IN_PROGRESS/READY/
                  VEHICLE_ASSIGNED; PRE_DISTRIBUTED исключён — см. warehouse_stock_engine)
                  СНИМАЕТСЯ С ОБЕИХ СТОРОН ДО выбора источника (`_free_sides`),
                  иначе на складе, где WMS уже отобрал товар, вычет идёт дважды
    qty_source  = по WbFbsWarehouse.stock_source от УЖЕ свободных сторон:
                  ledger | ff_mirror | min_of_both
    buf_pct     = ceil(qty_source × safety_stock_pct / 100)   # ПО КАЖДОМУ складу

    qty_computed(стр.) = Σ по складам max(0, qty_source − buf_pct)
                     Черновики сборки НЕ вычитаются: обязательство — только заявка.
    override           = ручное количество ПО ТОВАРУ на этом складе продавца
                         (`WbFbsStockOverride.qty`; строки нет — ограничения нет)
    available(строка)  = override == 0   → 0            «не отдавать»
                         override > 0    → min(qty_computed, override)  «потолок»
                         override нет    → qty_computed
    ФБО-гейт(строка)   = fbo_max_qty задан и fbo_qty > fbo_max_qty → 0
                         («отдаём в FBS только то, чего нет на складах WB»)
    amount(chrtId)     = max(0, Σ available по строкам chrtId
                                − открытые FBS-задания этого chrtId
                                − safety_stock_abs)          # абсолют ОДИН раз
    потолок ПОЗИЦИИ (Σ ручных количеств) → amount = min(amount, потолок)
    при max_qty_per_sku > 0 → amount = min(amount, max_qty_per_sku)
    Открытые задания, абсолютный буфер, потолок ручных количеств и max_qty_per_sku
    применяются ПОСЛЕ схлопывания по chrtId: позиция для WB — это chrtId, а один
    chrtId может стоять у нескольких баркодов.

Инварианты, которые легко потерять:
  • Обе стороны источника — то, что РЕАЛЬНО МОЖНО ОТГРУЗИТЬ. Наш ledger таким
    приходит сам (`quantity` без `defect_quantity`), из зеркала ФФ недоступное
    вычитается явно (`_net_mirror` поверх
    `fulfillment_service.ff_unavailable_by_nomenclature`): битое, собранное под
    чужую отгрузку, идущее в приёмке. Собранное под НАШИ активные заявки туда не
    входит — его убирает `reserved`, иначе вычли бы дважды.
    Недоступное ≠ брак: у migfull поля брака в API нет вовсе, и в заблокированном
    лежит в том числе товар на сборке. Поэтому колонка «Брак» показывает только
    ИЗМЕРЕННЫЙ брак — цифру провайдера, где он её считает, иначе наш
    `defect_quantity` (`_row_defect`); складывать их нельзя, это один товар.
  • «Заявка на сборку вычитает остаток из FBS» работает САМО: reserved считается
    на лету из активных заявок, отдельного поля резерва в БД нет.
  • Позиции без `Nomenclature.chrt_id` физически не транслируются (с 09.02.2026
    ключ методов остатков — chrtId, не баркод). Молча терять их нельзя:
    `blocked_reason="no_chrt"` в превью + счётчик `rows_no_chrt` в журнале.
  • Один chrtId может стоять у НЕСКОЛЬКИХ баркодов (many-to-one) — перед отправкой
    строки схлопываются по chrtId с суммированием, иначе WB получил бы дубликаты
    ключа в одном PUT, а `wb_fbs_stock_states` — конфликт уникальности. По той же
    причине потолок `max_qty_per_sku`, абсолютный буфер и вычет открытых заданий
    применяются ПОСЛЕ схлопывания (`_apply_chrt_limits`): SKU для WB — это chrtId,
    и вычет на отдельном баркоде сумму не удержал бы. Построчный вычет открытых
    заданий вдобавок гасился клампом `max(0, …)` — 12 проданных единиц на строке
    с нулевым остатком просто исчезали, и товар уезжал в WB второй раз.
  • Номенклатура с открытыми заданиями, но БЕЗ строки `WarehouseStock`, обязана
    попасть в выборку: иначе её `fbs_open` не вычитается вообще.
  • `safety_stock_abs` — «держим N штук про запас» на позицию (chrtId), а не на
    каждую привязку склада и не на каждый баркод: вычитается один раз из суммы,
    иначе N привязок / N баркодов съедали бы N×abs.
  • Ручное количество — ПОТОЛОК желания, а не назначенный остаток: канон
    владельца «фиксированное, но не больше чем свободных остатков от ФФ».
    `min` только режет и никогда не поднимает выдачу выше рассчитанной — иначе
    WB продолжит продавать то, чего физически нет. Задаётся ПО ТОВАРУ (одна
    строка `wb_fbs_stock_overrides` = один товар на одном складе продавца);
    очистка поля = удаление строки, ограничение снимается.
  • Ручное количество накладывается ДВАЖДЫ: построчно в `_build_row` (`qty_available`
    честен и для строк без chrtId, которые в WB не уезжают) и на
    сумму позиции в `_apply_chrt_limits._group_override_limit` — в WB уходит
    сумма группы по chrtId, и построчный `min` её не удерживает.
  • «Не отдавать» (`qty = 0`) отправляет на WB ИМЕННО НОЛЬ, а не выкидывает
    строку из пуша: выброшенная строка оставила бы у WB последний известный
    остаток, и он продолжил бы продавать запрещённый товар.
  • FBO-гейт (`fbo_max_qty`) сравнивает с ДОСТУПНЫМ остатком на складах WB,
    посчитанным БЕЗ сгоревших (🔥 «остатки не учитывать»), исключённых и
    транзитных псевдо-складов. Пустое зеркало FBO даёт `fbo_qty = None`, и
    гейт не применяется вовсе: с нулём вместо None порог 0 пропустил бы в FBS
    весь каталог.
  • Позиции, ушедшие в НОЛЬ, обязаны отправляться: иначе WB продолжит продавать то,
    чего нет. Дельта считается против `WbFbsStockState.qty_sent`, а сам набор строк
    включает состояния прошлых пушей — даже если строка склада уже исчезла.
  • Дельта СЛЕПА к тому, что мы никогда не отправляли: позиция с нашим нулём и
    без записи в `wb_fbs_stock_states` не уезжает вовсе. Остаток, проставленный
    в кабинете руками ДО подключения, так и продолжит продаваться — его ловит
    первичная сверка (`reconcile_warehouse` / `apply_reconcile`, «захват» склада):
    спрашиваем у WB фактические остатки по ВСЕЙ нашей номенклатуре и обнуляем то,
    чем мы не управляем.
  • Дельта СЛЕПА и к обратному расхождению: если `qty_sent` совпал с расчётом,
    а в кабинете стоит другая цифра (продавец правил руками) или ноль, пуш
    решит «не изменилось» и не отправит позицию НИКОГДА. Лечит это сверка:
    `_realign_mismatch_states` пишет в базу дельты прочитанное из WB значение,
    после чего ближайший обычный пуш отправляет верное число. Обнулять такие
    позиции нельзя — товар там живой.
  • WB отвечает 204 на PUT, даже когда остаток НЕ обновился (имена полей не
    валидируются) → после каждого PUT обязательна верификация POST /stocks.
    При расхождении в `qty_sent` пишется ПОДТВЕРЖДЁННОЕ значение, а не то, что
    отправляли: иначе следующая дельта решит «не изменилось» и фантомный остаток
    останется у WB навсегда.
  • Трансляция идёт под распределённым локом `wb_fbs:push_lock:{project_id}`:
    кнопка живёт в api-контейнере, джоб — в worker, `asyncio.Lock` тут бесполезен.
  • По ЗАВЕДОМО неполным данным не транслируем: усечённая выборка ledger'а
    отправила бы обрезанный хвост нулями и стёрла товар с витрины WB
    (`FbsStockDataTooLarge` вместо warning'а — см. `_load_ledger`).
  • В WB не ходим с открытой транзакцией: `db.commit()` до HTTP.
"""

import asyncio
import logging
import math
import re
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import case, delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from backend.cache import cached, invalidate_cache
from backend.models.cost import Nomenclature
from backend.models.fulfillment import FulfillmentStock
from backend.models.integrations import WbWarehouseRemains, WbWarehouseStock
from backend.models.refs import ProductSubcategory, ProductSubcategoryMap
from backend.models.warehouse import WarehouseStock
from backend.models.wb_fbs import (
    FBS_OPEN_STATUSES,
    FbsPushStatus,
    FbsStockSource,
    FbsWarehouseMode,
    WbFbsOrder,
    WbFbsStockOverride,
    WbFbsStockPush,
    WbFbsStockState,
    WbFbsWarehouse,
    WbFbsWarehouseLink,
)
from backend.schemas.wb_fbs import FbsOverrideSet
from backend.services import fulfillment_service
from backend.services.wb_fbs import orders_service, warehouse_service
from backend.services.wb_fbs.contour import contour_condition
from backend.services.wb_fbs.locks import PUSH_LOCK_NAME, acquire_lock, release_lock
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

CACHE_STOCK_PREVIEW = warehouse_service.CACHE_STOCK_PREVIEW

#: Потолок выборки остатков — страховка, а НЕ тихое усечение: шире потолка
#: прогон отказывается работать (`FbsStockDataTooLarge`).
_MAX_LEDGER_ROWS = 50_000
#: Потолок привязок склад WB ↔ наш склад, читаемых за раз.
_MAX_LINK_ROWS = 2_000
#: WB: 1..1000 элементов в одном PUT/POST /stocks.
_WB_CHUNK = 1000
#: WB: amount ≤ 100000.
_WB_MAX_AMOUNT = 100_000
#: Чанк IN-списка (asyncpg: ≤32767 bind-параметров на statement).
_IN_CHUNK = 5_000
#: Чанк UPSERT состояний (10 колонок × 2000 = 20k параметров).
_STATE_CHUNK = 2_000
#: Потолок строк номенклатуры первичной сверки: каждые 1000 chrtId — один POST
#: /stocks, то есть 20 000 строк ≈ 20 запросов из бакета 300/мин.
_MAX_RECONCILE_ROWS = 20_000

BLOCKED_NO_CHRT = "no_chrt"
#: Ручное количество 0 — «не отдавать этот товар вовсе».
BLOCKED_OVERRIDE_ZERO = "override_zero"
#: Позиция придержана FBO-гейтом: на складах WB её ещё достаточно.
BLOCKED_FBO_IN_STOCK = "fbo_in_stock"

#: Все коды `blocked_reason` — один список для теста контракта фронт ↔ бэкенд.
#: Тот же приём, что и у `RECONCILE_REASONS` ниже: промах фронтового словаря
#: `BLOCKED_REASON_LABEL` невидим в рантайме (сработает фолбэк «показать код как
#: есть»), и в колонке «Причина» — а заодно в Excel-выгрузке — вылезает машинный
#: код. Держится парой тестов: `tests/test_wb_fbs_overrides.py` и
#: `src/__tests__/lib/fbsStockOverride.test.ts`.
BLOCKED_REASONS = (BLOCKED_NO_CHRT, BLOCKED_OVERRIDE_ZERO, BLOCKED_FBO_IN_STOCK)

#: Псевдо-склады отчёта WB «Остатки на складах»: итоговая строка дублирует сумму
#: реальных складов, «в пути» — товар в межскладском транзите. Ни то, ни другое
#: не является доступным FBO-остатком (см. warehouse_stock_engine, те же имена).
_FBO_TOTAL_ROW = "Всего находится на складах"
#: Маркер транзитных строк («В пути до получателей», «В пути возвраты…»).
#: Сравнение через `casefold()`, а не `lower()` — важна кириллица.
_FBO_TRANSIT_MARK = "в пути"
#: Потолок числа имён складов WB, читаемых для фильтрации (их десятки).
_MAX_FBO_WAREHOUSES = 500

_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")

#: Виды строк сверки (зеркало `FbsReconcileRow.kind`).
RECONCILE_UNMANAGED = "unmanaged"
RECONCILE_MISMATCH = "mismatch"

#: Причины «мы этой позицией не управляем» — СТАБИЛЬНЫЕ МАШИННЫЕ КОДЫ, а не
#: русские фразы: человеческий текст живёт ровно в одном месте — во фронтовом
#: словаре `RECONCILE_REASON_LABEL` (`fbsReconcile.tsx`). Фраза в API залипала бы
#: в Excel-выгрузке, не переводилась бы и молча расходилась бы со словарём
#: (пользователь по этой колонке решает: обнулять или чинить привязку).
#: Меняешь код здесь — правь словарь там же; контракт закреплён тестами с обеих
#: сторон (`tests/test_wb_fbs_reconcile.py`, `src/__tests__/lib/fbsReconcile.test.ts`).
REASON_NO_LINKS = "not_linked"
REASON_OVERRIDE_ZERO = "override_zero"
REASON_ZERO = "zero"
REASON_NO_STOCK = "no_stock"
#: Обратное расхождение: в кабинете НОЛЬ, а мы уверены, что этот остаток уже
#: оттранслировали (`qty_sent` совпал с расчётом) — дельта такую позицию
#: не увидит никогда, товар просто не продаётся.
REASON_WB_ZERO = "wb_zero"

#: Все коды причин — один список для тестов контракта фронт ↔ бэкенд.
RECONCILE_REASONS = (REASON_NO_LINKS, REASON_OVERRIDE_ZERO, REASON_ZERO, REASON_NO_STOCK, REASON_WB_ZERO)

#: Склад продавца в обработке у WB — остатки он не примет.
_PROCESSING_MSG = "Склад в обработке у WB (isProcessing) — обновление остатков недоступно"
#: Тумблер трансляции выключен — склад пропускается независимо от режима.
_INACTIVE_MSG = (
    "Трансляция для этого склада выключена — остатки не передаются. "
    "Включите её на вкладке «Склады»."
)
#: Склад в режиме наблюдения: считаем и показываем, но в кабинет не пишем.
_OBSERVE_MSG = (
    "Склад в режиме «Только наблюдение» — остатки в WB не передаются. "
    "Включите режим «Транслировать остатки» в настройках склада."
)


class FbsStockDataTooLarge(Exception):
    """Исходные данные шире потолка выборки — считать остаток нечестно.

    Имя начинается на `Fbs`: роутер переводит такие исключения в 422 с текстом
    наружу (см. `routers/wb_fbs._fbs_errors`), а прогон трансляции финализируется
    в ERROR с этой же причиной.
    """


class FbsReconcileBusy(Exception):
    """Применить сверку нельзя: трансляция остатков уже идёт (лок проекта занят).

    Два одновременных `PUT /stocks` по одному складу — гонка «кто последний»:
    обнуление могло бы затереть только что отправленный живой остаток.
    Роутер переводит это в 409 (см. `routers/wb_fbs.apply_stock_reconcile`).
    """


class FbsWarehouseProcessing(Exception):
    """Склад продавца в обработке у WB — остатки он не примет (роутер → 409).

    Тот же гард, что у пуша (`_push_stocks_locked` пропускает такие склады):
    без него обнуление слало бы PUT'ы, каждый из которых возвращает 409
    StoreIsProcessing и стоит 10 запросов бакета.
    """


class FbsWarehouseObserveMode(Exception):
    """Склад продавца в режиме «Только наблюдение» — писать в кабинет нельзя (роутер → 409).

    Тот же гард, что у пуша (`_push_stocks_locked`), но для обнуления: `observe` —
    дефолт модели и обещание фичи «подключение склада НЕ перезаписывает ничего
    в кабинете, пока трансляцию явно не включили». Обнуление — такая же запись
    (`PUT /stocks` с amount 0), причём необратимая для витрины: товар, который
    продавец ведёт руками, снимается с продажи. ЧТЕНИЕ сверки (`GET`) при этом
    остаётся доступным — оно ничего не меняет.
    """


class FbsReconcileNoLinks(Exception):
    """Обнулять нечем: склад продавца не привязан ни к одному нашему складу.

    Без привязок наш расчёт ПУСТ, и сверка честно помечает неуправляемым ВЕСЬ
    ассортимент кабинета — кнопка обнуления снесла бы весь FBS-каталог. Это
    ровно «заведомо неполные данные» из шапки модуля: по ним не транслируем.
    Чтение сверки при этом полезно и не блокируется.
    """


def _chunked(seq: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for i in range(0, len(seq), size):
        yield list(seq[i : i + size])


# ─── Загрузка слагаемых (всё батчами, без N+1) ───────────────────────────────


async def _load_ledger(
    db: AsyncSession, project_id: int, warehouse_ids: list[int]
) -> tuple[dict[int, dict[int, int]], dict[int, int], dict[int, str]]:
    """Наш ledger: ({nom_id: {wh_id: годный}}, {nom_id: брак}, {nom_id: barcode}).

    Берём ВСЕ строки складов, включая нулевые: позиция, упавшая в ноль, обязана
    доехать до WB как 0, а фильтр `quantity > 0` её бы потерял.

    Шире потолка — `FbsStockDataTooLarge`, а не warning с усечением. Усечение
    было опаснее пустого прогона: обрезанные позиции всё равно попадали в набор
    строк (через состояния прошлых пушей), получали `ledger={}` → amount 0 и
    уезжали в WB нулями. Товар исчезал с витрины, а `qty_sent=0` делал это
    состояние «нормой» — следующий прогон уже не видел изменений.
    """
    if not warehouse_ids:
        return {}, {}, {}
    result = await db.execute(
        select(
            WarehouseStock.warehouse_id,
            WarehouseStock.nomenclature_id,
            WarehouseStock.barcode,
            WarehouseStock.quantity,
            WarehouseStock.defect_quantity,
        )
        .where(
            WarehouseStock.project_id == project_id,
            WarehouseStock.warehouse_id.in_(warehouse_ids),
        )
        .order_by(WarehouseStock.nomenclature_id, WarehouseStock.warehouse_id)
        # +1 отличает «ровно потолок» (данные полные) от «есть хвост» (усечение).
        .limit(_MAX_LEDGER_ROWS + 1)
    )
    per_wh: dict[int, dict[int, int]] = {}
    defects: dict[int, int] = {}
    barcodes: dict[int, str] = {}
    rows = result.all()
    if len(rows) > _MAX_LEDGER_ROWS:
        logger.error("FBS stock: ledger exceeds %s rows (project=%s)", _MAX_LEDGER_ROWS, project_id)
        raise FbsStockDataTooLarge(
            f"Остатки привязанных складов не помещаются в потолок выборки "
            f"({_MAX_LEDGER_ROWS} строк) — трансляция отменена. По неполным данным "
            "хвост позиций уехал бы в WB нулями и товар пропал бы с витрины. "
            "Сузьте список привязанных складов или обратитесь за поднятием потолка."
        )
    for row in rows:
        per_wh.setdefault(row.nomenclature_id, {})[row.warehouse_id] = int(row.quantity or 0)
        defects[row.nomenclature_id] = defects.get(row.nomenclature_id, 0) + int(row.defect_quantity or 0)
        if row.barcode:
            barcodes.setdefault(row.nomenclature_id, row.barcode)
    return per_wh, defects, barcodes


async def _load_mirror(db: AsyncSession, project_id: int, warehouse_ids: list[int]) -> dict[int, dict[int, int]]:
    """Зеркало ФФ: {nom_id: {wh_id: годная РОССЫПЬ в штуках}}.

    Только россыпь. Короб-строки (`base_barcode` заполнен) в зеркало не входят:
    маркетплейс покупает штуку, а короб продать нельзя, пока его не вскрыли и не
    приняли поштучно. Считая короба штуками (`qty_good × units_per_box`, как в
    `list_stocks`), экран обещал вчетверо больше отгружаемого — на натали 29 776
    против 7 041 реальных. Сколько лежит коробами, отдельно считает
    `fulfillment_service.ff_boxed_by_nomenclature` — это рабочий список «что вскрыть».

    Строки без номенклатуры (неопознанный товар провайдера) в формулу не входят:
    транслировать их всё равно нечем.
    """
    if not warehouse_ids:
        return {}
    # Короб-строки НЕ фильтруем в WHERE, а обнуляем в SUM. Разница принципиальная:
    # `_source_qty` трактует отсутствие ключа как «зеркала нет» и падает на наш
    # учёт, и позиция, которая у провайдера лежит ЦЕЛИКОМ коробами, уехала бы в WB
    # по нашим книгам — ровно то, чего короб-правило и должно не допустить.
    # Ключ есть → «провайдер эту позицию знает», значение 0 → «россыпью нет ничего».
    result = await db.execute(
        select(
            FulfillmentStock.warehouse_id,
            FulfillmentStock.nomenclature_id,
            func.sum(
                case(
                    (
                        FulfillmentStock.base_barcode.is_(None),
                        FulfillmentStock.qty_good * FulfillmentStock.units_per_box,
                    ),
                    else_=0,
                )
            ).label("qty"),
        )
        .where(
            FulfillmentStock.project_id == project_id,
            FulfillmentStock.warehouse_id.in_(warehouse_ids),
            FulfillmentStock.nomenclature_id.is_not(None),
        )
        .group_by(FulfillmentStock.warehouse_id, FulfillmentStock.nomenclature_id)
    )
    mirror: dict[int, dict[int, int]] = {}
    for row in result.all():
        mirror.setdefault(row.nomenclature_id, {})[row.warehouse_id] = int(row.qty or 0)
    return mirror


def _net_mirror(
    mirror: dict[int, dict[int, int]], unavailable: dict[int, dict[int, int]]
) -> dict[int, dict[int, int]]:
    """Зеркало ФФ за вычетом недоступного к отгрузке (клампится нулём).

    В WB уходит обещание отгрузить. Всё, что у ФФ лежит, но взять и отправить
    покупателю нельзя — битое, собранное под чужую отгрузку, идущее в приёмке —
    таким обещанием быть не может: заказ придёт, а собрать будет нечем.
    Что именно недоступно, знает домен ФФ
    (`fulfillment_service.ff_unavailable_by_nomenclature`) — здесь только вычитание.

    Двойного вычета нет: собранное под НАШИ активные заявки в `unavailable` не
    входит, его убирает сам расчёт (`reserved` в `_sum_by_warehouse`).

    Наш ledger в вычете не нуждается: `WarehouseStock.quantity` — годный, брак
    лежит отдельным счётчиком `defect_quantity` и в источник не входит.
    """
    if not unavailable:
        return mirror
    return {
        nom_id: {
            wid: max(0, qty - int(unavailable.get(wid, {}).get(nom_id, 0)))
            for wid, qty in per_wh.items()
        }
        for nom_id, per_wh in mirror.items()
    }


def _row_defect(
    nom_id: int | None,
    mirror: dict[int, dict[int, int]],
    ff_defect: dict[int, dict[int, int]],
    ledger_defects: dict[int, int],
) -> int:
    """Брак строки превью: цифра провайдера, если он её отдаёт, иначе наш ledger.

    Складывать их нельзя — это ДВА взгляда на один и тот же физический брак.
    Где провайдер брак СЧИТАЕТ (skladbot), авторитет у него: битый товар лежит у
    него, а наш `defect_quantity` наполняется документами и отстаёт от факта.
    Где не считает (migfull, wmscelicom — поля в API нет), показываем свой:
    выводить брак из заблокированного нельзя, там лежит в том числе товар на
    сборке (см. `fulfillment_service.ff_defect_by_nomenclature`).
    """
    if nom_id is None:
        return 0
    per_wh = mirror.get(nom_id) or {}
    from_provider = sum(int(ff_defect.get(wid, {}).get(nom_id, 0)) for wid in per_wh)
    return from_provider or int(ledger_defects.get(nom_id, 0))


async def _load_open_fbs_by_wh(
    db: AsyncSession, project_id: int, wb_warehouse_ids: list[int]
) -> dict[int, dict[int, int]]:
    """Открытые задания с разбивкой по складам продавца: {wb_wh_id: {nom_id: штук}}.

    Разбивка нужна обратному гейту: обязательство висит на складе ПРОДАВЦА, а
    наших складов, кормящих его, может быть несколько (домен разрешает N:1).
    Фильтр контура обязателен — задания песочницы не должны вычитаться из
    боевого остатка (services/wb_fbs/contour.py).
    """
    if not wb_warehouse_ids:
        return {}
    result = await db.execute(
        select(
            WbFbsOrder.wb_warehouse_id,
            WbFbsOrder.nomenclature_id,
            func.count().label("qty"),
        )
        .where(
            WbFbsOrder.project_id == project_id,
            WbFbsOrder.wb_warehouse_id.in_(wb_warehouse_ids),
            WbFbsOrder.supplier_status.in_(FBS_OPEN_STATUSES),
            # WB-отмена до сборки оставляет supplier_status='new' навсегда —
            # без этого условия мёртвое задание вечно держит остаток.
            orders_service.alive_condition(),
            WbFbsOrder.nomenclature_id.is_not(None),
            contour_condition(WbFbsOrder.raw),
        )
        .group_by(WbFbsOrder.wb_warehouse_id, WbFbsOrder.nomenclature_id)
    )
    out: dict[int, dict[int, int]] = {}
    for row in result.all():
        out.setdefault(int(row.wb_warehouse_id), {})[int(row.nomenclature_id)] = int(row.qty or 0)
    return out


async def _load_open_fbs(db: AsyncSession, project_id: int, wb_warehouse_ids: list[int]) -> dict[int, int]:
    """Открытые сборочные задания: {nom_id: штук}. Одно задание = одна единица."""
    totals: dict[int, int] = {}
    for per_nom in (await _load_open_fbs_by_wh(db, project_id, wb_warehouse_ids)).values():
        for nom_id, qty in per_nom.items():
            totals[nom_id] = totals.get(nom_id, 0) + qty
    return totals


async def _load_states(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> dict[int, WbFbsStockState]:
    """Последнее переданное состояние: {chrt_id: WbFbsStockState}."""
    result = await db.execute(
        select(WbFbsStockState)
        .where(
            WbFbsStockState.project_id == project_id,
            WbFbsStockState.wb_warehouse_id == wb_warehouse_id,
        )
        .order_by(WbFbsStockState.chrt_id)
        .limit(_MAX_LEDGER_ROWS)
    )
    return {int(s.chrt_id): s for s in result.scalars().all()}


async def _load_nomenclature(db: AsyncSession, project_id: int, nom_ids: Iterable[int]) -> dict[int, Any]:
    """{nom_id: строка номенклатуры} — barcode/chrt_id/артикулы/разрезы одним IN-запросом.

    `brand` и `subject` берутся здесь же (а не отдельным запросом): они нужны и
    строке превью для группировки и фильтра по категории на вкладке «Остатки».
    """
    ids = sorted({int(i) for i in nom_ids})
    out: dict[int, Any] = {}
    for chunk in _chunked(ids, _IN_CHUNK):
        result = await db.execute(
            select(
                Nomenclature.id,
                Nomenclature.barcode,
                Nomenclature.chrt_id,
                Nomenclature.article_seller,
                Nomenclature.article_wb,
                Nomenclature.brand,
                Nomenclature.subject,
            ).where(Nomenclature.project_id == project_id, Nomenclature.id.in_(chunk))
        )
        for row in result.all():
            out[int(row.id)] = row
    return out


async def _load_subcategories(
    db: AsyncSession, project_id: int, nm_ids: Iterable[int | None]
) -> dict[int, tuple[int, str | None]]:
    """Товарная под-категория по nm_id: {nm_id: (subcategory_id, name)}.

    Товарная категория в проекте — это `product_subcategories` + связка
    `product_subcategory_map` по `nm_id` (= `Nomenclature.article_wb`).
    С финансовой `category_ref` (категории ДДС) её путать нельзя: та к товарам
    отношения не имеет.

    Мягко удалённые справочные категории отсекаются: строка связки живёт дальше,
    и без фильтра превью показывало бы имя удалённой категории.
    """
    ids = sorted({int(i) for i in nm_ids if i})
    if not ids:
        return {}
    out: dict[int, tuple[int, str | None]] = {}
    for chunk in _chunked(ids, _IN_CHUNK):
        result = await db.execute(
            select(
                ProductSubcategoryMap.nm_id,
                ProductSubcategory.id,
                ProductSubcategory.name,
            )
            .join(ProductSubcategory, ProductSubcategory.id == ProductSubcategoryMap.subcategory_id)
            .where(
                ProductSubcategoryMap.project_id == project_id,
                ProductSubcategory.project_id == project_id,
                ProductSubcategory.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
                ProductSubcategoryMap.nm_id.in_(chunk),
            )
            # UNIQUE (project_id, nm_id) → строк не больше, чем запрошенных nm_id.
            .limit(len(chunk))
        )
        for nm_id, subcategory_id, name in result.all():
            out[int(nm_id)] = (int(subcategory_id), name)
    return out


async def _load_overrides(
    db: AsyncSession, project_id: int, wb_warehouse_id: int, nom_ids: Iterable[int]
) -> dict[int, int]:
    """Ручные количества склада продавца: {nom_id: qty} — один батч, без N+1.

    Строки нет — ограничения нет (ключ просто отсутствует в словаре). `qty = 0`
    здесь легально и означает «не отдавать»: отличать «ноль» от «не задано»
    обязаны все читатели, поэтому наружу уходит именно `dict`, а не `defaultdict`.
    """
    ids = sorted({int(i) for i in nom_ids})
    if not ids:
        return {}
    out: dict[int, int] = {}
    for chunk in _chunked(ids, _IN_CHUNK):
        result = await db.execute(
            select(WbFbsStockOverride.nomenclature_id, WbFbsStockOverride.qty).where(
                WbFbsStockOverride.project_id == project_id,
                WbFbsStockOverride.wb_warehouse_id == wb_warehouse_id,
                WbFbsStockOverride.nomenclature_id.in_(chunk),
            )
        )
        for nom_id, qty in result.all():
            out[int(nom_id)] = max(0, int(qty or 0))
    return out


async def _load_reserves(db: AsyncSession, project_id: int, warehouse_ids: list[int]) -> dict[int, dict[int, int]]:
    """Резерв заявок на сборку: {warehouse_id: {nomenclature_id: qty}}.

    Черновики сборки в расчёт FBS НЕ входят (решение владельца 2026-07-24):
    черновик — это намерение логиста, а не обязательство, и он может висеть
    часами, всё это время придерживая товар от продажи. Обязательством считается
    только заявка на сборку — она и вычитается здесь.

    Импорт локальный и это НАМЕРЕННО: обратный гейт добавляет вызов
    `get_open_fbs_qty` внутрь `warehouse_stock_engine`, и модульный импорт
    в обе стороны замкнул бы цикл.
    """
    from backend.services.warehouse_stock_engine import _get_reserved_map_batch

    return await _get_reserved_map_batch(db, project_id, set(warehouse_ids)) if warehouse_ids else {}


# ─── Остаток на складах WB (FBO) ─────────────────────────────────────────────
#
# Нужен гейту «отдаём в FBS только то, чего нет на FBO». Источник — зеркало
# отчёта кабинета `wb_warehouse_remains` (синк ежечасный, гранулярность —
# БАРКОД, совпадает с кабинетом 1:1). Если по проекту нет НИ ОДНОЙ строки
# remains (синк ещё не прошёл), берём суточное `wb_warehouse_stocks` по nm_id —
# без фолбэка гейт молча считал бы, что на WB пусто, и отдал бы в FBS всё.


def _strip_parens(name: str) -> str:
    """Базовое имя склада без скобочного суффикса.

    Ровно как в `assembly_load_forecast_service` / `settings_service`: настройки
    «исключённые» и «🔥 остатки не учитывать» хранятся БЕЗ «(…)», а WB отдаёт
    сырое «Рязань (Тюшевское)». Обе стороны сравнения обязаны быть одинаковыми.
    """
    return _PARENS_RE.sub("", name or "").strip()


def _is_sorting_centre(name: str) -> bool:
    """Сортировочный центр WB — перевалка, а не склад хранения.

    Товар там уже расписан по заказам и к продаже не доступен, поэтому в
    «остаток на FBO» он входить не должен. Имена: «СЦ Ижевск», «SC Tbilisi» —
    матчим ПРЕФИКС со следующим пробелом, иначе под правило попали бы обычные
    склады, начинающиеся на те же буквы («Сарапул», «Самара»).
    """
    low = (name or "").strip().casefold()
    return low.startswith("сц ") or low.startswith("sc ")


def _fbo_allowed_names(names: Iterable[str], ignored: set[str], excluded: set[str]) -> list[str]:
    """Имена складов WB, чьему остатку верим (для фильтра `IN`).

    Выкидываем: итоговую псевдо-строку (дубль суммы), транзит «в пути» (товар
    едет между складами WB и возвраты от клиентов — к продаже не доступны),
    сортировочные центры (перевалка, товар уже расписан по заказам),
    исключённые склады и сгоревшие (🔥, они же «остатки не учитывать» на вкладке
    «Сборка»): WB продолжает отдавать их остатки как живые, и гейт по ним решил
    бы «на FBO ещё полно» — товар навсегда остался бы вне FBS.
    """
    out: list[str] = []
    for raw in names:
        name = (raw or "").strip()
        if not name or name == _FBO_TOTAL_ROW:
            continue
        if _FBO_TRANSIT_MARK in name.casefold():
            continue
        if _is_sorting_centre(name):
            continue
        base = _strip_parens(name)
        if base in excluded or base in ignored or name in ignored:
            continue
        out.append(name)
    return out


async def _fbo_warehouse_names(db: AsyncSession, project_id: int, *, remains: bool) -> list[str]:
    """Имена складов WB проекта, прошедшие фильтр доверия (может быть пустым).

    Имена читаются отдельным запросом (их десятки), после чего агрегат
    группируется сразу по номенклатуре: иначе пришлось бы тянуть строки
    «товар × склад» и резать их в Python — на большом каталоге это десятки
    тысяч строк ради нескольких десятков имён.
    """
    from backend.services.settings_service import get_excluded_warehouses, get_stock_ignored_set

    model = WbWarehouseRemains if remains else WbWarehouseStock
    result = await db.execute(
        select(model.warehouse_name)
        .where(model.project_id == project_id)
        .distinct()
        .limit(_MAX_FBO_WAREHOUSES)
    )
    names = [str(n) for n in result.scalars().all() if n]
    if not names:
        return []
    ignored = {_strip_parens(n) for n in await get_stock_ignored_set(db, project_id)}
    excluded = {_strip_parens(n) for n in await get_excluded_warehouses(db, project_id)}
    return _fbo_allowed_names(names, ignored, excluded)


async def _load_fbo(db: AsyncSession, project_id: int, nom_ids: Iterable[int]) -> dict[int, int | None] | None:
    """Доступный остаток на складах WB: {nom_id: штук}. `None` — зеркала FBO нет.

    Разница между `None` и `{}` принципиальна: пустое зеркало (синк ещё ни разу
    не прошёл) обязано ОТКЛЮЧАТЬ гейт, а не притворяться нулём — с нулём порог
    `fbo_max_qty = 0` пропустил бы в FBS весь каталог, включая товар, которого
    на нашем складе физически нет. Живое зеркало без строки по позиции — это
    честный ноль (`.get(nom_id, 0)` у вызывающего).

    `None` в ЗНАЧЕНИИ словаря — та же логика на уровне одной позиции: зеркало
    есть, но его гранулярности не хватает, чтобы судить об этом баркоде
    (см. `_sum_fbo`). Гейт по такой позиции не применяется, в превью — прочерк.
    """
    ids = sorted({int(i) for i in nom_ids})
    if not ids:
        return None
    has_remains = (
        await db.execute(
            select(WbWarehouseRemains.id).where(WbWarehouseRemains.project_id == project_id).limit(1)
        )
    ).first() is not None
    has_stocks = (
        False
        if has_remains
        else (
            await db.execute(
                select(WbWarehouseStock.id).where(WbWarehouseStock.project_id == project_id).limit(1)
            )
        ).first()
        is not None
    )
    if not has_remains and not has_stocks:
        return None

    names = await _fbo_warehouse_names(db, project_id, remains=has_remains)
    if not names:
        # Зеркало есть, но всем складам в нём не верим (все исключены/сгорели) —
        # это осознанный ноль, а не «данных нет»: гейт обязан работать.
        return {}
    return await _sum_fbo(db, project_id, ids, names, remains=has_remains)


async def _sum_fbo(
    db: AsyncSession, project_id: int, nom_ids: list[int], names: list[str], *, remains: bool
) -> dict[int, int | None]:
    """Агрегат FBO по номенклатуре: remains — по баркоду (+фолбэк nm_id), stocks — по nm_id.

    `None` в значении — «по этой позиции судить нечем, гейт не применять»
    (см. `_multi_barcode_noms` на фолбэк-ветке).
    """
    out: dict[int, int | None] = {}
    for chunk in _chunked(nom_ids, _IN_CHUNK):
        if remains:
            # Джойн по баркоду точен per-size и не задваивает остаток, когда у
            # одного nm_id несколько карточек (канон warehouse_stock_engine).
            queries = [
                select(Nomenclature.id, func.sum(WbWarehouseRemains.quantity).label("qty"))
                .join(
                    WbWarehouseRemains,
                    (WbWarehouseRemains.barcode == Nomenclature.barcode)
                    & (WbWarehouseRemains.project_id == project_id),
                )
                .where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.id.in_(chunk),
                    WbWarehouseRemains.warehouse_name.in_(names),
                )
                .group_by(Nomenclature.id),
                # Фолбэк-джойн по nm_id — ВТОРАЯ половина канона
                # `warehouse_stock_engine`: строки remains, чей баркод не заведён
                # в номенклатуре (расхождение ШК у нас реальность), иначе
                # теряются целиком, и позиция с живым остатком на FBO уезжает
                # в FBS — ровно тот отказ, от которого гейт защищает.
                # `~exists()` держит ветки непересекающимися: строку, уже учтённую
                # по баркоду, фолбэк не берёт, поэтому суммы не задваиваются.
                _remains_by_nm_query(project_id, chunk, names),
            ]
        else:
            queries = [
                select(Nomenclature.id, func.sum(WbWarehouseStock.quantity).label("qty"))
                .join(
                    WbWarehouseStock,
                    (WbWarehouseStock.nm_id == Nomenclature.article_wb)
                    & (WbWarehouseStock.project_id == project_id),
                )
                .where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.id.in_(chunk),
                    WbWarehouseStock.warehouse_name.in_(names),
                )
                .group_by(Nomenclature.id)
            ]
        for query in queries:
            for nom_id, qty in (await db.execute(query)).all():
                prev = out.get(int(nom_id)) or 0
                out[int(nom_id)] = max(0, prev + int(qty or 0))
        if not remains:
            # `wb_warehouse_stocks` уникальна по (project_id, nm_id, warehouse_name) —
            # размерности в ней нет. У карточки с несколькими баркодами остаток
            # относится ко ВСЕЙ карточке, и раздать его каждому размеру нельзя:
            # размер, которого на FBO уже нет, получил бы чужие штуки и навсегда
            # остался бы вне FBS. Судить нечем → `None`, гейт для таких позиций
            # выключается (как при отсутствии зеркала), остальные считаются как прежде.
            for nom_id in await _multi_barcode_noms(db, project_id, chunk):
                out[nom_id] = None
    return out


async def fbo_by_warehouse(
    db: AsyncSession, project_id: int, nom_ids: Iterable[int]
) -> dict[int, list[tuple[str, int]]]:
    """Остаток FBO В РАЗРЕЗЕ СКЛАДОВ WB: `{nom_id: [(склад, штук), …]}`, по убыванию.

    Тот же фильтр доверия, что и у гейта (`_fbo_allowed_names`): без итоговой
    строки, транзита и возвратов «в пути», без сортировочных центров, без
    исключённых и сгоревших складов. Суммы поэтому сходятся с колонкой «FBO»
    — иначе раскрывающийся список противоречил бы числу над ним.

    Нужен матрице складов: «где именно лежит остаток» — вопрос, на который
    агрегат не отвечает, а вести человека за этим в кабинет WB глупо.
    """
    ids = sorted({int(i) for i in nom_ids})
    if not ids:
        return {}
    has_remains = (
        await db.execute(
            select(WbWarehouseRemains.id).where(WbWarehouseRemains.project_id == project_id).limit(1)
        )
    ).first() is not None
    names = await _fbo_warehouse_names(db, project_id, remains=has_remains)
    if not names:
        return {}

    out: dict[int, dict[str, int]] = {}
    for chunk in _chunked(ids, _IN_CHUNK):
        # Ветки разведены явно: remains джойнится по баркоду (точно per-size),
        # stocks — по nm_id (размерности в нём нет). Общей переменной модели тут
        # не сделать — у `WbWarehouseStock` поля `barcode` попросту нет.
        if has_remains:
            query = (
                select(
                    Nomenclature.id,
                    WbWarehouseRemains.warehouse_name,
                    func.sum(WbWarehouseRemains.quantity).label("qty"),
                )
                .join(
                    WbWarehouseRemains,
                    (WbWarehouseRemains.barcode == Nomenclature.barcode)
                    & (WbWarehouseRemains.project_id == project_id),
                )
                .where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.id.in_(chunk),
                    WbWarehouseRemains.warehouse_name.in_(names),
                )
                .group_by(Nomenclature.id, WbWarehouseRemains.warehouse_name)
            )
        else:
            query = (
                select(
                    Nomenclature.id,
                    WbWarehouseStock.warehouse_name,
                    func.sum(WbWarehouseStock.quantity).label("qty"),
                )
                .join(
                    WbWarehouseStock,
                    (WbWarehouseStock.nm_id == Nomenclature.article_wb)
                    & (WbWarehouseStock.project_id == project_id),
                )
                .where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.id.in_(chunk),
                    WbWarehouseStock.warehouse_name.in_(names),
                )
                .group_by(Nomenclature.id, WbWarehouseStock.warehouse_name)
            )
        for nom_id, wh_name, qty in (await db.execute(query)).all():
            amount = int(qty or 0)
            if amount <= 0:
                continue
            out.setdefault(int(nom_id), {})[str(wh_name)] = (
                out.get(int(nom_id), {}).get(str(wh_name), 0) + amount
            )
    return {
        nom_id: sorted(by_wh.items(), key=lambda kv: -kv[1])
        for nom_id, by_wh in out.items()
    }


def _remains_by_nm_query(project_id: int, chunk: list[int], names: list[str]) -> Any:
    """Остаток remains по строкам, чей баркод НЕ заведён в номенклатуре (джойн по nm_id)."""
    nom_bc = aliased(Nomenclature)
    return (
        select(Nomenclature.id, func.sum(WbWarehouseRemains.quantity).label("qty"))
        .join(
            WbWarehouseRemains,
            (WbWarehouseRemains.nm_id == Nomenclature.article_wb)
            & (WbWarehouseRemains.project_id == project_id),
        )
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.id.in_(chunk),
            WbWarehouseRemains.warehouse_name.in_(names),
            ~exists().where(
                (nom_bc.barcode == WbWarehouseRemains.barcode) & (nom_bc.project_id == project_id)
            ),
        )
        .group_by(Nomenclature.id)
    )


async def _multi_barcode_noms(db: AsyncSession, project_id: int, chunk: list[int]) -> set[int]:
    """Номенклатуры, у которых `article_wb` делится с другими баркодами (размеры одной карточки)."""
    sibling = aliased(Nomenclature)
    query = select(Nomenclature.id).where(
        Nomenclature.project_id == project_id,
        Nomenclature.id.in_(chunk),
        Nomenclature.article_wb.is_not(None),
        exists().where(
            (sibling.project_id == project_id)
            & (sibling.article_wb == Nomenclature.article_wb)
            & (sibling.id != Nomenclature.id)
        ),
    )
    return {int(row[0]) for row in (await db.execute(query)).all()}


# ─── Формула ─────────────────────────────────────────────────────────────────


def _source_qty(stock_source: str, ledger: int, mirror: int | None) -> int:
    """qty_source одного склада по настройке `stock_source`.

    Нет зеркала (склад без интеграции или SKU у провайдера отсутствует) → ledger:
    иначе склады «апл»/«хамза», которые ведутся только документами, обнулились бы.
    """
    if stock_source == FbsStockSource.FF_MIRROR.value:
        return ledger if mirror is None else mirror
    if stock_source == FbsStockSource.MIN_OF_BOTH.value:
        return ledger if mirror is None else min(ledger, mirror)
    return ledger


def _buffer_pct(qty_source: int, pct: Decimal) -> int:
    """Процентная часть буфера ОДНОГО склада (округление вверх).

    Процент — величина относительная, поэтому считается по каждому нашему складу
    от его собственного `qty_source`. Абсолютная часть (`safety_stock_abs`)
    здесь НЕ участвует: она вычитается один раз на позицию (см. `_build_row`).
    """
    # math.ceil на Decimal уже возвращает int — обёртка int() была бы холостой (RUF046).
    return math.ceil(Decimal(max(qty_source, 0)) * Decimal(pct or 0) / Decimal(100))


def _apply_override(qty_computed: int, override_qty: int | None) -> int:
    """Ручное количество поверх расчёта: 0 → 0, N → min(расчёт, N), нет → расчёт.

    Канон владельца дословно: «фиксированное, но не больше чем свободных
    остатков от ФФ». Ручная цифра задаёт ПОТОЛОК желания, физический свободный
    остаток всегда главнее — она не может ПОДНЯТЬ выдачу выше рассчитанной,
    иначе WB продолжит продавать то, чего на складе уже нет.

    `None` (строки нет / поле очистили) — это «ограничения нет», а НЕ ноль:
    очистка поля обязана возвращать позицию к обычному расчёту.
    """
    if override_qty is None:
        return qty_computed
    return max(0, min(qty_computed, int(override_qty)))


def _free_sides(qty_ledger: int, qty_mirror: int | None, reserved: int) -> tuple[int, int | None]:
    """Снять резерв активных заявок на сборку с ОБЕИХ сторон, не вычтя его дважды.

    Резерв — это наше обязательство: товар обещан заявке и продаваться по FBS не
    должен. Раньше он вычитался ПОСЛЕ выбора источника, и на складах, где WMS уже
    отобрал товар под заявку, вычет шёл вторым разом. Прод-кейс wms Домодедово
    27.07.2026 (chashka_vasilki): наш учёт 252, три заявки READY на 108, зеркало
    Целиком 144 — ровно 252 − 108. Формула брала min(252, 144) = 144 и снимала 108
    ещё раз → 36 вместо 144, товар зря снимался с продажи.

    Провайдер-специфики тут нет намеренно: одна и та же Целиком у части позиций
    товар уже отобрала, у части — ещё нет (40 позиций из 81 против 13), и правила
    «этот провайдер всегда вычитает» не существует. Смотрим на факт: насколько
    зеркало НИЖЕ наших книг — ровно столько провайдер из-под резерва уже и забрал,
    остаток резерва снимаем с зеркала сами.

    Возвращает (свободно по нашему учёту, свободно по зеркалу | None).
    """
    ledger_free = max(0, qty_ledger - reserved)
    if qty_mirror is None:
        return ledger_free, None
    already_taken = max(0, qty_ledger - int(qty_mirror))
    return ledger_free, max(0, int(qty_mirror) - max(0, reserved - already_taken))


def _sum_by_warehouse(
    nom_id: int | None,
    barcode: str,
    wh: WbFbsWarehouse,
    warehouse_ids: list[int],
    ledger: dict[int, int],
    mirror: dict[int, int],
    reserved: dict[int, dict[int, int]],
) -> tuple[dict[str, int], bool]:
    """Слагаемые формулы, просуммированные по привязанным складам (+ был ли зеркальный).

    Порядок важен: резерв сборки снимается с КАЖДОЙ стороны ДО выбора источника
    (`_free_sides`), и только потом берётся ledger / зеркало / минимум. Сравнивать
    «наш учёт с обязательствами» против «свободного у провайдера» — сравнивать
    разные величины; именно из этого рождалось расхождение на экране.
    """
    totals = {
        "ledger": 0, "mirror": 0, "ledger_free": 0, "mirror_free": 0,
        "source": 0, "reserved": 0, "buffer": 0, "available": 0,
    }
    has_mirror = False
    for wid in warehouse_ids:
        qty_ledger = int(ledger.get(wid, 0))
        qty_mirror = mirror.get(wid)
        if qty_mirror is not None:
            has_mirror = True
            totals["mirror"] += int(qty_mirror)
        res = int(reserved.get(wid, {}).get(nom_id, 0)) if nom_id else 0
        ledger_free, mirror_free = _free_sides(qty_ledger, qty_mirror, res)
        qty_source = _source_qty(wh.stock_source, ledger_free, mirror_free)
        buf = _buffer_pct(qty_source, wh.safety_stock_pct)
        totals["ledger"] += qty_ledger
        totals["ledger_free"] += ledger_free
        if mirror_free is not None:
            totals["mirror_free"] += mirror_free
        totals["source"] += qty_source
        totals["reserved"] += res
        totals["buffer"] += buf
        totals["available"] += max(0, qty_source - buf)
    return totals, has_mirror


def _build_row(
    nom_id: int | None,
    nom: Any,
    barcode: str,
    wh: WbFbsWarehouse,
    warehouse_ids: list[int],
    ledger: dict[int, int],
    mirror: dict[int, int],
    reserved: dict[int, dict[int, int]],
    fbs_open: int,
    state: WbFbsStockState | None,
    *,
    override_qty: int | None = None,
    fbo_qty: int | None = None,
    subcategory: tuple[int, str | None] | None = None,
) -> dict[str, Any]:
    """Одна строка превью со всей расшифровкой вычетов (см. FbsStockRow).

    Порядок железный: сначала физика (`qty_computed`), потом ручное количество
    и FBO-гейт (`qty_available`), и только затем — вычеты уровня chrtId в
    `_apply_chrt_limits`. Построчный потолок сам по себе суммы группы не
    удерживает (два баркода одного chrtId дали бы 2×потолок), поэтому тот же
    потолок ложится на позицию в `_apply_chrt_limits._group_override_limit`.
    """
    totals, has_mirror = _sum_by_warehouse(nom_id, barcode, wh, warehouse_ids, ledger, mirror, reserved)

    # Вычеты УРОВНЯ ПОЗИЦИИ здесь не применяются: `safety_stock_abs`, открытые
    # FBS-задания и потолок `max_qty_per_sku` заданы на SKU (chrtId), а один
    # chrtId может стоять у нескольких баркодов. Всё это накладывает
    # `_apply_chrt_limits` ПОСЛЕ схлопывания строк по chrtId. Прежний построчный
    # вычет открытых заданий вдобавок гасился клампом: строка с остатком 0 и
    # 12 заданиями давала 0 вместо −12, и 12 проданных единиц уезжали к WB
    # второй раз через строку-соседку с тем же chrtId.
    qty_computed = min(totals["available"], _WB_MAX_AMOUNT)

    qty_available = _apply_override(qty_computed, override_qty)
    fbo_blocked = _fbo_blocks(wh, fbo_qty)
    if fbo_blocked:
        qty_available = 0

    chrt_id = int(nom.chrt_id) if nom is not None and nom.chrt_id is not None else None
    return {
        "barcode": barcode,
        "chrt_id": chrt_id,
        "nomenclature_id": nom_id,
        "article_seller": getattr(nom, "article_seller", None),
        "nm_id": getattr(nom, "article_wb", None),
        "qty_ledger": totals["ledger"],
        "qty_ff_mirror": totals["mirror"] if has_mirror else None,
        "qty_ledger_free": totals["ledger_free"],
        "qty_ff_free": totals["mirror_free"] if has_mirror else None,
        "qty_source": totals["source"],
        "fbo_qty": fbo_qty,
        "reserved_assembly": totals["reserved"],
        "fbs_open": fbs_open,
        "defect": 0,
        "buffer": totals["buffer"],
        "qty_computed": qty_computed,
        "qty_available": qty_available,
        "qty_sent": state.qty_sent if state is not None else None,
        "qty_confirmed": state.qty_confirmed if state is not None else None,
        "override_qty": override_qty,
        "brand": getattr(nom, "brand", None),
        "subject": getattr(nom, "subject", None),
        "subcategory_id": subcategory[0] if subcategory else None,
        "subcategory_name": subcategory[1] if subcategory else None,
        "blocked_reason": _blocked_reason(chrt_id, override_qty, fbo_blocked=fbo_blocked),
    }


def _fbo_blocks(wh: WbFbsWarehouse, fbo_qty: int | None) -> bool:
    """Придержать ли позицию: на складах WB её больше порога `fbo_max_qty`.

    `fbo_max_qty is None` — на FBO не смотрим вовсе (гейт выключен).
    `fbo_qty is None` — зеркала FBO нет, сравнивать не с чем: молча считать это
    нулём нельзя, иначе порог 0 отправил бы в FBS весь каталог.
    """
    if wh.fbo_max_qty is None or fbo_qty is None:
        return False
    return int(fbo_qty) > int(wh.fbo_max_qty)


def _blocked_reason(chrt_id: int | None, override_qty: int | None, *, fbo_blocked: bool) -> str | None:
    """Почему позиция не уедет в WB (порядок — от непреодолимого к настройке).

    `no_chrt` первым: без ключа WB позицию физически нечем транслировать.
    Дальше явное решение пользователя (`override_zero`) и только потом гейт FBO —
    в UI показывается один ярлык, и он обязан называть ГЛАВНУЮ причину.
    """
    if not chrt_id:
        return BLOCKED_NO_CHRT
    if override_qty == 0:
        return BLOCKED_OVERRIDE_ZERO
    if fbo_blocked:
        return BLOCKED_FBO_IN_STOCK
    return None


async def _build_rows(
    db: AsyncSession, project_id: int, wh: WbFbsWarehouse, warehouse_ids: list[int]
) -> list[dict[str, Any]]:
    """Строки FBS-остатка для склада продавца: только батч-запросы, без N+1.

    Ручные количества, остаток FBO и товарные категории читаются ОДНИМ проходом
    на весь набор номенклатур — построчный резолв дал бы N+1 на каждой позиции.
    """
    ledger, defects, ledger_barcodes = await _load_ledger(db, project_id, warehouse_ids)
    # Зеркало ФФ ОБЯЗАНО быть тем, что реально можно отгрузить: у migfull
    # `qty_good` (stock_actual) — весь физический остаток, включая собранное и
    # битое, и без вычета мы обещали бы WB то, чего не отправим (`_net_mirror`).
    unavailable = await fulfillment_service.ff_unavailable_by_nomenclature(db, project_id, warehouse_ids)
    ff_defect = await fulfillment_service.ff_defect_by_nomenclature(db, project_id, warehouse_ids)
    # Короба в зеркало не входят — их нельзя продать поштучно. Но показать их
    # обязаны: это ровно тот запас, который «встанет» в FBS после вскрытия.
    boxed = await fulfillment_service.ff_boxed_by_nomenclature(db, project_id, warehouse_ids)
    mirror = _net_mirror(await _load_mirror(db, project_id, warehouse_ids), unavailable)
    fbs_open = await _load_open_fbs(db, project_id, [wh.wb_warehouse_id])
    states = await _load_states(db, project_id, wh.wb_warehouse_id)
    reserved = await _load_reserves(db, project_id, warehouse_ids)

    # `set(fbs_open)` обязателен: позиция, проданная по FBS, но не имеющая строки
    # WarehouseStock на привязанных складах, иначе не попадала бы в набор — и её
    # открытые задания не вычитались бы ВООБЩЕ (у соседнего баркода того же
    # chrtId остаток уезжал бы к WB целиком).
    nom_ids = (
        set(ledger) | set(mirror) | set(fbs_open) | {s.nomenclature_id for s in states.values() if s.nomenclature_id}
    )
    noms = await _load_nomenclature(db, project_id, nom_ids)
    overrides = await _load_overrides(db, project_id, wh.wb_warehouse_id, nom_ids)
    fbo = await _load_fbo(db, project_id, nom_ids)
    subcats = await _load_subcategories(db, project_id, (getattr(n, "article_wb", None) for n in noms.values()))

    rows: list[dict[str, Any]] = []
    covered_chrts: set[int] = set()
    for nom_id in sorted(nom_ids):
        nom = noms.get(nom_id)
        barcode = (getattr(nom, "barcode", None) or ledger_barcodes.get(nom_id) or "").strip()
        chrt = int(nom.chrt_id) if nom is not None and nom.chrt_id is not None else None
        nm_id = getattr(nom, "article_wb", None)
        row = _build_row(
            nom_id,
            nom,
            barcode,
            wh,
            warehouse_ids,
            ledger.get(nom_id, {}),
            mirror.get(nom_id, {}),
            reserved,
            int(fbs_open.get(nom_id, 0)),
            states.get(chrt) if chrt else None,
            override_qty=overrides.get(nom_id),
            # Живое зеркало без строки по позиции = честный ноль на FBO;
            # зеркала нет вовсе (`fbo is None`) или судить по нему об этом
            # баркоде нечем (значение `None`) — гейт выключен.
            fbo_qty=None if fbo is None else fbo.get(nom_id, 0),
            subcategory=subcats.get(int(nm_id)) if nm_id else None,
        )
        row["defect"] = _row_defect(nom_id, mirror, ff_defect, defects)
        box_pieces = box_count = 0
        for wid in warehouse_ids:
            pieces, boxes_n = boxed.get(wid, {}).get(nom_id, (0, 0))
            box_pieces += pieces
            box_count += boxes_n
        row["qty_ff_boxed"] = box_pieces
        row["ff_box_count"] = box_count
        rows.append(row)
        if chrt:
            covered_chrts.add(chrt)

    _apply_chrt_limits(
        rows,
        _open_by_chrt(fbs_open, noms),
        abs_buffer=int(wh.safety_stock_abs or 0),
        max_qty_per_sku=int(wh.max_qty_per_sku or 0),
    )
    rows.extend(_orphan_state_rows(states, covered_chrts))
    rows.sort(key=lambda r: (r["barcode"], r["chrt_id"] or 0))
    return rows


def _open_by_chrt(fbs_open: dict[int, int], noms: dict[int, Any]) -> dict[int, int]:
    """Открытые задания в разрезе chrtId: {chrt_id: штук}.

    Резолв заданий идёт по номенклатуре (`orders_service._resolve_nomenclature`
    детерминированно вешает их на МЕНЬШИЙ id), а вычитать их надо с позиции WB —
    то есть с chrtId, общего у всех баркодов позиции.
    """
    out: dict[int, int] = {}
    for nom_id, qty in fbs_open.items():
        nom = noms.get(nom_id)
        chrt = int(nom.chrt_id) if nom is not None and nom.chrt_id is not None else None
        if chrt and qty:
            out[chrt] = out.get(chrt, 0) + int(qty)
    return out


def _trim_group(group: list[dict[str, Any]], excess: int, field: str = "qty_available") -> None:
    """Снять `excess` штук с хвоста группы строк (мутирует указанное поле)."""
    for row in reversed(group):
        if excess <= 0:
            return
        cut = min(excess, int(row[field]))
        row[field] = int(row[field]) - cut
        excess -= cut


def _group_override_limit(group: list[dict[str, Any]]) -> int | None:
    """Потолок ручных количеств для ПОЗИЦИИ WB (chrtId); `None` — потолка нет.

    Строки группы — это разные баркоды ОДНОГО chrtId: в WB уезжает их СУММА,
    и построчного `_apply_override` мало. Потолок позиции — сумма потолков её
    строк; строка без ручного количества ничем не ограничена и вносит свой
    рассчитанный остаток (`qty_computed`).

    Что это удерживает:
      • «не отдавать» (0) на всех строках позиции даёт WB честный ноль, а не
        остаток соседнего баркода того же chrtId;
      • два баркода под потолками 10 и 5 не могут отдать больше 15 — раньше
        (правила уровня категории) сумма пробивала потолок ровно в N раз.

    Ограничение сознательное: `0` на ОДНОМ баркоде не гасит позицию целиком —
    ручное количество задано по ТОВАРУ, и у соседнего товара свой потолок.
    """
    if all(r.get("override_qty") is None for r in group):
        return None
    total = 0
    for row in group:
        override_qty = row.get("override_qty")
        if override_qty is None:
            total += max(0, int(row.get("qty_computed") or 0))
        else:
            total += max(0, int(override_qty))
    return total


def _apply_chrt_limits(
    rows: list[dict[str, Any]],
    open_by_chrt: dict[int, int],
    *,
    abs_buffer: int,
    max_qty_per_sku: int,
) -> None:
    """Вычеты УРОВНЯ ПОЗИЦИИ (chrtId) поверх построчного расчёта — мутирует строки.

    Позиция для WB — это chrtId, а один chrtId может стоять у нескольких
    баркодов, поэтому открытые задания, абсолютный буфер и потолок
    `max_qty_per_sku` применяются к СУММЕ группы:

        amount(chrt) = min(max(0, Σ qty_available − открытые − abs), cap, 100000)

    Излишек снимается с хвоста группы — превью показывает ровно то, что уедет
    (`_aggregate_by_chrt` только суммирует уже посчитанное).

    Потолок ручных количеств — тоже величина ПОЗИЦИИ (`_group_override_limit`):
    построчного `_apply_override` хватает ровно до тех пор, пока chrtId у одного
    баркода. См. ниже, что ломалось.

    Что ломалось до этого:
      • открытые задания вычитались построчно и гасились клампом `max(0, …)`:
        chrtId у двух баркодов, все 12 заданий на строке с нулевым остатком →
        группа отдавала полный остаток соседа, и проданное уезжало в WB снова;
      • `safety_stock_abs` вычитался из КАЖДОЙ строки → при N баркодах одного
        chrtId придерживалось N×abs, а колонка «Буфер» показывала abs в каждой
        строке, расходясь с фактически отправленным;
      • ручной потолок считался только построчно: два баркода одного chrtId под
        потолком 10 давали WB 20 (N×потолок), и «не больше 10» не соблюдалось.

    Строки без chrtId (blocked `no_chrt`) в WB не уезжают, но в превью обязаны
    показывать честную цифру — для них вычеты остаются построчными.
    """
    groups: dict[int, list[dict[str, Any]]] = {}
    loose: list[dict[str, Any]] = []
    for row in rows:
        chrt = row.get("chrt_id")
        if chrt:
            groups.setdefault(int(chrt), []).append(row)
        else:
            loose.append(row)

    for chrt, group in groups.items():
        total = sum(int(r["qty_available"]) for r in group)
        open_qty = int(open_by_chrt.get(chrt, 0))
        amount = total - open_qty - abs_buffer
        # Потолок ручных количеств считаем от ИСХОДНОГО `qty_computed`: ниже он
        # сам уменьшится на те же вычеты, и взятый после — вычел бы их дважды.
        override_limit = _group_override_limit(group)
        if override_limit is not None:
            amount = min(amount, override_limit)
        if max_qty_per_sku > 0:
            amount = min(amount, max_qty_per_sku)
        amount = max(0, min(amount, _WB_MAX_AMOUNT))
        _trim_group(group, total - amount)
        # То же самое — по «Можем отдать»: колонка обязана уже учитывать проданное
        # по FBS и абсолютный буфер, иначе на экране «Остаток» и «Можем отдать»
        # не сходятся с тем, что реально уедет. Ручное количество сюда не входит
        # намеренно: `qty_computed` — это потолок ДО вмешательства человека.
        computed_total = sum(int(r["qty_computed"]) for r in group)
        computed_amount = computed_total - open_qty - abs_buffer
        if max_qty_per_sku > 0:
            computed_amount = min(computed_amount, max_qty_per_sku)
        computed_amount = max(0, min(computed_amount, _WB_MAX_AMOUNT))
        _trim_group(group, computed_total - computed_amount, "qty_computed")
        if abs_buffer:
            # Буфер — величина позиции, а не строки: показываем его один раз
            # на группе, иначе расшифровка снова разойдётся с отправленным.
            group[0]["buffer"] = int(group[0]["buffer"]) + abs_buffer

    for row in loose:
        amount = int(row["qty_available"]) - int(row["fbs_open"]) - abs_buffer
        computed = int(row["qty_computed"]) - int(row["fbs_open"]) - abs_buffer
        if max_qty_per_sku > 0:
            amount = min(amount, max_qty_per_sku)
            computed = min(computed, max_qty_per_sku)
        row["qty_available"] = max(0, min(amount, _WB_MAX_AMOUNT))
        row["qty_computed"] = max(0, min(computed, _WB_MAX_AMOUNT))
        if abs_buffer:
            row["buffer"] = int(row["buffer"]) + abs_buffer


def _orphan_state_rows(states: dict[int, WbFbsStockState], covered: set[int]) -> list[dict[str, Any]]:
    """Строки-«сироты»: chrtId, который мы когда-то отправили, а сейчас его нет в остатках.

    Товар удалили из номенклатуры / отвязали склад — на WB он всё ещё лежит с
    ненулевым остатком и продаётся. Такие позиции обязаны уехать нулём.
    """
    orphans: list[dict[str, Any]] = []
    for chrt, state in states.items():
        if chrt in covered or not state.qty_sent:
            continue
        orphans.append(
            {
                "barcode": state.barcode or "",
                "chrt_id": chrt,
                "nomenclature_id": state.nomenclature_id,
                "article_seller": None,
                "nm_id": None,
                "qty_ledger": 0,
                "qty_ff_mirror": None,
                "qty_ledger_free": 0,
                "qty_ff_free": None,
                "qty_ff_boxed": 0,
                "ff_box_count": 0,
                "qty_source": 0,
                "fbo_qty": None,
                "reserved_assembly": 0,
                "fbs_open": 0,
                "defect": 0,
                "buffer": 0,
                # Ручное количество и разрезы сироте взять неоткуда: номенклатуры
                # уже нет. Ключи всё равно кладём — все строки превью обязаны быть
                # одной формы.
                "qty_computed": 0,
                "qty_available": 0,
                "qty_sent": state.qty_sent,
                "qty_confirmed": state.qty_confirmed,
                "override_qty": None,
                "brand": None,
                "subject": None,
                "subcategory_id": None,
                "subcategory_name": None,
                "blocked_reason": None,
            }
        )
    return orphans


# ─── Публичное чтение ────────────────────────────────────────────────────────


async def compute_fbs_stock(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> list[dict[str, Any]]:
    """Строки FBS-остатка склада продавца (FbsStockRow). Без кэша — источник истины."""
    wh = await warehouse_service.get_warehouse(db, project_id, wb_warehouse_id)
    warehouse_ids = await warehouse_service.get_linked_warehouse_ids(db, project_id, wb_warehouse_id)
    return await _build_rows(db, project_id, wh, warehouse_ids)


@cached(prefix="fbs:stock_preview", ttl=60)  # литерал: см. комментарий у CACHE_WAREHOUSES
async def preview_stock(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> dict[str, Any]:
    """Превью «что уедет на WB» (FbsStockPreviewOut).

    TTL короткий: остаток живой, а джоб пуша ходит раз в 3 минуты — держать
    превью дольше значит показывать пользователю уже отправленное прошлое.
    """
    wh = await warehouse_service.get_warehouse(db, project_id, wb_warehouse_id)
    warehouse_ids = await warehouse_service.get_linked_warehouse_ids(db, project_id, wb_warehouse_id)
    rows = await _build_rows(db, project_id, wh, warehouse_ids)
    wb_known = await _attach_wb_stock(db, project_id, wb_warehouse_id, rows)
    total_units = sum(int(r["qty_available"]) for r in rows if r.get("chrt_id"))
    return {
        "wb_warehouse_id": wb_warehouse_id,
        "wb_warehouse_name": wh.name,
        "warehouse_ids": warehouse_ids,
        "rows": rows,
        "wb_stock_known": wb_known,
        "total_rows": len(rows),
        # «Штук к передаче» — только то, что ФИЗИЧЕСКИ уедет. Позиция без chrt_id
        # не уйдёт никогда (ключ методов остатков WB), и считать её в этот итог
        # значит обещать пользователю передачу, которой не будет: на живом складе
        # плашка показывала 573 штуки при нулевой фактической выдаче.
        "total_units": total_units,
        "rows_no_chrt": _count_no_chrt(rows),
        # Расшифровка дельты с «Доступно» карточки склада (см. _push_breakdown).
        "breakdown": _push_breakdown(rows, total_units),
        "is_processing": wh.is_processing,
    }


async def _attach_wb_stock(
    db: AsyncSession, project_id: int, wb_warehouse_id: int, rows: list[dict[str, Any]]
) -> bool:
    """Дописать в строки `qty_wb` — ЖИВОЙ остаток из кабинета WB. True, если получилось.

    Читается автоматически вместе с превью, а не по кнопке: пользователю нужно
    видеть «сколько там сейчас» рядом с «сколько отдадим», иначе расхождение
    ловится только отдельной сверкой и по факту не ловится никогда.

    Цена вопроса — поход в WB на каждый расчёт превью, поэтому:
      • запрос идёт ТОЛЬКО за позициями с `chrt_id` (ключ методов остатков WB);
      • ответ кэшируется вместе со всем превью (`fbs:stock_preview`, TTL 60 c),
        так что чаще раза в минуту на склад мы WB не трогаем;
      • `POST /stocks` — ЧТЕНИЕ, гейт режима его не блокирует: колонка одинаково
        работает и в «Наблюдении», и в боевом режиме.

    Любая ошибка WB (401, 429, таймаут) НЕ роняет превью: `qty_wb` остаётся
    `None`, а флаг `wb_stock_known=False` говорит фронту показать прочерк вместо
    нуля. Ноль и «не знаем» — разные факты: ноль означает «в кабинете пусто».

    Число WB уже НЕТТО: кабинет сам вычитает то, что заказали, поэтому сравнивать
    его с нашим `qty_available` (тоже за вычетом открытых FBS-заказов) корректно.
    """
    chrt_ids = sorted({int(r["chrt_id"]) for r in rows if r.get("chrt_id")})
    if not chrt_ids:
        return False

    try:
        client = await _get_client(db, project_id)
    except Exception as exc:  # ключа нет / не расшифровался — не повод ронять экран
        logger.warning("wb_fbs.stock.wb_column project=%s клиент недоступен: %s", project_id, exc)
        return False

    await db.commit()  # не держим транзакцию через внешний HTTP
    try:
        remote = await client.get_stocks(wb_warehouse_id, chrt_ids)
    except Exception as exc:
        logger.warning(
            "wb_fbs.stock.wb_column project=%s wh=%s не прочитали остаток WB: %s",
            project_id,
            wb_warehouse_id,
            exc,
        )
        return False

    for row in rows:
        chrt = row.get("chrt_id")
        row["qty_wb"] = int(remote.get(int(chrt), 0)) if chrt else None
    return True


def _count_no_chrt(rows: list[dict[str, Any]]) -> int:
    """Сколько позиций не уедет из-за отсутствия chrt_id (ключ методов остатков WB).

    Считаем ВСЕ такие позиции, а не только те, где расчёт дал >0. Прежний вариант
    (`qty_available > 0`) занижал проблему до неузнаваемости: на складе, где ключ
    не заполнен вообще ни у чего, плашка показывала «без chrtId: 7» при 103
    непередаваемых позициях — и пользователь не понимал, почему ничего не уходит.
    """
    return sum(1 for r in rows if not r.get("chrt_id"))


def _push_breakdown(rows: list[dict[str, Any]], total_units: int) -> dict[str, int]:
    """Почему «Штук к передаче» меньше «Доступно» на складе — по слагаемым.

    Две цифры об одном товаре на двух экранах читаются как ошибка расчёта, хотя
    считают они разное: карточка склада — ТОЛЬКО наш учёт за вычетом резервов,
    а FBS — то же самое, но ещё через зеркало ФФ, буфер, проданное по FBS,
    потолки и ручное количество. Вопрос «почему 29 612, а не 30 692» без этой
    расшифровки не закрывается ничем, кроме чтения кода.

    Цепочка сходится по ПОСТРОЕНИЮ: «прочее» — это остаток, а не отдельный
    расчёт. Иначе первый же вычет, о котором здесь забыли (новый потолок,
    FBO-гейт), тихо сломал бы баланс, и расшифровка стала бы врать заметнее,
    чем цифра, которую она объясняет.
    """
    ledger_free = sum(int(r.get("qty_ledger_free") or 0) for r in rows)
    # Зеркало срезает только там, где провайдер видит МЕНЬШЕ нашего учёта;
    # обратный случай (источник ff_mirror выше ledger) — не вычет, а прибавка,
    # и в расшифровку «почему меньше» ей заходить нечего.
    by_mirror = sum(
        max(0, int(r.get("qty_ledger_free") or 0) - int(r.get("qty_source") or 0)) for r in rows
    )
    by_buffer = sum(int(r.get("buffer") or 0) for r in rows)
    no_chrt = sum(int(r.get("qty_ledger_free") or 0) for r in rows if not r.get("chrt_id"))
    # `qty_computed` — потолок ДО вмешательства человека (см. _apply_chrt_limits),
    # поэтому разница с `qty_available` и есть чистый эффект ручного количества.
    by_override = sum(
        max(0, int(r.get("qty_computed") or 0) - int(r.get("qty_available") or 0))
        for r in rows
        if r.get("chrt_id")
    )
    named = by_mirror + by_buffer + no_chrt + by_override
    return {
        "ledger_free": ledger_free,
        "cut_by_mirror": by_mirror,
        "cut_by_buffer": by_buffer,
        "cut_no_chrt": no_chrt,
        "cut_by_override": by_override,
        # Открытые FBS-задания, потолок max_qty_per_sku, FBO-гейт — всё, что
        # снимается на уровне позиции уже после схлопывания по chrtId.
        "cut_other": max(0, ledger_free - named - total_units),
    }



# ─── Матрица складов продавца WB ─────────────────────────────────────────────


#: Окна тренда, которые умеет отдавать `get_unified_stock_summary`.
_MATRIX_TREND_KEYS = {7: "trend_7", 14: "trend_14", 30: "trend_30"}


async def stock_matrix(db: AsyncSession, project_id: int, trend_days: int = 14) -> dict[str, Any]:
    """Матрица «товар × склад продавца WB»: что там стоит и что могли бы поставить.

    Колонки — ТОЛЬКО склады со связкой на наши (`WbFbsWarehouseLink`): без
    привязки поставить физически нечего, и пустой столбец лишь мешает.

    Обе цифры ячейки берутся из ОБЩЕГО `preview_stock`, а не считаются заново:
    вкладка «Остатки» и матрица обязаны показывать одно и то же число, иначе
    расхождение читается как ошибка расчёта (та же причина, по которой выручка
    FBS сведена в одну формулу). Побочная выгода — общий кэш превью, так что
    матрица не добавляет походов в WB сверх одного на склад в минуту.

    Деньги (реализация и маржа) — из `get_unified_stock_summary`: они считаются
    по КАРТОЧКЕ и по складам не делятся, поэтому живут отдельными колонками
    справа, а не в ячейках складов.
    """
    from backend.services.warehouse_stock_engine import get_unified_stock_summary

    trend_key = _MATRIX_TREND_KEYS.get(int(trend_days), "trend_14")

    linked = await db.execute(
        select(WbFbsWarehouse)
        .where(
            WbFbsWarehouse.project_id == project_id,
            WbFbsWarehouse.wb_warehouse_id.in_(
                select(WbFbsWarehouseLink.wb_warehouse_id).where(
                    WbFbsWarehouseLink.project_id == project_id,
                    WbFbsWarehouseLink.is_active == True,  # noqa: E712
                )
            ),
        )
        .order_by(WbFbsWarehouse.name)
        .limit(100)
    )
    warehouses = list(linked.scalars().all())
    if not warehouses:
        return {"warehouses": [], "rows": [], "wb_stock_known": False, "trend_days": int(trend_days)}

    wb_known = False
    # nomenclature_id → строка матрицы
    by_nom: dict[int, dict[str, Any]] = {}
    for wh in warehouses:
        preview = await preview_stock(db, project_id, wh.wb_warehouse_id)
        wb_known = wb_known or bool(preview.get("wb_stock_known"))
        for r in preview.get("rows", []):
            nom_id = r.get("nomenclature_id")
            if not nom_id:
                continue  # без номенклатуры строку не с чем сшить по деньгам
            row = by_nom.setdefault(
                int(nom_id),
                {
                    "nomenclature_id": int(nom_id),
                    "barcode": r.get("barcode"),
                    "article_seller": r.get("article_seller"),
                    "nm_id": r.get("nm_id"),
                    "brand": r.get("brand"),
                    "subject": r.get("subject"),
                    "cells": {},
                    "total_wb": 0,
                    "total_can": 0,
                    "total_boxed": 0,
                    "total_boxes": 0,
                    "fbo": None,
                    "fbo_by_warehouse": [],
                },
            )
            wb_qty = r.get("qty_wb")
            can_qty = int(r.get("qty_available") or 0)
            # Короба показываем прямо в ячейке склада: на Натали это основная
            # масса товара, и без них «можем поставить 0» выглядит как «нечего
            # везти», хотя на складе лежат тысячи штук — просто невскрытые.
            boxed_qty = int(r.get("qty_ff_boxed") or 0)
            boxes_n = int(r.get("ff_box_count") or 0)
            row["cells"][str(wh.wb_warehouse_id)] = {
                "wb": wb_qty,
                "can": can_qty,
                "boxed": boxed_qty,
                "boxes": boxes_n,
            }
            row["total_wb"] += int(wb_qty or 0)
            row["total_can"] += can_qty
            row["total_boxed"] += boxed_qty
            row["total_boxes"] += boxes_n
            # FBO приходит уже посчитанным в превью — берём оттуда, чтобы колонка
            # не разошлась с гейтом «только то, чего нет на FBO».
            if row.get("fbo") is None:
                row["fbo"] = r.get("fbo_qty")

    # Деньги одним запросом на всю выдачу — не по складу и не по строке.
    money: dict[int, dict[str, Any]] = {}
    try:
        for u in await get_unified_stock_summary(db, project_id):
            nom_id = u.get("nomenclature_id")
            if nom_id:
                money[int(nom_id)] = u.get(trend_key) or {}
    except Exception as exc:  # тренда нет — матрица обязана остаться рабочей
        logger.warning("wb_fbs.matrix project=%s тренд недоступен: %s", project_id, exc)

    # Разбивка FBO по складам — одним запросом на всю выдачу, не по строке.
    fbo_split = await fbo_by_warehouse(db, project_id, by_nom.keys())

    rows = []
    for row in by_nom.values():
        # `fbo` берётся из превью, а превью КЭШИРУЕТСЯ — разбивка же считается
        # каждый раз заново, и на устаревшем кэше число расходилось с раскрытым
        # списком (ловилось живьём: 1617 против 1059). Внутри одного экрана
        # цифра и её расшифровка обязаны сходиться, поэтому итог берём из
        # разбивки. `None` не трогаем: это «зеркала нет / судить нечем», и
        # подменять его суммой значило бы обойти собственный отказ доверять.
        split = fbo_split.get(row["nomenclature_id"], [])
        if row.get("fbo") is None:
            row["fbo_by_warehouse"] = []
        else:
            row["fbo_by_warehouse"] = [{"name": name, "qty": qty} for name, qty in split]
            if split:
                row["fbo"] = sum(qty for _name, qty in split)
        t = money.get(row["nomenclature_id"], {})
        revenue = Decimal(str(t.get("revenue") or 0))
        profit = Decimal(str(t.get("profit") or 0))
        row["revenue"] = revenue
        row["profit"] = profit
        # Маржинальность без выручки не определена — None, а не 0: ноль означал бы
        # «продавали в ноль», а мы просто не продавали.
        row["margin_pct"] = float(profit / revenue * 100) if revenue else None
        row["sale_qty"] = int(t.get("sale_qty") or 0)
        row["avg_daily_qty"] = float(t.get("avg_daily_qty") or 0)
        rows.append(row)

    rows.sort(key=lambda r: (-(r["revenue"] or 0), -(r["total_can"] or 0)))
    return {
        "warehouses": [
            {"wb_warehouse_id": w.wb_warehouse_id, "name": w.name} for w in warehouses
        ],
        "rows": rows,
        "wb_stock_known": wb_known,
        "trend_days": int(trend_days),
    }

# ─── Потоварная замена количества ────────────────────────────────────────────


#: «Аргумент не передан» — отличает его от осознанного `qty = None` («снять»).
_UNSET: Any = object()


async def set_overrides(
    db: AsyncSession,
    project_id: int,
    target: int | FbsOverrideSet,
    nomenclature_ids: Iterable[int] | None = None,
    qty: int | None = _UNSET,
) -> int:
    """Задать (или снять) ручное количество по товарам склада продавца.

    `qty = None` — снять ограничение (удалить строки, позиция возвращается к
    расчёту). `qty = 0` — не отдавать вовсе. `qty > 0` — потолок.
    Возвращает число затронутых товаров.

    Две формы вызова, обе легальны:
      • `set_overrides(db, project_id, wb_warehouse_id, nomenclature_ids, qty)`;
      • `set_overrides(db, project_id, payload)` — готовый `FbsOverrideSet`
        из роутера (схема — замороженный контракт домена, и разбирать её в
        роутере значило бы дублировать разбор при каждом новом вызывающем).

    Что здесь обязательно:
      • склад продавца проверяется по справочнику — ручное количество на чужой
        `wb_warehouse_id` было бы строкой-призраком: в базе есть, не применяется;
      • номенклатуры фильтруются по проекту — иначе через список id можно было бы
        завести настройку на чужой товар (и увидеть его существование);
      • ключи дедуплицируются ДО `executemany` — иначе UPSERT падает
        `CardinalityViolation` на повторе в одном statement.
    """
    # Пер-товарная форма (`items`) — отдельный путь: у каждой позиции своё число,
    # и общий `qty` его не выражает.
    if isinstance(target, FbsOverrideSet) and target.items:
        return await _set_overrides_per_item(db, project_id, target)

    wb_warehouse_id, nomenclature_ids, qty = _override_args(target, nomenclature_ids, qty)
    await warehouse_service.get_warehouse(db, project_id, wb_warehouse_id)
    ids = sorted({int(i) for i in nomenclature_ids})
    if not ids:
        return 0
    valid = await _filter_project_noms(db, project_id, ids)
    if not valid:
        return 0

    if qty is None:
        for chunk in _chunked(valid, _IN_CHUNK):
            await db.execute(
                delete(WbFbsStockOverride).where(  # no-soft-delete-check: таблица настройки, без SoftDeleteMixin
                    WbFbsStockOverride.project_id == project_id,
                    WbFbsStockOverride.wb_warehouse_id == wb_warehouse_id,
                    WbFbsStockOverride.nomenclature_id.in_(chunk),
                )
            )
    else:
        value = max(0, int(qty))
        for chunk in _chunked(valid, _STATE_CHUNK):
            stmt = pg_insert(WbFbsStockOverride).values(
                [
                    {
                        "project_id": project_id,
                        "wb_warehouse_id": wb_warehouse_id,
                        "nomenclature_id": nom_id,
                        "qty": value,
                    }
                    for nom_id in chunk
                ]
            )
            await db.execute(
                stmt.on_conflict_do_update(constraint="uq_wb_fbs_override", set_={"qty": stmt.excluded.qty})
            )

    await db.commit()
    # Превью держится 60 c, а цифра зависит от ручного количества напрямую:
    # без сброса пользователь сохранил бы значение и не увидел эффекта.
    await invalidate_cache(CACHE_STOCK_PREVIEW)
    logger.info(
        "FBS overrides set: project=%s wb_wh=%s rows=%s qty=%s",
        project_id,
        wb_warehouse_id,
        len(valid),
        qty,
    )
    return len(valid)


async def _set_overrides_per_item(db: AsyncSession, project_id: int, payload: FbsOverrideSet) -> int:
    """Проставить КАЖДОМУ товару своё количество (форма `items`).

    Нужна там, где число берётся из данных, а не вводится руками — «проставить
    из зеркала ФФ»: у каждой позиции свой остаток. Слать по запросу на позицию
    нельзя (`rate_limit_write` режет серию), поэтому весь набор идёт одним
    батчевым UPSERT'ом.

    Проверки те же, что и у формы «одно число на список»: склад — по справочнику
    проекта, номенклатуры — по проекту, ключи дедуплицируются ДО `executemany`
    (иначе `CardinalityViolation` на повторе в одном statement). Повтор
    выигрывает ПОСЛЕДНИЙ: словарь перезаписывается по ходу, и это ровно то, что
    ожидает отправитель, если он случайно продублировал позицию в теле.
    """
    await warehouse_service.get_warehouse(db, project_id, payload.wb_warehouse_id)
    by_nom: dict[int, int] = {int(i.nomenclature_id): max(0, int(i.qty)) for i in payload.items or []}
    if not by_nom:
        return 0
    valid = await _filter_project_noms(db, project_id, sorted(by_nom))
    if not valid:
        return 0

    for chunk in _chunked(valid, _STATE_CHUNK):
        stmt = pg_insert(WbFbsStockOverride).values(
            [
                {
                    "project_id": project_id,
                    "wb_warehouse_id": payload.wb_warehouse_id,
                    "nomenclature_id": nom_id,
                    "qty": by_nom[nom_id],
                }
                for nom_id in chunk
            ]
        )
        await db.execute(
            stmt.on_conflict_do_update(constraint="uq_wb_fbs_override", set_={"qty": stmt.excluded.qty})
        )

    await db.commit()
    await invalidate_cache(CACHE_STOCK_PREVIEW)
    logger.info(
        "FBS overrides set per item: project=%s wb_wh=%s rows=%s",
        project_id,
        payload.wb_warehouse_id,
        len(valid),
    )
    return len(valid)


def _override_args(
    target: int | FbsOverrideSet, nomenclature_ids: Iterable[int] | None, qty: int | None
) -> tuple[int, Iterable[int], int | None]:
    """Развернуть обе формы вызова `set_overrides` в (склад, товары, количество)."""
    if isinstance(target, int):
        if nomenclature_ids is None or qty is _UNSET:
            raise TypeError("set_overrides(db, project_id, wb_warehouse_id, nomenclature_ids, qty)")
        return target, nomenclature_ids, qty
    return target.wb_warehouse_id, target.nomenclature_ids, target.qty


async def _filter_project_noms(db: AsyncSession, project_id: int, nom_ids: list[int]) -> list[int]:
    """Оставить только номенклатуры проекта (multi-tenancy на входе мутации)."""
    out: list[int] = []
    for chunk in _chunked(nom_ids, _IN_CHUNK):
        result = await db.execute(
            select(Nomenclature.id).where(Nomenclature.project_id == project_id, Nomenclature.id.in_(chunk))
        )
        out.extend(int(i) for i in result.scalars().all())
    return sorted(set(out))


async def list_pushes(db: AsyncSession, project_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """Журнал последних прогонов трансляции (FbsStockPushOut)."""
    result = await db.execute(
        select(WbFbsStockPush)
        .where(WbFbsStockPush.project_id == project_id)
        .order_by(WbFbsStockPush.started_at.desc(), WbFbsStockPush.id.desc())
        .limit(max(1, min(limit, 500)))
    )
    return [
        {
            "id": p.id,
            "wb_warehouse_id": p.wb_warehouse_id,
            "trigger": p.trigger,
            "status": p.status,
            "started_at": p.started_at,
            "finished_at": p.finished_at,
            "rows_total": p.rows_total,
            "rows_changed": p.rows_changed,
            "rows_sent": p.rows_sent,
            "rows_no_chrt": p.rows_no_chrt,
            "rows_mismatch": p.rows_mismatch,
            "error_msg": p.error_msg,
        }
        for p in result.scalars().all()
    ]


async def _load_link_pools(db: AsyncSession, project_id: int, warehouse_ids: list[int]) -> dict[int, list[int]]:
    """Пулы привязок складов WB, кормящихся из `warehouse_ids`: {wb_wh_id: [наши склады]}.

    Пул возвращается ПОЛНЫЙ (все наши склады склада продавца), а не только
    запрошенные: чтобы разложить обязательство склада WB, нужно знать всех, кто
    его кормит.
    """
    if not warehouse_ids:
        return {}
    wb_result = await db.execute(
        select(WbFbsWarehouseLink.wb_warehouse_id)
        .where(
            WbFbsWarehouseLink.project_id == project_id,
            WbFbsWarehouseLink.warehouse_id.in_(warehouse_ids),
            WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
        )
        .distinct()
        .limit(200)
    )
    wb_ids = [int(x) for x in wb_result.scalars().all()]
    if not wb_ids:
        return {}
    pool_result = await db.execute(
        select(WbFbsWarehouseLink.wb_warehouse_id, WbFbsWarehouseLink.warehouse_id)
        .where(
            WbFbsWarehouseLink.project_id == project_id,
            WbFbsWarehouseLink.wb_warehouse_id.in_(wb_ids),
            WbFbsWarehouseLink.is_active == True,  # noqa: E712 — SQLAlchemy expression
        )
        .order_by(WbFbsWarehouseLink.wb_warehouse_id, WbFbsWarehouseLink.warehouse_id)
        .limit(_MAX_LINK_ROWS)
    )
    pools: dict[int, list[int]] = {}
    for wb_id, warehouse_id in pool_result.all():
        pools.setdefault(int(wb_id), []).append(int(warehouse_id))
    return pools


async def _load_quantities(
    db: AsyncSession, project_id: int, warehouse_ids: list[int], nom_ids: list[int]
) -> dict[tuple[int, int], int]:
    """Годный остаток пула: {(wh_id, nom_id): штук} — веса для разнесения заданий."""
    if not warehouse_ids or not nom_ids:
        return {}
    out: dict[tuple[int, int], int] = {}
    for chunk in _chunked(nom_ids, _IN_CHUNK):
        result = await db.execute(
            select(
                WarehouseStock.warehouse_id,
                WarehouseStock.nomenclature_id,
                WarehouseStock.quantity,
            ).where(
                WarehouseStock.project_id == project_id,
                WarehouseStock.warehouse_id.in_(warehouse_ids),
                WarehouseStock.nomenclature_id.in_(chunk),
            )
        )
        for warehouse_id, nomenclature_id, quantity in result.all():
            out[(int(warehouse_id), int(nomenclature_id))] = int(quantity or 0)
    return out


def _split_open_by_stock(total: int, weights: dict[int, int]) -> dict[int, int]:
    """Разнести `total` открытых заданий склада WB по нашим складам пула.

    Пропорционально остатку методом наибольшего остатка (Σ долей == total).
    Остатка нет нигде → поровну: обязательство всё равно существует, и потерять
    его нельзя. Детерминизм при равных весах — по id склада.
    """
    ids = sorted(weights)
    if total <= 0 or not ids:
        return {}
    if len(ids) == 1:
        return {ids[0]: total}
    w = {i: max(0, int(weights[i])) for i in ids}
    base = sum(w.values())
    if base <= 0:  # пул пуст — делим поровну
        w = dict.fromkeys(ids, 1)
        base = len(ids)

    shares: dict[int, int] = {}
    remainders: list[tuple[Decimal, int, int]] = []
    for i in ids:
        exact = Decimal(total) * Decimal(w[i]) / Decimal(base)
        floor_part = int(exact)
        shares[i] = floor_part
        remainders.append((exact - floor_part, w[i], i))
    left = total - sum(shares.values())
    remainders.sort(key=lambda t: (-t[0], -t[1], t[2]))
    for position in range(left):
        shares[remainders[position % len(remainders)][2]] += 1
    return shares


async def get_open_fbs_qty(db: AsyncSession, project_id: int, warehouse_ids: list[int]) -> dict[int, int]:
    """Сколько штук ЗАПРОШЕННЫХ складов держат открытые FBS-задания: {nom_id: штук}.

    Обратный гейт для сборки: товар, уже проданный по FBS, нельзя набрать в
    заявку. Пустой ответ, если складов нет или FBS в проекте не настроен —
    поведение расчёта доступного тогда бит-в-бит прежнее.

    Дедуп складов WB обязателен: один склад продавца может кормиться из N наших
    складов, и наивный JOIN посчитал бы каждое задание N раз.

    Ключевое: при схеме «один склад WB ← N наших складов» обязательство склада
    продавца РАЗНОСИТСЯ по пулу пропорционально остатку, а не вычитается целиком
    из каждого склада. Прежний возврат полной цифры на каждый склад блокировал
    N×fbs_open: два склада по 10 шт при 10 заданиях давали available 0 на обоих,
    хотя свободно 10. Прямой расчёт FBS-остатка вычитает задания ОДИН раз с
    суммы по складам (`_build_row` + `_apply_chrt_limits`) — гейты обязаны
    сходиться. Списание (`writeoff_completed_orders`) тоже берёт единицу с
    ОДНОГО линкованного склада, а не с каждого.
    """
    if not warehouse_ids:
        return {}
    requested = {int(w) for w in warehouse_ids}
    pools = await _load_link_pools(db, project_id, sorted(requested))
    if not pools:
        return {}
    open_by_wh = await _load_open_fbs_by_wh(db, project_id, sorted(pools))
    if not open_by_wh:
        return {}

    pool_wh_ids = sorted({wid for pool in pools.values() for wid in pool})
    nom_ids = sorted({nom_id for per_nom in open_by_wh.values() for nom_id in per_nom})
    quantities = await _load_quantities(db, project_id, pool_wh_ids, nom_ids)

    out: dict[int, int] = {}
    for wb_id, per_nom in open_by_wh.items():
        pool = pools.get(wb_id) or []
        mine = [wid for wid in pool if wid in requested]
        if not mine:
            continue
        for nom_id, qty in per_nom.items():
            shares = _split_open_by_stock(qty, {wid: quantities.get((wid, nom_id), 0) for wid in pool})
            share = sum(shares.get(wid, 0) for wid in mine)
            if share:
                out[nom_id] = out.get(nom_id, 0) + share
    return out


# ─── Трансляция остатков ─────────────────────────────────────────────────────


async def _get_client(db: AsyncSession, project_id: int) -> Any:
    """Клиент Marketplace API (локальный импорт — см. warehouse_service._get_client)."""
    from backend.services.wb_fbs.client_factory import get_fbs_client

    return await get_fbs_client(db, project_id)


def _aggregate_by_chrt(rows: list[dict[str, Any]], max_qty_per_sku: int = 0) -> list[dict[str, Any]]:
    """Схлопнуть строки по chrtId (много баркодов → один chrtId) с суммой количеств.

    Вычеты уровня позиции (открытые задания, `safety_stock_abs`, потолок) уже
    наложены на строки в `_apply_chrt_limits` — здесь остаётся сумма и повторное
    применение потолков как страховка для вызова на «сырых» строках
    (идемпотентно: на уже урезанной группе min ничего не меняет).
    Жёсткий потолок WB (`amount ≤ 100000`) накладывается последним.
    """
    agg: dict[int, dict[str, Any]] = {}
    for row in rows:
        chrt = row["chrt_id"]
        if not chrt:
            continue
        item = agg.get(chrt)
        if item is None:
            agg[chrt] = {
                "chrt_id": int(chrt),
                "barcode": row["barcode"] or None,
                "nomenclature_id": row["nomenclature_id"],
                "amount": int(row["qty_available"]),
                "prev": row["qty_sent"],
            }
        else:
            item["amount"] += int(row["qty_available"])
    for item in agg.values():
        if max_qty_per_sku > 0:
            item["amount"] = min(item["amount"], max_qty_per_sku)
        item["amount"] = min(item["amount"], _WB_MAX_AMOUNT)
    return sorted(agg.values(), key=lambda i: i["chrt_id"])


def _select_delta(items: list[dict[str, Any]], *, force: bool) -> list[dict[str, Any]]:
    """Что реально слать: изменившееся против прошлого пуша (при force — всё уже слаемое).

    Позиция, которую никогда не отправляли и которая сейчас 0, пропускается даже
    при force — слать «ноль впервые» бессмысленно. Всё остальное, что ушло в 0,
    отправляется обязательно.
    """
    out = []
    for item in items:
        prev = item["prev"]
        need = item["amount"] > 0 if prev is None else (force or item["amount"] != prev)
        if need:
            out.append(item)
    return out


async def _put_chunks(
    client: Any,
    wb_warehouse_id: int,
    items: list[dict[str, Any]],
    *,
    fatal: tuple[type[BaseException], ...] = (),
) -> tuple[list[dict[str, Any]], list[str], BaseException | None]:
    """PUT /stocks чанками ≤1000 → (отправленные строки, ошибки, фатальная ошибка).

    Чанки изолированы: падение одного не должно ронять остальные — иначе один
    невалидный chrtId блокирует обнуление всего склада.

    `fatal` — исключения, которые отказывают ВСЕМУ прогону: гейт режима
    (`WbFbsWriteBlocked`) и 429 (`WbFbsRateLimited`) у синхронной кнопки. Такую
    ошибку НЕ бросаем прямо из цикла: вместе со стеком терялись бы уже
    отправленные чанки — журнал рапортовал бы `rows_sent=0` после реального
    обнуления тысячи позиций, `WbFbsStockState` остался бы со старым `qty_sent`
    (дельта врёт), а верификации «204-lie» по ним не было бы вовсе. Отдаём её
    ТРЕТЬИМ элементом: вызывающий сначала верифицирует и сохраняет фактически
    ушедшее, и только потом пробрасывает наверх. Дефолт — пустой кортеж
    (`except ()` не ловит ничего), поэтому путь фонового пуша не меняется:
    там любая ошибка обязана лечь в журнал, а не уронить прогон соседних складов.
    """
    sent: list[dict[str, Any]] = []
    errors: list[str] = []
    fatal_exc: BaseException | None = None
    for chunk in _chunked(items, _WB_CHUNK):
        payload = [(int(i["chrt_id"]), int(i["amount"])) for i in chunk]
        try:
            await client.put_stocks(wb_warehouse_id, payload)
            sent.extend(chunk)
        except asyncio.CancelledError:
            raise
        except fatal as e:
            fatal_exc = e
            break
        except Exception as e:  # текст ошибки WB кладём в журнал прогона
            errors.append(f"PUT {len(chunk)} поз.: {type(e).__name__}: {e}")
            logger.warning("FBS put_stocks failed: wb_wh=%s chunk=%s err=%s", wb_warehouse_id, len(chunk), e)
    return sent, errors, fatal_exc


async def _verify_chunks(
    client: Any, wb_warehouse_id: int, sent: list[dict[str, Any]], errors: list[str]
) -> tuple[dict[int, int], set[int]]:
    """POST /stocks теми же chrtId — WB отвечает 204 на PUT даже когда не обновил.

    Возвращает (подтверждённые количества, множество реально проверенных chrtId):
    провалившуюся верификацию нельзя трактовать как «расхождение».
    """
    confirmed: dict[int, int] = {}
    verified: set[int] = set()
    for chunk in _chunked(sent, _WB_CHUNK):
        chrt_ids = [int(i["chrt_id"]) for i in chunk]
        try:
            got = await client.get_stocks(wb_warehouse_id, chrt_ids)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            errors.append(f"VERIFY {len(chunk)} поз.: {type(e).__name__}: {e}")
            logger.warning("FBS get_stocks failed: wb_wh=%s err=%s", wb_warehouse_id, e)
            continue
        verified.update(chrt_ids)
        for chrt, amount in (got or {}).items():
            confirmed[int(chrt)] = int(amount)
    return confirmed, verified


async def _upsert_states(
    db: AsyncSession,
    project_id: int,
    wb_warehouse_id: int,
    sent: list[dict[str, Any]],
    confirmed: dict[int, int],
    verified: set[int],
) -> int:
    """Записать переданное состояние по (склад WB, chrtId). Возвращает число расхождений."""
    now = utcnow()
    mismatch = 0
    payload: list[dict[str, Any]] = []
    for item in sent:
        chrt = int(item["chrt_id"])
        is_verified = chrt in verified
        conf = confirmed.get(chrt, 0) if is_verified else None
        error = None
        qty_sent = int(item["amount"])
        if is_verified and conf != item["amount"]:
            mismatch += 1
            error = f"WB подтвердил {conf}, отправляли {item['amount']}"
            # Базовая точка дельты — то, что РЕАЛЬНО лежит у WB, а не то, что мы
            # хотели отправить. Иначе после «204-lie» следующий прогон увидел бы
            # amount == prev, решил «не изменилось» и оставил бы у WB фантомный
            # остаток навсегда (лечилось бы только ручным force).
            qty_sent = int(conf or 0)
        payload.append(
            {
                "project_id": project_id,
                "wb_warehouse_id": wb_warehouse_id,
                "chrt_id": chrt,
                "barcode": item.get("barcode"),
                "nomenclature_id": item.get("nomenclature_id"),
                "qty_sent": qty_sent,
                "qty_confirmed": conf,
                "sent_at": now,
                "verified_at": now if is_verified else None,
                "last_error": error,
            }
        )
    for chunk in _chunked(payload, _STATE_CHUNK):
        stmt = pg_insert(WbFbsStockState).values(chunk)
        await db.execute(
            stmt.on_conflict_do_update(
                constraint="uq_wb_fbs_stock_state",
                set_={
                    "barcode": stmt.excluded.barcode,
                    "nomenclature_id": stmt.excluded.nomenclature_id,
                    "qty_sent": stmt.excluded.qty_sent,
                    "qty_confirmed": stmt.excluded.qty_confirmed,
                    "sent_at": stmt.excluded.sent_at,
                    "verified_at": stmt.excluded.verified_at,
                    "last_error": stmt.excluded.last_error,
                },
            )
        )
    return mismatch


def _finalize_push(push: WbFbsStockPush, *, sent: int, errors: list[str], mismatch: int) -> None:
    """Статус прогона: OK / PARTIAL (часть чанков или расхождения) / ERROR (ничего не уехало)."""
    push.rows_sent = sent
    push.rows_mismatch = mismatch
    push.finished_at = utcnow()
    if errors and sent == 0:
        push.status = FbsPushStatus.ERROR.value
    elif errors or mismatch:
        push.status = FbsPushStatus.PARTIAL.value
    else:
        push.status = FbsPushStatus.OK.value
    parts = errors[:5]
    if mismatch:
        parts.append(f"расхождение верификации: {mismatch} поз.")
    push.error_msg = "; ".join(parts)[:2000] or None


async def _push_one_warehouse(
    db: AsyncSession,
    project_id: int,
    wh: WbFbsWarehouse,
    rows: list[dict[str, Any]],
    push: WbFbsStockPush,
    *,
    client: Any,
    force: bool,
) -> None:
    """Один склад продавца: дельта → PUT чанками → верификация → состояние + журнал."""
    to_send = _select_delta(
        _aggregate_by_chrt(rows, int(wh.max_qty_per_sku or 0)),
        force=force,
    )
    push.rows_changed = len(to_send)
    if not to_send:
        _finalize_push(push, sent=0, errors=[], mismatch=0)
        await db.commit()
        return

    # Фоновый путь без `fatal`: третий элемент здесь всегда None.
    sent, errors, _ = await _put_chunks(client, wh.wb_warehouse_id, to_send)
    confirmed, verified = await _verify_chunks(client, wh.wb_warehouse_id, sent, errors)
    mismatch = await _upsert_states(db, project_id, wh.wb_warehouse_id, sent, confirmed, verified)
    _finalize_push(push, sent=len(sent), errors=errors, mismatch=mismatch)
    await db.commit()
    logger.info(
        "FBS push done: project=%s wb_wh=%s status=%s changed=%s sent=%s mismatch=%s no_chrt=%s",
        project_id,
        wh.wb_warehouse_id,
        push.status,
        push.rows_changed,
        push.rows_sent,
        mismatch,
        push.rows_no_chrt,
    )


async def _select_push_targets(
    db: AsyncSession, project_id: int, wb_warehouse_ids: list[int] | None
) -> list[WbFbsWarehouse]:
    """Склады продавца проекта под трансляцию (опционально — только указанные).

    Автопрогон берёт только активные. ЯВНЫЙ запрос по конкретным складам берёт
    и выключенные — не чтобы их транслировать, а чтобы `_push_stocks_locked`
    оставил в журнале причину отказа: отсечение прямо в запросе давало пустой
    `targets`, ранний `return []` и полную тишину — кнопка отвечала успехом,
    а следов прогона не появлялось вовсе.
    """
    query = select(WbFbsWarehouse).where(WbFbsWarehouse.project_id == project_id)
    if wb_warehouse_ids:
        query = query.where(WbFbsWarehouse.wb_warehouse_id.in_(wb_warehouse_ids))
    else:
        query = query.where(WbFbsWarehouse.is_active == True)  # noqa: E712 — SQLAlchemy expression
    result = await db.execute(query.order_by(WbFbsWarehouse.wb_warehouse_id).limit(200))
    return list(result.scalars().all())


async def push_stocks(
    db: AsyncSession,
    project_id: int,
    *,
    wb_warehouse_ids: list[int] | None = None,
    force: bool = False,
    trigger: str = "auto",
    user_id: int | None = None,
    chrt_ids: list[int] | None = None,
) -> list[int]:
    """Передать остатки в кабинет WB. Возвращает id созданных прогонов.

    Порядок железный: посчитать строки и создать журналы → `commit()` →
    и только потом HTTP. Держать транзакцию через поход в WB нельзя —
    серверный коннект pgbouncer повиснет `idle in transaction` и выест пул.

    Весь прогон идёт под распределённым локом проекта: обе точки входа (кнопка
    из api-контейнера и джоб из worker) зовут именно эту функцию, и два
    одновременных PUT по одному складу дают гонку «кто последний» на живых
    остатках. Лок занят → возвращаем пустой список (прогон не создаём).

    Склады в режиме `observe` пропускаются здесь же (`_push_stocks_locked`), а
    не в роутере: джоб зовёт эту же функцию и роутерный гейт обошёл бы.
    """
    token = await acquire_lock(PUSH_LOCK_NAME, project_id)
    if token is None:
        logger.info("FBS push skipped: project=%s — трансляция уже идёт (лок занят)", project_id)
        return []
    try:
        return await _push_stocks_locked(
            db,
            project_id,
            wb_warehouse_ids=wb_warehouse_ids,
            force=force,
            trigger=trigger,
            user_id=user_id,
            chrt_ids=chrt_ids,
        )
    finally:
        await release_lock(PUSH_LOCK_NAME, project_id, token)


async def _push_stocks_locked(
    db: AsyncSession,
    project_id: int,
    *,
    wb_warehouse_ids: list[int] | None,
    force: bool,
    trigger: str,
    user_id: int | None,
    chrt_ids: list[int] | None = None,
) -> list[int]:
    """Тело трансляции — вызывается только из `push_stocks`, уже под локом."""
    # Точечная отправка: правка одной позиции не должна гонять весь склад и жечь
    # лимит WB. Фильтруем ПОСЛЕ `_build_rows` — формула остатка обязана остаться
    # той же самой, меняется только состав отправляемого.
    only_chrts = {int(c) for c in chrt_ids} if chrt_ids else None
    targets = await _select_push_targets(db, project_id, wb_warehouse_ids)
    if not targets:
        return []

    explicit = bool(wb_warehouse_ids)
    plans: list[tuple[WbFbsWarehouse, list[dict[str, Any]], WbFbsStockPush]] = []
    blocked: list[WbFbsStockPush] = []
    for wh in targets:
        if wh.mode != FbsWarehouseMode.TRANSLATE.value:
            # Режим наблюдения — PUT в WB не делается НИКОГДА. Гейт стоит здесь,
            # а не в роутере: джоб зовёт этот же сервис и роутерную проверку
            # обошёл бы (подключение к складу с ручными остатками не должно
            # ничего перезаписать без явного включения трансляции).
            logger.info(
                "FBS push skipped: project=%s wb_wh=%s — режим observe",
                project_id,
                wh.wb_warehouse_id,
            )
            if explicit:
                blocked.append(_new_push(project_id, wh, trigger, user_id, error_msg=_OBSERVE_MSG))
            continue
        if not wh.is_active:
            # Тумблер «трансляция вкл» — второе, независимое от режима поле.
            # Явный запрос по выключенному складу обязан оставить след: без него
            # ручка отвечала успехом, а прогона не появлялось вообще, и
            # пользователь считал, что остатки уехали.
            logger.info(
                "FBS push skipped: project=%s wb_wh=%s — трансляция выключена",
                project_id,
                wh.wb_warehouse_id,
            )
            if explicit:
                blocked.append(_new_push(project_id, wh, trigger, user_id, error_msg=_INACTIVE_MSG))
            continue
        if wh.is_processing:
            # WB: при isProcessing обновление остатков недоступно. В авто-прогоне
            # молча пропускаем, на явный запрос — оставляем след, почему пусто.
            if explicit:
                blocked.append(_new_push(project_id, wh, trigger, user_id, error_msg=_PROCESSING_MSG))
            continue
        warehouse_ids = await warehouse_service.get_linked_warehouse_ids(db, project_id, wh.wb_warehouse_id)
        try:
            rows = await _build_rows(db, project_id, wh, warehouse_ids)
        except FbsStockDataTooLarge as e:
            # Причина обязана быть видна в журнале: молчаливый пропуск склада
            # читается как «всё передали», а не «данные неполные».
            blocked.append(_new_push(project_id, wh, trigger, user_id, error_msg=str(e)))
            logger.error("FBS push skipped: project=%s wb_wh=%s — %s", project_id, wh.wb_warehouse_id, e)
            continue
        if only_chrts is not None:
            rows = [r for r in rows if r.get("chrt_id") and int(r["chrt_id"]) in only_chrts]
            if not rows:
                continue
        plans.append((wh, rows, _new_push(project_id, wh, trigger, user_id, rows=rows)))

    for _, _, push in plans:
        db.add(push)
    for push in blocked:
        db.add(push)
    await db.flush()
    push_ids = [p.id for _, _, p in plans] + [p.id for p in blocked]
    await db.commit()  # ДО походов в WB

    if plans:
        await _run_plans(db, project_id, plans, force=force)

    await invalidate_cache(CACHE_STOCK_PREVIEW)
    return push_ids


async def _run_plans(
    db: AsyncSession,
    project_id: int,
    plans: list[tuple[WbFbsWarehouse, list[dict[str, Any]], WbFbsStockPush]],
    *,
    force: bool,
) -> None:
    """Отработать запланированные прогоны, не оставляя журналы висеть в RUNNING.

    Любая неожиданная ошибка (нет ключа интеграции, брейкер открыт, сбой WB)
    финализируется в статус ERROR: страница опрашивает `/fbs/stock/pushes`,
    и вечный RUNNING читался бы как «всё ещё идёт».
    """
    try:
        client = await _get_client(db, project_id)
        await db.commit()  # чтение ключа интеграции тоже открывает транзакцию
    except asyncio.CancelledError:
        raise
    except Exception as e:  # причина уезжает в журнал прогона
        await db.rollback()
        for _, _, push in plans:
            _finalize_push(push, sent=0, errors=[f"{type(e).__name__}: {e}"], mismatch=0)
        await db.commit()
        logger.warning("FBS push aborted: project=%s err=%s", project_id, e)
        return

    for wh, rows, push in plans:
        try:
            await _push_one_warehouse(db, project_id, wh, rows, push, client=client, force=force)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await db.rollback()
            _finalize_push(push, sent=0, errors=[f"{type(e).__name__}: {e}"], mismatch=0)
            await db.commit()
            logger.exception("FBS push failed: project=%s wb_wh=%s", project_id, wh.wb_warehouse_id)


def _new_push(
    project_id: int,
    wh: WbFbsWarehouse,
    trigger: str,
    user_id: int | None,
    *,
    rows: list[dict[str, Any]] | None = None,
    error_msg: str | None = None,
) -> WbFbsStockPush:
    """Строка журнала прогона. `error_msg` — отправки не будет, журнал сразу ERROR."""
    push = WbFbsStockPush(
        project_id=project_id,
        wb_warehouse_id=wh.wb_warehouse_id,
        trigger=trigger,
        user_id=user_id,
        status=FbsPushStatus.RUNNING.value,
        started_at=utcnow(),
        rows_total=len(rows or []),
        rows_no_chrt=_count_no_chrt(rows or []),
    )
    if error_msg:
        push.status = FbsPushStatus.ERROR.value
        push.finished_at = push.started_at
        push.error_msg = error_msg[:2000]
    return push


async def fail_running_pushes(db: AsyncSession, project_id: int, *, since: datetime, reason: str) -> int:
    """Закрыть журналы, зависшие в RUNNING после прерванного прогона. Возвращает число строк.

    Нужна ОБОИМ путям: прогон снимается по бюджету времени (`wait_for`), а
    `_run_plans` пробрасывает отмену, не дописав журнал. Без этого страница
    вечно показывала бы «Выполняется» — ровно то состояние, которого домен
    избегает в `_run_plans`. Ручной путь зовёт её из роутера, фоновый — из
    `scheduler/jobs/wb_fbs._handle_stock_push`.
    """
    result = await db.execute(
        update(WbFbsStockPush)
        .where(
            WbFbsStockPush.project_id == project_id,
            WbFbsStockPush.status == FbsPushStatus.RUNNING.value,
            WbFbsStockPush.started_at >= since,
        )
        .values(status=FbsPushStatus.ERROR.value, finished_at=utcnow(), error_msg=reason[:2000])
    )
    await db.commit()
    await invalidate_cache(CACHE_STOCK_PREVIEW)
    return int(result.rowcount or 0)  # type: ignore[attr-defined]


# ─── Первичная сверка склада («захват») ──────────────────────────────────────
#
# Пуш ДЕЛЬТОВЫЙ: позиция, которую мы считаем в ноль и ни разу не отправляли,
# не уезжает вовсе (`_select_delta`), а `_orphan_state_rows` ходит только по
# `wb_fbs_stock_states` — то есть по тому, что МЫ уже слали. Остаток,
# проставленный в кабинете руками ДО подключения, не виден ни одному из этих
# путей: он так и продаётся, хотя на привязанном складе товара может не быть
# (прод-кейс: склад «Фрунзе Мигфул», ковёр 2043788808268 — 125 шт в кабинете).
#
# Сверка спрашивает у WB фактические остатки по ВСЕЙ нашей номенклатуре
# (POST /stocks чанками ≤1000) и сравнивает с тем, что отдал бы пуш. Обратная
# сторона метода: chrtId, которого нет в нашей номенклатуре, WB не покажет —
# «списка всех остатков склада» в Marketplace API не существует, спрашивать
# можно только про известные ключи.


async def _load_project_chrts(db: AsyncSession, project_id: int) -> tuple[dict[int, Any], int]:
    """Номенклатура проекта в разрезе chrtId: ({chrt_id: строка}, сколько без chrtId).

    Один chrtId может стоять у нескольких баркодов — для показа берём МЕНЬШИЙ
    id номенклатуры (тот же детерминизм, что у `orders_service._resolve_nomenclature`).
    Строки без `chrt_id` в WB не транслируются и проверить их состояние нечем:
    они уходят отдельным счётчиком, а не молча теряются.
    """
    result = await db.execute(
        select(
            Nomenclature.id,
            Nomenclature.chrt_id,
            Nomenclature.barcode,
            Nomenclature.article_seller,
            Nomenclature.article_wb,
        )
        .where(Nomenclature.project_id == project_id, Nomenclature.chrt_id.is_not(None))
        .order_by(Nomenclature.chrt_id, Nomenclature.id)
        # +1 отличает «ровно потолок» (данные полные) от «есть хвост».
        .limit(_MAX_RECONCILE_ROWS + 1)
    )
    rows = result.all()
    if len(rows) > _MAX_RECONCILE_ROWS:
        raise FbsStockDataTooLarge(
            f"Номенклатура проекта не помещается в потолок сверки ({_MAX_RECONCILE_ROWS} строк). "
            "Частичная сверка врала бы: непроверенные позиции выглядели бы как «всё в порядке»."
        )
    by_chrt: dict[int, Any] = {}
    for row in rows:
        by_chrt.setdefault(int(row.chrt_id), row)

    no_chrt = await db.execute(
        select(func.count())
        .select_from(Nomenclature)
        .where(Nomenclature.project_id == project_id, Nomenclature.chrt_id.is_(None))
    )
    return by_chrt, int(no_chrt.scalar() or 0)


async def _fetch_wb_stocks(client: Any, wb_warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
    """Что РЕАЛЬНО стоит в кабинете WB: POST /stocks чанками ≤1000 → {chrt_id: amount}."""
    out: dict[int, int] = {}
    for chunk in _chunked(chrt_ids, _WB_CHUNK):
        got = await client.get_stocks(wb_warehouse_id, chunk)
        for chrt, amount in (got or {}).items():
            out[int(chrt)] = int(amount or 0)
    return out


def _override_blocked_chrts(rows: list[dict[str, Any]]) -> set[int]:
    """chrtId, которым ручное количество запрещает выдачу (потолок позиции 0).

    Берётся тот же `_group_override_limit`, что и в расчёте: причина в сверке
    обязана совпадать с тем, почему позиция реально получила ноль.
    """
    groups: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        chrt = row.get("chrt_id")
        if chrt:
            groups.setdefault(int(chrt), []).append(row)
    return {chrt for chrt, group in groups.items() if _group_override_limit(group) == 0}


def _reconcile_reason(chrt: int, ours: dict[int, int], blocked: set[int], *, has_links: bool) -> str:
    """Почему позиция нам не принадлежит — человекочитаемо."""
    if not has_links:
        return REASON_NO_LINKS
    if chrt in blocked:
        return REASON_OVERRIDE_ZERO
    if chrt in ours:
        return REASON_ZERO
    return REASON_NO_STOCK


def _reconcile_row(
    chrt: int,
    nom: Any,
    wb_amount: int,
    our_amount: int,
    *,
    kind: str,
    reason: str | None,
) -> dict[str, Any]:
    """Строка сверки (FbsReconcileRow).

    `nomenclature_id` схема наружу не отдаёт — ключ нужен `apply_reconcile`
    для записи `WbFbsStockState` (Pydantic лишний ключ игнорирует).
    """
    return {
        "chrt_id": chrt,
        "barcode": getattr(nom, "barcode", None),
        "article_seller": getattr(nom, "article_seller", None),
        "nm_id": getattr(nom, "article_wb", None),
        "nomenclature_id": getattr(nom, "id", None),
        "wb_amount": wb_amount,
        "our_amount": our_amount,
        "kind": kind,
        "reason": reason,
    }


def _classify_reconcile(
    wb_stocks: dict[int, int],
    ours: dict[int, int],
    by_chrt: dict[int, Any],
    blocked: set[int],
    *,
    has_links: bool,
    prev_sent: dict[int, int | None] | None = None,
) -> list[dict[str, Any]]:
    """Расхождения между кабинетом и нашим расчётом, опасные — первыми.

    `unmanaged` — в WB лежит остаток, а мы этой позицией не управляем (наш
    расчёт даёт ноль или позиции нет в расчёте вовсе): именно она продаётся
    мимо нас. `mismatch` — цифры разошлись: сам пуш это НЕ чинит, пока база
    дельты (`qty_sent`) равна нашему расчёту, — выравнивает `apply_reconcile`
    (`_realign_mismatch_states`), после чего ближайший пуш отправит верное число.

    Ноль в кабинете отбрасывается НЕ безусловно. Обратное расхождение
    («мы отдаём N, а в кабинете 0») при `qty_sent == N` — тупик: дельта решит
    «не изменилось» и не отправит позицию никогда, а WB её не продаёт
    (потерянные продажи). Такую строку показываем как `mismatch` с причиной
    `wb_zero`. Прочие нули пропускаем: у нас тоже ноль (продавать нечего) либо
    цифру просто ещё не отправляли — это догонит обычный пуш.
    """
    rows: list[dict[str, Any]] = []
    prev_sent = prev_sent or {}
    for chrt, wb_amount in wb_stocks.items():
        our_amount = int(ours.get(chrt, 0))
        if wb_amount <= 0:
            if our_amount > 0 and prev_sent.get(chrt) == our_amount:
                rows.append(
                    _reconcile_row(
                        chrt, by_chrt.get(chrt), 0, our_amount, kind=RECONCILE_MISMATCH, reason=REASON_WB_ZERO
                    )
                )
            continue
        if our_amount <= 0:
            kind, reason = RECONCILE_UNMANAGED, _reconcile_reason(chrt, ours, blocked, has_links=has_links)
        elif our_amount != wb_amount:
            kind, reason = RECONCILE_MISMATCH, None
        else:
            continue  # сошлось — в отчёт не тащим
        rows.append(_reconcile_row(chrt, by_chrt.get(chrt), wb_amount, our_amount, kind=kind, reason=reason))
    # Сначала самое опасное: чем больше висит в кабинете, тем дороже ошибка.
    rows.sort(key=lambda r: (-int(r["wb_amount"]), int(r["chrt_id"])))
    return rows


async def reconcile_warehouse(db: AsyncSession, project_id: int, wb_warehouse_id: int) -> dict[str, Any]:
    """Первичная сверка склада продавца: что висит в WB помимо нашей трансляции.

    ЧТЕНИЕ (POST /stocks по HTTP, состояние WB не меняется) — работает и в
    режиме `safe`. Наш расчёт берётся из ОБЩЕЙ формулы (`_build_rows` +
    `_aggregate_by_chrt`), то есть ровно тот, что уехал бы пушем: вторая
    формула здесь разошлась бы с реальностью на первом же ручном количестве.
    """
    wh = await warehouse_service.get_warehouse(db, project_id, wb_warehouse_id)
    warehouse_ids = await warehouse_service.get_linked_warehouse_ids(db, project_id, wb_warehouse_id)
    by_chrt, rows_no_chrt = await _load_project_chrts(db, project_id)

    our_rows = await _build_rows(db, project_id, wh, warehouse_ids)
    aggregated = _aggregate_by_chrt(our_rows, int(wh.max_qty_per_sku or 0))
    ours = {int(i["chrt_id"]): int(i["amount"]) for i in aggregated}
    # База дельты (`qty_sent`) нужна классификации: только по ней видно, когда
    # расхождение — тупик, а когда его догонит обычный пуш.
    prev_sent: dict[int, int | None] = {
        int(i["chrt_id"]): (None if i["prev"] is None else int(i["prev"])) for i in aggregated
    }
    blocked = _override_blocked_chrts(our_rows)

    chrt_ids = sorted(by_chrt)
    if not chrt_ids:
        # Пустая номенклатура (или chrtId ещё не синхронизированы) — в WB не идём:
        # спрашивать нечего, а FbsNotConfigured на пустом каталоге только пугает.
        await db.commit()
        return _reconcile_out(wh, [], checked=0, rows_no_chrt=rows_no_chrt)

    client = await _get_client(db, project_id)
    await db.commit()  # не держим транзакцию через поход в WB
    wb_stocks = await _fetch_wb_stocks(client, wb_warehouse_id, chrt_ids)

    rows = _classify_reconcile(
        wb_stocks, ours, by_chrt, blocked, has_links=bool(warehouse_ids), prev_sent=prev_sent
    )
    return _reconcile_out(wh, rows, checked=len(chrt_ids), rows_no_chrt=rows_no_chrt)


def _reconcile_out(
    wh: WbFbsWarehouse, rows: list[dict[str, Any]], *, checked: int, rows_no_chrt: int
) -> dict[str, Any]:
    """Итог сверки (FbsReconcileOut) со счётчиками по видам строк."""
    unmanaged = [r for r in rows if r["kind"] == RECONCILE_UNMANAGED]
    return {
        "wb_warehouse_id": wh.wb_warehouse_id,
        "wb_warehouse_name": wh.name,
        "checked": checked,
        "unmanaged_positions": len(unmanaged),
        "unmanaged_units": sum(int(r["wb_amount"]) for r in unmanaged),
        "mismatch_positions": sum(1 for r in rows if r["kind"] == RECONCILE_MISMATCH),
        "rows": rows,
        "rows_no_chrt": rows_no_chrt,
    }


async def apply_reconcile(
    db: AsyncSession,
    project_id: int,
    wb_warehouse_id: int,
    chrt_ids: list[int] | None = None,
    *,
    user_id: int | None = None,
) -> dict[str, Any]:
    """Обнулить в WB то, чем мы не управляем. Возвращает FbsActionOut.

    Сверка ПЕРЕСЧИТЫВАЕТСЯ здесь заново, а переданные `chrt_ids` работают только
    как фильтр по свежему результату. Слепая отправка клиентского списка была бы
    дырой: через эту ручку можно было бы занулить любую позицию склада, включая
    ту, которой мы управляем и которая прямо сейчас продаётся.

    Прогон пишется в `WbFbsStockPush(trigger="manual")` — «захват» склада обязан
    остаться в журнале (кто и когда), — и обновляет `WbFbsStockState(qty_sent=0)`,
    иначе следующий дельта-пуш этих позиций снова не увидит.

    Три отказа ДО лока и до похода в WB (все необратимы, если их пропустить):
    склад в режиме наблюдения (в кабинет не пишем НИКОГДА, пока трансляцию
    не включили явно), склад в обработке у WB (каждый PUT вернул бы 409 ценой
    10 запросов бакета) и склад без единой привязки (наш расчёт пуст →
    «неуправляем» ВЕСЬ каталог кабинета, и кнопка снесла бы все продажи по FBS).
    """
    from backend.integrations.wb_fbs_api import WbFbsRateLimited, WbFbsWriteBlocked

    wh = await warehouse_service.get_warehouse(db, project_id, wb_warehouse_id)
    if wh.mode != FbsWarehouseMode.TRANSLATE.value:
        # Обнуление — такая же запись в кабинет, как пуш (`PUT /stocks`, amount 0),
        # и гейт режима обязан стоять здесь же, а не только в `_push_stocks_locked`:
        # иначе кнопка «Обнулить их в WB» снимала бы с продажи товар склада,
        # остатки которого продавец ведёт руками (см. DOMAIN_WB_FBS: склад
        # в `observe` не получает PUT НИКОГДА).
        raise FbsWarehouseObserveMode(_OBSERVE_MSG)
    if wh.is_processing:
        raise FbsWarehouseProcessing(_PROCESSING_MSG)
    if not await warehouse_service.get_linked_warehouse_ids(db, project_id, wb_warehouse_id):
        raise FbsReconcileNoLinks(
            "Склад продавца не привязан ни к одному нашему складу — обнулять нельзя: "
            "без привязок неуправляемым выглядит весь каталог кабинета. "
            "Привяжите склад и повторите сверку."
        )
    # Лок берётся ДО пересчёта, а не перед самим PUT: иначе между сверкой и
    # обнулением успевает пройти тик джоба, отправить свежий остаток — и мы
    # затрём его нулём по протухшему отчёту.
    token = await acquire_lock(PUSH_LOCK_NAME, project_id)
    if token is None:
        raise FbsReconcileBusy("Трансляция остатков уже идёт — повторите сверку через минуту")
    try:
        report = await reconcile_warehouse(db, project_id, wb_warehouse_id)
        # Выравнивание базы дельты идёт по ВСЕМ `mismatch`-строкам отчёта, а не
        # по клиентскому фильтру: фронт присылает только `unmanaged`-позиции,
        # и с фильтром выравнивание было бы вечным no-op. В WB это ничего
        # не пишет — правится только наш `qty_sent`.
        aligned = await _realign_mismatch_states(db, project_id, wb_warehouse_id, report["rows"])
        wanted = {int(c) for c in chrt_ids} if chrt_ids else None
        targets = [
            r
            for r in report["rows"]
            if r["kind"] == RECONCILE_UNMANAGED and (wanted is None or int(r["chrt_id"]) in wanted)
        ]
        if not targets:
            return {
                "ok": True,
                "message": "Обнулять нечего: неуправляемых позиций в WB не найдено" + _aligned_suffix(aligned),
                "affected": 0,
            }
        out = await _apply_reconcile_locked(
            db,
            project_id,
            wh,
            targets,
            user_id=user_id,
            fatal=(WbFbsWriteBlocked, WbFbsRateLimited),
        )
        out["message"] = f"{out.get('message') or ''}{_aligned_suffix(aligned)}"
        return out
    finally:
        await release_lock(PUSH_LOCK_NAME, project_id, token)


def _aligned_suffix(aligned: int) -> str:
    """Хвост сообщения про выровненные `mismatch`-позиции (пусто, если их не было)."""
    if not aligned:
        return ""
    return f". Выровнена база дельты у {aligned} поз. — ближайшая трансляция отправит верные цифры"


async def _realign_mismatch_states(
    db: AsyncSession, project_id: int, wb_warehouse_id: int, rows: list[dict[str, Any]]
) -> int:
    """Записать в базу дельты то, что РЕАЛЬНО стоит у WB (строки `mismatch`).

    Дельта считается против `WbFbsStockState.qty_sent`. Если продавец правил
    остаток руками (или чужой прогон нарвался на «204-lie»), наш расчёт может
    совпасть с `qty_sent` — тогда `_select_delta` решает «не изменилось», пуш
    молчит, и чужая цифра продаётся в кабинете вечно. Обнулять такую позицию
    нельзя (товар живой), поэтому лечим базу дельты: пишем прочитанное из WB
    значение — ближайший обычный пуш увидит разницу и отправит верное число.
    Это тот же канон, что уже применён в `_upsert_states` после «204-lie»:
    база дельты — подтверждённое кабинетом, а не то, что мы хотели отправить.

    В WB здесь ничего не уходит — только наша таблица состояний.
    """
    targets = [r for r in rows if r["kind"] == RECONCILE_MISMATCH]
    if not targets:
        return 0
    items: list[dict[str, Any]] = [
        {
            "chrt_id": int(r["chrt_id"]),
            "amount": int(r["wb_amount"]),
            "barcode": r.get("barcode"),
            "nomenclature_id": r.get("nomenclature_id"),
        }
        for r in targets
    ]
    # Значения только что прочитаны из кабинета (`_fetch_wb_stocks`) — это и есть
    # подтверждённое состояние, «расхождением» его считать нельзя.
    confirmed = {int(r["chrt_id"]): int(r["wb_amount"]) for r in targets}
    await _upsert_states(db, project_id, wb_warehouse_id, items, confirmed, set(confirmed))
    await db.commit()
    logger.info(
        "FBS reconcile realign: project=%s wb_wh=%s rows=%s", project_id, wb_warehouse_id, len(items)
    )
    return len(items)


async def _apply_reconcile_locked(
    db: AsyncSession,
    project_id: int,
    wh: WbFbsWarehouse,
    targets: list[dict[str, Any]],
    *,
    user_id: int | None,
    fatal: tuple[type[BaseException], ...],
) -> dict[str, Any]:
    """Тело обнуления — уже под локом проекта. Журнал закрывается при любом исходе.

    Фатальная ошибка (гейт режима, 429) обрывает отправку, но НЕ отменяет уже
    отправленное: обнуление необратимо, поэтому сначала верифицируем и пишем
    состояние по фактически ушедшим чанкам, закрываем журнал реальным
    `rows_sent`, и только потом пробрасываем ошибку наверх.
    """
    push = _new_push(project_id, wh, "manual", user_id)
    push.rows_total = len(targets)
    push.rows_changed = len(targets)
    db.add(push)
    await db.flush()
    await db.commit()  # ДО походов в WB

    items = [
        {
            "chrt_id": int(r["chrt_id"]),
            "amount": 0,
            "barcode": r.get("barcode"),
            "nomenclature_id": r.get("nomenclature_id"),
        }
        for r in targets
    ]
    try:
        client = await _get_client(db, project_id)
        await db.commit()  # чтение ключа интеграции тоже открывает транзакцию
        sent, errors, fatal_exc = await _put_chunks(client, wh.wb_warehouse_id, items, fatal=fatal)
        # Верификация и состояние — по ФАКТИЧЕСКИ ушедшим строкам, даже когда
        # прогон дальше оборвала фатальная ошибка: иначе журнал напишет
        # «отправлено 0» после реального обнуления, `qty_sent` останется старым,
        # а «204-lie» на ушедших чанках никто не проверит.
        confirmed, verified = await _verify_chunks(client, wh.wb_warehouse_id, sent, errors)
        mismatch = await _upsert_states(db, project_id, wh.wb_warehouse_id, sent, confirmed, verified)
        if fatal_exc is not None:
            errors.append(f"{type(fatal_exc).__name__}: {fatal_exc}")
        _finalize_push(push, sent=len(sent), errors=errors, mismatch=mismatch)
        await db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # «Нет ключа», сбой БД и прочее неожиданное — наверх как есть
        # (роутер → 409/422/500), но журнал не бросаем в RUNNING.
        await db.rollback()
        _finalize_push(push, sent=0, errors=[f"{type(e).__name__}: {e}"], mismatch=0)
        await db.commit()
        logger.warning("FBS reconcile failed: project=%s wb_wh=%s err=%s", project_id, wh.wb_warehouse_id, e)
        raise

    await invalidate_cache(CACHE_STOCK_PREVIEW)
    sent_chrts = {int(i["chrt_id"]) for i in sent}
    units = sum(int(r["wb_amount"]) for r in targets if int(r["chrt_id"]) in sent_chrts)
    logger.info(
        "FBS reconcile applied: project=%s wb_wh=%s zeroed=%s units=%s status=%s",
        project_id,
        wh.wb_warehouse_id,
        len(sent),
        units,
        push.status,
    )
    if fatal_exc is not None:
        # Журнал и состояния уже сохранены — теперь можно отдать причину отказа
        # наверх (роутер → 409/429). Сколько успело уехать, видно в логе прогона.
        raise fatal_exc
    message = f"Обнулено в WB: {len(sent)} поз. / {units} шт"
    if push.error_msg:
        message = f"{message}. {push.error_msg}"
    return {"ok": push.status == FbsPushStatus.OK.value, "message": message, "affected": len(sent)}
