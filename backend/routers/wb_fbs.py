# ruff: noqa: RUF001, RUF002, RUF003
"""
Router: /fbs — WB FBS (продажи со склада продавца, Marketplace API).

Тонкий HTTP-слой: вся логика — в `backend/services/wb_fbs/*`. Здесь только
валидация входа, перевод доменных исключений в внятные HTTP-коды и фоновый
запуск трансляции остатков.

Два правила, которые легко нарушить и трудно поймать:
  • статические пути объявлены ДО path-параметров (иначе `/supplies/sync`
    матчится как `/supplies/{wb_supply_id}`);
  • `POST /fbs/stock/push` НЕ ждёт похода в WB — rate-limit Marketplace API
    (300/мин, плюс верификация каждого чанка) держит запрос минутами, фронт
    словил бы таймаут. Прогон уходит в фоновый таск со своей сессией БД,
    прогресс читается из `GET /fbs/stock/pushes`.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import AsyncSessionLocal, get_db
from backend.integrations.wb_fbs_api import (
    WbFbsApiError,
    WbFbsRateLimited,
    WbFbsWriteBlocked,
    current_mode,
    is_write_enabled,
    resolve_base_url,
)
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.schemas.wb_fbs import (
    ALLOWED_ORDER_STATUS_FILTERS,
    ALLOWED_STICKER_TYPES,
    ALLOWED_SUPPLY_STATUSES,
    FbsActionOut,
    FbsLinkCreate,
    FbsModeOut,
    FbsOfficeOut,
    FbsOrderBackfillOut,
    FbsOrderBackfillRequest,
    FbsOrderListOut,
    FbsOrderStatsOut,
    FbsOrderOut,
    FbsOrderTimelineOut,
    FbsOverrideSet,
    FbsPickListOut,
    FbsReconcileApply,
    FbsMatrixOut,
    FbsReconcileOut,
    FbsStickerOut,
    FbsStickerRequest,
    FbsStockPreviewOut,
    FbsStockPushOut,
    FbsStockPushRequest,
    FbsSupplyAddOrders,
    FbsSupplyBarcodeOut,
    FbsSupplyBulkCreate,
    FbsSupplyBulkOut,
    FbsSupplyCreate,
    FbsSupplyOut,
    FbsSupplyPlanOut,
    FbsWarehouseCreate,
    FbsWarehouseOut,
    FbsWarehouseSummaryOut,
    FbsWarehouseRename,
    FbsWarehouseSettingsUpdate,
    FbsWriteoffIssuesOut,
)
from backend.services.wb_fbs import (
    orders_service,
    stock_service,
    supplies_service,
    warehouse_service,
)
from backend.services.wb_fbs import orders_stats as orders_stats_service
from backend.services.wb_fbs.client_factory import (
    FBS_SANDBOX_SERVICE,
    FBS_SERVICE,
    FbsNotConfigured,
    get_fbs_api_key,
    service_for_mode,
)
from backend.services.wb_fbs.locks import PUSH_LOCK_NAME, PUSH_RUN_BUDGET_SEC, is_locked
from backend.utils.rate_limit import rate_limit_write
from backend.utils.time import utcnow

logger = logging.getLogger("dds.wb_fbs")

router = APIRouter(prefix="/fbs", tags=["WB FBS"])

#: Единственный текст про отсутствие ключа — фронт показывает его как есть.
FBS_NOT_CONFIGURED = (
    "К проекту не подключён ключ WB категории «Маркетплейс». "
    "Добавьте его в Настройках → Интеграции (тип ключа wb_marketplace)."
)
#: То же для режима песочницы: там нужен ОТДЕЛЬНЫЙ токен, боевой не подойдёт.
FBS_SANDBOX_NOT_CONFIGURED = (
    "Включён режим песочницы WB, а ключа песочницы у проекта нет. "
    "Добавьте токен «Маркетплейс» со scope Test в Настройках → Интеграции "
    "(тип ключа wb_marketplace_sandbox)."
)

#: Ссылки на фоновые таски, иначе GC может собрать таск на середине прогона.
_background_tasks: set[asyncio.Task] = set()


# ─── Перевод исключений домена в HTTP ────────────────────────────────────────


def _raise_wb_error(exc: WbFbsApiError) -> NoReturn:
    """Ошибка Marketplace API → код, по которому фронту понятно, что делать."""
    msg = str(exc) or "Ошибка WB Marketplace API"
    if isinstance(exc, WbFbsRateLimited):
        retry = int(getattr(exc, "retry_after", 0) or 0)
        raise HTTPException(
            429,
            f"WB просит подождать {retry} с и повторить: {msg}",
            headers={"Retry-After": str(max(retry, 1))},
        ) from exc
    status = int(getattr(exc, "status", 0) or 0)
    if status == 404:
        raise HTTPException(404, msg) from exc
    # 409 (UploadDataLimit, StoreIsProcessing, SupplyNotClosed…) и 406
    # (WarehouseStocksUpdateBlock) — конфликт состояния, не ошибка ввода.
    if status in (406, 409):
        raise HTTPException(409, msg) from exc
    if 400 <= status < 500:
        raise HTTPException(422, msg) from exc
    raise HTTPException(502, f"WB Marketplace API недоступен: {msg}") from exc


def _raise_domain_error(exc: Exception) -> NoReturn:
    """Доменная ошибка сервиса → 404/422 (никогда не голый 500)."""
    msg = str(exc) or "Некорректный запрос"
    low = msg.lower()
    if "не найден" in low or "не существует" in low:
        raise HTTPException(404, msg) from exc
    raise HTTPException(422, msg) from exc


@contextmanager
def _fbs_errors() -> Iterator[None]:
    """Единая трансляция исключений сервисов FBS в HTTPException."""
    try:
        yield
    except FbsNotConfigured as e:
        # Текст исключения уже пользовательский (в песочнице он объясняет
        # про отдельный токен scope Test) — константа остаётся фолбэком.
        raise HTTPException(409, str(e) or FBS_NOT_CONFIGURED) from e
    except WbFbsWriteBlocked as e:
        # Режим safe: запись в WB отключена. Это конфликт состояния контура,
        # а не ошибка ввода и тем более не 500.
        raise HTTPException(409, e.message) from e
    except WbFbsApiError as e:
        _raise_wb_error(e)
    except (ValueError, LookupError) as e:
        _raise_domain_error(e)
    except Exception as e:
        # Доменные исключения сервисов FBS (FbsOrderError, FbsSupplyError, …)
        # наследуются от Exception напрямую и живут каждое в своём модуле —
        # ловим их по имени класса, чтобы роутер не тащил импорт из каждого.
        # Всё остальное — настоящая ошибка, пусть уходит в 500 с трейсом.
        if type(e).__name__.startswith("Fbs"):
            _raise_domain_error(e)
        raise


# ─── Режим контура ───────────────────────────────────────────────────────────


@router.get("/mode", response_model=FbsModeOut)
async def get_mode(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Режим контура FBS — фронт рисует по нему баннер и гасит кнопки записи.

    Оба флага ключей возвращаем всегда: в песочнице нужен свой токен (scope Test),
    и пользователю должно быть видно, какого именно не хватает.
    """
    mode = current_mode()
    return FbsModeOut(
        mode=mode,
        write_enabled=is_write_enabled(mode),
        api_base=resolve_base_url(mode),
        has_key=await get_fbs_api_key(db, project.id, service=FBS_SERVICE) is not None,
        has_sandbox_key=await get_fbs_api_key(db, project.id, service=FBS_SANDBOX_SERVICE) is not None,
    )


