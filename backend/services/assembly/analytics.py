# ruff: noqa: RUF001, RUF002, RUF003
"""
Assembly Request service — FBO sync and logistics analytics.

Part of the assembly service package. See backend/DOMAIN_ASSEMBLY.md for spec.
One-way dependency: analytics -> crud (never crud -> analytics).
"""

import asyncio
import logging
import statistics
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import cached, invalidate_cache
from backend.models.assembly import (
    AssemblyKind,
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.cost import Nomenclature
from backend.models.counterparty import Counterparty
from backend.models.fulfillment import FfRequestKind, FulfillmentRequest
from backend.models.warehouse import (
    OutboundShipment,
    OutboundShipmentItem,
    OutboundStatus,
    Warehouse,
)
from backend.models.gazelka import GazelkaOrder, GazelkaOrderStatus
from backend.models.wb_fbo import WbFboSupply, WbFboSupplyItem
from backend.schemas.assembly import AssemblyItemResponse, RefreshFromFboResponse
from backend.services.warehouse_service import _resolve_barcode
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

# --- FBO sync ---------------------------------------------------------------


async def refresh_from_fbo(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    api_client: Any = None,
    *,
    skip_force_enrich: bool = False,
    invalidate: bool = True,
) -> RefreshFromFboResponse:
    """
    Re-sync items from linked WbFboSupply.
    Available: PENDING -> VEHICLE_ASSIGNED (not SHIPPED, not CANCELLED).

    Перед сравнением форс-перетягиваем построчный состав поставки из WB (goods),
    иначе диф идёт по устаревшему локальному зеркалу WbFboSupplyItem и не видит
    позиций, добавленных в кабинете WB после создания поставки. api_client —
    инъекция для тестов (в проде строится из WB-ключа внутри enrich-хелпера).

    skip_force_enrich=True — НЕ дёргать WB (зеркало уже обновлено вызывающим);
    диф идёт по локальному WbFboSupplyItem. Используется фоновой пачкой
    refresh_active_assemblies_from_fbo, которая перетягивает goods по поставке
    один раз на все её сборки (дедуп WB-вызовов + троттлинг).
    invalidate=False — не инвалидировать кэши здесь; пачка делает это один раз
    после всех сборок (иначе N×2 инвалидаций на прогон).

    Совместная поставка (≥2 сборок на одну WB-поставку): зеркало WB обновляем как
    обычно, но позиции сборки НЕ перестраиваем — общий состав поставки нельзя
    разложить по ФФ-источникам. Расхождение для таких считается по СУММЕ всех
    сборок поставки (fulfillment_service.compute_doc_ff_mismatch).
    """
    from .crud import get_assembly_request

    req = await get_assembly_request(db, project_id, request_id)
    if not req:
        raise ValueError("Assembly request not found")

    if req.status in (AssemblyStatus.SHIPPED, AssemblyStatus.DELIVERED, AssemblyStatus.CANCELLED):
        raise ValueError(f"Cannot refresh in status {req.status}")

    if not req.wb_fbo_supply_id:
        raise ValueError("Cannot refresh items: no FBO supply linked")

    # Load FBO supply (для wb_status — определяет что считать «фактом»)
    supply_result = await db.execute(
        select(WbFboSupply).where(
            WbFboSupply.id == req.wb_fbo_supply_id,
            WbFboSupply.project_id == project_id,
        )
    )
    supply = supply_result.scalar_one_or_none()
    if not supply:
        raise ValueError("Linked FBO supply not found")

    # Force-pull WB detail + per-barcode goods, чтобы локальное зеркало
    # WbFboSupplyItem отражало текущий состав в кабинете WB (а не застрявший
    # снимок). Best-effort: при отсутствии ключа/ошибке WB diff пойдёт по тому,
    # что уже лежит в БД.
    if not skip_force_enrich:
        from .crud import _try_force_enrich_supply

        await _try_force_enrich_supply(db, project_id, supply, force=True, with_goods=True, api_client=api_client)
    if supply.warehouse_name and req.wb_warehouse_name_manual != supply.warehouse_name:
        req.wb_warehouse_name_manual = supply.warehouse_name

    # Совместная поставка (≥2 сборок на одну WB-поставку): зеркало WB обновлено
    # выше, но позиции НЕ перестраиваем — общий состав поставки нельзя разложить
    # по ФФ-источникам (иначе одна сборка получила бы весь объём → ложное
    # расхождение). Расхождение для совместных считается по СУММЕ всех сборок
    # поставки (fulfillment_service.compute_doc_ff_mismatch). Возвращаем текущие
    # позиции без изменений.
    joint_cnt = (
        await db.execute(
            select(func.count(AssemblyRequest.id)).where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.wb_fbo_supply_id == req.wb_fbo_supply_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status != AssemblyStatus.CANCELLED,
            )
        )
    ).scalar() or 0
    if joint_cnt >= 2:
        await db.commit()
        await db.refresh(req, ["items"])
        if invalidate:
            await invalidate_cache("reports:assembly_flow")
            await invalidate_cache("reports:assembly_link_anomalies")
        return RefreshFromFboResponse(
            added=0,
            removed=0,
            changed=0,
            skipped=[],
            items=[
                AssemblyItemResponse(
                    id=item.id,
                    nomenclature_id=item.nomenclature_id,
                    barcode=item.barcode,
                    quantity=item.quantity,
                )
                for item in req.items
            ],
        )

    # Load FBO supply items
    fbo_items_result = await db.execute(
        select(WbFboSupplyItem).where(
            WbFboSupplyItem.supply_id == req.wb_fbo_supply_id,
        )
    )
    fbo_items = fbo_items_result.scalars().all()

    # Если поставка уже принята WB (ACCEPTED) — подтягиваем фактически принятое
    # (accepted_qty), а не заявленное (quantity). Иначе синхим по quantity.
    use_accepted = supply.wb_status == "ACCEPTED"

    def _effective_qty(it: WbFboSupplyItem) -> int:
        return it.accepted_qty if use_accepted else it.quantity

    # Build maps by barcode
    current_map: dict[str, AssemblyRequestItem] = {item.barcode: item for item in req.items}
    fbo_map: dict[str, WbFboSupplyItem] = {item.barcode: item for item in fbo_items}

    added = 0
    removed = 0
    changed = 0
    skipped: list[str] = []

    # Remove items not in FBO anymore
    for barcode, item in current_map.items():
        if barcode not in fbo_map:
            await db.delete(item)  # no-soft-delete-check: AssemblyRequestItem has no SoftDeleteMixin
            removed += 1

    # Add new / update existing
    for barcode, fbo_item in fbo_map.items():
        qty = _effective_qty(fbo_item)
        if barcode in current_map:
            existing = current_map[barcode]
            if qty == 0:
                # WB не принял эту позицию — убираем из заявки
                await db.delete(existing)  # no-soft-delete-check: AssemblyRequestItem has no SoftDeleteMixin
                removed += 1
            elif existing.quantity != qty:
                existing.quantity = qty
                changed += 1
        else:
            if qty == 0:
                continue  # новую позицию с 0 не добавляем
            # New barcode — резолвим номенклатуру. Нет в справочнике → не валим
            # весь рефреш (был бы HTTP 400, юзер не увидел бы остальные правки),
            # а копим в skipped, чтобы показать «не смогли добавить эти ШК».
            try:
                nom = await _resolve_barcode(db, project_id, barcode)
            except ValueError:
                skipped.append(barcode)
                continue
            new_item = AssemblyRequestItem(
                project_id=project_id,
                assembly_request_id=req.id,
                nomenclature_id=nom.id,
                barcode=barcode,
                quantity=qty,
            )
            db.add(new_item)
            added += 1

    await db.commit()
    await db.refresh(req, ["items"])
    if invalidate:
        await invalidate_cache("reports:assembly_flow")
        await invalidate_cache("reports:assembly_link_anomalies")
        await invalidate_cache("reports:warehouse_need")

    return RefreshFromFboResponse(
        added=added,
        removed=removed,
        changed=changed,
        skipped=skipped,
        items=[
            AssemblyItemResponse(
                id=item.id,
                nomenclature_id=item.nomenclature_id,
                barcode=item.barcode,
                quantity=item.quantity,
            )
            for item in req.items
        ],
    )


# Статусы, для которых фоновый авто-рефреш из FBO имеет смысл — состав ещё может
# меняться и сверяется с заявками ФФ (зеркало _MISMATCH_STATUSES в link_anomalies;
# SHIPPED/закрытые refresh_from_fbo всё равно отвергает).
_FBO_AUTOREFRESH_STATUSES: tuple[str, ...] = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
)
# Кап поставок за один прогон. WB-лимит 6 req/min: с троттлингом ~2 вызова/поставку
# выходит ~22 c/поставку, т.е. 60 поставок ≈ 22 мин (укладывается в timeout джобы).
# Остаток подхватит следующий часовой прогон.
_FBO_AUTOREFRESH_MAX_SUPPLIES = 60


