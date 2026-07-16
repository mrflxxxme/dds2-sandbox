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

import asyncio
import re
from collections import defaultdict
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import invalidate_cache
from backend.models import AssemblyRequest, AssemblyWbSupply, WbFboSupply, WbSupplySyncStatus
from backend.schemas.assembly_wb import (
    WbCabinetBox,
    WbCabinetBoxes,
    WbCabinetBoxItem,
    WbCabinetPass,
    WbSupplyState,
)
from backend.services import integrations_service
from backend.services.warehouse_acceptance_service import (
    _is_spec_acceptance_wh,
    _normalize_acceptance_wh,
)
from backend.integrations.wb_portal_client import (
    WbPortalClient,
    WbPortalError,
    WbSessionExpired,
)
from backend.utils.phone import normalize_ru_phone
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


@dataclass(frozen=True)
class _CabinetMeta:
    """Сводка карточки поставки из кабинета (supplyDetails)."""

    name: str | None
    state_id: int | None
    supply_date: datetime | None
    reject_reason: str | None


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
    "ON_DELIVERY": "Отгрузка разрешена",
    "IN_PROGRESS": "Идёт приёмка",
    "ACCEPTED": "Принята",
    "CANCELLED": "Отклонена",
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


def _parse_wb_dt(value: object) -> datetime | None:
    """ISO-дата кабинета («2026-07-23T00:00:00+03:00») → naive datetime.

    tzinfo отбрасываем БЕЗ конверсии в UTC: `supplyDate` — это календарная дата
    слота сдачи в таймзоне склада; перевод в UTC сдвинул бы её на сутки назад.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


async def _cabinet_status(client: WbPortalClient, supply_id: int) -> "_CabinetMeta":
    """
    АВТОРИТЕТНАЯ сводка поставки из кабинета (supplyDetails).

    Статус (`statusName`/`statusId`) — именно тот, что видит пользователь в
    кабинете; `statusId` — та же шкала, что `statusID` Suppliers API (см.
    `FBW_STATUS_MAP`), но с готовым текстом. Плюс `supplyDate` (забронированный
    слот сдачи) и `rejectReason` (текст кабинетных ошибок поставки — «Не заполнены
    ШК коробов…», «Не заполнен пропуск…»).
    """
    detail = await client.supply_details(supply_id)
    name = detail.get("statusName")
    sid = detail.get("statusId")
    reason = detail.get("rejectReason")
    return _CabinetMeta(
        name=name if isinstance(name, str) and name else None,
        state_id=sid if isinstance(sid, int) else None,
        supply_date=_parse_wb_dt(detail.get("supplyDate")),
        reject_reason=reason.strip() if isinstance(reason, str) and reason.strip() else None,
    )


def _build_goods(assembly: AssemblyRequest) -> list[dict]:
    """Наполнение → [{"barcode": str, "quantity": int}], агрегируем по баркоду."""
    agg: dict[str, int] = defaultdict(int)
    for item in assembly.items:
        if item.barcode and item.quantity:
            agg[item.barcode] += item.quantity
    return [{"barcode": bc, "quantity": qty} for bc, qty in agg.items()]


async def _resolve_warehouse_id(client: WbPortalClient, name: str) -> int:
    """Имя направления → числовой warehouseID портала.

    Направление заявки — КАНОНИЧЕСКОЕ имя (ACCEPTANCE_TO_STOCK_NAME: «Краснодар»,
    «Самара (Новосемейкино)»), а фильтр портала отдаёт СЫРЫЕ кабинетные имена
    («Краснодар (Тихорецкая)», «Новосемейкино») — точного совпадения может не
    быть в принципе (ASM-689/693). Порядок матча: точный → casefold → канон
    обеих сторон (спец-двойники СГТ/Питание/СЦ исключаются, иначе id спец-склада
    перетирает реальный — см. learnings про last-wins).
    """
    items = await client.get_warehouse_filter_items()
    by_name = {w["warehouseName"]: w["warehouseID"] for w in items if w.get("warehouseName")}
    if name in by_name:
        return int(by_name[name])
    # Фолбэк 1: сравнение без учёта регистра/пробелов.
    norm = name.strip().casefold()
    for wname, wid in by_name.items():
        if wname.strip().casefold() == norm:
            return int(wid)
    # Фолбэк 2: канонический матч (только реальные склады, без спец-двойников).
    target = _normalize_acceptance_wh(name)
    candidates: dict[int, str] = {}
    for wname, wid in by_name.items():
        if _is_spec_acceptance_wh(wname):
            continue
        if _normalize_acceptance_wh(wname) == target:
            candidates[int(wid)] = wname
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise WbSupplyError(
            f"WB-склад «{name}» неоднозначен в кабинете: "
            f"{', '.join(sorted(candidates.values()))} — выберите склад вручную."
        )
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
    # Заодно тянем дату брони слота и текст кабинетных ошибок поставки; если
    # строка связи реальна (есть в БД) — персистим, чтобы список сборок показывал
    # их без похода в WB на каждую строку.
    supply_id_eff = link.supply_id or adopt
    if supply_id_eff:
        try:
            client = await _client(db, project_id)
            meta = await _cabinet_status(client, supply_id_eff)
            if meta.name:
                updates["wb_supply_state"] = meta.name
                updates["wb_supply_state_id"] = meta.state_id
            updates["supply_date"] = meta.supply_date
            updates["reject_reason"] = meta.reject_reason
            if link.id is not None:
                link.wb_supply_state = meta.name or link.wb_supply_state
                link.wb_supply_state_id = meta.state_id
                link.supply_date = meta.supply_date
                link.reject_reason = meta.reject_reason
                link.wb_state_synced_at = utcnow()
                await db.commit()
        except (WbSessionExpired, WbPortalError, ValueError):
            pass
    return state.model_copy(update=updates)


# Статусы заявки, для которых WB-поставка ещё «живая» (её статус меняется и
# интересен). Терминальные (SHIPPED/DELIVERED/CLOSED/CANCELLED/RETURNED) не синкаем —
# их сотни в истории, а статус уже финальный → лишние вызовы и рейт-лимит.
# SHIPPED («в пути») тоже синкаем: поставка ещё не принята (→DELIVERED после
# приёмки WB), а блок «Расхождение поставок ФФ» и снимок кабинетного пропуска
# нужны именно для назначенных/в пути. Терминальные (DELIVERED/CLOSED/архив) — нет.
_SYNC_ACTIVE_STATUSES = ("IN_PROGRESS", "READY", "VEHICLE_ASSIGNED", "SHIPPED")
_SYNC_SUPPLY_CAP = 200      # предохранитель от лавины вызовов
_SYNC_DELAY = 0.25         # пауза между supplyDetails (щадим анти-бот)


async def sync_all_states(db: AsyncSession, project_id: int) -> dict:
    """
    Bulk-синк АВТОРИТЕТНОГО кабинетного статуса АКТИВНЫХ поставок проекта.

    Кабинетный `statusName` — готовый текст той же шкалы, что `WbFboSupply.wb_status`.
    Берём его per-supply через `supplyDetails` (listSupplies НЕ пагинируется по
    offset — отдаёт только верхние ~20). Чтобы не упереться в рейт-лимит,
    синкаем ТОЛЬКО активные заявки (IN_PROGRESS/READY/VEHICLE_ASSIGNED — не
    архивные, не терминальные): их немного. Пауза + 1 ретрай на WbPortalError.
    Для адоптированных из FBO без строки — строка создаётся. supplyDetails также
    используется в панели (get_state). Возврат: {checked, updated, supplies_seen}.
    """
    # 1. ВСЕ строки связи АКТИВНЫХ незаархивированных заявок — в т.ч. «голые»
    #    (без supply_id): иначе адопция FBO ниже создала бы ДУБЛЬ по unique-индексу
    #    ix_assembly_wb_supply_assembly_request_id (напр. локальный пропуск без брони).
    links_res = await db.execute(
        select(AssemblyWbSupply)
        .join(AssemblyRequest, AssemblyWbSupply.assembly_request_id == AssemblyRequest.id)
        .where(
            AssemblyWbSupply.project_id == project_id,
            AssemblyRequest.status.in_(_SYNC_ACTIVE_STATUSES),
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.is_archived.is_(False),
        )
    )
    links = list(links_res.scalars().all())
    by_assembly = {link.assembly_request_id: link for link in links}

    # 2. Активные заявки с забронированной FBO-поставкой (адопция).
    fbo_res = await db.execute(
        select(AssemblyRequest.id, WbFboSupply)
        .join(WbFboSupply, AssemblyRequest.wb_fbo_supply_id == WbFboSupply.id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.status.in_(_SYNC_ACTIVE_STATUSES),
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.is_archived.is_(False),
        )
    )
    work: list[tuple[int, int, AssemblyWbSupply | None]] = [
        (link.assembly_request_id, int(link.supply_id), link)
        for link in links
        if link.supply_id is not None
    ]
    for aid, fbo in fbo_res.all():
        existing = by_assembly.get(aid)
        if existing and existing.supply_id:
            continue
        sid = fbo_adopted_supply_id(fbo)
        if sid:
            work.append((aid, sid, existing))

    if not work:
        return {"checked": 0, "updated": 0, "supplies_seen": 0}

    try:
        client = await _client(db, project_id)
    except ValueError as e:
        raise WbSupplyError(str(e)) from e

    updated = 0
    checked = 0
    autopushed = 0
    for aid, sid, link in work[:_SYNC_SUPPLY_CAP]:
        try:
            meta = await _cabinet_status(client, sid)
        except WbSessionExpired as e:
            await integrations_service.mark_wb_portal_expired(db, project_id)
            raise WbSupplyError(
                "Сессия WB-кабинета истекла. Обновите доступ WB в настройках."
            ) from e
        except WbPortalError:
            # Рейт-лимит/транзиент — один ретрай с бэкоффом; иначе пропускаем.
            await asyncio.sleep(1.5)
            try:
                meta = await _cabinet_status(client, sid)
            except WbPortalError:
                await asyncio.sleep(_SYNC_DELAY)
                continue
        checked += 1
        if meta.name:
            if link is None:
                link = AssemblyWbSupply(
                    project_id=project_id,
                    assembly_request_id=aid,
                    supply_id=sid,
                    sync_status=WbSupplySyncStatus.BOOKED.value,
                    boxes=[],
                )
                db.add(link)
            elif link.supply_id is None:
                # «Голая» строка (пропуск без брони) адоптирует supply_id из FBO.
                link.supply_id = sid
            if link.wb_supply_state != meta.name or link.wb_supply_state_id != meta.state_id:
                link.wb_supply_state = meta.name
                link.wb_supply_state_id = meta.state_id
                updated += 1
            link.supply_date = meta.supply_date
            link.reject_reason = meta.reject_reason
            link.wb_state_synced_at = utcnow()
            # Снимок кабинетного пропуска (для сверки ДДС↔ВБ) — best-effort:
            # сбой пропуска не роняет синк статуса; сессия истекла — пробрасываем.
            try:
                cab = _parse_cabinet_pass(await client.trn_details(sid))
            except WbSessionExpired as e:
                await integrations_service.mark_wb_portal_expired(db, project_id)
                raise WbSupplyError(
                    "Сессия WB-кабинета истекла. Обновите доступ WB в настройках."
                ) from e
            except WbPortalError:
                cab = None
            if cab is not None:
                _apply_cabinet_pass_snapshot(link, cab)
                # Фоновый добор авто-заноса пропуска: дата забронирована, пропуск
                # заполнен, а в кабинете его ещё нет → заносим (страховка на любой
                # порядок событий назначение↔бронь). `cab` уже разобран — не ходим
                # в trn_details повторно.
                if await try_autopush_pass(
                    db, project_id, link, client=client, cabinet_pass=cab
                ):
                    autopushed += 1
        await asyncio.sleep(_SYNC_DELAY)

    await db.commit()
    if autopushed:
        # Пропуск(и) оформлены (sync_status→PASSED) → строки уходят из блока
        # «Расхождение поставок ФФ» (pass_missing). Гасим кэш вкладки.
        await invalidate_cache("reports:assembly_link_anomalies")
    return {
        "checked": checked,
        "updated": updated,
        "supplies_seen": checked,
        "autopushed": autopushed,
    }


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
    # Телефон сразу в формат WB-пропуска (79XXXXXXXXX) — иначе setTRNDetails
    # отбивает «8-…»/«+7…» как «Номер телефона не валиден».
    link.pass_driver_phone = normalize_ru_phone(data.get("driver_phone")) or None
    link.pass_car_model = data.get("car_model")
    link.pass_car_number = data.get("car_number")
    link.pass_pallets = data.get("pallets")
    await db.commit()
    # pass_pallets кормит блок «Расхождение поставок ФФ» (pallet_mismatch) — гасим кэш.
    await invalidate_cache("reports:assembly_link_anomalies")
    return link


# Русский госномер: логист вводит «Номер, водитель, ТК» одной строкой (vehicle_info).
# Вынимаем сам номер (кабинет WB хранит его отдельным полем) и best-effort ФИО.
_PLATE_RE = re.compile(
    r"[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{2,3}",
    re.IGNORECASE,
)
# Телефон: 6+ подряд цифр (с разделителями) — убираем из строки перед парсингом ФИО.
_PHONE_RE = re.compile(r"[+()\d][\d\-()\s]{5,}\d")


def _extract_plate(text: str) -> str | None:
    """Госномер из свободной строки машины заявки (или None)."""
    m = _PLATE_RE.search(text or "")
    return m.group(0) if m else None


def _parse_driver_name(text: str, plate: str | None) -> tuple[str | None, str | None]:
    """Best-effort ФИО из строки машины: убираем госномер и телефон, остаток —
    слова ФИО (порядок кабинета WB: Фамилия Имя …). Возвращает (first, last).

    Парсинг эвристический — логист дозаполняет/правит поля в панели пропуска
    перед заносом в WB, поэтому ошибка парсинга не критична.
    """
    rest = text or ""
    if plate:
        rest = rest.replace(plate, " ")
    rest = _PHONE_RE.sub(" ", rest)
    words = [w for w in re.split(r"[\s,]+", rest) if w and any(ch.isalpha() for ch in w)]
    if not words:
        return None, None
    last = words[0]
    first = words[1] if len(words) > 1 else None
    return first, last


async def sync_pass_from_vehicle(
    db: AsyncSession, project_id: int, assembly: AssemblyRequest
) -> None:
    """F3: при назначении машины зеркалим её реквизиты в WB-пропуск заявки.

    Госномер/марку/телефон берём из машины (источник истины — назначение,
    перезаписываем). ФИО парсим best-effort и ставим ТОЛЬКО если пусто (не
    затираем ручной ввод). Число паллет — из заявки, если в пропуске пусто.
    Занос в WB (push_pass) остаётся ручным: логист дозаполняет ФИО и жмёт
    «Занести пропуск в WB». Строку НЕ коммитим — коммитит вызывающий
    (assign_vehicle) в своей транзакции.
    """
    veh_info = (assembly.vehicle_info or "").strip()
    veh_brand = (assembly.vehicle_brand or "").strip()
    veh_phone = (assembly.driver_phone or "").strip()
    first_name = (assembly.driver_first_name or "").strip()
    last_name = (assembly.driver_last_name or "").strip()
    if not (veh_info or veh_brand or veh_phone or first_name or last_name):
        return
    # adopt_supply_id=None: siblings совместной поставки могут не иметь загруженного
    # relationship wb_fbo_supply (lazy-load в async упадёт). Пропуск сохраняем
    # локально; supply_id доберётся при заносе/открытии панели (get_state).
    link = await _get_or_create_link(db, project_id, assembly.id)

    # Госномер: с приведением модалки к составу пропуска `vehicle_info` — это чистый
    # госномер. У СТАРЫХ заявок там свободная строка «Номер, водитель, ТК» → regex.
    if veh_info:
        link.pass_car_number = _extract_plate(veh_info) or veh_info
    if veh_brand:
        link.pass_car_model = veh_brand
    if veh_phone:
        link.pass_driver_phone = normalize_ru_phone(veh_phone) or None

    # ФИО: явные поля заявки — источник истины. Их нет только у старых заявок →
    # тогда best-effort парсинг из свободной строки, и только в пустой пропуск.
    if first_name or last_name:
        if first_name:
            link.pass_driver_first = first_name
        if last_name:
            link.pass_driver_last = last_name
    elif not (link.pass_driver_first or link.pass_driver_last):
        parsed_first, parsed_last = _parse_driver_name(veh_info, _extract_plate(veh_info))
        if parsed_first:
            link.pass_driver_first = parsed_first
        if parsed_last:
            link.pass_driver_last = parsed_last

    if link.pass_pallets is None and assembly.pallets_count:
        link.pass_pallets = assembly.pallets_count


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
        goods_errors = _extract_goods_errors(validation)
        if goods_errors:
            shown = "; ".join(goods_errors[:8])
            more = f" (и ещё {len(goods_errors) - 8})" if len(goods_errors) > 8 else ""
            raise WbSupplyError(
                f"WB не принимает часть товаров на складе «{name}»: {shown}{more}. "
                "Уберите эти товары из заявки или выберите другой склад."
            )
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
        # Дата только что забронирована → сразу пробуем занести пропуск, если он
        # уже заполнен (например, машину назначили раньше брони).
        pushed = await try_autopush_pass(db, project_id, link, client=client)
        await db.commit()
        if pushed:
            await invalidate_cache("reports:assembly_link_anomalies")
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
        # Только что записали пропуск в WB — кабинетный снимок = отправленное.
        # Обновляем сразу, иначе «Номер ВБ» в блоке ждёт следующего синка состояний.
        link.wb_pass_present = True
        link.wb_pass_car_number = car_number
        link.wb_pass_pallets = pallets
        link.wb_pass_driver = f"{last_name} {first_name}".strip() or None
        link.wb_pass_synced_at = utcnow()
        link.last_error = None
        link.last_synced_at = utcnow()
        await db.commit()
        # Пропуск оформлен (sync_status→PASSED) → строка уходит из блока
        # «Расхождение поставок ФФ» (pass_missing). Гасим кэш вкладки.
        await invalidate_cache("reports:assembly_link_anomalies")
        return link

    return await _run(db, project_id, link, _flow())


# ─── авто-занос пропуска в WB (F3+) ───────────────────────────────────────────
# Логист назначил машину → пропуск должен уехать в кабинет WB САМ, без ручного
# клика. Физическое ограничение: занести пропуск можно только после брони даты
# (нужен supply_id), а бронь — ручной антибот-шаг в портале. Поэтому «занос при
# назначении» = попытка при КАЖДОМ событии, когда он становится возможен:
#   1) назначение машины / правка реквизитов (assign_vehicle, update_assembly_request);
#   2) подхват брони (sync_supply_id);
#   3) фоновый добор (sync_all_states, шедулер) — страховка на любой порядок событий.
# Все точки идемпотентны и best-effort: сбой не роняет вызывающего.


def _pass_is_complete(link: AssemblyWbSupply) -> bool:
    """Все поля, которые требует WB (setTRNDetails): ФИО, телефон, госномер,
    марка, паллеты > 0. Неполный пропуск НЕ заносим — ждём дозаполнения логистом
    (иначе WB отклонит занос). Зеркалит `missingPass` в панели «Поставка WB»."""
    return bool(
        (link.pass_driver_first or "").strip()
        and (link.pass_driver_last or "").strip()
        and (link.pass_driver_phone or "").strip()
        and (link.pass_car_number or "").strip()
        and (link.pass_car_model or "").strip()
        and (link.pass_pallets or 0) > 0
    )


async def try_autopush_pass(
    db: AsyncSession,
    project_id: int,
    link: AssemblyWbSupply,
    *,
    client: WbPortalClient | None = None,
    cabinet_pass: "WbCabinetPass | None" = None,
) -> bool:
    """Занести пропуск в WB автоматически, если это возможно (best-effort, БЕЗ commit).

    Заносим ТОЛЬКО когда:
      • есть supply_id (дата забронирована);
      • пропуск ещё не занесён (sync_status != PASSED);
      • заполнены все обязательные поля (_pass_is_complete);
      • в кабинете пропуска ещё нет — не перетираем ручной кабинетный ввод.

    `cabinet_pass` можно передать (уже разобранный в sync_all_states) — тогда не
    ходим в trn_details повторно. Ошибки НЕ пробрасываем (фоновая операция не
    должна ронять назначение/синк) — только логируем; commit делает вызывающий.
    Возвращает True, если занос выполнен.
    """
    if not link.supply_id:
        return False
    if link.sync_status == WbSupplySyncStatus.PASSED.value:
        return False
    if not _pass_is_complete(link):
        return False
    supply_id = link.supply_id
    try:
        if client is None:
            client = await _client(db, project_id)
        if cabinet_pass is None:
            cabinet_pass = _parse_cabinet_pass(await client.trn_details(supply_id))
        # В кабинете уже есть пропуск (завели руками/раньше) — не трогаем, расхождение
        # покажет блок сверки. Занос только в ПУСТОЙ кабинетный пропуск.
        if cabinet_pass.has_pass:
            return False
        barcode_id = cabinet_pass.barcode_id
        if not barcode_id:
            return False  # WB ещё не выдал ШК пропуска — попробуем на следующем событии
        # _pass_is_complete гарантирует непустые значения — `or ""` только сужает
        # тип для mypy (Optional-колонки), фолбэк фактически не срабатывает.
        first_name = link.pass_driver_first or ""
        last_name = link.pass_driver_last or ""
        car_number = link.pass_car_number or ""
        car_model = link.pass_car_model or ""
        phone = link.pass_driver_phone or ""
        pallets = link.pass_pallets or 0
        await client.set_trn(
            barcode_id=int(barcode_id),
            supply_id=supply_id,
            first_name=first_name,
            last_name=last_name,
            car_model=car_model,
            car_number=car_number,
            phone=phone,
            pallets=pallets,
        )
        # Снимок как в push_pass: только что записали пропуск в WB.
        link.barcode_id = int(barcode_id)
        link.sync_status = WbSupplySyncStatus.PASSED.value
        link.wb_pass_present = True
        link.wb_pass_car_number = car_number
        link.wb_pass_pallets = pallets
        link.wb_pass_driver = f"{last_name} {first_name}".strip() or None
        link.wb_pass_synced_at = utcnow()
        link.last_error = None
        link.last_synced_at = utcnow()
        logger.info(
            "wb pass auto-pushed",
            project_id=project_id,
            assembly_request_id=link.assembly_request_id,
            supply_id=supply_id,
        )
        return True
    except WbSessionExpired:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        logger.warning(
            "wb pass auto-push: session expired",
            project_id=project_id,
            assembly_request_id=link.assembly_request_id,
        )
        return False
    except WbPortalError as e:
        # WB отклонил занос (напр. формат данных) — не флипаем в ERROR, чтобы
        # фон не спамил панель; логиста разблокирует ручной «Занести пропуск в WB».
        logger.warning(
            "wb pass auto-push: portal rejected",
            project_id=project_id,
            assembly_request_id=link.assembly_request_id,
            error=str(e),
        )
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "wb pass auto-push: unexpected error",
            project_id=project_id,
            assembly_request_id=link.assembly_request_id,
            exc_info=True,
        )
        return False


async def try_autopush_pass_by_assembly(
    db: AsyncSession, project_id: int, assembly_id: int
) -> bool:
    """Авто-занос пропуска для заявки по id (триггеры назначения машины).

    Грузит заявку с `wb_fbo_supply` (адопция supply_id безопасна — relationship
    загружен явно), затем `try_autopush_pass`. Коммитит и гасит кэш сверки ТОЛЬКО
    при успешном заносе. Best-effort: любая ошибка проглатывается.
    """
    try:
        assembly = await _load_assembly(db, project_id, assembly_id)
    except WbSupplyError:
        return False
    link = await _get_or_create_link(
        db, project_id, assembly_id, adopt_supply_id=_adopted_supply_id(assembly)
    )
    pushed = await try_autopush_pass(db, project_id, link)
    if pushed:
        await db.commit()
        await invalidate_cache("reports:assembly_link_anomalies")
    return pushed


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


async def get_cabinet_pass(
    db: AsyncSession, project_id: int, assembly_id: int
) -> WbCabinetPass:
    """
    Существующий пропуск поставки из кабинета WB (trn_details) — водитель/авто/
    паллеты/ШК пропуска. Нужен, когда пропуск завели прямо в кабинете (наш авто-
    синк тянет только статус, не пропуск) — панель показывает то, что уже в WB.

    supply_id — из реплей-связи ИЛИ адопции из FBO.
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
        return WbCabinetPass()

    client = await _client(db, project_id)
    try:
        details = await client.trn_details(supply_id)
    except WbSessionExpired as e:
        await integrations_service.mark_wb_portal_expired(db, project_id)
        raise WbSupplyError("Сессия WB-кабинета истекла. Обновите доступ WB.") from e
    except WbPortalError as e:
        raise WbSupplyError(f"WB отклонил операцию: {e}") from e

    return _parse_cabinet_pass(details)


