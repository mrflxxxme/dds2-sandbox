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

from backend.models import AssemblyRequest, AssemblyWbSupply, WbSupplySyncStatus
from backend.services import integrations_service
from backend.integrations.wb_portal_client import (
    WbPortalClient,
    WbPortalError,
    WbSessionExpired,
)
from backend.utils.time import utcnow

logger = structlog.get_logger("dds.wb_supply")

# Тип упаковки WB по умолчанию (короб). Реальный boxTypeID берём из ответа
# валидации (availableBoxTypes), это — фолбэк.
DEFAULT_BOX_TYPE_ID = 2


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
    db: AsyncSession, project_id: int, assembly_id: int
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
        db.add(link)
        await db.flush()
    return link


def _direction_name(assembly: AssemblyRequest) -> str | None:
    """Имя WB-склада-направления: из привязанной поставки, иначе manual."""
    if assembly.wb_fbo_supply and assembly.wb_fbo_supply.warehouse_name:
        return assembly.wb_fbo_supply.warehouse_name
    return assembly.wb_warehouse_name_manual


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


async def get_state(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyWbSupply:
    """
    Текущее состояние связи. НЕ создаёт строку (панель грузится на каждой
    детали сборки — иначе плодили бы пустые записи и ловили гонку INSERT).
    Пока заявку в WB не заводили — отдаём транзиентное состояние NONE.
    """
    await _load_assembly(db, project_id, assembly_id)  # проверка доступа/существования
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
    return link


async def save_boxes(
    db: AsyncSession, project_id: int, assembly_id: int, boxes: list[dict]
) -> AssemblyWbSupply:
    """Локально сохранить раскладку коробов (до заноса в WB)."""
    await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(db, project_id, assembly_id)
    link.boxes = boxes
    await db.commit()
    return link


async def save_pass(
    db: AsyncSession, project_id: int, assembly_id: int, data: dict
) -> AssemblyWbSupply:
    """Локально сохранить данные пропуска (до заноса в WB)."""
    await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(db, project_id, assembly_id)
    link.pass_driver_first = data.get("driver_first")
    link.pass_driver_last = data.get("driver_last")
    link.pass_driver_phone = data.get("driver_phone")
    link.pass_car_model = data.get("car_model")
    link.pass_car_number = data.get("car_number")
    link.pass_pallets = data.get("pallets")
    await db.commit()
    return link


async def create_preorder(db: AsyncSession, project_id: int, assembly_id: int) -> AssemblyWbSupply:
    """Шаги 1-4: черновик → наполнение → валидация → преордер."""
    assembly = await _load_assembly(db, project_id, assembly_id)
    goods = _build_goods(assembly)
    if not goods:
        raise WbSupplyError("В заявке нет товаров для поставки")
    name = _direction_name(assembly)
    if not name:
        raise WbSupplyError("У заявки не задан WB-склад направления")

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
        box_types = _extract_box_types(validation)
        box_type_id = box_types[0] if box_types else DEFAULT_BOX_TYPE_ID
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
    await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(db, project_id, assembly_id)
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
    await _load_assembly(db, project_id, assembly_id)
    link = await _get_or_create_link(db, project_id, assembly_id)
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


# ─── парсеры ответов WB ──────────────────────────────────────────────────────


def _extract_box_types(validation: dict) -> list[int]:
    items = validation.get("items") or []
    for it in items:
        types = it.get("availableBoxTypes")
        if types:
            return [int(t) for t in types]
    return []


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