async def refresh_active_assemblies_from_fbo(
    db: AsyncSession,
    project_id: int,
    api_client: Any = None,
) -> dict[str, int]:
    """Фоновый авто-рефреш состава активных сборок из привязанных FBO-поставок.

    Зеркалит ручную кнопку «Из FBO» (refresh_from_fbo), но без клика и для ВСЕХ
    активных сборок проекта с привязкой к WbFboSupply — чтобы «Расхождение
    наполнения» с заявками ФФ обновлялось само (планировщик зовёт раз в час).

    WB-вызовы дедуплицируются по поставке (несколько сборок на один wb_fbo_supply_id
    тянут goods один раз) и троттлятся (FBW_RATE_LIMIT_DELAY между detail/goods и
    между поставками) — иначе 429-шторм на лимите 6 req/min и голодание плановых
    fbo-джоб того же ключа. Кэши отчётов инвалидируются один раз в конце.

    Best-effort: ошибка по поставке/сборке не валит остальные (session.rollback,
    идём дальше). api_client — один на проект (инъекция; в проде строится из
    WB-ключа в джобе), чтобы не дёргать ключ на каждую сборку.

    Returns: {processed, refreshed, errors, supplies}.
    """
    from backend.services.fbo_supply.mappers import FBW_RATE_LIMIT_DELAY

    from .crud import _try_force_enrich_supply

    rows = (
        await db.execute(
            select(AssemblyRequest.id, AssemblyRequest.wb_fbo_supply_id)
            .where(
                AssemblyRequest.project_id == project_id,
                AssemblyRequest.is_deleted == False,  # noqa: E712
                AssemblyRequest.status.in_(_FBO_AUTOREFRESH_STATUSES),
                AssemblyRequest.wb_fbo_supply_id.is_not(None),
            )
            .order_by(AssemblyRequest.wb_fbo_supply_id, AssemblyRequest.id)
            .limit(2000)  # активных сборок с FBO-привязкой — десятки; кап как страховка
        )
    ).all()

    # Группируем сборки по поставке — WB-вызовы делаем один раз на поставку.
    by_supply: dict[int, list[int]] = {}
    for aid, sid in rows:
        by_supply.setdefault(sid, []).append(aid)
    supply_ids = list(by_supply)[:_FBO_AUTOREFRESH_MAX_SUPPLIES]
    if len(by_supply) > _FBO_AUTOREFRESH_MAX_SUPPLIES:
        logger.info(
            "assembly.fbo_autorefresh_capped",
            extra={"project_id": project_id, "total": len(by_supply), "cap": _FBO_AUTOREFRESH_MAX_SUPPLIES},
        )

    processed = 0
    refreshed = 0
    errors = 0
    any_change = False

    for i, supply_id in enumerate(supply_ids):
        # Совместная поставка (≥2 её сборок в авто-рефреш-статусах): состав не
        # перестраиваем, а её расхождение считается по СУММЕ сборок (FF-based) —
        # WB-зеркало для этого не нужно. Пропускаем и WB-вызов, и диф (экономим
        # WB-квоту). Кросс-статусный случай (одна сборка вне авто-рефреш-статусов)
        # подстрахует non-destructive ветка в refresh_from_fbo.
        if len(by_supply[supply_id]) >= 2:
            continue
        # 1) Один троттлёный force-pull goods на поставку (для всех её сборок).
        supply = (
            await db.execute(
                select(WbFboSupply).where(
                    WbFboSupply.id == supply_id,
                    WbFboSupply.project_id == project_id,
                )
            )
        ).scalar_one_or_none()
        if supply is not None and api_client is not None:
            if i > 0:
                await asyncio.sleep(FBW_RATE_LIMIT_DELAY)
            try:
                await _try_force_enrich_supply(
                    db, project_id, supply, force=True, with_goods=True, api_client=api_client, throttle=True
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.warning(
                    "assembly.fbo_autorefresh_enrich_failed",
                    extra={"project_id": project_id, "supply_id": supply_id, "error": str(e)[:200]},
                )

        # 2) Диффим каждую сборку по уже свежему локальному зеркалу (без WB).
        for aid in by_supply[supply_id]:
            processed += 1
            try:
                res = await refresh_from_fbo(
                    db, project_id, aid, skip_force_enrich=True, invalidate=False
                )
                if res.added or res.removed or res.changed:
                    refreshed += 1
                    any_change = True
            except Exception as e:
                errors += 1
                await db.rollback()
                logger.warning(
                    "assembly.fbo_autorefresh_failed",
                    extra={"project_id": project_id, "assembly_id": aid, "error": str(e)[:200]},
                )

    if any_change:
        await invalidate_cache("reports:assembly_flow")
        await invalidate_cache("reports:assembly_link_anomalies")
        await invalidate_cache("reports:warehouse_need")

    return {"processed": processed, "refreshed": refreshed, "errors": errors, "supplies": len(supply_ids)}


# --- Logistics Analytics ---------------------------------------------------


# Destination warehouse name: снимок назначения на отгрузке, fallback — manual заявки.
_DEST_WAREHOUSE = func.coalesce(
    OutboundShipment.destination,
    AssemblyRequest.wb_warehouse_name_manual,
)
# avg_cost = average cost PER PALLET (not per shipment)
_COST_PER_PALLET = case(
    (OutboundShipment.pallets_count > 0, OutboundShipment.pickup_cost / OutboundShipment.pallets_count),
    else_=OutboundShipment.pickup_cost,
)
# Кап строк для построчной выдачи отгрузок (период шире — режем + флаг truncated).
_SHIPMENTS_LIMIT = 5000
# Минимум отгрузок для устойчивой статистики ₽/паллета (иначе медиана/MAD шумят):
# склады с меньшей выборкой считаются по глобальной статистике; если и глобальной
# выборки мало — over/under не размечаем (остаётся только no_cost).
_MIN_STATS_SAMPLE = 5


def _logistics_base_filters(
    project_id: int,
    *,
    date_from: date | None,
    date_to: date | None,
    warehouse_ids: list[int] | None,
    brands: list[str] | None,
    carrier_id: int | None,
    dest_warehouse: str | None = None,
) -> list:
    """Единый набор фильтров для аналитики и построчной выдачи отгрузок.

    Один источник истины, чтобы таблица «История отправок» и сводки шли по
    идентичной выборке (период считается по shipped_date, как в аналитике).
    """
    filters = [
        OutboundShipment.project_id == project_id,
        OutboundShipment.is_deleted == False,  # noqa: E712
        OutboundShipment.assembly_request_id.isnot(None),
        OutboundShipment.status != OutboundStatus.CANCELLED,
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
    ]
    if date_from is not None:
        filters.append(OutboundShipment.shipped_date >= date_from)
    if date_to is not None:
        filters.append(OutboundShipment.shipped_date < date_to + timedelta(days=1))
    if warehouse_ids:
        filters.append(OutboundShipment.warehouse_id.in_(warehouse_ids))
    if carrier_id is not None:
        filters.append(OutboundShipment.counterparty_id == carrier_id)
    if dest_warehouse:
        filters.append(_DEST_WAREHOUSE == dest_warehouse)
    if brands:
        # Filter by brand via items -> nomenclature join (по заявке отгрузки)
        brand_subq = (
            select(AssemblyRequestItem.assembly_request_id)
            .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
            .where(Nomenclature.brand.in_(brands))
            .distinct()
            .scalar_subquery()
        )
        filters.append(OutboundShipment.assembly_request_id.in_(brand_subq))
    return filters


def _bucket_for_pallets(p: int) -> tuple[str, int]:
    """Корзина размера отгрузки для кривой «объём → ₽/паллета»."""
    if p <= 1:
        return "1", 1
    if p <= 3:
        return "2-3", 2
    if p <= 5:
        return "4-5", 3
    if p <= 10:
        return "6-10", 4
    return "11+", 5


def _robust_stats(values: list[float]) -> tuple[float, float]:
    """Медиана и робастная сигма (1.4826·MAD) — устойчивы к выбросам логистики."""
    if not values:
        return 0.0, 0.0
    med = statistics.median(values)
    mad = statistics.median([abs(v - med) for v in values])
    return med, 1.4826 * mad


def _median_iqr(values: list[float]) -> tuple[float, float, float]:
    """Медиана и квартильный коридор (p25, p75) — типичная цена и её разброс.

    Медиана устойчива к выбросам (аномально дорогим/дешёвым перевозкам), квартили
    дают реалистичный диапазон прогноза. Для 1 точки — все три равны ей.
    """
    if not values:
        return 0.0, 0.0, 0.0
    vs = sorted(values)
    med = statistics.median(vs)
    if len(vs) < 2:
        return med, med, med
    q = statistics.quantiles(vs, n=4)  # [p25, p50, p75]
    return med, q[0], q[2]


def _classify_cpp(
    cpp: float,
    dest: str,
    dest_stats: dict[str, tuple[float, float]],
    global_stats: tuple[float, float],
) -> tuple[str | None, float, float | None, float | None, str]:
    """Классифицировать ₽/паллета как over/under/норму по складу (fallback — глобально).

    Returns (anomaly_type|None, severity, expected_low, expected_high, reason).
    """
    med, sigma = dest_stats.get(dest, (0.0, 0.0))
    scope = "складу"
    if med <= 0 or sigma <= 0:
        med, sigma = global_stats
        scope = "всем складам"
    if med <= 0:
        return None, 0.0, None, None, ""
    if sigma <= 0:
        # Разброса нет — относительный коридор ±40 %.
        low, high = med * 0.6, med * 1.4
        if cpp > high:
            return "overpriced", round(cpp / med, 2), low, high, f"Выше типичной по {scope} ({med:,.0f} ₽/пал)"
        if cpp < low:
            return "underpriced", round(med / cpp, 2) if cpp else 0.0, low, high, f"Ниже типичной по {scope} ({med:,.0f} ₽/пал)"
        return None, 0.0, low, high, ""
    z = (cpp - med) / sigma
    low = max(0.0, med - 3.5 * sigma)
    high = med + 3.5 * sigma
    # Требуем ОБА условия: статзначимость (|z|>3.5) И заметное отклонение цены
    # (±30% от медианы). Только z недостаточно — при низком разбросе даже мелкая
    # абсолютная разница даёт большой z и плодит ложные «аномалии».
    if z > 3.5 and cpp >= med * 1.3:
        return "overpriced", round(z, 2), low, high, f"Завышена: {cpp:,.0f} при медиане {med:,.0f} ₽/пал по {scope}"
    if z < -3.5 and cpp <= med * 0.7:
        return "underpriced", round(abs(z), 2), low, high, f"Занижена: {cpp:,.0f} при медиане {med:,.0f} ₽/пал по {scope}"
    return None, 0.0, low, high, ""


@cached(prefix="reports:logistics_analytics_v2", ttl=300)
async def get_logistics_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    brands: list[str] | None = None,
    carrier_id: int | None = None,
) -> dict:
    """
    Logistics cost analytics — по ПОПЫТКАМ отгрузки (OutboundShipment).

    Каждая отгрузка заявки = одна оплаченная перевозка. Переотгрузка после возврата
    (новый водитель/FBW) даёт отдельную попытку — её стоимость суммируется (а не
    затирается, как было бы при счёте по AssemblyRequest). Отменённые отгрузки
    soft-удалены и не считаются; возвраты (RETURNED/CLOSED) — считаются (оплачено).

    Returns summary, by_destination, by_route, by_carrier, pallet_buckets,
    cost_points и anomalies.
    """
    dest_warehouse = _DEST_WAREHOUSE.label("dest_warehouse")
    src_warehouse = Warehouse.name.label("src_warehouse")
    base_filters = _logistics_base_filters(
        project_id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=warehouse_ids,
        brands=brands,
        carrier_id=carrier_id,
    )

    # JOIN заявки ко всем запросам (для dest manual-fallback и is_deleted-гейта).
    req_join = (AssemblyRequest, OutboundShipment.assembly_request_id == AssemblyRequest.id)
    cost_per_pallet = _COST_PER_PALLET

    # --- Summary (агрегаты по отгрузкам) ---
    weight_expr = func.coalesce(OutboundShipment.pallet_weight_kg, Decimal("0")) * func.coalesce(
        OutboundShipment.pallets_count, 0
    )
    summary_q = (
        select(
            func.coalesce(func.sum(OutboundShipment.pickup_cost), Decimal("0")).label("total_cost"),
            func.coalesce(func.sum(OutboundShipment.pallets_count), 0).label("total_pallets"),
            func.count().label("total_shipments"),
            func.count(func.distinct(OutboundShipment.assembly_request_id)).label("total_requests"),
            func.count(func.distinct(dest_warehouse)).label("total_destinations"),
            func.count(func.distinct(OutboundShipment.counterparty_id)).label("total_carriers"),
            func.coalesce(func.sum(weight_expr), Decimal("0")).label("total_weight"),
        )
        .join(*req_join)
        .where(*base_filters)
    )
    summary_row = (await db.execute(summary_q)).one()

    # Штуки/SKU товара — через позиции отгрузок (OutboundShipmentItem).
    items_q = (
        select(
            func.coalesce(func.sum(OutboundShipmentItem.quantity), 0).label("total_items"),
            func.count(func.distinct(OutboundShipmentItem.nomenclature_id)).label("total_skus"),
        )
        .select_from(OutboundShipmentItem)
        .join(OutboundShipment, OutboundShipment.id == OutboundShipmentItem.shipment_id)
        .join(*req_join)
        .where(*base_filters, OutboundShipmentItem.project_id == project_id)
    )
    items_row = (await db.execute(items_q)).one()

    total_pallets = int(summary_row.total_pallets)
    total_cost = summary_row.total_cost or Decimal("0")
    avg_cost_per_pallet = (total_cost / Decimal(str(total_pallets))) if total_pallets > 0 else Decimal("0")

    summary = {
        "total_cost": total_cost,
        "avg_cost_per_pallet": avg_cost_per_pallet.quantize(Decimal("0.01")),
        "total_pallets": total_pallets,
        "total_shipments": int(summary_row.total_shipments),
        "total_requests": int(summary_row.total_requests),
        "total_items": int(items_row.total_items),
        "total_skus": int(items_row.total_skus),
        "total_weight_kg": (summary_row.total_weight or Decimal("0")).quantize(Decimal("0.01")),
        "total_destinations": int(summary_row.total_destinations),
        "total_carriers": int(summary_row.total_carriers),
    }

    # --- By destination (+ контекст объёма паллет) ---
    dest_q = (
        select(
            dest_warehouse,
            func.avg(cost_per_pallet).label("avg_cost"),
            func.sum(OutboundShipment.pickup_cost).label("total_cost"),
            func.count().label("shipments_count"),
            func.coalesce(func.sum(OutboundShipment.pallets_count), 0).label("total_pallets"),
            func.avg(OutboundShipment.pallets_count).label("avg_pallets"),
            func.min(cost_per_pallet).label("min_cpp"),
            func.max(cost_per_pallet).label("max_cpp"),
        )
        .join(*req_join)
        .where(*base_filters)
        .group_by(dest_warehouse)
        .order_by(func.sum(OutboundShipment.pickup_cost).desc())
    )
    dest_rows = (await db.execute(dest_q)).all()

    by_destination = [
        {
            "dest_warehouse": row.dest_warehouse or "N/A",
            "avg_cost": (row.avg_cost or Decimal("0")).quantize(Decimal("0.01")),
            "total_cost": row.total_cost or Decimal("0"),
            "shipments_count": int(row.shipments_count),
            "total_pallets": int(row.total_pallets),
            "avg_pallets": (row.avg_pallets or Decimal("0")).quantize(Decimal("0.1")),
            "min_cost_per_pallet": (row.min_cpp or Decimal("0")).quantize(Decimal("0.01")),
            "max_cost_per_pallet": (row.max_cpp or Decimal("0")).quantize(Decimal("0.01")),
        }
        for row in dest_rows
    ]

    # --- By route (src -> dest) ---
    route_q = (
        select(
            src_warehouse,
            dest_warehouse,
            func.avg(cost_per_pallet).label("avg_cost"),
            func.count().label("shipments_count"),
        )
        .join(*req_join)
        .join(Warehouse, and_(OutboundShipment.warehouse_id == Warehouse.id, Warehouse.project_id == project_id))
        .where(*base_filters)
        .group_by(src_warehouse, dest_warehouse)
        .order_by(func.count().desc())
    )
    route_rows = (await db.execute(route_q)).all()

    by_route = [
        {
            "src_warehouse": row.src_warehouse or "N/A",
            "dest_warehouse": row.dest_warehouse or "N/A",
            "avg_cost": (row.avg_cost or Decimal("0")).quantize(Decimal("0.01")),
            "shipments_count": int(row.shipments_count),
        }
        for row in route_rows
    ]

    # --- By carrier (top подрядчиков) ---
    carrier_q = (
        select(
            OutboundShipment.counterparty_id,
            Counterparty.inn.label("carrier_inn"),
            Counterparty.name.label("carrier_name"),
            func.count().label("shipments_count"),
            func.coalesce(func.sum(OutboundShipment.pallets_count), 0).label("total_pallets"),
            func.coalesce(func.sum(OutboundShipment.pickup_cost), Decimal("0")).label("total_cost"),
            func.avg(cost_per_pallet).label("avg_cost"),
            func.count(func.distinct(dest_warehouse)).label("destinations_count"),
        )
        .join(*req_join)
        .join(Counterparty, and_(OutboundShipment.counterparty_id == Counterparty.id, Counterparty.project_id == project_id))
        .where(*base_filters, OutboundShipment.counterparty_id.isnot(None))
        .group_by(OutboundShipment.counterparty_id, Counterparty.inn, Counterparty.name)
        .order_by(func.sum(OutboundShipment.pickup_cost).desc())
    )
    carrier_rows = (await db.execute(carrier_q)).all()

    by_carrier = [
        {
            "counterparty_id": row.counterparty_id,
            "carrier_inn": row.carrier_inn,
            "carrier_name": row.carrier_name or "Без названия",
            "shipments_count": int(row.shipments_count),
            "total_pallets": int(row.total_pallets),
            "total_cost": row.total_cost or Decimal("0"),
            "avg_cost_per_pallet": (row.avg_cost or Decimal("0")).quantize(Decimal("0.01")),
            "destinations_count": int(row.destinations_count),
        }
        for row in carrier_rows
    ]

    # --- Pallet buckets (объём → ₽/паллета) + scatter-точки + аномалии ---
    # Тянем построчно (только нужные поля) — кап для аномалий/scatter одинаков.
    detail_q = (
        select(
            OutboundShipment.id.label("shipment_id"),
            OutboundShipment.assembly_request_id.label("assembly_request_id"),
            AssemblyRequest.number.label("assembly_number"),
            dest_warehouse,
            Counterparty.name.label("carrier_name"),
            OutboundShipment.pallets_count.label("pallets_count"),
            OutboundShipment.pickup_cost.label("pickup_cost"),
            OutboundShipment.shipped_date.label("shipped_date"),
        )
        .join(*req_join)
        .outerjoin(Counterparty, and_(OutboundShipment.counterparty_id == Counterparty.id, Counterparty.project_id == project_id))
        .where(*base_filters)
        .order_by(OutboundShipment.shipped_date.desc().nullslast(), OutboundShipment.id.desc())
        .limit(_SHIPMENTS_LIMIT)
    )
    detail_rows = (await db.execute(detail_q)).all()

    # CPP по складам для робастной статистики.
    cpp_by_dest: dict[str, list[float]] = {}
    all_cpp: list[float] = []
    for r in detail_rows:
        if r.pickup_cost and r.pallets_count and r.pallets_count > 0:
            cpp = float(r.pickup_cost) / r.pallets_count
            cpp_by_dest.setdefault(r.dest_warehouse or "N/A", []).append(cpp)
            all_cpp.append(cpp)
    # Только склады с достаточной выборкой — иначе медиана/MAD по 1-2 точкам шумят
    # и нормальная отгрузка ложно становится аномалией. Малые склады → глобальная
    # статистика; мало и глобально → (0,0) (over/under не размечаем).
    dest_stats = {d: _robust_stats(v) for d, v in cpp_by_dest.items() if len(v) >= _MIN_STATS_SAMPLE}
    global_stats = _robust_stats(all_cpp) if len(all_cpp) >= _MIN_STATS_SAMPLE else (0.0, 0.0)

    # Корзины размеров отгрузки (глобально + по складам сдачи).
    bucket_acc: dict[str, dict] = {}
    dest_bucket_acc: dict[tuple[str, str], dict] = {}
    cost_points: list[dict] = []
    anomalies: list[dict] = []
    for r in detail_rows:
        pallets = r.pallets_count or 0
        cost = r.pickup_cost or Decimal("0")
        dest = r.dest_warehouse or "N/A"
        cpp = (float(r.pickup_cost) / pallets) if (r.pickup_cost and pallets > 0) else None

        if cpp is not None:
            label, order = _bucket_for_pallets(pallets)
            acc = bucket_acc.setdefault(
                label,
                {"sort_order": order, "n": 0, "pallets": 0, "cost": Decimal("0")},
            )
            acc["n"] += 1
            acc["pallets"] += pallets
            acc["cost"] += cost
            dacc = dest_bucket_acc.setdefault(
                (dest, label),
                {"sort_order": order, "n": 0, "pallets": 0, "cost": Decimal("0")},
            )
            dacc["n"] += 1
            dacc["pallets"] += pallets
            dacc["cost"] += cost
            if len(cost_points) < 1500:
                cost_points.append(
                    {
                        "dest_warehouse": dest,
                        "pallets": pallets,
                        "cost_per_pallet": Decimal(str(round(cpp, 2))),
                        "shipped_date": r.shipped_date,
                    }
                )

        # Аномалии.
        if not r.pickup_cost or cost <= 0:
            anomalies.append(
                {
                    "shipment_id": r.shipment_id,
                    "assembly_request_id": r.assembly_request_id,
                    "assembly_number": r.assembly_number,
                    "dest_warehouse": dest,
                    "carrier_name": r.carrier_name,
                    "pallets_count": pallets or None,
                    "pickup_cost": cost if r.pickup_cost is not None else None,
                    "cost_per_pallet": None,
                    "shipped_date": r.shipped_date,
                    "anomaly_type": "no_cost",
                    "severity": Decimal("0"),
                    "expected_low": None,
                    "expected_high": None,
                    "reason": "Не указана стоимость перевозки",
                }
            )
        elif cpp is not None:
            atype, severity, low, high, reason = _classify_cpp(cpp, dest, dest_stats, global_stats)
            if atype:
                anomalies.append(
                    {
                        "shipment_id": r.shipment_id,
                        "assembly_request_id": r.assembly_request_id,
                        "assembly_number": r.assembly_number,
                        "dest_warehouse": dest,
                        "carrier_name": r.carrier_name,
                        "pallets_count": pallets,
                        "pickup_cost": cost,
                        "cost_per_pallet": Decimal(str(round(cpp, 2))),
                        "shipped_date": r.shipped_date,
                        "anomaly_type": atype,
                        "severity": Decimal(str(severity)),
                        "expected_low": Decimal(str(round(low, 2))) if low is not None else None,
                        "expected_high": Decimal(str(round(high, 2))) if high is not None else None,
                        "reason": reason,
                    }
                )

    pallet_buckets = [
        {
            "bucket": label,
            "sort_order": acc["sort_order"],
            "shipments_count": acc["n"],
            "avg_pallets": (Decimal(str(acc["pallets"])) / Decimal(str(acc["n"]))).quantize(Decimal("0.1")),
            "total_pallets": acc["pallets"],
            "total_cost": acc["cost"],
            "avg_cost_per_pallet": (acc["cost"] / Decimal(str(acc["pallets"]))).quantize(Decimal("0.01")),
        }
        for label, acc in bucket_acc.items()
    ]
    pallet_buckets.sort(key=lambda b: b["sort_order"])

    # Матрица «склад сдачи × размер отгрузки → ₽/паллета» (per-warehouse кривая объёма).
    dest_pallet_cells = [
        {
            "dest_warehouse": dest,
            "bucket": label,
            "sort_order": acc["sort_order"],
            "shipments_count": acc["n"],
            "total_pallets": acc["pallets"],
            "avg_cost_per_pallet": (acc["cost"] / Decimal(str(acc["pallets"]))).quantize(Decimal("0.01")),
        }
        for (dest, label), acc in dest_bucket_acc.items()
    ]
    dest_pallet_cells.sort(key=lambda c: (c["dest_warehouse"], c["sort_order"]))

    # no_cost первыми, затем по severity убыв.; кап 100.
    anomalies.sort(key=lambda a: (a["anomaly_type"] != "no_cost", -float(a["severity"])))
    anomalies = anomalies[:100]

    return {
        "summary": summary,
        "by_destination": by_destination,
        "by_route": by_route,
        "by_carrier": by_carrier,
        "pallet_buckets": pallet_buckets,
        "dest_pallet_cells": dest_pallet_cells,
        "cost_points": cost_points,
        "anomalies": anomalies,
    }


