"""
Service: занос заявки-сборки в FBW-поставку WB через реплей портала.

Оркестрирует WbPortalClient поверх домена сборки:
  create_preorder  — шаги 1-4 (черновик → наполнение → валидация → преордер);
  sync_supply_id   — найти supply_id после ручной брони даты (антибот-гейт);
  push_boxes       — короба (createBoxBarcodes + bindBarcodes);
  push_pass        — пропуск (setTRNDetails).

Все мутации project-scoped. WbSessionExpired → помечаем сессию EXPIRED и
пробрасываем понятную ошибку (UI попросит обновить токен).
"""

from collections import defaultdict
from collections.abc import Awaitable
from typing import TypeVar

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.models import AssemblyRequest, AssemblyWbSupply, WbFboSupply, WbSupplySyncStatus
from backend.schemas.assembly_wb import (
    WbCabinetBox,
    WbCabinetBoxes,
    WbCabinetBoxItem,
    WbSupplyState,
)
from backend.services import integrations_service
from backend.integrations.wb_portal_client import (
    WbPortalClient,
    WbPortalError,
    WbSessionExpired,
)
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.wb_supply")

# Тип упаковки заявки (PackageType) → WB boxTypeID. Подтверждено вживую:
# 2=Короб, 5=Монопаллета; 6=Суперсейф — стандарт WB. Ответ валидации
# (availableBoxTypes) страхует от неверного id: если WB не принимает запрошенный
# тип для этих товаров/склада — понятная ошибка со списком доступных.
PACKAGE_TYPE_TO_BOX_TYPE_ID = {"BOX": 2, "MONOPALLET": 5, "SUPERSAFE": 6}
BOX_TYPE_ID_LABEL = {2: "Короб", 5: "Монопаллета", 6: "Суперсейф"}


class WbSupplyError(Exception):
    """Доменная ошибка заноса поставки (для 400 в роутере)."""


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _load_assembly(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyRequest:
    result = await db.execute(
        select(AssemblyRequest)
        .where(
            AssemblyRequest.id == assembly_id,
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
        )
        .options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.wb_fbo_supply),
        )
    )
    assembly = result.scalar_one_or_none()
    if not assembly:
        raise WbSupplyError("Заявка не найдена")
    return assembly


