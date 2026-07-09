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
from sqlalchemy import or_, select
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
    # забронированная FBO-поставка — показываем её как BOOKED с supply_id из FBO
    # и ЖИВЫМ статусом (обновляется FBO-синком Marketplace API ~30 мин).
    #   • нет строки/supply_id → полностью синтезируем (транзиентно, на GET не пишем);
    #   • строка уже усыновлена (supply_id из FBO) → освежаем живой статус.
    # Реальная строка появляется при заносе коробов/пропуска (_get_or_create_link).
    if not link.preorder_id:
        adopt = _adopted_supply_id(assembly)
        if adopt and link.supply_id in (None, adopt):
            if not link.supply_id:
                updates["supply_id"] = adopt
                updates["sync_status"] = WbSupplySyncStatus.BOOKED.value
            updates["wb_supply_state"] = _fbo_state_label(assembly)
    return state.model_copy(update=updates)


async def sync_all_states(db: AsyncSession, project_id: int) -> dict:
    """
    Bulk-синк живого состояния WB-поставок проекта через кабинет (listSupplies).

    Забирает справочник статусов + все поставки кабинета постранично и для каждой
    локальной связи (preorder_id/supply_id) обновляет `wb_supply_state`/
    `wb_supply_state_id`/`wb_state_synced_at`. Проекты без заведённых поставок в
    WB не ходят в кабинет вообще. Возврат: {checked, updated, supplies_seen}.
    """
    result = await db.execute(
        select(AssemblyWbSupply).where(
            AssemblyWbSupply.project_id == project_id,
            or_(
                AssemblyWbSupply.preorder_id.isnot(None),
                AssemblyWbSupply.supply_id.isnot(None),
            ),
        )
    )
    links = list(result.scalars().all())
    if not links:
        return {"checked": 0, "updated": 0, "supplies_seen": 0}

    try:
        client = await _client(db, project_id)
    except ValueError as e:
        # Сессия кабинета не задана — не поднимаем 500, тихо сигналим наверх.
        raise WbSupplyError(str(e)) from e

    try:
        statuses = await client.list_statuses()
        supplies: list[dict] = []
        page_size = 100
        offset = 0
        while True:
            page = await client.list_supplies(limit=page_size, offset=offset)
            supplies.extend(page)
            if len(page) < page_size or offset > 2000:
                break
            offset += page_size
    except WbSessionExpired as e:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        raise WbSupplyError(
            "Сессия WB-кабинета истекла. Обновите доступ WB в настройках."
        ) from e
    except WbPortalError as e:
        raise WbSupplyError(f"WB отклонил операцию: {e}") from e

    name_by_id = _build_status_name_map(statuses)

    updated = 0
    for link in links:
        supply = _find_supply_for_link(supplies, link)
        if supply is None:
            continue
        state_id, state_name = _extract_supply_state(supply, name_by_id)
        if state_id is None and state_name is None:
            continue
        if link.wb_supply_state != state_name or link.wb_supply_state_id != state_id:
            link.wb_supply_state = state_name
            link.wb_supply_state_id = state_id
            updated += 1
        link.wb_state_synced_at = utcnow()

    await db.commit()
    return {"checked": len(links), "updated": updated, "supplies_seen": len(supplies)}


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


def _build_status_name_map(statuses: list[dict]) -> dict[int, str]:
    """Справочник статусов → {statusID: name}. Ключи id/statusID и name/statusName варьируются."""
    result: dict[int, str] = {}
    for s in statuses or []:
        sid = s.get("id") or s.get("statusID") or s.get("statusId")
        name = s.get("name") or s.get("statusName")
        if sid is not None and name:
            try:
                result[int(sid)] = str(name)
            except (TypeError, ValueError):
                continue
    return result


def _find_supply_for_link(supplies: list[dict], link: "AssemblyWbSupply") -> dict | None:
    """Найти поставку кабинета для связи: по supply_id, иначе по preorder_id."""
    for s in supplies or []:
        raw_sid = s.get("supplyId") or s.get("id")
        if link.supply_id and raw_sid:
            try:
                if int(raw_sid) == link.supply_id:
                    return s
            except (TypeError, ValueError):
                pass
        if link.preorder_id:
            pres = s.get("preorders") or s.get("preorderIds") or []
            if (
                link.preorder_id in pres
                or s.get("preorderId") == link.preorder_id
                or s.get("preOrderId") == link.preorder_id
            ):
                return s
    return None


def _extract_supply_state(
    supply: dict, name_by_id: dict[int, str]
) -> tuple[int | None, str | None]:
    """Достать (statusID, statusName) из поставки. Ключи варьируются: statusID/
    statusId/status (число) + statusName/status (строка); имя резолвим по справочнику."""
    state_id: int | None = None
    for key in ("statusID", "statusId"):
        v = supply.get(key)
        if isinstance(v, int):
            state_id = v
            break
        if isinstance(v, str) and v.isdigit():
            state_id = int(v)
            break
    status_val = supply.get("status")
    if state_id is None:
        if isinstance(status_val, int):
            state_id = status_val
        elif isinstance(status_val, str) and status_val.isdigit():
            state_id = int(status_val)

    state_name = supply.get("statusName")
    if not state_name and isinstance(status_val, str) and not status_val.isdigit():
        state_name = status_val
    if not state_name and state_id is not None:
        state_name = name_by_id.get(state_id) or str(state_id)
    return state_id, state_name