# ─── Склады WB (offices) и склады продавца ───────────────────────────────────


@router.get("/offices", response_model=list[FbsOfficeOut])
async def list_offices(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Справочник складов WB (пунктов приёма) — живой запрос в кабинет."""
    with _fbs_errors():
        return await warehouse_service.list_offices(db, project.id)


@router.get("/warehouses", response_model=list[FbsWarehouseOut])
async def list_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Склады продавца WB с нашими настройками трансляции и привязками."""
    with _fbs_errors():
        return await warehouse_service.list_warehouses(db, project.id)


@router.post("/warehouses/sync", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def sync_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Синк справочника складов продавца из WB. `affected` — сколько строк обновлено."""
    with _fbs_errors():
        count = await warehouse_service.sync_warehouses(db, project.id)
    return FbsActionOut(ok=True, message="Справочник складов обновлён", affected=int(count or 0))


@router.post("/warehouses", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def create_warehouse(
    payload: FbsWarehouseCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать склад продавца НА СТОРОНЕ WB. `affected` — wb_warehouse_id нового склада."""
    with _fbs_errors():
        wb_warehouse_id = await warehouse_service.create_wb_warehouse(
            db, project.id, payload.name, payload.office_id
        )
    return FbsActionOut(
        ok=True,
        message=f"Склад «{payload.name}» создан в WB (id {wb_warehouse_id})",
        affected=int(wb_warehouse_id),
    )


@router.put(
    "/warehouses/{wb_warehouse_id}",
    response_model=FbsActionOut,
    dependencies=[Depends(rate_limit_write)],
)
async def rename_warehouse(
    payload: FbsWarehouseRename,
    wb_warehouse_id: int = Path(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Переименовать склад продавца в WB (смена офиса — не чаще раза в сутки)."""
    with _fbs_errors():
        await warehouse_service.rename_wb_warehouse(
            db, project.id, wb_warehouse_id, payload.name, payload.office_id
        )
    return FbsActionOut(ok=True, message="Склад обновлён в WB", affected=1)


@router.patch(
    "/warehouses/{wb_warehouse_id}/settings",
    response_model=FbsWarehouseOut,
    dependencies=[Depends(rate_limit_write)],
)
async def update_warehouse_settings(
    payload: FbsWarehouseSettingsUpdate,
    wb_warehouse_id: int = Path(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Наши настройки трансляции (WB не трогаем).

    Тумблер, источник остатка, буфер, потолок на SKU, режим склада
    (`observe` / `translate`) и гейт по FBO (`fbo_max_qty`: отдаём в FBS,
    только пока на складах WB осталось не больше этого; `-1` — снять гейт).

    Включение рискованной тройки «is_active + translate + ff_mirror» при
    зеркале ФФ выше учёта гейтится сервисом: 409 со структурированным detail
    (`code=fbs_mirror_above_ledger` + цифры разрыва — фронт опознаёт «свой»
    конфликт по коду, а не по тексту); `force=true` применяет как есть.

    🔴 `except FbsMirrorAboveLedger` обязан стоять ВНУТРИ `_fbs_errors()`:
    имя класса начинается с `Fbs`, и общий обработчик перевёл бы его в 422
    (`_raise_domain_error`) — 409 деградировал бы молча (закреплено тестом).
    """
    with _fbs_errors():
        try:
            return await warehouse_service.update_settings(db, project.id, wb_warehouse_id, payload)
        except warehouse_service.FbsMirrorAboveLedger as e:
            # Конфликт состояния склада (зеркало обещает больше учёта), а не
            # ошибка ввода: 409, как у гардов сверки выше. Структурированный
            # detail — прецедент backend/routers/counterparty.py (inn_conflict).
            raise HTTPException(
                409,
                detail={
                    "code": "fbs_mirror_above_ledger",
                    "message": str(e),
                    "mirror_over_ledger": e.mirror_over_ledger,
                    "ledger_total": e.ledger_total,
                    "mirror_total": e.mirror_total,
                },
            ) from e


# ─── Привязки наших складов ──────────────────────────────────────────────────


@router.post("/links", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def link_warehouse(
    payload: FbsLinkCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Привязать наш склад к складу продавца WB. `affected` — id привязки."""
    with _fbs_errors():
        link_id = await warehouse_service.link_warehouse(
            db, project.id, payload.wb_warehouse_id, payload.warehouse_id
        )
    return FbsActionOut(ok=True, message="Склад привязан", affected=int(link_id))


@router.delete("/links/{link_id}", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def unlink_warehouse(
    link_id: int = Path(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Снять привязку нашего склада от склада продавца WB."""
    with _fbs_errors():
        await warehouse_service.unlink_warehouse(db, project.id, link_id)
    return FbsActionOut(ok=True, message="Привязка снята", affected=1)


# ─── Остатки ─────────────────────────────────────────────────────────────────


@router.get("/stock/preview", response_model=FbsStockPreviewOut)
async def preview_stock(
    wb_warehouse_id: int = Query(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Что уедет в WB: полная расшифровка формулы по каждой позиции."""
    with _fbs_errors():
        return await stock_service.preview_stock(db, project.id, wb_warehouse_id)


async def _push_stocks_bg(
    project_id: int,
    wb_warehouse_ids: list[int],
    force: bool,
    user_id: int | None,
    chrt_ids: list[int] | None = None,
) -> None:
    """Прогон трансляции вне HTTP-запроса: своя сессия БД (сессия запроса уже закрыта).

    Бюджет времени — тот же, что у джоба (`PUSH_RUN_BUDGET_SEC`), и он строго
    меньше TTL распределённого лока. Без `wait_for` ручной прогон был не
    ограничен ничем: с `force=true` по нескольким складам он переживал TTL, лок
    снимался сам, и ближайший тик джоба заходил в критическую секцию — два PUT
    по одному складу и `qty_sent` от чужого прогона.
    """
    started_at = utcnow()
    try:
        async with AsyncSessionLocal() as db:
            push_ids = await asyncio.wait_for(
                stock_service.push_stocks(
                    db,
                    project_id,
                    wb_warehouse_ids=wb_warehouse_ids or None,
                    force=force,
                    trigger="manual",
                    user_id=user_id,
                    chrt_ids=chrt_ids,
                ),
                timeout=PUSH_RUN_BUDGET_SEC,
            )
        logger.info(
            "FBS stock push (manual): project %s — прогоны %s",
            project_id,
            push_ids,
        )
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        # Лок снят (finally в push_stocks) — следующий прогон стартует штатно.
        # Журналы прерванного прогона закрываем сами: вечный RUNNING на странице
        # читается как «всё ещё идёт».
        logger.error(
            "FBS stock push (manual) timeout: project %s — прогон прерван по бюджету %d c",
            project_id,
            PUSH_RUN_BUDGET_SEC,
        )
        async with AsyncSessionLocal() as db:
            await stock_service.fail_running_pushes(
                db,
                project_id,
                since=started_at,
                reason=f"Прогон прерван по бюджету {PUSH_RUN_BUDGET_SEC} c — повторите по складам",
            )
    except Exception as e:
        # Ошибку прогона видно в самой строке WbFbsStockPush (её финализирует
        # сервис); здесь — страховка, чтобы таск не умирал молча.
        logger.error("FBS stock push (manual) failed: project %s — %s", project_id, e)


@router.post("/stock/push", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def push_stock(
    payload: FbsStockPushRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Запустить трансляцию остатков в WB и сразу вернуть управление.

    Ответ приходит ДО похода в WB: `affected` — сколько складов поставлено
    в очередь (0 — «все активные»). Прогресс и результат — в
    `GET /fbs/stock/pushes`.
    """
    # Ключ проверяем синхронно: иначе единственным следом «нет ключа»
    # осталась бы строка в логе фонового таска, а фронт увидел бы 200.
    # Ключ берётся по режиму — в песочнице это отдельный токен scope Test.
    if await get_fbs_api_key(db, project.id) is None:
        raise HTTPException(
            409,
            FBS_NOT_CONFIGURED if service_for_mode() == FBS_SERVICE else FBS_SANDBOX_NOT_CONFIGURED,
        )

    # Трансляция идёт под распределённым локом проекта (его берёт push_stocks —
    # см. services/wb_fbs/locks.py). Здесь — только честный ответ пользователю:
    # иначе фоновый таск тихо вернул бы «ничего», а фронт показал бы «запущено».
    if await is_locked(PUSH_LOCK_NAME, project.id):
        raise HTTPException(409, "Трансляция остатков уже идёт — прогресс в логе прогонов")

    # Дедуп с сохранением порядка: повторы в теле множили бы прогоны по складу.
    wb_ids = list(dict.fromkeys(payload.wb_warehouse_ids))
    # Точечная отправка: дедуп тем же порядком, пустой список = весь склад.
    chrt_ids = list(dict.fromkeys(payload.chrt_ids)) or None
    task = asyncio.create_task(
        _push_stocks_bg(project.id, wb_ids, payload.force, user.id, chrt_ids)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return FbsActionOut(
        ok=True,
        message="Трансляция остатков запущена — прогресс в логе прогонов",
        affected=len(wb_ids),
    )


@router.get("/stock/pushes", response_model=list[FbsStockPushOut])
async def list_pushes(
    limit: int = Query(50, ge=1, le=200),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Лог прогонов трансляции остатков (последние сверху)."""
    with _fbs_errors():
        return await stock_service.list_pushes(db, project.id, limit=limit)


# ─── Потоварная замена количества ────────────────────────────────────────────
#
# Сколько отдаём на WB, задаётся прямо в строке таблицы остатков — одним полем
# на товар и склад продавца:
#   qty = 0     → позицию не отдавать вовсе;
#   qty > 0     → потолок: итог = min(qty, рассчитанный доступный) — физический
#                 свободный остаток всегда главнее, поднять выше него нельзя;
#   qty = null  → снять ручное количество (вернуться к расчёту).
#
# Ручка пишет в НАШУ базу, в WB не ходит: режимом контура (`safe`) не гейтится
# и работает без ключа «Маркетплейс». Но это мутация → `rate_limit_write`.
# Путь статический и объявлен до всех path-параметров домена.


@router.post("/stock/override", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def set_stock_override(
    payload: FbsOverrideSet,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Проставить или снять ручное количество по выделенным товарам склада."""
    with _fbs_errors():
        result = await stock_service.set_overrides(db, project.id, payload)

    # Контракт сервиса — число затронутых позиций; готовый ответ (модель или
    # словарь) пропускаем как есть, чтобы деталь из сервиса не потерялась.
    if isinstance(result, FbsActionOut | dict):
        return result
    affected = int(result or 0)
    if payload.items:
        # У каждой позиции своё число — одну цифру в тексте назвать нечем.
        message = f"Количество проставлено по позициям: {affected}"
    elif payload.qty is None:
        message = "Ручное количество снято"
    else:
        message = f"Ручное количество {payload.qty} проставлено"
    return FbsActionOut(ok=True, message=message, affected=affected)


# ─── Первичная сверка склада («захват») ──────────────────────────────────────
#
# Пуш остатков ДЕЛЬТОВЫЙ и по определению не знает про то, чего никогда не
# отправлял: остаток, проставленный в кабинете руками до подключения, продолжает
# продаваться. GET спрашивает у WB фактические остатки по нашей номенклатуре
# (ЧТЕНИЕ — работает и в режиме `safe`), POST обнуляет то, чем мы не управляем
# (ЗАПИСЬ — гейтится режимом контура в клиенте).


@router.get("/stock/matrix", response_model=FbsMatrixOut)
async def get_stock_matrix(
    trend_days: int = Query(14, description="Окно тренда для денег: 7 / 14 / 30"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Матрица «товар × склад продавца WB»: стоит / можем поставить + деньги.

    Колонки — только склады со связкой на наши: без привязки поставить нечего.
    Обе цифры ячейки берутся из общего превью остатков, поэтому матрица и
    вкладка «Остатки» не могут разойтись в числах.
    """
    with _fbs_errors():
        return await stock_service.stock_matrix(db, project.id, trend_days=trend_days)


@router.get("/stock/reconcile", response_model=FbsReconcileOut)
async def reconcile_stock(
    wb_warehouse_id: int = Query(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сверка кабинета с нашим расчётом: что продаётся в WB помимо трансляции."""
    with _fbs_errors():
        return await stock_service.reconcile_warehouse(db, project.id, wb_warehouse_id)


@router.post("/stock/reconcile", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def apply_stock_reconcile(
    payload: FbsReconcileApply,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Обнулить в WB позиции, которыми мы не управляем («захват» склада).

    Сверка пересчитывается в сервисе заново, а `chrt_ids` — только фильтр по
    свежему результату: слепая отправка клиентского списка позволяла бы занулить
    любую позицию, в том числе управляемую нами и прямо сейчас продающуюся.

    Ответ синхронный (в отличие от `/stock/push`): обнуляются только неуправляемые
    позиции, их единицы, а не весь каталог — поход в WB укладывается в запрос.
    """
    with _fbs_errors():
        try:
            return await stock_service.apply_reconcile(
                db,
                project.id,
                payload.wb_warehouse_id,
                payload.chrt_ids or None,
                user_id=user.id,
            )
        except stock_service.FbsReconcileBusy as e:
            # Конфликт состояния (идёт трансляция), а не ошибка ввода: 409, не 422.
            raise HTTPException(409, str(e)) from e
        except stock_service.FbsWarehouseProcessing as e:
            # Тоже конфликт состояния: WB не принимает остатки, пока склад
            # в обработке (тот же гард, что пропускает такие склады в пуше).
            raise HTTPException(409, str(e)) from e
        except stock_service.FbsWarehouseObserveMode as e:
            # Склад в режиме наблюдения: в кабинет не пишем вовсе. Состояние
            # склада, а не ошибка ввода — 409, как и у двух гардов выше.
            raise HTTPException(409, str(e)) from e


# ─── Сборочные задания ───────────────────────────────────────────────────────


@router.get("/orders/warehouse-summary", response_model=FbsWarehouseSummaryOut)
async def orders_warehouse_summary(
    date_from: date | None = Query(None, description="окно ТОЛЬКО для фаз доставки"),
    date_to: date | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Очередь по складам продавца одним запросом: новые / на сборке / фазы доставки.

    Период сужает ТОЛЬКО «в доставке» и «отсортировано»: очередь сборки резать
    окном нельзя — старое несобранное задание пропало бы у сборщика с глаз.
    """
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from позже date_to")
    with _fbs_errors():
        return await orders_service.warehouse_summary(
            db, project.id, date_from=date_from, date_to=date_to
        )


@router.get("/orders", response_model=FbsOrderListOut)
async def list_orders(
    status: str | None = Query(
        None,
        description="supplier_status: new/confirm/complete/cancel/cancel_carrier "
        "либо псевдо-статусы in_delivery (переданные и ещё не доставленные) / "
        "sorted / in_delivery_stuck (зависшие в пути — период игнорируется) / "
        "delivered (завершённые: получено покупателем или брак)",
    ),
    supply_id: str | None = Query(None, max_length=50),
    wb_warehouse_id: int | None = Query(None, ge=1),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Список сборочных заданий из нашего зеркала + счётчики по статусам."""
    if status is not None and status not in ALLOWED_ORDER_STATUS_FILTERS:
        raise HTTPException(422, f"status должен быть одним из: {ALLOWED_ORDER_STATUS_FILTERS}")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from позже date_to")
    with _fbs_errors():
        return await orders_service.list_orders(
            db,
            project.id,
            status=status,
            supply_id=supply_id,
            wb_warehouse_id=wb_warehouse_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )


@router.get("/orders/stats", response_model=FbsOrderStatsOut)
async def orders_stats(
    date_from: date | None = Query(None, description="МСК-дата начала; по умолчанию — 30 дней назад"),
    date_to: date | None = Query(None, description="МСК-дата конца включительно; по умолчанию — сегодня"),
    wb_warehouse_id: int | None = Query(None, ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сводка заказов FBS за период: выручка, разрезы и доля в объёме воронки."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from позже date_to")
    with _fbs_errors():
        return await orders_stats_service.orders_stats(
            db,
            project.id,
            date_from=date_from,
            date_to=date_to,
            wb_warehouse_id=wb_warehouse_id,
        )


@router.get("/orders/writeoff-issues", response_model=FbsWriteoffIssuesOut)
async def orders_writeoff_issues(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Незакрытые списания: задания `complete`, которые НЕЧЕМ списать со склада.

    Агрегат по товару с причиной (`no_card` / `no_link` / `no_stock` /
    `queued` — остатка хватает, ждёт ближайшего прогона) и остатками
    привязанных складов — раньше отказ был виден только warning'ом
    в логе воркера. Читает наше зеркало, в WB не ходит; путь статический —
    объявлен до path-параметров.
    """
    with _fbs_errors():
        return await orders_service.writeoff_issues(db, project.id)


@router.post("/orders/sync", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def sync_orders(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Забрать новые сборочные задания из WB. `affected` — сколько новых заданий."""
    with _fbs_errors():
        count = await orders_service.sync_new_orders(db, project.id)
    return FbsActionOut(ok=True, message="Сборочные задания обновлены", affected=int(count or 0))


@router.post("/orders/backfill", response_model=FbsOrderBackfillOut, dependencies=[Depends(rate_limit_write)])
async def backfill_orders(
    payload: FbsOrderBackfillRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Обратная загрузка истории заданий (`GET /api/v3/orders`, окна ≤30 дней).

    `POST`, а не `GET`: ходит в WB десятками запросов и пишет в зеркало.
    Идемпотентна — тот же UPSERT по `(project_id, wb_order_id)`, что и синк.
    """
    with _fbs_errors():
        result = await orders_service.backfill_orders_history(db, project.id, days=payload.days)
    return FbsOrderBackfillOut(**result)


@router.post("/orders/stickers", response_model=list[FbsStickerOut], dependencies=[Depends(rate_limit_write)])
async def get_stickers(
    payload: FbsStickerRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Стикеры заданий (WB: ≤100 за запрос, только confirm/complete). Файл — base64."""
    with _fbs_errors():
        return await orders_service.get_stickers(
            db,
            project.id,
            payload.order_ids,
            sticker_type=payload.type,
            width=payload.width,
            height=payload.height,
        )


@router.patch(
    "/orders/{wb_order_id}/cancel",
    response_model=FbsActionOut,
    dependencies=[Depends(rate_limit_write)],
)
async def cancel_order(
    wb_order_id: int = Path(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отменить сборочное задание (WB принимает, пока `is_cancellable`)."""
    with _fbs_errors():
        await orders_service.cancel_order(db, project.id, wb_order_id)
    return FbsActionOut(ok=True, message="Задание отменено", affected=1)


@router.get("/orders/{wb_order_id}/timeline", response_model=FbsOrderTimelineOut)
async def get_order_timeline(
    wb_order_id: int = Path(..., ge=1),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Таймлайн «Статус заказа» задания — модалка как в кабинете WB.

    Read-only, читает наше зеркало (WB историю статусов не отдаёт вовсе):
    якоря из точных дат (`approx=false`) + журнал переходов, зафиксированных
    синком (`approx=true`, точность = каденс синка). Отсортировано по времени
    DESC — свежее сверху.
    """
    with _fbs_errors():
        return await orders_service.get_order_timeline(db, project.id, wb_order_id)


# ─── Поставки ────────────────────────────────────────────────────────────────


@router.get("/supplies", response_model=list[FbsSupplyOut])
async def list_supplies(
    done: bool | None = Query(None, description="true — закрытые, false — активные"),
    status: str | None = Query(
        None, description="active | to_ship | in_delivery | rejected — точнее, чем `done`"
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Поставки FBS из нашего зеркала.

    `done` оставлен ради обратной совместимости; `status` — та же выборка, но
    разложенная на четыре состояния кабинета (см. `supply_status`).
    """
    if status is not None and status not in ALLOWED_SUPPLY_STATUSES:
        raise HTTPException(status_code=422, detail=f"Неизвестный статус поставки: {status}")
    with _fbs_errors():
        return await supplies_service.list_supplies(
            db, project.id, done=done, status=status, limit=limit, offset=offset
        )


@router.post("/supplies/sync", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def sync_supplies(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Синк поставок из WB. `affected` — сколько строк обновлено."""
    with _fbs_errors():
        count = await supplies_service.sync_supplies(db, project.id)
    return FbsActionOut(ok=True, message="Поставки обновлены", affected=int(count or 0))


@router.post("/supplies/plan", response_model=FbsSupplyPlanOut)
async def plan_supplies(
    payload: FbsSupplyBulkCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Предпросмотр разбиения выделенных заданий на поставки — БЕЗ похода в WB.

    Единица работы в кабинете WB — поставка, а не задание, и WB запрещает
    класть в одну поставку задания с РАЗНЫХ складов продавца; габарит
    (`cargoType` / `crossBorderType`) фиксирует первое добавленное задание.
    Поэтому при нескольких складах пользователь физически обязан завести
    несколько поставок — план показывает это ДО записи, а не ловит 409 от WB.

    Три следствия для HTTP-слоя:
      • ручка ЧИТАЮЩАЯ (только наше зеркало заданий) — значит обязана работать
        в режиме `safe`, где запись в WB заблокирована, и не гейтится
        `rate_limit_write` (iron rule 9 — про write-ручки);
      • тело — тот же `FbsSupplyBulkCreate`, что у `/supplies/bulk`: фронт шлёт
        ОДИН payload сначала на план, потом на создание, и они гарантированно
        не разъезжаются. `name_prefix` / `reuse_existing` здесь не влияют
        ни на что и игнорируются;
      • POST, а не GET, потому что заданий в выделении бывают тысячи —
        в query-строку такой список не помещается.
    """
    with _fbs_errors():
        return await supplies_service.plan_supplies(db, project.id, payload.order_ids)


@router.post("/supplies/bulk", response_model=FbsSupplyBulkOut, dependencies=[Depends(rate_limit_write)])
async def create_supplies_bulk(
    payload: FbsSupplyBulkCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать поставки по плану: одна на каждую группу «склад + габарит».

    Пишет в WB (создание поставки + `PATCH /supplies/{id}/orders` чанками
    по 100), поэтому в режиме `safe` уходит в 409 через общий гейт записи.
    Ответ не «всё или ничего»: часть групп может создаться, часть — попасть
    в `errors`, поэтому это 200 с разбором, а не исключение на первой ошибке.
    """
    with _fbs_errors():
        return await supplies_service.create_supplies_bulk(
            db,
            project.id,
            payload.order_ids,
            name_prefix=payload.name_prefix,
            reuse_existing=payload.reuse_existing,
        )


@router.post("/supplies", response_model=FbsActionOut, dependencies=[Depends(rate_limit_write)])
async def create_supply(
    payload: FbsSupplyCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать поставку в WB. `message` — id поставки (`WB-GI-…`)."""
    with _fbs_errors():
        wb_supply_id = await supplies_service.create_supply(db, project.id, payload.name)
    return FbsActionOut(ok=True, message=str(wb_supply_id), affected=1)


@router.patch(
    "/supplies/{wb_supply_id}/orders",
    response_model=FbsActionOut,
    dependencies=[Depends(rate_limit_write)],
)
async def add_orders_to_supply(
    payload: FbsSupplyAddOrders,
    wb_supply_id: str = Path(..., min_length=1, max_length=50),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Добавить задания в поставку (WB: ≤100 за запрос, один склад и один габарит)."""
    with _fbs_errors():
        count = await supplies_service.add_orders(db, project.id, wb_supply_id, payload.order_ids)
    return FbsActionOut(ok=True, message="Задания добавлены в поставку", affected=int(count or 0))


@router.get("/supplies/{wb_supply_id}/orders", response_model=list[FbsOrderOut])
async def list_supply_orders(
    wb_supply_id: str = Path(..., min_length=1, max_length=50),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Состав поставки — задания, лежащие внутри неё (раскрытие строки списка).

    Читает наше зеркало, в WB не ходит: в режиме `safe` тоже работает.
    """
    with _fbs_errors():
        return await supplies_service.list_supply_orders(db, project.id, wb_supply_id)


@router.get("/supplies/{wb_supply_id}/pick-list", response_model=FbsPickListOut)
async def supply_pick_list(
    wb_supply_id: str = Path(..., min_length=1, max_length=50),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Лист подбора поставки — что и сколько снять со склада (агрегат ПО ТОВАРУ).

    Не путать с составом поставки (`/orders`): там задания по одному, здесь —
    позиции с количеством, как в кабинете WB. Считается по нашему зеркалу, в
    WB не ходит → в режиме `safe` тоже работает.
    """
    with _fbs_errors():
        return await supplies_service.pick_list(db, project.id, wb_supply_id)


@router.patch(
    "/supplies/{wb_supply_id}/deliver",
    response_model=FbsActionOut,
    dependencies=[Depends(rate_limit_write)],
)
async def deliver_supply(
    wb_supply_id: str = Path(..., min_length=1, max_length=50),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Передать поставку в доставку (после этого дописать задания нельзя)."""
    with _fbs_errors():
        await supplies_service.deliver_supply(db, project.id, wb_supply_id)
    return FbsActionOut(ok=True, message="Поставка передана в доставку", affected=1)


@router.delete(
    "/supplies/{wb_supply_id}",
    response_model=FbsActionOut,
    dependencies=[Depends(rate_limit_write)],
)
async def delete_supply(
    wb_supply_id: str = Path(..., min_length=1, max_length=50),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Удалить поставку в WB (только активную и пустую)."""
    with _fbs_errors():
        await supplies_service.delete_supply(db, project.id, wb_supply_id)
    return FbsActionOut(ok=True, message="Поставка удалена", affected=1)


@router.get("/supplies/{wb_supply_id}/barcode", response_model=FbsSupplyBarcodeOut)
async def get_supply_barcode(
    wb_supply_id: str = Path(..., min_length=1, max_length=50),
    type: str = Query("png", description=f"один из: {ALLOWED_STICKER_TYPES}"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """QR поставки — WB отдаёт его только после `deliver` (иначе 409 SupplyNotClosed)."""
    if type not in ALLOWED_STICKER_TYPES:
        raise HTTPException(422, f"type должен быть одним из: {ALLOWED_STICKER_TYPES}")
    with _fbs_errors():
        return await supplies_service.get_supply_barcode(db, project.id, wb_supply_id, sticker_type=type)