def _parse_cabinet_pass(details: dict) -> WbCabinetPass:
    """trn_details WB → WbCabinetPass (водитель/авто/паллеты/ШК пропуска)."""
    trns = ((details.get("details") or {}).get("trns")) or []
    if not trns:
        return WbCabinetPass(has_pass=False)
    t = trns[0]
    bc = t.get("barcode") or {}
    pallets = t.get("quantity")
    return WbCabinetPass(
        has_pass=bool(t.get("firstName") or t.get("lastName") or t.get("carNumber")),
        driver_first=t.get("firstName") or None,
        driver_last=t.get("lastName") or None,
        driver_phone=t.get("phone") or None,
        car_model=t.get("carModel") or None,
        car_number=t.get("carNumber") or None,
        pallets=int(pallets) if isinstance(pallets, int) else None,
        barcode_id=bc.get("barcodeId"),
        barcode_prefix=bc.get("barcodePrefix"),
        date_from=t.get("dateFrom"),
        date_to=t.get("dateTo"),
    )


def _apply_cabinet_pass_snapshot(link: AssemblyWbSupply, cab: WbCabinetPass) -> None:
    """Снимок кабинетного пропуска в link + заполнение ТОЛЬКО пустых полей ДДС.

    Снимок (`wb_pass_*`) — для сверки нашего пропуска с кабинетным. Пустые поля
    нашего пропуска (`pass_*`) подтягиваем из кабинета (пропуск завели прямо в WB);
    непустые НЕ трогаем — расхождение показывается в блоке (см. link_anomalies).
    """
    link.wb_pass_present = cab.has_pass
    link.wb_pass_car_number = cab.car_number
    link.wb_pass_pallets = cab.pallets
    link.wb_pass_driver = " ".join(x for x in (cab.driver_last, cab.driver_first) if x) or None
    link.wb_pass_synced_at = utcnow()
    if not cab.has_pass:
        return
    if not link.pass_driver_first and cab.driver_first:
        link.pass_driver_first = cab.driver_first
    if not link.pass_driver_last and cab.driver_last:
        link.pass_driver_last = cab.driver_last
    if not link.pass_car_model and cab.car_model:
        link.pass_car_model = cab.car_model
    if not link.pass_car_number and cab.car_number:
        link.pass_car_number = cab.car_number
    if not link.pass_driver_phone and cab.driver_phone:
        link.pass_driver_phone = normalize_ru_phone(cab.driver_phone) or None
    if link.pass_pallets is None and cab.pallets is not None:
        link.pass_pallets = cab.pallets