# --- Стоимость логистики ₽/шт и ₽/короб по категории/бренду + динамика --------

# Кап отгрузок для агрегации ₽/шт (период шире — режем + флаг truncated).
_COST_PER_UNIT_SHIPMENTS_LIMIT = 8000
_NO_BRAND = "Без бренда"
_NO_CATEGORY = "Без категории"


def _period_bucket(d: date, group_by: str) -> str:
    """Ключ временного ряда по дате отгрузки: day → YYYY-MM-DD, week → понедельник
    недели (YYYY-MM-DD), month → YYYY-MM."""
    if group_by == "day":
        return d.isoformat()
    if group_by == "week":
        return (d - timedelta(days=d.weekday())).isoformat()
    return f"{d.year:04d}-{d.month:02d}"  # month (дефолт)


@cached(prefix="reports:logistics_cost_per_unit", ttl=300)
async def get_logistics_cost_per_unit(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    brands: list[str] | None = None,
    categories: list[str] | None = None,
    group_by: str = "month",
) -> dict:
    """Стоимость логистики (забора) НА ШТУКУ и НА КОРОБ в разрезе категории и бренда,
    плюс динамика за период.

    `pickup_cost` — стоимость перевозки на ВСЮ отгрузку. Разносим по позициям: ₽/шт
    забора = pickup_cost / Σштук, ₽/короб = pickup_cost / Σкоробов (короб = ⌈кол-во /
    кратность⌉ per (склад отгрузки, ШК); позиции без кратности в короба не идут). Каждая
    позиция несёт долю стоимости пропорционально штукам (unit-alloc) и коробам (box-alloc);
    агрегат по категории/бренду/периоду = Σдоли / Σштук(коробов). `total_cost` — unit-alloc
    (покрывает ВСЕ штуки, включая позиции без кратности).

    Фильтры brands/categories применяются НА УРОВНЕ ПОЗИЦИИ: в срез попадают только позиции
    нужного бренда/категории, но знаменатель ₽/шт забора считается по ПОЛНОМУ составу
    отгрузки (стоимость единицы забора от фильтра не зависит). Категория — эффективная
    (`CategoryOverride ?? Nomenclature.subject`).
    """
    from .draft_category_history import _category_by_nm
    from .weight import resolve_box_qty_by_warehouse

    if group_by not in ("day", "week", "month"):
        group_by = "month"

    base_filters = _logistics_base_filters(
        project_id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=warehouse_ids,
        brands=None,  # бренд фильтруем на уровне позиции (ниже), не сабквери по всей отгрузке
        carrier_id=None,
    )
    ship_q = (
        select(
            OutboundShipment.id,
            OutboundShipment.pickup_cost,
            OutboundShipment.shipped_date,
            OutboundShipment.warehouse_id,
        )
        .join(AssemblyRequest, OutboundShipment.assembly_request_id == AssemblyRequest.id)
        .where(*base_filters, OutboundShipment.pickup_cost.isnot(None))
        .order_by(OutboundShipment.shipped_date.desc().nullslast(), OutboundShipment.id.desc())
        .limit(_COST_PER_UNIT_SHIPMENTS_LIMIT + 1)
    )
    ship_rows = (await db.execute(ship_q)).all()
    truncated = len(ship_rows) > _COST_PER_UNIT_SHIPMENTS_LIMIT
    ship_rows = ship_rows[:_COST_PER_UNIT_SHIPMENTS_LIMIT]

    empty = {
        "summary": {"total_cost": Decimal("0"), "total_units": 0, "total_boxes": 0,
                    "cost_per_unit": None, "cost_per_box": None, "shipments": 0},
        "by_category": [], "by_brand": [], "dynamics": [],
        "brands_available": [], "categories_available": [], "group_by": group_by,
        "truncated": False,
    }
    if not ship_rows:
        return empty

    ship_meta = {r.id: (r.pickup_cost, r.shipped_date, r.warehouse_id) for r in ship_rows}
    ship_ids = list(ship_meta.keys())

    items_q = select(
        OutboundShipmentItem.shipment_id,
        OutboundShipmentItem.nomenclature_id,
        OutboundShipmentItem.barcode,
        OutboundShipmentItem.quantity,
    ).where(
        OutboundShipmentItem.shipment_id.in_(ship_ids),
        OutboundShipmentItem.project_id == project_id,
    )
    item_rows = (await db.execute(items_q)).all()

    # Бренд по nomenclature_id; категория — через article_wb (nm_id) с override.
    nom_ids = {r.nomenclature_id for r in item_rows if r.nomenclature_id is not None}
    brand_by_nom: dict[int, str] = {}
    art_by_nom: dict[int, int] = {}
    if nom_ids:
        noms = await db.execute(
            select(Nomenclature.id, Nomenclature.brand, Nomenclature.article_wb).where(
                Nomenclature.project_id == project_id, Nomenclature.id.in_(nom_ids)
            )
        )
        for nid, brand, art in noms.all():
            brand_by_nom[int(nid)] = (brand or "").strip() or _NO_BRAND
            if art is not None:
                art_by_nom[int(nid)] = int(art)
    cat_by_art = await _category_by_nm(db, project_id, set(art_by_nom.values()))

    def _category_of(nom_id: int | None) -> str:
        art = art_by_nom.get(nom_id) if nom_id is not None else None
        return cat_by_art.get(art) or _NO_CATEGORY if art is not None else _NO_CATEGORY

    # Кратность короба per (склад отгрузки, ШК).
    wh_to_barcodes: dict[int, list[str]] = {}
    for r in item_rows:
        wh_to_barcodes.setdefault(ship_meta[r.shipment_id][2], []).append(r.barcode)
    box_qty = await resolve_box_qty_by_warehouse(db, project_id, wh_to_barcodes)

    items_by_ship: dict[int, list] = {}
    for r in item_rows:
        items_by_ship.setdefault(r.shipment_id, []).append(r)

    brand_set = set(brands) if brands else None
    cat_set = set(categories) if categories else None

    def _new_acc() -> dict:
        return {"units": 0, "boxes": 0, "cost_unit": Decimal("0"), "cost_box": Decimal("0")}

    by_category: dict[str, dict] = {}
    by_brand: dict[str, dict] = {}
    dynamics: dict[str, dict] = {}
    brands_seen: set[str] = set()
    cats_seen: set[str] = set()
    tot = _new_acc()
    shipments_counted = 0

    for sid, (pickup_cost, shipped_date, wh_id) in ship_meta.items():
        raw = items_by_ship.get(sid) or []
        if not raw or pickup_cost is None:
            continue
        # Знаменатели по ПОЛНОМУ составу отгрузки (стоимость единицы забора).
        enriched: list[tuple[int, int, str, str]] = []
        total_units = 0
        total_boxes = 0
        for i in raw:
            q = int(i.quantity or 0)
            if q <= 0:
                continue
            bq = box_qty.get((wh_id, i.barcode))
            b = -(-q // bq) if (bq and bq > 0) else 0
            enriched.append((q, b, brand_by_nom.get(i.nomenclature_id, _NO_BRAND), _category_of(i.nomenclature_id)))
            total_units += q
            total_boxes += b
        if total_units <= 0:
            continue
        cost = Decimal(pickup_cost)
        unit_cost = cost / total_units
        box_cost = (cost / total_boxes) if total_boxes > 0 else None
        pkey = _period_bucket(shipped_date, group_by) if shipped_date else "—"
        shipments_counted += 1
        for q, b, brand, category in enriched:
            brands_seen.add(brand)
            cats_seen.add(category)
            if brand_set is not None and brand not in brand_set:
                continue
            if cat_set is not None and category not in cat_set:
                continue
            u_cost = unit_cost * q
            b_cost = (box_cost * b) if box_cost is not None else Decimal("0")
            for bucket, key in ((by_category, category), (by_brand, brand), (dynamics, pkey)):
                acc = bucket.setdefault(key, _new_acc())
                acc["units"] += q
                acc["boxes"] += b
                acc["cost_unit"] += u_cost
                acc["cost_box"] += b_cost
            tot["units"] += q
            tot["boxes"] += b
            tot["cost_unit"] += u_cost
            tot["cost_box"] += b_cost

    def _row(name: str, acc: dict) -> dict:
        return {
            "name": name,
            "units": acc["units"],
            "boxes": acc["boxes"],
            "total_cost": acc["cost_unit"],
            "cost_per_unit": (acc["cost_unit"] / acc["units"]) if acc["units"] > 0 else None,
            "cost_per_box": (acc["cost_box"] / acc["boxes"]) if acc["boxes"] > 0 else None,
        }

    by_category_rows = sorted((_row(k, v) for k, v in by_category.items()), key=lambda r: r["total_cost"], reverse=True)
    by_brand_rows = sorted((_row(k, v) for k, v in by_brand.items()), key=lambda r: r["total_cost"], reverse=True)
    dynamics_rows = [{**_row(k, v), "period": k} for k, v in sorted(dynamics.items())]
    return {
        "summary": {
            "total_cost": tot["cost_unit"],
            "total_units": tot["units"],
            "total_boxes": tot["boxes"],
            "cost_per_unit": (tot["cost_unit"] / tot["units"]) if tot["units"] > 0 else None,
            "cost_per_box": (tot["cost_box"] / tot["boxes"]) if tot["boxes"] > 0 else None,
            "shipments": shipments_counted,
        },
        "by_category": by_category_rows,
        "by_brand": by_brand_rows,
        "dynamics": dynamics_rows,
        "brands_available": sorted(brands_seen),
        "categories_available": sorted(cats_seen),
        "group_by": group_by,
        "truncated": truncated,
    }


async def get_logistics_shipments(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    brands: list[str] | None = None,
    carrier_id: int | None = None,
    dest_warehouse: str | None = None,
) -> dict:
    """Построчная «История отправок» за период (для сортируемой таблицы).

    Источник — OutboundShipment (попытки), а не AssemblyRequest: переотгрузки
    видны отдельно и совпадают с аналитикой. Возвращаем ВСЕ строки периода (кап
    _SHIPMENTS_LIMIT) — клиент сортирует/пагинирует по полному набору, поэтому
    сортировка работает по всему периоду, а не по странице.
    """
    dest_expr = _DEST_WAREHOUSE.label("dest_warehouse")
    base_filters = _logistics_base_filters(
        project_id,
        date_from=date_from,
        date_to=date_to,
        warehouse_ids=warehouse_ids,
        brands=brands,
        carrier_id=carrier_id,
        dest_warehouse=dest_warehouse,
    )
    req_join = (AssemblyRequest, OutboundShipment.assembly_request_id == AssemblyRequest.id)

    total = (
        await db.execute(
            select(func.count()).select_from(OutboundShipment).join(*req_join).where(*base_filters)
        )
    ).scalar() or 0

    weight_expr = func.coalesce(OutboundShipment.pallet_weight_kg, Decimal("0")) * func.coalesce(
        OutboundShipment.pallets_count, 0
    )
    rows = (
        await db.execute(
            select(
                OutboundShipment.id.label("shipment_id"),
                OutboundShipment.attempt_no.label("attempt_no"),
                OutboundShipment.assembly_request_id.label("assembly_request_id"),
                AssemblyRequest.number.label("assembly_number"),
                AssemblyRequest.status.label("status"),
                Warehouse.name.label("src_warehouse"),
                dest_expr,
                OutboundShipment.counterparty_id.label("counterparty_id"),
                Counterparty.inn.label("carrier_inn"),
                Counterparty.name.label("carrier_name"),
                func.coalesce(OutboundShipment.wb_supply_id, WbFboSupply.wb_supply_id).label("wb_supply_id"),
                WbFboSupply.name.label("wb_supply_name"),
                WbFboSupply.wb_status.label("wb_fbo_status"),
                WbFboSupply.planned_date.label("wb_fbo_planned_date"),
                WbFboSupply.actual_date.label("wb_fbo_actual_date"),
                OutboundShipment.pallets_count.label("pallets_count"),
                OutboundShipment.shipped_as_boxes.label("shipped_as_boxes"),
                OutboundShipment.pickup_cost.label("pickup_cost"),
                weight_expr.label("total_weight_kg"),
                OutboundShipment.shipped_date.label("shipped_date"),
                OutboundShipment.created_at.label("shipped_at"),
            )
            .join(*req_join)
            .join(Warehouse, and_(OutboundShipment.warehouse_id == Warehouse.id, Warehouse.project_id == project_id))
            .outerjoin(Counterparty, and_(OutboundShipment.counterparty_id == Counterparty.id, Counterparty.project_id == project_id))
            .outerjoin(WbFboSupply, and_(OutboundShipment.wb_fbo_supply_id == WbFboSupply.id, WbFboSupply.project_id == project_id))
            .where(*base_filters)
            .order_by(OutboundShipment.shipped_date.desc().nullslast(), OutboundShipment.id.desc())
            .limit(_SHIPMENTS_LIMIT)
        )
    ).all()

    # Brands per assembly (batch, без N+1).
    req_ids = [r.assembly_request_id for r in rows if r.assembly_request_id]
    brands_by_req: dict[int, str] = {}
    if req_ids:
        brand_rows = (
            await db.execute(
                select(
                    AssemblyRequestItem.assembly_request_id,
                    Nomenclature.brand,
                )
                .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
                .where(
                    AssemblyRequestItem.assembly_request_id.in_(set(req_ids)),
                    Nomenclature.brand.isnot(None),
                )
                .distinct()
            )
        ).all()
        acc: dict[int, set[str]] = {}
        for rid, brand in brand_rows:
            if brand:
                acc.setdefault(rid, set()).add(brand)
        brands_by_req = {rid: ", ".join(sorted(bs)) for rid, bs in acc.items()}

    # Отгрузки, ушедшие через Газельку (есть SENT/MATCHED-связь у сборки) — бейдж «Газелька».
    gazelka_req_ids: set[int] = set()
    if req_ids:
        gz_rows = (
            await db.execute(
                select(GazelkaOrder.assembly_request_id)
                .where(
                    GazelkaOrder.project_id == project_id,
                    GazelkaOrder.assembly_request_id.in_(set(req_ids)),
                    GazelkaOrder.status.in_([GazelkaOrderStatus.SENT, GazelkaOrderStatus.MATCHED]),
                )
                .distinct()
            )
        ).all()
        gazelka_req_ids = {rid for (rid,) in gz_rows if rid is not None}

    # Робастная статистика ₽/паллета по складам (как в аналитике) — для флага аномалии.
    cpp_by_dest: dict[str, list[float]] = {}
    all_cpp: list[float] = []
    for r in rows:
        if r.pickup_cost and r.pallets_count and r.pallets_count > 0:
            cpp = float(r.pickup_cost) / r.pallets_count
            cpp_by_dest.setdefault(r.dest_warehouse or "N/A", []).append(cpp)
            all_cpp.append(cpp)
    # Только склады с достаточной выборкой — иначе медиана/MAD по 1-2 точкам шумят
    # и нормальная отгрузка ложно становится аномалией. Малые склады → глобальная
    # статистика; мало и глобально → (0,0) (over/under не размечаем).
    dest_stats = {d: _robust_stats(v) for d, v in cpp_by_dest.items() if len(v) >= _MIN_STATS_SAMPLE}
    global_stats = _robust_stats(all_cpp) if len(all_cpp) >= _MIN_STATS_SAMPLE else (0.0, 0.0)

    items = []
    for r in rows:
        pallets = r.pallets_count or 0
        cpp = (float(r.pickup_cost) / pallets) if (r.pickup_cost and pallets > 0) else None
        dest = r.dest_warehouse or "N/A"
        anomaly_type: str | None = None
        if not r.pickup_cost or r.pickup_cost <= 0:
            anomaly_type = "no_cost"
        elif cpp is not None:
            atype, *_ = _classify_cpp(cpp, dest, dest_stats, global_stats)
            anomaly_type = atype
        items.append(
            {
                "shipment_id": r.shipment_id,
                "attempt_no": r.attempt_no,
                "assembly_request_id": r.assembly_request_id,
                "assembly_number": r.assembly_number,
                "status": r.status,
                "brands": brands_by_req.get(r.assembly_request_id) if r.assembly_request_id else None,
                "src_warehouse": r.src_warehouse,
                "dest_warehouse": dest,
                "counterparty_id": r.counterparty_id,
                "carrier_inn": r.carrier_inn,
                "carrier_name": r.carrier_name,
                "wb_supply_id": r.wb_supply_id,
                "wb_supply_name": r.wb_supply_name,
                "wb_fbo_status": r.wb_fbo_status,
                "wb_fbo_planned_date": r.wb_fbo_planned_date,
                "wb_fbo_actual_date": r.wb_fbo_actual_date,
                "pallets_count": r.pallets_count,
                "shipped_as_boxes": r.shipped_as_boxes,
                "pickup_cost": r.pickup_cost,
                "cost_per_pallet": Decimal(str(round(cpp, 2))) if cpp is not None else None,
                "total_weight_kg": (r.total_weight_kg or Decimal("0")).quantize(Decimal("0.01")),
                "shipped_date": r.shipped_date,
                "shipped_at": r.shipped_at,
                "anomaly_type": anomaly_type,
                "via_gazelka": r.assembly_request_id in gazelka_req_ids,
            }
        )

    return {"items": items, "total": int(total), "truncated": total > _SHIPMENTS_LIMIT}


# --- Cost forecast for unassigned requests ----------------------------------


@cached(prefix="reports:logistics_forecast", ttl=300)
async def get_cost_forecast(
    db: AsyncSession,
    project_id: int,
    *,
    lookback_days: int | None = None,
) -> dict:
    """Прогнозная ₽/паллета по истории отгрузок — для ещё не назначенных заявок.

    Модель строится из прошлых отгрузок (OutboundShipment): для каждого склада
    сдачи и размера отгрузки берём медиану ₽/паллета (+ квартильный коридор).
    Фронт по (склад сдачи, кол-во паллет заявки) выбирает самый точный уровень:
    склад+размер → склад → глобально. Медиана устойчива к аномальным ценам.
    """
    dest_warehouse = _DEST_WAREHOUSE.label("dest_warehouse")
    filters = _logistics_base_filters(
        project_id,
        date_from=None,
        date_to=None,
        warehouse_ids=None,
        brands=None,
        carrier_id=None,
    )
    filters.append(OutboundShipment.pickup_cost > 0)
    filters.append(OutboundShipment.pallets_count > 0)
    if lookback_days is not None:
        cutoff = (utcnow() - timedelta(days=lookback_days)).date()
        filters.append(OutboundShipment.shipped_date >= cutoff)

    rows = (
        await db.execute(
            select(
                dest_warehouse,
                OutboundShipment.pallets_count.label("pallets"),
                OutboundShipment.pickup_cost.label("cost"),
            )
            .join(AssemblyRequest, OutboundShipment.assembly_request_id == AssemblyRequest.id)
            .where(*filters)
            .limit(_SHIPMENTS_LIMIT)
        )
    ).all()

    by_dest: dict[str, list[float]] = {}
    by_dest_bucket: dict[tuple[str, str, int], list[float]] = {}
    all_cpp: list[float] = []
    for r in rows:
        if not r.cost or not r.pallets or r.pallets <= 0:
            continue
        cpp = float(r.cost) / r.pallets
        dest = r.dest_warehouse or "N/A"
        label, order = _bucket_for_pallets(r.pallets)
        by_dest.setdefault(dest, []).append(cpp)
        by_dest_bucket.setdefault((dest, label, order), []).append(cpp)
        all_cpp.append(cpp)

    def _pack(values: list[float]) -> dict:
        med, low, high = _median_iqr(values)
        return {
            "cpp": Decimal(str(round(med, 2))),
            "low": Decimal(str(round(low, 2))),
            "high": Decimal(str(round(high, 2))),
            "sample_size": len(values),
        }

    g_med, g_low, g_high = _median_iqr(all_cpp)

    warehouses = []
    for dest, vals in sorted(by_dest.items()):
        buckets = [
            {"bucket": label, "sort_order": order, **_pack(bvals)}
            for (d, label, order), bvals in by_dest_bucket.items()
            if d == dest
        ]
        buckets.sort(key=lambda b: b["sort_order"])
        warehouses.append({"dest_warehouse": dest, **_pack(vals), "buckets": buckets})

    return {
        "global_cpp": Decimal(str(round(g_med, 2))),
        "global_low": Decimal(str(round(g_low, 2))),
        "global_high": Decimal(str(round(g_high, 2))),
        "sample_size": len(all_cpp),
        "warehouses": warehouses,
    }


# --- Flow Analytics ----------------------------------------------------------

# Этапы, длительность которых измеряется (порядок = порядок в ответе).
_FLOW_STAGES: tuple[str, ...] = (
    AssemblyStatus.IN_PROGRESS.value,
    AssemblyStatus.READY.value,
    AssemblyStatus.VEHICLE_ASSIGNED.value,
    AssemblyStatus.SHIPPED.value,
)
# «В работе сейчас» (summary.active_count + кандидаты в аномалии):
# не CANCELLED / CLOSED / DELIVERED. PENDING — legacy, активных заявок в нём нет.
_FLOW_ACTIVE_STATUSES: tuple[AssemblyStatus, ...] = (
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
    AssemblyStatus.SHIPPED,
)
_FLOW_LIMIT = 5000
_ANOMALY_PRIORITY = {
    "wb_accepted_not_shipped": 0,
    "ff_closed_not_shipped": 1,
    "stuck_shipment": 2,
    "stuck_assembly": 3,
    "shipped_not_accepted": 4,
}


def _days_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 86400.0


def _walk_history(
    req: AssemblyRequest,
    recs: list[AssemblyStatusHistory],
) -> tuple[dict[str, float], list[tuple[str | None, str, float | None]]]:
    """Walk one request's status history chronologically.

    Returns:
      - days spent per stage (sum of COMPLETED intervals; the current,
        unfinished stage is not counted — «от входа до следующего перехода»);
      - transition datapoints (old_status, new_status, days in old_status or
        None when not computable — e.g. the creation record old_status=None).

    Draft-created requests have no start record (old_status=None) in history —
    fallback: вход в IN_PROGRESS = created_at заявки.
    """
    stage_days: dict[str, float] = {}
    transitions: list[tuple[str | None, str, float | None]] = []

    cur_status: str | None = None
    cur_since: datetime = req.created_at
    if not any(r.old_status is None for r in recs):
        # No creation record → request was born directly in IN_PROGRESS (draft path).
        cur_status = AssemblyStatus.IN_PROGRESS.value

    for r in recs:
        dur: float | None = None
        if r.old_status is not None and cur_status == r.old_status:
            dur = max(0.0, _days_between(cur_since, r.changed_at))
        transitions.append((r.old_status, r.new_status, dur))
        if dur is not None and cur_status in _FLOW_STAGES and cur_status is not None:
            stage_days[cur_status] = stage_days.get(cur_status, 0.0) + dur
        cur_status = r.new_status
        cur_since = r.changed_at

    return stage_days, transitions


async def get_assembly_wb_warehouses(db: AsyncSession, project_id: int) -> list[str]:
    """Distinct «города сдачи» (целевой склад ВБ) по заявкам проекта.

    Имя из связанной поставки приоритетно, wb_warehouse_name_manual — fallback
    (та же семантика, что в строках аномалий и фильтре wb_warehouses).
    """
    dest = func.coalesce(WbFboSupply.warehouse_name, AssemblyRequest.wb_warehouse_name_manual)
    rows = (
        (
            await db.execute(
                select(dest)
                .select_from(AssemblyRequest)
                .outerjoin(
                    WbFboSupply,
                    and_(
                        WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id,
                        WbFboSupply.project_id == project_id,
                    ),
                )
                .where(
                    AssemblyRequest.project_id == project_id,
                    AssemblyRequest.is_deleted == False,  # noqa: E712
                    dest.isnot(None),
                )
                .distinct()
                .order_by(dest)
                .limit(500)
            )
        )
        .scalars()
        .all()
    )
    return [r for r in rows if r]


# Терминальные статусы: заявка больше не занимает ни одного этапа потока.
# RETURNED (возврат, ждёт переотгрузки/закрытия) тоже не занимает этап.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    (
        AssemblyStatus.DELIVERED.value,
        AssemblyStatus.RETURNED.value,
        AssemblyStatus.CANCELLED.value,
        AssemblyStatus.CLOSED.value,
    )
)