async def _get_or_create_link(
    db: AsyncSession,
    project_id: int,
    assembly_id: int,
    adopt_supply_id: int | None = None,
) -> AssemblyWbSupply:
    result = await db.execute(
        select(AssemblyWbSupply).where(
            AssemblyWbSupply.assembly_request_id == assembly_id,
            AssemblyWbSupply.project_id == project_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        link = AssemblyWbSupply(project_id=project_id, assembly_request_id=assembly_id)
        # «Усыновление» забронированной поставки из FBO: строка сразу с реальным
        # supply_id и статусом BOOKED → короба/пропуск идут по нему.
        if adopt_supply_id:
            link.supply_id = adopt_supply_id
            link.sync_status = WbSupplySyncStatus.BOOKED.value
        db.add(link)
        await db.flush()
    elif adopt_supply_id and not link.supply_id and not link.preorder_id:
        # Строка была (напр. локальный черновик пропуска), но реплей не начинали —
        # добираем supply_id из FBO.
        link.supply_id = adopt_supply_id
        if link.sync_status == WbSupplySyncStatus.NONE.value:
            link.sync_status = WbSupplySyncStatus.BOOKED.value
    return link


def _direction_name(assembly: AssemblyRequest) -> str | None:
    """Имя WB-склада-направления: из привязанной поставки, иначе manual."""
    if assembly.wb_fbo_supply and assembly.wb_fbo_supply.warehouse_name:
        return assembly.wb_fbo_supply.warehouse_name
    return assembly.wb_warehouse_name_manual


# Метки статуса FBO-поставки (Marketplace API) для отображения живого состояния
# «усыновлённых» поставок в панели/списке (см. _adopted_supply_id).
_FBO_STATE_LABEL = {
    "ACTIVE": "Запланирована",
    "ON_DELIVERY": "В пути",  # noqa: RUF001
    "IN_PROGRESS": "Разгрузка разрешена",
    "ACCEPTED": "Принята",
    "CANCELLED": "Отменена",
}


def fbo_adopted_supply_id(fbo: "WbFboSupply | None") -> int | None:
    """
    supply_id для «усыновления» уже забронированной поставки из FBO-привязки.

    Портальный supply_id == `WbFboSupply.wb_supply_id` (подтверждено вживую:
    trn_details принимает этот id). Для не-отменённых FBO-поставок с числовым
    wb_supply_id возвращаем его — тогда панель «Поставка WB» оживает БЕЗ ручного
    заведения (короба/пропуск идут по этому supply_id), а состояние берётся из
    FBO-синка (обновляется Marketplace API каждые ~30 мин).

    Портальный `list_supplies` для забронированных/в-приёмке поставок отдаёт
    пусто, поэтому источник — именно FBO, а не реплей-список.
    """
    if (
        fbo
        and fbo.wb_status != "CANCELLED"
        and fbo.wb_supply_id
        and str(fbo.wb_supply_id).isdigit()
    ):
        return int(fbo.wb_supply_id)
    return None


def fbo_state_label(wb_status: str | None) -> str | None:
    """Человекочитаемая метка живого статуса FBO-поставки."""
    return _FBO_STATE_LABEL.get(wb_status, wb_status) if wb_status else None


def _adopted_supply_id(assembly: AssemblyRequest) -> int | None:
    return fbo_adopted_supply_id(assembly.wb_fbo_supply)


def _fbo_state_label(assembly: AssemblyRequest) -> str | None:
    fbo = assembly.wb_fbo_supply
    return fbo_state_label(fbo.wb_status if fbo else None)


async def _cabinet_status(client: WbPortalClient, supply_id: int) -> tuple[str | None, int | None]:
    """
    АВТОРИТЕТНЫЙ статус поставки из кабинета (supplyDetails.statusName/statusId).

    Именно его видит пользователь в кабинете. Отличается от FBO Marketplace API
    (`WbFboSupply.wb_status`): напр. кабинет «Запланировано» vs FBO «В пути».
    """
    detail = await client.supply_details(supply_id)
    name = detail.get("statusName")
    sid = detail.get("statusId")
    return (
        name if isinstance(name, str) and name else None,
        sid if isinstance(sid, int) else None,
    )


def _build_goods(assembly: AssemblyRequest) -> list[dict]:
    """Наполнение → [{"barcode": str, "quantity": int}], агрегируем по баркоду."""
    agg: dict[str, int] = defaultdict(int)
    for item in assembly.items:
        if item.barcode and item.quantity:
            agg[item.barcode] += item.quantity
    return [{"barcode": bc, "quantity": qty} for bc, qty in agg.items()]


async def _resolve_warehouse_id(client: WbPortalClient, name: str) -> int:
    """Имя направления → числовой warehouseID портала. Точный матч по имени."""
    items = await client.get_warehouse_filter_items()
    by_name = {w["warehouseName"]: w["warehouseID"] for w in items if w.get("warehouseName")}
    if name in by_name:
        return int(by_name[name])
    # Фолбэк: сравнение без учёта регистра/пробелов.
    norm = name.strip().casefold()
    for wname, wid in by_name.items():
        if wname.strip().casefold() == norm:
            return int(wid)
    raise WbSupplyError(
        f"WB-склад «{name}» не распознан в кабинете. Выберите склад вручную."
    )


async def _client(db: AsyncSession, project_id: int) -> WbPortalClient:
    return await integrations_service.get_wb_portal_client(db, project_id)


_T = TypeVar("_T")


async def _run(
    db: AsyncSession, project_id: int, link: AssemblyWbSupply, coro: Awaitable[_T]
) -> _T:
    """
    Выполнить операцию реплея, конвертируя ошибки:
      WbSessionExpired → пометить сессию EXPIRED + WbSupplyError;
      WbPortalError    → link.status=ERROR + WbSupplyError.
    """
    try:
        return await coro
    except WbSessionExpired as e:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        link.sync_status = WbSupplySyncStatus.ERROR.value
        link.last_error = f"Сессия WB истекла: {e}"
        await db.commit()
        raise WbSupplyError("Сессия WB-кабинета истекла. Обновите доступ WB в настройках.") from e
    except WbPortalError as e:
        link.sync_status = WbSupplySyncStatus.ERROR.value
        link.last_error = str(e)
        await db.commit()
        raise WbSupplyError(f"WB отклонил операцию: {e}") from e


# ─── публичные операции ──────────────────────────────────────────────────────


async def get_state(db: AsyncSession, project_id: int, assembly_id: int) -> WbSupplyState:
    """
    Текущее состояние связи. НЕ создаёт строку (панель грузится на каждой
    детали сборки — иначе плодили бы пустые записи и ловили гонку INSERT).
    Пока заявку в WB не заводили — отдаём транзиентное состояние NONE.

    Возвращает готовую схему: к полям связи домешивается read-only зеркало
    назначенной машины и числа паллет заявки (не колонки таблицы wb-поставки) —
    для префилла пропуска (F3) и баннера «паллеты ≠ WB» (F2).
    """
    assembly = await _load_assembly(db, project_id, assembly_id)  # проверка доступа/существования
    result = await db.execute(
        select(AssemblyWbSupply).where(
            AssemblyWbSupply.assembly_request_id == assembly_id,
            AssemblyWbSupply.project_id == project_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        link = AssemblyWbSupply(
            project_id=project_id,
            assembly_request_id=assembly_id,
            sync_status=WbSupplySyncStatus.NONE.value,
            boxes=[],
        )
    state = WbSupplyState.model_validate(link)
    updates: dict = {
        "assembly_vehicle_info": assembly.vehicle_info,
        "assembly_vehicle_brand": assembly.vehicle_brand,
        "assembly_driver_phone": assembly.driver_phone,
        "assembly_pallets_count": assembly.pallets_count,
    }
    # Авто-адопция: если это НЕ DDS-реплей (нет preorder_id), а у заявки есть
    # забронированная FBO-поставка — показываем её как BOOKED с supply_id из FBO.
    # Реальная строка появляется при заносе коробов/пропуска (_get_or_create_link).
    adopt = None if link.preorder_id else _adopted_supply_id(assembly)
    if adopt and link.supply_id in (None, adopt):
        if not link.supply_id:
            updates["supply_id"] = adopt
            updates["sync_status"] = WbSupplySyncStatus.BOOKED.value
        # Фолбэк-статус из FBO — если кабинет недоступен ниже.
        updates["wb_supply_state"] = link.wb_supply_state or _fbo_state_label(assembly)

    # АВТОРИТЕТНЫЙ живой статус — из кабинета (supplyDetails), best-effort: при
    # недоступности WB оставляем сохранённое/FBO-метку, панель не роняем.
    supply_id_eff = link.supply_id or adopt
    if supply_id_eff:
        try:
            client = await _client(db, project_id)
            name, state_id = await _cabinet_status(client, supply_id_eff)
            if name:
                updates["wb_supply_state"] = name
                updates["wb_supply_state_id"] = state_id
        except (WbSessionExpired, WbPortalError, ValueError):
            pass
    return state.model_copy(update=updates)


_SYNC_MAX_PAGES = 40  # ≤ ~800 свежих поставок (страница listSupplies ≈ 20)


async def sync_all_states(db: AsyncSession, project_id: int) -> dict:
    """
    Bulk-синк АВТОРИТЕТНОГО кабинетного статуса поставок проекта.

    Кабинетный статус (`statusName`) отличается от FBO Marketplace API
    (`WbFboSupply.wb_status`) — напр. кабинет «Запланировано» vs FBO «В пути».
    Тянем его пагинацией `listSupplies` (ключ `data`, статус в каждом элементе,
    ≈20 на страницу, свежие сверху) и строим карту supplyId→статус — так один
    заход в кабинет ~десяток вызовов покрывает все активные поставки БЕЗ
    рейт-лимита (per-supply `supplyDetails` для сотен поставок кабинет отбивает).
    Матч по supply_id; для адоптированных из FBO без строки — строка создаётся.
    supplyDetails остаётся для живого статуса ОДНОЙ поставки в панели (get_state).
    Возврат: {checked, updated, supplies_seen}.
    """
    # 1. Существующие связи с известным supply_id.
    links_res = await db.execute(
        select(AssemblyWbSupply).where(
            AssemblyWbSupply.project_id == project_id,
            AssemblyWbSupply.supply_id.isnot(None),
        )
    )
    links = list(links_res.scalars().all())
    by_assembly = {link.assembly_request_id: link for link in links}

    # 2. Заявки с забронированной FBO-поставкой (адопция) — supply_id из FBO.
    fbo_res = await db.execute(
        select(AssemblyRequest.id, WbFboSupply)
        .join(WbFboSupply, AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
        )
    )
    # work: (assembly_id, supply_id, link|None)
    work: list[tuple[int, int, AssemblyWbSupply | None]] = [
        (link.assembly_request_id, int(link.supply_id), link)
        for link in links
        if link.supply_id is not None
    ]
    for aid, fbo in fbo_res.all():
        existing = by_assembly.get(aid)
        if existing and existing.supply_id:
            continue  # уже покрыта связью
        sid = fbo_adopted_supply_id(fbo)
        if sid:
            work.append((aid, sid, existing))

    if not work:
        return {"checked": 0, "updated": 0, "supplies_seen": 0}

    try:
        client = await _client(db, project_id)
    except ValueError as e:
        raise WbSupplyError(str(e)) from e

    targets = {sid for _, sid, _ in work}
    # Пагинация listSupplies → карта supplyId → (statusId, statusName).
    status_map: dict[int, tuple[int | None, str | None]] = {}
    try:
        offset = 0
        for _ in range(_SYNC_MAX_PAGES):
            page = await client.list_supplies(limit=100, offset=offset)
            if not page:
                break
            for s in page:
                sid = s.get("supplyId") or s.get("id")
                if isinstance(sid, int):
                    status_map[sid] = (s.get("statusId"), s.get("statusName"))
            if targets <= set(status_map):
                break  # все наши поставки уже покрыты
            offset += len(page)
            if len(page) < 20:  # последняя страница (кабинет отдаёт ≈20)
                break
    except WbSessionExpired as e:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        raise WbSupplyError(
            "Сессия WB-кабинета истекла. Обновите доступ WB в настройках."
        ) from e
    except WbPortalError as e:
        raise WbSupplyError(f"WB отклонил операцию: {e}") from e

    updated = 0
    checked = 0
    for aid, sid, link in work:
        st = status_map.get(sid)
        if st is None:
            continue  # поставки нет в свежих страницах (старая/архивная) — пропуск
        checked += 1
        state_id, name = st
        if not name:
            continue
        if link is None:
            link = AssemblyWbSupply(
                project_id=project_id,
                assembly_request_id=aid,
                supply_id=sid,
                sync_status=WbSupplySyncStatus.BOOKED.value,
                boxes=[],
            )
            db.add(link)
        if link.wb_supply_state != name or link.wb_supply_state_id != state_id:
            link.wb_supply_state = name
            link.wb_supply_state_id = state_id
            updated += 1
        link.wb_state_synced_at = utcnow()

    await db.commit()
    return {"checked": checked, "updated": updated, "supplies_seen": len(status_map)}


async def save_boxes(
    db: AsyncSession, project_id: int, assembly_id: int, boxes: list[dict]
) -> AssemblyWbSupply:
    """Локально сохранить раскладку коробов (до заноса в WB)."""
    assembly = await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(
        db, project_id, assembly_id, adopt_supply_id=_adopted_supply_id(assembly)
    )
    link.boxes = boxes
    await db.commit()
    return link


async def save_pass(
    db: AsyncSession, project_id: int, assembly_id: int, data: dict
) -> AssemblyWbSupply:
    """Локально сохранить данные пропуска (до заноса в WB)."""
    assembly = await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(
        db, project_id, assembly_id, adopt_supply_id=_adopted_supply_id(assembly)
    )
    link.pass_driver_first = data.get("driver_first")
    link.pass_driver_last = data.get("driver_last")
    link.pass_driver_phone = data.get("driver_phone")
    link.pass_car_model = data.get("car_model")
    link.pass_car_number = data.get("car_number")
    link.pass_pallets = data.get("pallets")
    await db.commit()
    return link


async def create_preorder(
    db: AsyncSession, project_id: int, assembly_id: int, package_type: str | None = None
) -> AssemblyWbSupply:
    """
    Шаги 1-4: черновик → наполнение → валидация → преордер.

    Тип упаковки: явный `package_type` (BOX/MONOPALLET/SUPERSAFE — выбор в панели)
    ИЛИ тип заявки (`assembly.package_type`, проставлен на «Распределить»). WB
    должен разрешать его для этих товаров/склада — иначе понятная ошибка со
    списком доступных типов (пользователь меняет тип в панели и повторяет).
    """
    assembly = await _load_assembly(db, project_id, assembly_id)
    goods = _build_goods(assembly)
    if not goods:
        raise WbSupplyError("В заявке нет товаров для поставки")
    name = _direction_name(assembly)
    if not name:
        raise WbSupplyError("У заявки не задан WB-склад направления")

    pkg = (package_type or assembly.package_type or "BOX").upper()
    desired_box_type = PACKAGE_TYPE_TO_BOX_TYPE_ID.get(pkg)
    if desired_box_type is None:
        raise WbSupplyError(f"Неизвестный тип упаковки: {pkg}")

    link = await _get_or_create_link(db, project_id, assembly_id)
    client = await _client(db, project_id)

    async def _flow() -> AssemblyWbSupply:
        warehouse_id = await _resolve_warehouse_id(client, name)
        draft_id = await client.draft_create()
        # Фиксируем draft_id сразу — если дальше упадёт, черновик не «повиснет»
        # в кабинете безымянным (виден локально как DRAFT).
        link.draft_id = draft_id
        link.warehouse_id_wb = warehouse_id
        link.sync_status = WbSupplySyncStatus.DRAFT.value
        await db.commit()
        await client.draft_update_goods(draft_id, goods)
        validation = await client.validate_warehouse_goods(draft_id, warehouse_id)
        box_type_id = _choose_box_type(desired_box_type, _extract_box_types(validation), pkg, name)
        preorder_id = await client.supply_create(draft_id, warehouse_id, box_type_id)

        link.warehouse_id_wb = warehouse_id
        link.box_type_id = box_type_id
        link.draft_id = draft_id
        link.preorder_id = preorder_id
        link.sync_status = WbSupplySyncStatus.PREORDER.value
        link.last_error = None
        link.last_synced_at = utcnow()
        await db.commit()
        return link

    return await _run(db, project_id, link, _flow())


async def update_preorder_goods(
    db: AsyncSession, project_id: int, assembly_id: int
) -> AssemblyWbSupply:
    """Дослать текущее наполнение заявки в готовый преордер WB (upsert).

    Шлём ВСЕ товары заявки; WB валидирует поштучно. Если хоть один товар
    отклонён (`hasError`) — операция считается неуспешной, тексты ошибок WB
    поднимаются наверх через WbSupplyError.
    """
    assembly = await _load_assembly(db, project_id, assembly_id)
    goods = _build_goods(assembly)
    if not goods:
        raise WbSupplyError("В заявке нет товаров для поставки")

    link = await _get_or_create_link(db, project_id, assembly_id)
    if not link.preorder_id:
        raise WbSupplyError("Преордер ещё не создан")
    preorder_id = link.preorder_id
    client = await _client(db, project_id)

    async def _flow() -> AssemblyWbSupply:
        res = await client.edit_preorder_goods(preorder_id, goods)
        bad = [r for r in res if r.get("hasError")]
        if bad:
            msgs = "; ".join(
                e.get("error", "") for r in bad for e in (r.get("errors") or [])
            )
            raise WbSupplyError(f"WB отклонил часть товаров: {msgs}")
        link.last_error = None
        link.last_synced_at = utcnow()
        await db.commit()
        return link

    return await _run(db, project_id, link, _flow())


async def sync_supply_id(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyWbSupply:
    """Найти supply_id после ручной брони даты (матч по preorder_id)."""
    await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(db, project_id, assembly_id)
    if not link.preorder_id:
        raise WbSupplyError("Преордер ещё не создан")
    preorder_id = link.preorder_id
    client = await _client(db, project_id)

    async def _flow() -> AssemblyWbSupply:
        supplies = await client.list_supplies()
        supply_id = _match_supply(supplies, preorder_id)
        if not supply_id:
            raise WbSupplyError(
                "Поставка ещё не забронирована. Подтвердите дату в кабинете WB и повторите."
            )
        link.supply_id = supply_id
        link.sync_status = WbSupplySyncStatus.BOOKED.value
        link.last_error = None
        link.last_synced_at = utcnow()
        await db.commit()
        return link

    return await _run(db, project_id, link, _flow())


async def push_boxes(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyWbSupply:
    """Шаг 6: создать короба и разложить товары по локальной раскладке link.boxes."""
    assembly = await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(
        db, project_id, assembly_id, adopt_supply_id=_adopted_supply_id(assembly)
    )
    if not link.supply_id:
        raise WbSupplyError("Сначала забронируйте дату в кабинете WB (шаг брони)")
    supply_id = link.supply_id
    boxes = link.boxes or []
    if not boxes:
        raise WbSupplyError("Не задана раскладка коробов")
    client = await _client(db, project_id)

    async def _flow() -> AssemblyWbSupply:
        boxcodes = await client.create_box_barcodes(supply_id, len(boxes))
        if len(boxcodes) != len(boxes):
            raise WbPortalError(
                f"WB вернул {len(boxcodes)} ШК коробов на {len(boxes)} запрошенных — "
                "раскладка не привязана, повторите"
            )
        bind = []
        new_boxes = []
        for box, boxcode in zip(boxes, boxcodes, strict=True):
            items = [
                {"barcode": it["barcode"], "expirationDate": None, "quantity": it["quantity"]}
                for it in box.get("items", [])
            ]
            bind.append({"boxcode": boxcode, "barcodes": items, "quantity": 1})
            new_boxes.append({"boxcode": boxcode, "items": box.get("items", [])})
        await client.bind_barcodes(supply_id, bind)
        link.boxes = new_boxes
        link.sync_status = WbSupplySyncStatus.BOXED.value
        link.last_error = None
        link.last_synced_at = utcnow()
        await db.commit()
        return link

    return await _run(db, project_id, link, _flow())


async def push_pass(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyWbSupply:
    """Шаг 7: занести пропуск (водитель/авто/паллеты) через setTRNDetails."""
    assembly = await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(
        db, project_id, assembly_id, adopt_supply_id=_adopted_supply_id(assembly)
    )
    if not link.supply_id:
        raise WbSupplyError("Сначала забронируйте дату в кабинете WB (шаг брони)")
    if not (link.pass_driver_first and link.pass_driver_last and link.pass_car_number):
        raise WbSupplyError("Заполните данные водителя и госномер")
    supply_id = link.supply_id
    first_name = link.pass_driver_first
    last_name = link.pass_driver_last
    car_number = link.pass_car_number
    car_model = link.pass_car_model or ""
    phone = link.pass_driver_phone or ""
    pallets = link.pass_pallets or 0
    client = await _client(db, project_id)

    async def _flow() -> AssemblyWbSupply:
        details = await client.trn_details(supply_id)
        barcode_id = _extract_barcode_id(details)
        if not barcode_id:
            raise WbSupplyError("WB не вернул barcodeId пропуска")
        await client.set_trn(
            barcode_id=barcode_id,
            supply_id=supply_id,
            first_name=first_name,
            last_name=last_name,
            car_model=car_model,
            car_number=car_number,
            phone=phone,
            pallets=pallets,
        )
        link.barcode_id = barcode_id
        link.sync_status = WbSupplySyncStatus.PASSED.value
        link.last_error = None
        link.last_synced_at = utcnow()
        await db.commit()
        return link

    return await _run(db, project_id, link, _flow())


async def get_drivers(db: AsyncSession, project_id: int) -> list[dict]:
    """Справочник водителей из истории кабинета (автозаполнение пропуска)."""
    client = await _client(db, project_id)
    try:
        return await client.pass_history()
    except WbSessionExpired as e:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        raise WbSupplyError("Сессия WB-кабинета истекла. Обновите доступ WB.") from e


async def get_cabinet_boxes(
    db: AsyncSession, project_id: int, assembly_id: int
) -> WbCabinetBoxes:
    """
    Короба поставки с содержимым из кабинета (вкладка «Упаковка», как в WB).

    supply_id берётся из реплей-связи ИЛИ из забронированной FBO-поставки
    (адопция) — так короба видны и для поставок, собранных прямо в кабинете.
    """
    assembly = await _load_assembly(db, project_id, assembly_id)
    result = await db.execute(
        select(AssemblyWbSupply).where(
            AssemblyWbSupply.assembly_request_id == assembly_id,
            AssemblyWbSupply.project_id == project_id,
        )
    )
    link = result.scalar_one_or_none()
    supply_id = (link.supply_id if link else None) or _adopted_supply_id(assembly)
    if not supply_id:
        return WbCabinetBoxes()

    client = await _client(db, project_id)
    try:
        raw = await client.list_boxes(supply_id)
    except WbSessionExpired as e:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        raise WbSupplyError("Сессия WB-кабинета истекла. Обновите доступ WB.") from e
    except WbPortalError as e:
        raise WbSupplyError(f"WB отклонил операцию: {e}") from e

    boxes: list[WbCabinetBox] = []
    total_units = 0
    seen_barcodes: set[str] = set()
    for b in raw:
        items = []
        for it in b.get("barcodes") or []:
            bc = str(it.get("barcode") or "")
            qty = int(it.get("quantity") or 0)
            total_units += qty
            if bc:
                seen_barcodes.add(bc)
            items.append(
                WbCabinetBoxItem(
                    barcode=bc,
                    quantity=qty,
                    imt_name=it.get("imtName"),
                    img_src=it.get("imgSrc"),
                    brand=it.get("brand"),
                    sa_nm=it.get("saNm"),
                    nm_id=it.get("nmID"),
                    color_name=it.get("colorName"),
                    volume=it.get("volume"),
                )
            )
        boxes.append(
            WbCabinetBox(
                boxcode=str(b.get("boxcode") or ""),
                quantity=int(b.get("quantity") or 1),
                items=items,
            )
        )
    return WbCabinetBoxes(
        boxes=boxes,
        total_boxes=len(boxes),
        total_barcodes=len(seen_barcodes),
        total_units=total_units,
    )


# ─── парсеры ответов WB ──────────────────────────────────────────────────────


def _extract_box_types(validation: dict) -> list[int]:
    items = validation.get("items") or []
    for it in items:
        types = it.get("availableBoxTypes")
        if types:
            return [int(t) for t in types]
    return []


def _choose_box_type(desired: int, available: list[int], pkg: str, warehouse: str) -> int:
    """
    boxTypeID для supply/create: запрошенный тип, если WB его разрешает; иначе
    понятная ошибка со списком доступных (WB не принимает эту упаковку для товаров/склада).
    Пустой available (WB не вернул список) — доверяем запросу.
    """
    if not available or desired in available:
        return desired
    allowed = ", ".join(BOX_TYPE_ID_LABEL.get(b, str(b)) for b in available)
    want = BOX_TYPE_ID_LABEL.get(desired, pkg)
    raise WbSupplyError(
        f"WB не принимает упаковку «{want}» для этих товаров на складе «{warehouse}». "
        f"Доступно: {allowed}. Выберите доступный тип упаковки и повторите."
    )


def _extract_barcode_id(details: dict) -> int | None:
    trns = ((details.get("details") or {}).get("trns")) or []
    for trn in trns:
        bc = (trn.get("barcode") or {}).get("barcodeId")
        if bc:
            return int(bc)
    return None


def _match_supply(supplies: list, preorder_id: int) -> int | None:
    """Найти supplyId, чей preorders содержит наш preorder_id."""
    for s in supplies or []:
        pres = s.get("preorders") or s.get("preorderIds") or []
        matched = (
            preorder_id in pres
            or s.get("preorderId") == preorder_id
            or s.get("preOrderId") == preorder_id
        )
        if matched:
            val = s.get("supplyId") or s.get("id")
            return int(val) if val else None
    return None