# ─── парсеры ответов WB ──────────────────────────────────────────────────────


def _extract_box_types(validation: dict) -> list[int]:
    items = validation.get("items") or []
    for it in items:
        types = it.get("availableBoxTypes")
        if types:
            return [int(t) for t in types]
    return []


def _extract_goods_errors(validation: dict) -> list[str]:
    """Пер-товарные ошибки validateWarehouseGoodsV2 → человекочитаемые строки.

    WB отдаёт items[].hasError + errors[{field, error}] (снято живьём на ASM-701:
    «Запрет завоза предмета на склад» — категория запрещена на складе). Если
    такие товары не отсеять, supply/create падает с -32003 и ПУСТЫМ innerMsg
    («есть ошибки в товарах поставки») — причину пользователь не видит.
    """
    out: list[str] = []
    for it in validation.get("items") or []:
        if not it.get("hasError"):
            continue
        label = it.get("sa") or it.get("nmSa") or str(it.get("nmId") or "?")
        msgs = sorted(
            {
                e["error"]
                for e in (it.get("errors") or [])
                if isinstance(e, dict) and e.get("error")
            }
        )
        text = ", ".join(msgs) if msgs else "ошибка без описания"
        out.append(f"{label} ({it.get('barcode', '?')}) — {text}")
    return out


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