def _occupancy_intervals(
    req: AssemblyRequest,
    recs: list[AssemblyStatusHistory],
) -> list[tuple[str | None, datetime, datetime | None]]:
    """Интервалы пребывания заявки в статусах: (status, start, end|None).

    end=None — открытый интервал по текущий момент; его статус берётся из
    req.status (источник истины), а не из последней записи истории.
    Терминальные заявки открытого интервала не имеют. Fallback для заявок
    без стартовой записи — как в _walk_history (вход в IN_PROGRESS = created_at).
    """
    intervals: list[tuple[str | None, datetime, datetime | None]] = []
    cur_status: str | None = None
    cur_since: datetime = req.created_at
    if not any(r.old_status is None for r in recs):
        cur_status = AssemblyStatus.IN_PROGRESS.value

    for r in recs:
        intervals.append((cur_status, cur_since, r.changed_at))
        cur_status = r.new_status
        cur_since = r.changed_at

    if req.status not in _TERMINAL_STATUSES:
        intervals.append((req.status, cur_since, None))
    return intervals


def _snap_day_range(t1: datetime, t2: datetime | None, today: date) -> tuple[date, date] | None:
    """Дни D, в чьи end-of-day снапшоты (min(now, полночь D+1)) попадает [t1, t2).

    Возвращает (first, last) включительно или None, если интервал короче суток
    и ни один снапшот в него не попал.
    """
    base = t1 - timedelta(days=1)
    first = base.date() if base == datetime.combine(base.date(), time.min) else base.date() + timedelta(days=1)
    if t2 is None:
        last = today
    else:
        base2 = t2 - timedelta(days=1)
        last = base2.date() - timedelta(days=1) if base2 == datetime.combine(base2.date(), time.min) else base2.date()
    if last < first:
        return None
    return first, last


