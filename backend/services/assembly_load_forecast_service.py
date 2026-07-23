# ruff: noqa: RUF001, RUF002, RUF003
"""Прогноз загрузки WB-складов с учётом черновика сборки.

По одному черновику считает, как поставка ляжет на WB-склады:
  • текущий остаток (`wb_warehouse_stocks`) + входящая поставка − продажи за
    lead-time → прогноз остатка, дней покрытия и светофор по складу.
    Входящая = `draft.tgt` + уже-в-работе поставки по тем же SKU (товар в пути
    SHIPPED + активные сборки PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED), по
    целевому WB-складу — чтобы прогноз отражал РЕАЛЬНЫЙ будущий остаток округа,
    а не только текущий черновик;
  • примерный ИНДЕКС ЛОКАЛИЗАЦИИ до/после поставки.

Допущения (всё «примерно», см. вопрос пользователя):
  • lead-time = среднее (сборка+отправка) + (доставка до приёмки WB) из истории
    статусов заявок (`AssemblyStatusHistory`), проект-уровень.
  • Скорость продаж WB даёт funnel ТОЛЬКО суммарно по SKU → раскладываем по
    WB-складам пропорционально доле спроса федерального округа (из `wb_orders`,
    та же модель, что у индекса локализации), а внутри округа — по доле
    (остаток+входящая) склада.

Чистых мутаций нет — только чтение. См. DOMAIN_ASSEMBLY / DOMAIN_LOCALIZATION.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import (
    AssemblyRequest,
    AssemblyRequestItem,
    AssemblyStatus,
    AssemblyStatusHistory,
)
from backend.models.cost import Nomenclature
from backend.models.integrations import WbFunnelDaily, WbWarehouseStock
from backend.models.wb_fbo import WbFboSupply
from backend.models.wb_order import WbOrder
from backend.schemas.assembly_draft import (
    AssemblyDraftDistribution,
    ForecastItem,
    ForecastLeadTime,
    ForecastLocalization,
    ForecastLocalizationDistrict,
    ForecastResponse,
    ForecastSkuLocalization,
    ForecastSummary,
    ForecastWarehouse,
)
from backend.services import assembly_draft_service
from backend.services.localization_tariff import get_ktr, status_label
from backend.services.settings_service import get_excluded_warehouses, get_stock_ignored_set
from backend.services.stock_forecast_service import classify_traffic_light, compute_days_left
from backend.services.warehouse_district import (
    DISTRICT_LABELS,
    DISTRICT_ORDER,
    DISTRICT_UNKNOWN,
    canonical_warehouse_name,
    okrug_to_district,
    warehouse_to_district,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

VELOCITY_WINDOW_DAYS = 30  # окно средней скорости продаж (funnel)
GEO_WINDOW_DAYS = 60  # окно долей спроса по округам (wb_orders)
LEAD_TIME_WINDOW_DAYS = 90  # окно средних времён сборки/отправки/доставки
LOCALIZATION_HORIZON_DAYS = 30  # горизонт спроса для оценки локализации
DEFAULT_ASSEMBLY_SHIP_DAYS = 5.0
DEFAULT_DELIVERY_DAYS = 7.0
_WB_FBO_WAREHOUSE_TYPE = "Склад WB"


_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _strip_parens(name: str) -> str:
    """Базовое имя склада без скобочного суффикса — как хранит excluded_warehouses
    (set_excluded_warehouses режет «(…)»). Для матчинга «закрытых» складов."""
    return _PARENS_RE.sub("", name).strip()


def _wh_district(name: str) -> str:
    return warehouse_to_district(canonical_warehouse_name(name))


def _normalize(counts: dict[str, int]) -> dict[str, float]:
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in counts.items()}


async def _compute_lead_time(db: AsyncSession, project_id: int) -> ForecastLeadTime:
    """Среднее (сборка+отправка) и (доставка до приёмки WB) из истории статусов."""
    since = utcnow() - timedelta(days=LEAD_TIME_WINDOW_DAYS)

    # created_at → shipped_at (сборка + отправка)
    res = await db.execute(
        select(
            func.avg(
                func.extract("epoch", AssemblyRequest.shipped_at - AssemblyRequest.created_at) / 86400.0
            )
        ).where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted == False,  # noqa: E712
            AssemblyRequest.shipped_at.is_not(None),
            AssemblyRequest.shipped_at >= since,
        )
    )
    assembly_ship_raw = res.scalar()

    # shipped_at → DELIVERED (доставка до приёмки WB)
    res2 = await db.execute(
        select(
            func.avg(
                func.extract("epoch", AssemblyStatusHistory.changed_at - AssemblyRequest.shipped_at) / 86400.0
            )
        )
        .join(AssemblyRequest, AssemblyRequest.id == AssemblyStatusHistory.assembly_request_id)
        .where(
            AssemblyStatusHistory.project_id == project_id,
            AssemblyStatusHistory.new_status == AssemblyStatus.DELIVERED.value,
            AssemblyRequest.shipped_at.is_not(None),
            AssemblyStatusHistory.changed_at >= since,
            AssemblyStatusHistory.changed_at >= AssemblyRequest.shipped_at,
        )
    )
    delivery_raw = res2.scalar()

    has_history = assembly_ship_raw is not None or delivery_raw is not None
    assembly_ship = max(0.0, float(assembly_ship_raw)) if assembly_ship_raw is not None else DEFAULT_ASSEMBLY_SHIP_DAYS
    delivery = max(0.0, float(delivery_raw)) if delivery_raw is not None else DEFAULT_DELIVERY_DAYS
    return ForecastLeadTime(
        assembly_ship_days=round(assembly_ship, 1),
        delivery_days=round(delivery, 1),
        total_days=round(assembly_ship + delivery, 1),
        has_history=has_history,
    )


async def _sku_velocity(db: AsyncSession, project_id: int, nm_ids: set[int]) -> dict[int, float]:
    """Средняя скорость продаж SKU (шт/день) из funnel за VELOCITY_WINDOW_DAYS."""
    if not nm_ids:
        return {}
    since = utcnow().date() - timedelta(days=VELOCITY_WINDOW_DAYS)
    res = await db.execute(
        select(WbFunnelDaily.nm_id, func.sum(WbFunnelDaily.orders_count))
        .where(
            WbFunnelDaily.project_id == project_id,
            WbFunnelDaily.nm_id.in_(nm_ids),
            WbFunnelDaily.date >= since,
        )
        .group_by(WbFunnelDaily.nm_id)
    )
    return {int(nm): float(total or 0) / VELOCITY_WINDOW_DAYS for nm, total in res.all()}


async def _sku_district_shares(
    db: AsyncSession, project_id: int, nm_ids: set[int]
) -> tuple[dict[int, dict[str, float]], dict[str, float]]:
    """Доли спроса по ФО per-SKU (из wb_orders) + проектный профиль-fallback."""
    if not nm_ids:
        return {}, {}
    since = utcnow() - timedelta(days=GEO_WINDOW_DAYS)
    res = await db.execute(
        select(
            WbOrder.nm_id,
            WbOrder.oblast_okrug_name,
            WbOrder.country_name,
            func.count(),
        )
        .where(
            WbOrder.project_id == project_id,
            WbOrder.nm_id.in_(nm_ids),
            WbOrder.order_date >= since,
            WbOrder.is_cancel == False,  # noqa: E712
            WbOrder.warehouse_type == _WB_FBO_WAREHOUSE_TYPE,
        )
        .group_by(WbOrder.nm_id, WbOrder.oblast_okrug_name, WbOrder.country_name)
    )
    per_nm: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    overall: dict[str, int] = defaultdict(int)
    for nm, okrug, country, cnt in res.all():
        district = okrug_to_district(okrug, country)
        if district == DISTRICT_UNKNOWN:
            continue
        per_nm[int(nm)][district] += int(cnt)
        overall[district] += int(cnt)
    return {nm: _normalize(c) for nm, c in per_nm.items()}, _normalize(overall)


async def _current_stock(
    db: AsyncSession, project_id: int, nm_ids: set[int]
) -> tuple[dict[int, dict[str, int]], dict[int, str]]:
    """Текущий остаток WB по (nm_id, канон. имя склада) + nm→vendor_code."""
    if not nm_ids:
        return {}, {}
    res = await db.execute(
        select(
            WbWarehouseStock.nm_id,
            WbWarehouseStock.warehouse_name,
            WbWarehouseStock.quantity,
            WbWarehouseStock.vendor_code,
        ).where(
            WbWarehouseStock.project_id == project_id,
            WbWarehouseStock.nm_id.in_(nm_ids),
        )
    )
    stock: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    vendor: dict[int, str] = {}
    for nm, wh_name, qty, vc in res.all():
        name = canonical_warehouse_name(wh_name) or wh_name
        stock[int(nm)][name] += int(qty or 0)
        if vc and int(nm) not in vendor:
            vendor[int(nm)] = vc
    return stock, vendor


# Активные сборки, ещё едущие к WB (не отгружены) + отгруженные (в пути).
_PIPELINE_ACTIVE_STATUSES = (
    AssemblyStatus.PENDING,
    AssemblyStatus.IN_PROGRESS,
    AssemblyStatus.READY,
    AssemblyStatus.VEHICLE_ASSIGNED,
)


async def _pipeline_incoming(
    db: AsyncSession,
    project_id: int,
    nm_ids: set[int],
    *,
    exclude_draft_id: int | None = None,
) -> dict[int, dict[str, int]]:
    """Уже-в-работе поставки по (nm_id, целевой WB-склад): транзит (SHIPPED) +
    активные сборки (PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED).

    Целевой склад = COALESCE(WbFboSupply.warehouse_name, AssemblyRequest.wb_warehouse_name_manual)
    — тот же паттерн, что в warehouse_need_service. Имя нормализуется в канон-форму
    (как остаток/входящая), чтобы агрегировалось в те же округа.

    Анти-double-count: сборки, рождённые из текущего открытого черновика
    (`source_draft_id == exclude_draft_id`), исключаются — их tgt уже в incoming.
    Открытый черновик обычно ещё НЕ является AssemblyRequest, так что пересечения
    нет; гард — на случай уже закоммиченного черновика.
    """
    if not nm_ids:
        return {}

    target_label = func.coalesce(
        WbFboSupply.warehouse_name,
        AssemblyRequest.wb_warehouse_name_manual,
    ).label("target_wh")

    stmt = (
        select(
            Nomenclature.article_wb,
            target_label,
            func.sum(AssemblyRequestItem.quantity).label("qty"),
        )
        .join(AssemblyRequest, AssemblyRequestItem.assembly_request_id == AssemblyRequest.id)
        .join(Nomenclature, Nomenclature.id == AssemblyRequestItem.nomenclature_id)
        .outerjoin(WbFboSupply, WbFboSupply.id == AssemblyRequest.wb_fbo_supply_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_([*_PIPELINE_ACTIVE_STATUSES, AssemblyStatus.SHIPPED]),
            Nomenclature.article_wb.in_(nm_ids),
        )
        .group_by(Nomenclature.article_wb, target_label)
    )
    if exclude_draft_id is not None:
        # NULL-safe: НЕ дропать сборки без source_draft_id (FBO/manual-путь).
        stmt = stmt.where(
            (AssemblyRequest.source_draft_id.is_(None))
            | (AssemblyRequest.source_draft_id != exclude_draft_id)
        )

    res = await db.execute(stmt)
    out: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for nm, target_wh, qty in res.all():
        q = int(qty or 0)
        if not target_wh or q <= 0:
            continue
        name = canonical_warehouse_name(target_wh) or target_wh
        out[int(nm)][name] += q
    return out


async def forecast_draft_load(
    db: AsyncSession,
    project_id: int,
    draft_id: int,
    *,
    horizon_days: int = LOCALIZATION_HORIZON_DAYS,
) -> ForecastResponse:
    """Прогноз загрузки WB-складов и локализации для одного черновика."""
    draft = await assembly_draft_service.get_draft(db, project_id, draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="Draft not found")

    distribution = AssemblyDraftDistribution.model_validate(draft.distribution or {})

    # Входящая поставка: агрегируем tgt по (nm_id, канон. склад) поверх баркодов.
    incoming: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    vendor_from_draft: dict[int, str] = {}
    for row in distribution.rows:
        if row.vendor_code and row.nm_id not in vendor_from_draft:
            vendor_from_draft[row.nm_id] = row.vendor_code
        for wh_name, qty in row.tgt.items():
            q = int(qty or 0)
            if q <= 0:
                continue
            name = canonical_warehouse_name(wh_name) or wh_name
            incoming[row.nm_id][name] += q

    nm_ids = set(incoming.keys())
    if not nm_ids:
        # Пустой черновик — отдаём пустой прогноз, не падаем.
        return ForecastResponse(
            draft_id=draft_id,
            lead_time=await _compute_lead_time(db, project_id),
            summary=ForecastSummary(
                sku_count=0, warehouse_count=0, total_incoming=0,
                traffic_light_counts={"red": 0, "orange": 0, "yellow": 0, "green": 0},
            ),
            localization=ForecastLocalization(
                index_current=0.0, index_after=0.0,
                avg_loc_pct_current=0.0, avg_loc_pct_after=0.0,
                status_current="neutral", status_after="neutral",
                horizon_days=horizon_days, by_district=[],
            ),
            sku_localization=[],
            newcomer_nm_ids=[],
            warehouses=[], items=[], generated_at=utcnow(),
        )

    # Уже-в-работе поставки (транзит SHIPPED + чужие активные сборки) по тем же
    # SKU → реальный будущий остаток по округу, а не только текущий черновик.
    pipeline = await _pipeline_incoming(db, project_id, nm_ids, exclude_draft_id=draft_id)
    for nm, by_wh in pipeline.items():
        for wh_name, qty in by_wh.items():
            incoming[nm][wh_name] += qty

    lead_time = await _compute_lead_time(db, project_id)
    velocity = await _sku_velocity(db, project_id, nm_ids)
    per_nm_shares, overall_shares = await _sku_district_shares(db, project_id, nm_ids)
    stock, vendor_from_stock = await _current_stock(db, project_id, nm_ids)

    # «Закрытые» (исключённые) склады не показываем — выкидываем из остатка и входящей
    # (а значит из матрицы, списка и локализации). Excluded хранится без «(…)».
    excluded_raw = await get_excluded_warehouses(db, project_id)
    if excluded_raw:
        excl = {_strip_parens(n) for n in excluded_raw}

        def _is_open(wh: str) -> bool:
            return _strip_parens(wh) not in excl

        incoming = {nm: {wh: q for wh, q in d.items() if _is_open(wh)} for nm, d in incoming.items()}
        stock = {nm: {wh: q for wh, q in d.items() if _is_open(wh)} for nm, d in stock.items()}

    # 🔥 Сгоревшие склады (stock_ignored): их ОСТАТКАМ не верим (фантом), но
    # входящую поставку/спрос не трогаем — флаг независим от excluded.
    stock_ignored = await get_stock_ignored_set(db, project_id)
    if stock_ignored:
        ign = {_strip_parens(n) for n in stock_ignored}
        stock = {
            nm: {wh: q for wh, q in d.items() if _strip_parens(wh) not in ign}
            for nm, d in stock.items()
        }

    lead = lead_time.total_days

    # ─── Прогноз по (склад × SKU) ────────────────────────────────────────────
    items: list[ForecastItem] = []
    wh_agg: dict[str, dict[str, int]] = defaultdict(
        lambda: {"current": 0, "incoming": 0, "sku": 0}
    )
    wh_light: dict[str, dict[str, int]] = defaultdict(
        lambda: {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    )

    for nm in sorted(nm_ids):
        vel_nm = velocity.get(nm, 0.0)
        share = per_nm_shares.get(nm) or overall_shares
        vendor = vendor_from_draft.get(nm) or vendor_from_stock.get(nm, "")

        warehouses = set(stock.get(nm, {})) | set(incoming.get(nm, {}))
        # Группируем склады по округу — для внутри-окружного дележа скорости.
        by_district: dict[str, list[str]] = defaultdict(list)
        for wh in warehouses:
            by_district[_wh_district(wh)].append(wh)

        for district, whs in by_district.items():
            district_vel = vel_nm * share.get(district, 0.0)
            totals = {
                wh: stock.get(nm, {}).get(wh, 0) + incoming.get(nm, {}).get(wh, 0) for wh in whs
            }
            district_total = sum(totals.values())
            for wh in whs:
                cur = stock.get(nm, {}).get(wh, 0)
                inc = incoming.get(nm, {}).get(wh, 0)
                total = cur + inc
                # Внутри округа скорость делим по доле (остаток+входящая) склада.
                vel_w = district_vel * (total / district_total) if district_total > 0 else 0.0
                projected = int(round(total - vel_w * lead))
                days_cover = compute_days_left(total, vel_w)
                light = classify_traffic_light(days_cover)
                items.append(
                    ForecastItem(
                        warehouse_name=wh,
                        district=district,
                        district_label=DISTRICT_LABELS.get(district, district),
                        nm_id=nm,
                        vendor_code=vendor,
                        current_stock=cur,
                        incoming=inc,
                        avg_daily=round(vel_w, 2),
                        projected_on_arrival=projected,
                        days_cover=days_cover,
                        traffic_light=light,
                    )
                )
                wh_agg[wh]["current"] += cur
                wh_agg[wh]["incoming"] += inc
                wh_agg[wh]["sku"] += 1
                wh_light[wh][light] += 1

    # ─── Сводка по складам ───────────────────────────────────────────────────
    warehouses_out: list[ForecastWarehouse] = []
    for wh, agg in wh_agg.items():
        d = _wh_district(wh)
        warehouses_out.append(
            ForecastWarehouse(
                warehouse_name=wh,
                district=d,
                district_label=DISTRICT_LABELS.get(d, d),
                sku_count=agg["sku"],
                current_stock=agg["current"],
                incoming=agg["incoming"],
                traffic_light_counts=dict(wh_light[wh]),
            )
        )
    warehouses_out.sort(key=lambda w: w.incoming, reverse=True)

    # ─── Примерный индекс локализации до/после ───────────────────────────────
    localization, sku_localization = _compute_localization(
        nm_ids, velocity, per_nm_shares, overall_shares, stock, incoming, horizon_days
    )
    newcomer_set = await assembly_draft_service.fetch_newcomer_nm_ids(db, project_id, nm_ids)

    total_light: dict[str, int] = {"red": 0, "orange": 0, "yellow": 0, "green": 0}
    for it in items:
        total_light[it.traffic_light] += 1

    return ForecastResponse(
        draft_id=draft_id,
        lead_time=lead_time,
        summary=ForecastSummary(
            sku_count=len(nm_ids),
            warehouse_count=len(wh_agg),
            total_incoming=sum(a["incoming"] for a in wh_agg.values()),
            traffic_light_counts=total_light,
        ),
        localization=localization,
        sku_localization=sku_localization,
        newcomer_nm_ids=sorted(newcomer_set),
        warehouses=warehouses_out,
        items=items,
        generated_at=utcnow(),
    )


def _compute_localization(
    nm_ids: set[int],
    velocity: dict[int, float],
    per_nm_shares: dict[int, dict[str, float]],
    overall_shares: dict[str, float],
    stock: dict[int, dict[str, int]],
    incoming: dict[int, dict[str, int]],
    horizon_days: int,
) -> tuple[ForecastLocalization, list[ForecastSkuLocalization]]:
    """Взвешенный по скорости ИЛ (КТР) + средняя доля локализации до/после.

    Доля локализации SKU = Σ_округ min(спрос_округа, остаток_округа) / Σ спрос.
    «после» учитывает входящую поставку. ИЛ = Σ(vel×КТР)/Σ vel (НИЖЕ = лучше).
    Возвращает агрегат + per-SKU долю локализации (для столбца в матрице).
    """
    total_w = 0.0
    sum_ktr_cur = sum_ktr_after = 0.0
    loc_cur_acc = loc_after_acc = 0.0
    dist_demand: dict[str, float] = defaultdict(float)
    dist_avail_cur: dict[str, float] = defaultdict(float)
    dist_avail_after: dict[str, float] = defaultdict(float)
    per_nm: list[ForecastSkuLocalization] = []

    for nm in nm_ids:
        vel = velocity.get(nm, 0.0)
        share = per_nm_shares.get(nm) or overall_shares
        if vel <= 0 or not share:
            continue
        demand_d = {d: vel * f * horizon_days for d, f in share.items() if f > 0}
        total_demand = sum(demand_d.values())
        if total_demand <= 0:
            continue

        avail_cur: dict[str, float] = defaultdict(float)
        avail_after: dict[str, float] = defaultdict(float)
        for wh in set(stock.get(nm, {})) | set(incoming.get(nm, {})):
            d = _wh_district(wh)
            if d == DISTRICT_UNKNOWN:
                continue
            cur = stock.get(nm, {}).get(wh, 0)
            inc = incoming.get(nm, {}).get(wh, 0)
            avail_cur[d] += cur
            avail_after[d] += cur + inc

        local_cur = sum(min(dem, avail_cur.get(d, 0.0)) for d, dem in demand_d.items())
        local_after = sum(min(dem, avail_after.get(d, 0.0)) for d, dem in demand_d.items())
        loc_pct_cur = local_cur / total_demand * 100
        loc_pct_after = local_after / total_demand * 100
        per_nm.append(
            ForecastSkuLocalization(
                nm_id=nm,
                loc_pct_current=round(loc_pct_cur, 1),
                loc_pct_after=round(loc_pct_after, 1),
            )
        )

        total_w += vel
        sum_ktr_cur += vel * float(get_ktr(Decimal(str(loc_pct_cur))))
        sum_ktr_after += vel * float(get_ktr(Decimal(str(loc_pct_after))))
        loc_cur_acc += vel * loc_pct_cur
        loc_after_acc += vel * loc_pct_after

        for d, dem in demand_d.items():
            dist_demand[d] += dem
            dist_avail_cur[d] += avail_cur.get(d, 0.0)
            dist_avail_after[d] += avail_after.get(d, 0.0)

    index_cur = sum_ktr_cur / total_w if total_w else 0.0
    index_after = sum_ktr_after / total_w if total_w else 0.0
    avg_loc_cur = loc_cur_acc / total_w if total_w else 0.0
    avg_loc_after = loc_after_acc / total_w if total_w else 0.0

    by_district: list[ForecastLocalizationDistrict] = []
    for d in DISTRICT_ORDER:
        dem = dist_demand.get(d, 0.0)
        if dem <= 0:
            continue
        ac = dist_avail_cur.get(d, 0.0)
        aa = dist_avail_after.get(d, 0.0)
        by_district.append(
            ForecastLocalizationDistrict(
                district=d,
                label=DISTRICT_LABELS.get(d, d),
                demand=int(round(dem)),
                avail_current=int(round(ac)),
                avail_after=int(round(aa)),
                local_pct_current=round(min(dem, ac) / dem * 100, 1),
                local_pct_after=round(min(dem, aa) / dem * 100, 1),
            )
        )

    localization = ForecastLocalization(
        index_current=round(index_cur, 2),
        index_after=round(index_after, 2),
        avg_loc_pct_current=round(avg_loc_cur, 1),
        avg_loc_pct_after=round(avg_loc_after, 1),
        status_current=status_label(Decimal(str(avg_loc_cur))),
        status_after=status_label(Decimal(str(avg_loc_after))),
        horizon_days=horizon_days,
        by_district=by_district,
    )
    return localization, per_nm