@cached(prefix="reports:assembly_flow", ttl=300)
async def get_assembly_flow_analytics(
    db: AsyncSession,
    project_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    warehouse_ids: list[int] | None = None,
    categories: list[str] | None = None,
    wb_warehouses: list[str] | None = None,
    assembly_threshold_days: int = 3,
    ship_threshold_days: int = 2,
    delivery_threshold_days: int = 7,
) -> dict:
    """Аналитика потока сборки: этапы, переходы, аномалии, дневная динамика.

    Период (date_from/date_to, по created_at; None = без границы — «всё время»)
    применяется к stages / transitions / summary-метрикам. АНОМАЛИИ считаются
    по ТЕКУЩЕМУ состоянию активных заявок (IN_PROGRESS..SHIPPED) — период к ним
    не применяется. Каждая заявка попадает максимум в одну категорию аномалии
    (приоритет: wb_accepted_not_shipped > stuck_shipment > stuck_assembly >
    shipped_not_accepted).

    Критерий «ВБ принял поставку» — `WbFboSupply.wb_status == "ACCEPTED"`:
    единственный надёжный сигнал приёмки. Так же определяет приёмку весь
    остальной код (auto-deliver в fbo_supply/sync.py `_auto_deliver_assembly`,
    дашборд недоприёмки в fbo_supply/service.py); WB-статусы 4/5/6 (включая
    частичную приёмку) маппятся в ACCEPTED (fbo_supply/mappers.py).
    `actual_date` (factDate) не используется: заполняется не для всех поставок
    и может быть проставлен до фактической приёмки.
    """
    now = utcnow()

    # --- Общие scope-фильтры (применяются к периодной, active- и occupancy-выборкам) ---
    scope_filters = [
        AssemblyRequest.project_id == project_id,
        AssemblyRequest.is_deleted == False,  # noqa: E712
        # kind=fbs — учётные зеркала поставок FBS: их статусы двигает джоб по фазе
        # поставки, в метрики потока (длительности этапов) и аномалии (stuck_*,
        # shipped_not_accepted — до сортировки СЦ проходят недели) они не входят.
        AssemblyRequest.kind != AssemblyKind.FBS.value,
    ]
    if warehouse_ids:
        scope_filters.append(AssemblyRequest.warehouse_id.in_(warehouse_ids))
    # Категория (Nomenclature.subject) — заявка попадает, если в ней есть хотя бы
    # одна позиция выбранной категории.
    if categories:
        cat_subq = (
            select(AssemblyRequestItem.assembly_request_id)
            .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
            .where(
                AssemblyRequestItem.project_id == project_id,
                Nomenclature.subject.in_(categories),
            )
            .distinct()
            .scalar_subquery()
        )
        scope_filters.append(AssemblyRequest.id.in_(cat_subq))
    # Город сдачи (целевой склад ВБ): имя из связанной поставки приоритетно,
    # wb_warehouse_name_manual — fallback (та же семантика, что в строках аномалий).
    if wb_warehouses:
        supply_name_sq = (
            select(WbFboSupply.warehouse_name)
            .where(
                WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id,
                WbFboSupply.project_id == project_id,
            )
            .correlate(AssemblyRequest)
            .scalar_subquery()
        )
        scope_filters.append(func.coalesce(supply_name_sq, AssemblyRequest.wb_warehouse_name_manual).in_(wb_warehouses))

    # --- Load period requests (для stages/transitions/summary) ---
    period_filters = [*scope_filters]
    if date_from is not None:
        period_filters.append(AssemblyRequest.created_at >= datetime.combine(date_from, time.min))
    if date_to is not None:
        period_filters.append(AssemblyRequest.created_at < datetime.combine(date_to + timedelta(days=1), time.min))

    period_reqs = list(
        (
            await db.execute(
                select(AssemblyRequest)
                .where(*period_filters)
                .order_by(AssemblyRequest.created_at.desc())
                .limit(_FLOW_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    # --- Load active requests (текущее состояние — для аномалий и active_count) ---
    active_filters = [*scope_filters, AssemblyRequest.status.in_(_FLOW_ACTIVE_STATUSES)]

    active_reqs = list(
        (
            await db.execute(
                select(AssemblyRequest)
                .where(*active_filters)
                .order_by(AssemblyRequest.created_at.desc())
                .limit(_FLOW_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    # --- Occupancy-кандидаты (для stage_daily): жившие в окне, включая созданные
    # ДО date_from. Окно ≤ 365 дней (для «всё время» — последний год).
    occ_to = min(date_to, now.date()) if date_to is not None else now.date()
    occ_from = date_from if date_from is not None else occ_to - timedelta(days=364)
    if (occ_to - occ_from).days > 364:
        occ_from = occ_to - timedelta(days=364)
    occ_window_start = datetime.combine(occ_from, time.min)
    occ_window_end = datetime.combine(occ_to + timedelta(days=1), time.min)

    occ_filters = [
        *scope_filters,
        AssemblyRequest.created_at < occ_window_end,
        # Либо ещё в потоке, либо покинула его не раньше начала окна
        # (updated_at — дешёвый верхний прокси момента терминального перехода).
        or_(
            AssemblyRequest.status.in_(_FLOW_ACTIVE_STATUSES),
            AssemblyRequest.updated_at >= occ_window_start,
        ),
    ]
    occ_reqs = list(
        (
            await db.execute(
                select(AssemblyRequest)
                .where(*occ_filters)
                .order_by(AssemblyRequest.created_at.desc())
                .limit(_FLOW_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    if len(occ_reqs) == _FLOW_LIMIT:
        # Срезались самые старые — именно долгоживущие «созданные до периода»,
        # ради которых выборка и делалась. stage_daily будет занижен.
        logger.warning("assembly_flow occupancy: достигнут _FLOW_LIMIT=%s, stage_daily занижен", _FLOW_LIMIT)

    # --- History for all sets — one query ---
    all_ids = {r.id for r in period_reqs} | {r.id for r in active_reqs} | {r.id for r in occ_reqs}
    history_map: dict[int, list[AssemblyStatusHistory]] = {}
    if all_ids:
        hist_rows = (
            (
                await db.execute(
                    select(AssemblyStatusHistory)
                    .where(
                        AssemblyStatusHistory.project_id == project_id,
                        AssemblyStatusHistory.assembly_request_id.in_(all_ids),
                    )
                    .order_by(AssemblyStatusHistory.changed_at.asc(), AssemblyStatusHistory.id.asc())
                    .limit(_FLOW_LIMIT * 10)
                )
            )
            .scalars()
            .all()
        )
        if len(hist_rows) == _FLOW_LIMIT * 10:
            logger.warning(
                "assembly_flow history: достигнут лимит %s — отрезаны новейшие переходы, интервалы неполные",
                _FLOW_LIMIT * 10,
            )
        for h in hist_rows:
            history_map.setdefault(h.assembly_request_id, []).append(h)

    # --- Stages & transitions (по заявкам периода) ---
    stage_totals: dict[str, list[float]] = {s: [] for s in _FLOW_STAGES}
    trans_acc: dict[tuple[str | None, str], list[float | None]] = {}
    for req in period_reqs:
        stage_days, trans = _walk_history(req, history_map.get(req.id, []))
        for stage_name, days_val in stage_days.items():
            stage_totals[stage_name].append(days_val)
        for old, new, dur in trans:
            trans_acc.setdefault((old, new), []).append(dur)

    stages: list[dict] = [
        {
            "stage": stage_name,
            "avg_days": round(statistics.fmean(vals), 2) if vals else None,
            "median_days": round(statistics.median(vals), 2) if vals else None,
            "count": len(vals),
        }
        for stage_name, vals in stage_totals.items()  # insertion order = _FLOW_STAGES
    ]

    transitions: list[dict] = []
    for (old, new), durs in trans_acc.items():
        known = [d for d in durs if d is not None]
        transitions.append(
            {
                "from_status": old,
                "to_status": new,
                "count": len(durs),
                "avg_days": round(statistics.fmean(known), 2) if known else None,
            }
        )
    transitions.sort(key=lambda t: (-t["count"], t["from_status"] or "", t["to_status"]))

    # --- Summary metrics (период) ---
    cycle_vals = [_days_between(r.created_at, r.shipped_at) for r in period_reqs if r.shipped_at is not None]
    assembly_vals = [
        float((r.actual_ready_date - r.created_at.date()).days) for r in period_reqs if r.actual_ready_date is not None
    ]
    # Только DELIVERED («Принято ВБ»): CLOSED — возврат (ВБ не принял), не успех.
    completed_in_period = sum(1 for r in period_reqs if r.status == AssemblyStatus.DELIVERED)

    # --- Daily series (график «динамика по дням») ---
    # Заявки/товары — по дате создания; средний цикл — по дате отгрузки.
    # SQL-агрегация по тем же period_filters (без _FLOW_LIMIT-усечения).
    created_day = func.date(AssemblyRequest.created_at)
    created_rows = (
        await db.execute(
            select(
                created_day.label("day"),
                func.count(func.distinct(AssemblyRequest.id)).label("created_count"),
                func.coalesce(func.sum(AssemblyRequestItem.quantity), 0).label("created_qty"),
            )
            .outerjoin(
                AssemblyRequestItem,
                # project_id в ON (не WHERE): иначе outer-join выродится во
                # внутренний и заявки без позиций выпадут из created_count.
                and_(
                    AssemblyRequestItem.assembly_request_id == AssemblyRequest.id,
                    AssemblyRequestItem.project_id == project_id,
                ),
            )
            .where(*period_filters)
            .group_by(created_day)
        )
    ).all()

    shipped_day = func.date(AssemblyRequest.shipped_at)
    cycle_rows = (
        await db.execute(
            select(
                shipped_day.label("day"),
                func.count().label("shipped_count"),
                func.avg(
                    func.extract("epoch", AssemblyRequest.shipped_at - AssemblyRequest.created_at) / 86400.0
                ).label("avg_cycle_days"),
            )
            .where(*period_filters, AssemblyRequest.shipped_at.isnot(None))
            .group_by(shipped_day)
        )
    ).all()

    daily_map: dict[str, dict] = {}
    for row in created_rows:
        d = row.day.isoformat()
        daily_map[d] = {
            "date": d,
            "created_count": int(row.created_count),
            "created_qty": int(row.created_qty),
            "shipped_count": 0,
            "avg_cycle_days": None,
        }
    for row in cycle_rows:
        d = row.day.isoformat()
        entry = daily_map.setdefault(
            d,
            {"date": d, "created_count": 0, "created_qty": 0, "shipped_count": 0, "avg_cycle_days": None},
        )
        entry["shipped_count"] = int(row.shipped_count)
        entry["avg_cycle_days"] = round(float(row.avg_cycle_days), 2) if row.avg_cycle_days is not None else None
    daily = sorted(daily_map.values(), key=lambda x: x["date"])

    # --- Stage occupancy (товары по этапам по дням): снимок на конец дня ---
    occ_qty_map: dict[int, int] = {}
    occ_ids = [r.id for r in occ_reqs]
    if occ_ids:
        occ_qty_rows = (
            await db.execute(
                select(
                    AssemblyRequestItem.assembly_request_id,
                    func.coalesce(func.sum(AssemblyRequestItem.quantity), 0).label("qty"),
                )
                .where(
                    AssemblyRequestItem.project_id == project_id,
                    AssemblyRequestItem.assembly_request_id.in_(occ_ids),
                )
                .group_by(AssemblyRequestItem.assembly_request_id)
            )
        ).all()
        occ_qty_map = {row.assembly_request_id: int(row.qty) for row in occ_qty_rows}

    _stage_key = {
        AssemblyStatus.IN_PROGRESS.value: "in_progress_qty",
        AssemblyStatus.READY.value: "ready_qty",
        AssemblyStatus.VEHICLE_ASSIGNED.value: "vehicle_assigned_qty",
        AssemblyStatus.SHIPPED.value: "shipped_qty",
    }
    today = now.date()
    stage_daily: list[dict] = [
        {
            "date": (occ_from + timedelta(days=i)).isoformat(),
            "in_progress_qty": 0,
            "ready_qty": 0,
            "vehicle_assigned_qty": 0,
            "shipped_qty": 0,
        }
        for i in range((occ_to - occ_from).days + 1)
    ]
    for req in occ_reqs:
        qty = occ_qty_map.get(req.id, 0)
        if qty == 0:
            continue
        for status_name, t1, t2 in _occupancy_intervals(req, history_map.get(req.id, [])):
            key = _stage_key.get(status_name) if status_name is not None else None
            if key is None:
                continue
            rng = _snap_day_range(t1, t2, today)
            if rng is None:
                continue
            first = max(rng[0], occ_from)
            last = min(rng[1], occ_to)
            if first > last:
                continue
            for i in range((first - occ_from).days, (last - occ_from).days + 1):
                stage_daily[i][key] += qty

    # --- WB supplies for active requests — one query ---
    supply_ids = {r.wb_fbo_supply_id for r in active_reqs if r.wb_fbo_supply_id is not None}
    supply_map: dict[int, tuple[str, str | None, str]] = {}
    if supply_ids:
        supply_rows = (
            await db.execute(
                select(
                    WbFboSupply.id,
                    WbFboSupply.wb_status,
                    WbFboSupply.warehouse_name,
                    WbFboSupply.wb_supply_id,
                ).where(
                    WbFboSupply.project_id == project_id,
                    WbFboSupply.id.in_(supply_ids),
                )
            )
        ).all()
        supply_map = {row.id: (row.wb_status, row.warehouse_name, row.wb_supply_id) for row in supply_rows}

    # --- ФФ-заявки, закрытые/в архиве у провайдера, для активных сборок ---
    # Сигнал «ФФ отгрузил/закрыл, а наша сборка ещё не отгружена» → аномалия
    # ff_closed_not_shipped (статус НЕ меняем — отгрузка двигает сток вручную).
    # Только пред-SHIPPED статусы: для SHIPPED закрытие ФФ ожидаемо. Локально
    # заархивированные строки зеркала исключаем. {assembly_request_id: ff_number}.
    # Сборка может иметь НЕСКОЛЬКО ФФ-заявок (migfull/«Натали», N:1) → собираем
    # все номера закрытых заявок и склеиваем через запятую.
    ff_closed_nums: dict[int, list[str]] = {}
    pre_ship_ids = [r.id for r in active_reqs if r.status != AssemblyStatus.SHIPPED]
    if pre_ship_ids:
        ff_rows = (
            await db.execute(
                select(FulfillmentRequest.assembly_request_id, FulfillmentRequest.number)
                .where(
                    FulfillmentRequest.project_id == project_id,
                    FulfillmentRequest.assembly_request_id.in_(pre_ship_ids),
                    FulfillmentRequest.kind == FfRequestKind.ASSEMBLY.value,
                    or_(
                        FulfillmentRequest.is_completed.is_(True),
                        FulfillmentRequest.archived.is_(True),
                    ),
                    FulfillmentRequest.local_archived.is_(False),
                )
                .order_by(FulfillmentRequest.assembly_request_id, FulfillmentRequest.id)
            )
        ).all()
        for aid, num in ff_rows:
            if aid is None:
                continue
            nums = ff_closed_nums.setdefault(aid, [])  # ключ есть даже без номера → аномалия срабатывает
            if num:
                nums.append(num)
    ff_closed_map: dict[int, str | None] = {
        aid: (", ".join(nums) if nums else None) for aid, nums in ff_closed_nums.items()
    }

    # --- Anomalies (текущее состояние, максимум одна категория на заявку) ---
    anomalies_raw: list[tuple[AssemblyRequest, str, datetime | None]] = []
    for req in active_reqs:
        recs = history_map.get(req.id, [])
        supply = supply_map.get(req.wb_fbo_supply_id) if req.wb_fbo_supply_id is not None else None
        wb_status = supply[0] if supply else None

        kind: str | None = None
        since: datetime | None = None

        # --- since: начало текущего этапа (по статусу) ---
        if req.status in (AssemblyStatus.READY, AssemblyStatus.VEHICLE_ASSIGNED):
            # Начало этапа отгрузки: точное время перехода в READY из истории →
            # actual_ready_date (Date → полночь) → последний переход → updated_at
            entered_ready = next(
                (r.changed_at for r in reversed(recs) if r.new_status == AssemblyStatus.READY),
                None,
            )
            if entered_ready is not None:
                since = entered_ready
            elif req.actual_ready_date is not None:
                since = datetime.combine(req.actual_ready_date, time.min)
            elif recs:
                since = recs[-1].changed_at
            else:
                since = req.updated_at
        elif req.status == AssemblyStatus.IN_PROGRESS:
            entered = next(
                (r.changed_at for r in reversed(recs) if r.new_status == AssemblyStatus.IN_PROGRESS),
                None,
            )
            since = entered or req.created_at  # draft-созданные — без стартовой записи
        elif req.status == AssemblyStatus.SHIPPED:
            since = req.shipped_at or (recs[-1].changed_at if recs else None) or req.updated_at

        # --- kind: одна категория по приоритету; пороги — в дробных днях ---
        if req.status != AssemblyStatus.SHIPPED and wb_status == "ACCEPTED":
            # ВБ уже принял поставку, а заявка не отгружена — «забыли отгрузить».
            # Любой активный статус до SHIPPED. Без порога: критично сразу.
            kind = "wb_accepted_not_shipped"
        elif req.status != AssemblyStatus.SHIPPED and req.id in ff_closed_map:
            # ФФ закрыл/заархивировал заявку (= собрал и отгрузил), а наша сборка
            # ещё не отгружена. Без порога: сигнал «отгрузите вручную».
            kind = "ff_closed_not_shipped"
        elif req.status in (AssemblyStatus.READY, AssemblyStatus.VEHICLE_ASSIGNED):
            if since is not None and _days_between(since, now) > ship_threshold_days:
                kind = "stuck_shipment"
        elif req.status == AssemblyStatus.IN_PROGRESS:
            if since is not None and _days_between(since, now) > assembly_threshold_days:
                kind = "stuck_assembly"
        elif (
            req.status == AssemblyStatus.SHIPPED
            and since is not None
            and _days_between(since, now) > delivery_threshold_days
        ):
            kind = "shipped_not_accepted"

        if kind is not None:
            anomalies_raw.append((req, kind, since))

    # total_qty для аномалий — одним запросом (без N+1)
    anomaly_ids = [r.id for r, _, _ in anomalies_raw]
    qty_map: dict[int, int] = {}
    if anomaly_ids:
        qty_rows = (
            await db.execute(
                select(
                    AssemblyRequestItem.assembly_request_id,
                    func.coalesce(func.sum(AssemblyRequestItem.quantity), 0).label("qty"),
                )
                .where(
                    AssemblyRequestItem.project_id == project_id,
                    AssemblyRequestItem.assembly_request_id.in_(anomaly_ids),
                )
                .group_by(AssemblyRequestItem.assembly_request_id)
            )
        ).all()
        qty_map = {row.assembly_request_id: int(row.qty) for row in qty_rows}

    # Имена складов — для FK-резолва включаем soft-deleted (display only,
    # как get_created_groups с именами черновиков); project_id обязателен.
    wh_ids_used = {r.warehouse_id for r in active_reqs} | {r.warehouse_id for r in period_reqs}
    wh_name_map: dict[int, str] = {}
    if wh_ids_used:
        wh_rows = (
            await db.execute(
                select(Warehouse.id, Warehouse.name).where(
                    Warehouse.project_id == project_id,
                    Warehouse.id.in_(wh_ids_used),
                )
            )
        ).all()
        wh_name_map = {row.id: row.name for row in wh_rows}

    anomalies: list[dict] = []
    for req, kind, since in anomalies_raw:
        supply = supply_map.get(req.wb_fbo_supply_id) if req.wb_fbo_supply_id is not None else None
        anomalies.append(
            {
                "id": req.id,
                "number": req.number,
                "status": req.status,
                "warehouse_id": req.warehouse_id,
                "warehouse_name": wh_name_map.get(req.warehouse_id),
                "wb_warehouse_name": (supply[1] if supply else None) or req.wb_warehouse_name_manual,
                "kind": kind,
                "days_stuck": max(0, (now - since).days) if since is not None else 0,
                "since": since.isoformat() if since is not None else None,
                "total_qty": qty_map.get(req.id, 0),
                "wb_fbo_status": supply[0] if supply else None,
                "wb_supply_number": supply[2] if supply else None,
                "pallets_count": req.pallets_count,
                "ff_request_number": ff_closed_map.get(req.id),
            }
        )
    anomalies.sort(key=lambda a: (_ANOMALY_PRIORITY[a["kind"]], -a["days_stuck"]))

    # --- By warehouse ---
    active_by_wh: dict[int, int] = {}
    for r in active_reqs:
        active_by_wh[r.warehouse_id] = active_by_wh.get(r.warehouse_id, 0) + 1
    cycle_by_wh: dict[int, list[float]] = {}
    for r in period_reqs:
        if r.shipped_at is not None:
            cycle_by_wh.setdefault(r.warehouse_id, []).append(_days_between(r.created_at, r.shipped_at))
    anomaly_by_wh: dict[int, int] = {}
    for a in anomalies:
        anomaly_by_wh[a["warehouse_id"]] = anomaly_by_wh.get(a["warehouse_id"], 0) + 1

    by_warehouse: list[dict] = [
        {
            "warehouse_id": wid,
            "warehouse_name": wh_name_map.get(wid),
            "active_count": active_by_wh.get(wid, 0),
            "avg_cycle_days": round(statistics.fmean(cycle_by_wh[wid]), 2) if wid in cycle_by_wh else None,
            "anomaly_count": anomaly_by_wh.get(wid, 0),
        }
        for wid in set(active_by_wh) | set(cycle_by_wh) | set(anomaly_by_wh)
    ]
    by_warehouse.sort(key=lambda w: (-w["anomaly_count"], -w["active_count"], w["warehouse_id"]))

    return {
        "summary": {
            "active_count": len(active_reqs),
            "completed_in_period": completed_in_period,
            "avg_cycle_days": round(statistics.fmean(cycle_vals), 2) if cycle_vals else None,
            "avg_assembly_days": round(statistics.fmean(assembly_vals), 2) if assembly_vals else None,
            "anomaly_count": len(anomalies),
        },
        "stages": stages,
        "transitions": transitions,
        "by_warehouse": by_warehouse,
        "anomalies": anomalies,
        "daily": daily,
        "stage_daily": stage_daily,
        "thresholds": {
            "assembly_days": assembly_threshold_days,
            "ship_days": ship_threshold_days,
            "delivery_days": delivery_threshold_days,
        },
    }
