# ruff: noqa: RUF001, RUF002, RUF003 — mixed Cyrillic/Latin is intentional (Russian comments + WB receipt prefix ВЗ-)
"""
WB Goods Returns service — отчёт «Возвраты и перемещение товаров».

Flow:
  WB API → WbGoodsReturn (sync каждые 30 мин)
  user выбирает активные srid → POST /create-receipt
  → InboundReceipt(status=EXPECTED, is_defect=true) + InboundReceiptItem(*)
  склад затем подтверждает ACCEPTED через обычный warehouse flow.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Integer, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.integrations.wb_api import WBApiClient
from backend.models.cost import Nomenclature
from backend.models.warehouse import (
    InboundReceipt,
    InboundReceiptItem,
    InboundStatus,
    Warehouse,
)
from backend.models.wb_returns import WbGoodsReturn
from backend.utils.time import utcnow

# MSK timezone для пользовательской даты в номере приёмки.
# Не зависит от TZ контейнера (который обычно UTC).
_MSK = timezone(timedelta(hours=3))

# Advisory lock namespace для _next_receipt_number (pg_advisory_xact_lock).
# 2 int4 args → (namespace, project_id). Namespace фиксированный хеш строки
# "wb_returns.next_receipt_number".
_RECEIPT_NUMBER_LOCK_NS = 0x57425257  # 'WBRW' ≈ WB Returns Write

logger = logging.getLogger("dds.wb_returns")

# WB API camelCase → model snake_case field mapping
_WB_FIELDS = {
    "srid": "srid",
    "shkId": "shk_id",
    "stickerId": "sticker_id",
    "barcode": "barcode",
    "nmId": "nm_id",
    "subjectName": "subject_name",
    "brand": "brand",
    "techSize": "tech_size",
    "orderId": "order_id",
    "status": "status",
    "returnType": "return_type",
    "reason": "reason",
    "isStatusActive": "is_status_active",
    "dstOfficeId": "dst_office_id",
    "dstOfficeAddress": "dst_office_address",
}
_WB_DATE_FIELDS = {
    "orderDt": "order_dt",
}
_WB_DATETIME_FIELDS = {
    "readyToReturnDt": "ready_to_return_dt",
    "expiredDt": "expired_dt",
    "completedDt": "completed_dt",
}


# ─── Helpers ────────────────────────────────────────────────────────────────


def _parse_wb_datetime(value: str | None) -> datetime | None:
    """Parse WB ISO datetime (optionally with Z) → naive UTC datetime."""
    if not value:
        return None
    try:
        s = value.strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(UTC).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _parse_wb_date(value: str | None) -> date | None:
    """Parse WB date (YYYY-MM-DD) or ISO datetime → date."""
    if not value:
        return None
    try:
        s = value.strip()
        if not s:
            return None
        # If datetime, extract date
        if "T" in s:
            dt = _parse_wb_datetime(s)
            return dt.date() if dt else None
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def _wb_to_model_fields(item: dict) -> dict:
    """Convert WB API dict (camelCase) to model kwargs (snake_case)."""
    out: dict[str, Any] = {}
    for src, dst in _WB_FIELDS.items():
        if src in item:
            out[dst] = item[src]
    for src, dst in _WB_DATE_FIELDS.items():
        if src in item:
            out[dst] = _parse_wb_date(item.get(src))
    for src, dst in _WB_DATETIME_FIELDS.items():
        if src in item:
            out[dst] = _parse_wb_datetime(item.get(src))
    # WB может отдавать shkId/stickerId/barcode как int — модель ждёт String.
    for str_field in ("shk_id", "sticker_id", "barcode"):
        if str_field in out and out[str_field] is not None and not isinstance(out[str_field], str):
            out[str_field] = str(out[str_field])
    # Coerce isStatusActive to bool with default True
    if "is_status_active" in out and out["is_status_active"] is None:
        out["is_status_active"] = True
    return out


def classify_ui_state(
    ret: WbGoodsReturn,
    receipt_status: str | None,
    now: datetime | None = None,
) -> str:
    """
    Classify the display state of a return row.

    Ordered rules (first match wins):
      - archived: archived_at IS NOT NULL (ручной архив «забрали без оформления»)
      - expired: inactive AND expired_dt passed AND not completed
      - received: receipt ACCEPTED
      - picked_up_pending_receipt: linked EXPECTED receipt AND completed_dt set
      - pickup_planned: linked EXPECTED receipt AND completed_dt NOT set
      - picked_without_receipt: completed_dt set AND NO receipt link
      - ready_for_pickup: active AND ready_to_return_dt set AND not completed
      - in_transit_to_pvz: otherwise (active without ready_to_return_dt)
    """
    now = now or utcnow()
    has_link = ret.inbound_receipt_id is not None
    status = (receipt_status or "").upper()
    # archived: пользователь вручную отправил «забрали без оформления» в архив
    if ret.archived_at is not None:
        return "archived"
    # expired: inactive, expired date passed, никогда не забран с ПВЗ
    if not ret.is_status_active and ret.expired_dt is not None and ret.expired_dt < now and ret.completed_dt is None:
        return "expired"
    # received: приёмка на склад подтверждена
    if status == "ACCEPTED":
        return "received"
    # picked_up_pending_receipt: забрали с ПВЗ, приёмка в EXPECTED
    if has_link and status == "EXPECTED" and ret.completed_dt is not None:
        return "picked_up_pending_receipt"
    # pickup_planned: приёмка создана, ещё не забрали
    if has_link and status == "EXPECTED" and ret.completed_dt is None:
        return "pickup_planned"
    # picked_without_receipt: забрали, но приёмку не создали (ретро-приёмка нужна)
    if ret.completed_dt is not None and not has_link:
        return "picked_without_receipt"
    # ready_for_pickup: активен, готов к выдаче
    if ret.is_status_active and ret.ready_to_return_dt is not None and ret.completed_dt is None:
        return "ready_for_pickup"
    return "in_transit_to_pvz"


def _enrich_return(
    ret: WbGoodsReturn,
    now: datetime,
    article_by_barcode: dict[str, str] | None = None,
) -> dict:
    """Convert WbGoodsReturn + linked receipt to dict for API response.

    Soft-deleted приёмка отсекается — фронт не должен видеть её номер/статус.
    `article_by_barcode` — прекомпюченный маппинг barcode→article_seller
    (см. list_returns), чтобы не делать N+1 в цикле.
    """
    receipt: InboundReceipt | None = getattr(ret, "inbound_receipt", None)
    if receipt is not None and getattr(receipt, "is_deleted", False):
        receipt = None
    receipt_status = receipt.status if receipt is not None else None
    receipt_number = receipt.number if receipt is not None else None
    warehouse_id = receipt.warehouse_id if receipt is not None else None
    warehouse_name: str | None = None
    if receipt is not None and getattr(receipt, "warehouse", None) is not None:
        warehouse_name = receipt.warehouse.name
    ui_state = classify_ui_state(ret, receipt_status, now=now)
    article_seller: str | None = None
    if article_by_barcode is not None and ret.barcode:
        article_seller = article_by_barcode.get(ret.barcode)
    return {
        "id": ret.id,
        "srid": ret.srid,
        "shk_id": ret.shk_id,
        "sticker_id": ret.sticker_id,
        "barcode": ret.barcode,
        "nm_id": ret.nm_id,
        "subject_name": ret.subject_name,
        "brand": ret.brand,
        "tech_size": ret.tech_size,
        "order_id": ret.order_id,
        "order_dt": ret.order_dt,
        "ready_to_return_dt": ret.ready_to_return_dt,
        "expired_dt": ret.expired_dt,
        "completed_dt": ret.completed_dt,
        "status": ret.status,
        "return_type": ret.return_type,
        "reason": ret.reason,
        "is_status_active": ret.is_status_active,
        "dst_office_id": ret.dst_office_id,
        "dst_office_address": ret.dst_office_address,
        "inbound_receipt_id": ret.inbound_receipt_id,
        "inbound_receipt_number": receipt_number,
        "inbound_receipt_status": receipt_status,
        "inbound_warehouse_id": warehouse_id,
        "inbound_warehouse_name": warehouse_name,
        "ui_state": ui_state,
        "archived_at": ret.archived_at,
        "article_seller": article_seller,
        "synced_at": ret.synced_at,
    }


# ─── List / Summary ────────────────────────────────────────────────────────


async def list_returns(
    db: AsyncSession,
    project_id: int,
    *,
    tab: str = "all",
    search: str | None = None,
    pvz_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """
    List WB goods returns with tab-based filtering.

    tab:
      - "pvz"       — active at PVZ, no InboundReceipt (ready to create receipt)
      - "in_transit"— linked EXPECTED receipt (в пути на склад)
      - "history"   — receipt ACCEPTED/CANCELLED OR inactive+expired
      - "all"       — no filter

    Returns (items: list[dict], total: int).
    """
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    base_q = (
        select(WbGoodsReturn)
        .outerjoin(InboundReceipt, WbGoodsReturn.inbound_receipt_id == InboundReceipt.id)
        .where(
            WbGoodsReturn.project_id == project_id,
            WbGoodsReturn.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
        .options(selectinload(WbGoodsReturn.inbound_receipt).selectinload(InboundReceipt.warehouse))
    )

    # InboundReceipt.is_deleted == False — чтобы soft-deleted приёмка не светилась
    # во вкладках (и не считалась "заблокировавшей" srid).
    receipt_live = or_(
        InboundReceipt.id.is_(None),  # нет линка — нет проблем
        InboundReceipt.is_deleted.is_(False),
    )

    if tab == "pvz":
        # На вкладке «На ПВЗ» показываем всё что требует действия:
        # - активные на ПВЗ (ожидают выдачи или готовы к выдаче)
        # - уже забранные с ПВЗ без оформления приёмки (picked_without_receipt —
        #   retro-оформление)
        # Плюс считаем srid свободным если его "линк" ведёт на soft-deleted приёмку.
        # Архивные (archived_at IS NOT NULL) исключаются — они только в «Истории».
        base_q = base_q.where(
            WbGoodsReturn.archived_at.is_(None),
            or_(
                WbGoodsReturn.inbound_receipt_id.is_(None),
                InboundReceipt.is_deleted.is_(True),
            ),
            or_(
                WbGoodsReturn.is_status_active.is_(True),
                WbGoodsReturn.completed_dt.is_not(None),
            ),
        )
    elif tab == "in_transit":
        base_q = base_q.where(
            WbGoodsReturn.archived_at.is_(None),
            WbGoodsReturn.inbound_receipt_id.is_not(None),
            InboundReceipt.is_deleted.is_(False),
            InboundReceipt.status == InboundStatus.EXPECTED,
        )
    elif tab == "history":
        now = utcnow()
        # История: архивные ИЛИ приёмка ACCEPTED/CANCELLED ИЛИ inactive+expired.
        # Для архивных receipt_live не требуется (могут иметь soft-deleted link —
        # пользователь архивировал запись с «мёртвым» линком); для остальных
        # криетриев оставляем фильтр живого линка.
        base_q = base_q.where(
            or_(
                WbGoodsReturn.archived_at.is_not(None),
                (
                    receipt_live
                    & or_(
                        InboundReceipt.status.in_([InboundStatus.ACCEPTED, InboundStatus.CANCELLED]),
                        (WbGoodsReturn.is_status_active.is_(False) & (WbGoodsReturn.expired_dt < now)),
                    )
                ),
            ),
        )

    if search:
        esc = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        term = f"%{esc}%"
        base_q = base_q.where(
            or_(
                WbGoodsReturn.srid.ilike(term, escape="\\"),
                WbGoodsReturn.barcode.ilike(term, escape="\\"),
                WbGoodsReturn.brand.ilike(term, escape="\\"),
                WbGoodsReturn.subject_name.ilike(term, escape="\\"),
            )
        )

    if pvz_id is not None:
        base_q = base_q.where(WbGoodsReturn.dst_office_id == pvz_id)

    if date_from is not None:
        base_q = base_q.where(WbGoodsReturn.order_dt >= date_from)
    if date_to is not None:
        base_q = base_q.where(WbGoodsReturn.order_dt <= date_to)

    # Total (count without limit/offset)
    total_result = await db.execute(select(func.count()).select_from(base_q.order_by(None).subquery()))
    total = total_result.scalar() or 0

    rows_q = (
        base_q.order_by(
            WbGoodsReturn.expired_dt.asc().nulls_last(),
            WbGoodsReturn.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    rows_result = await db.execute(rows_q)
    rows = rows_result.scalars().all()

    # Bulk-load article_seller by barcode (один запрос на весь список — без N+1).
    barcodes = [r.barcode for r in rows if r.barcode]
    article_by_barcode: dict[str, str] = {}
    if barcodes:
        nom_q = select(Nomenclature.barcode, Nomenclature.article_seller).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(barcodes),
            Nomenclature.article_seller.is_not(None),
        )
        for bc, art in (await db.execute(nom_q)).all():
            if bc and art:
                article_by_barcode[bc] = art

    now = utcnow()
    return [_enrich_return(r, now, article_by_barcode) for r in rows], total


async def list_pvz_groups(
    db: AsyncSession,
    project_id: int,
    *,
    search: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """
    Group active-at-PVZ returns by dst_office_id.
    Returns list of {dst_office_id, dst_office_address, total, ready_count,
    in_transit_count, nearest_expired_dt, items}.
    """
    items, _ = await list_returns(db, project_id, tab="pvz", search=search, limit=limit, offset=0)
    groups: dict[int | None, dict] = {}
    for it in items:
        key = it["dst_office_id"]
        g = groups.setdefault(
            key,
            {
                "dst_office_id": key,
                "dst_office_address": it["dst_office_address"],
                "total": 0,
                "ready_count": 0,
                "in_transit_count": 0,
                "picked_without_receipt_count": 0,
                "nearest_expired_dt": None,
                "items": [],
            },
        )
        g["total"] += 1
        if it["ui_state"] == "ready_for_pickup":
            g["ready_count"] += 1
        elif it["ui_state"] == "in_transit_to_pvz":
            g["in_transit_count"] += 1
        elif it["ui_state"] == "picked_without_receipt":
            g["picked_without_receipt_count"] += 1
        exp = it["expired_dt"]
        if exp is not None and (g["nearest_expired_dt"] is None or exp < g["nearest_expired_dt"]):
            g["nearest_expired_dt"] = exp
        g["items"].append(it)
    return sorted(
        groups.values(),
        key=lambda x: (x["nearest_expired_dt"] is None, x["nearest_expired_dt"] or datetime.max),
    )


async def get_summary(db: AsyncSession, project_id: int) -> dict:
    """
    Count returns by ui_state for KPI header.

    Агрегирует classify_ui_state в SQL через CASE WHEN (матчит порядок правил
    в `classify_ui_state` — при изменении одного правила синхронизируй оба).
    Soft-deleted приёмка считается "нет линка" (receipt_status=None).
    """
    now = utcnow()
    soon_threshold = now + timedelta(days=3)

    # receipt_status = статус живой приёмки (soft-deleted → None, т.е. "без линка")
    receipt_status = case(
        (
            InboundReceipt.id.is_not(None) & InboundReceipt.is_deleted.is_(False),
            InboundReceipt.status,
        ),
        else_=None,
    )
    has_live_link = InboundReceipt.id.is_not(None) & InboundReceipt.is_deleted.is_(False)

    ui_state_expr = case(
        # archived: ручной архив (приоритет выше всего)
        (WbGoodsReturn.archived_at.is_not(None), "archived"),
        # expired: inactive, expired_dt < now, not completed
        (
            (WbGoodsReturn.is_status_active.is_(False))
            & (WbGoodsReturn.expired_dt.is_not(None))
            & (WbGoodsReturn.expired_dt < now)
            & (WbGoodsReturn.completed_dt.is_(None)),
            "expired",
        ),
        # received: receipt ACCEPTED
        (receipt_status == InboundStatus.ACCEPTED, "received"),
        # picked_up_pending_receipt: linked EXPECTED AND completed_dt set
        (
            has_live_link & (receipt_status == InboundStatus.EXPECTED) & (WbGoodsReturn.completed_dt.is_not(None)),
            "picked_up_pending_receipt",
        ),
        # pickup_planned: linked EXPECTED AND completed_dt null
        (
            has_live_link & (receipt_status == InboundStatus.EXPECTED) & (WbGoodsReturn.completed_dt.is_(None)),
            "pickup_planned",
        ),
        # picked_without_receipt: completed_dt set AND no live link
        (
            (WbGoodsReturn.completed_dt.is_not(None)) & (~has_live_link),
            "picked_without_receipt",
        ),
        # ready_for_pickup: active, ready_to_return_dt set, not completed
        (
            (WbGoodsReturn.is_status_active.is_(True))
            & (WbGoodsReturn.ready_to_return_dt.is_not(None))
            & (WbGoodsReturn.completed_dt.is_(None)),
            "ready_for_pickup",
        ),
        else_="in_transit_to_pvz",
    ).label("ui_state")

    q = (
        select(
            ui_state_expr,
            func.count().label("cnt"),
            func.sum(
                case(
                    (
                        (WbGoodsReturn.expired_dt.is_not(None)) & (WbGoodsReturn.expired_dt <= soon_threshold),
                        1,
                    ),
                    else_=0,
                )
            ).label("soon_cnt"),
        )
        .select_from(WbGoodsReturn)
        .outerjoin(InboundReceipt, WbGoodsReturn.inbound_receipt_id == InboundReceipt.id)
        .where(
            WbGoodsReturn.project_id == project_id,
            WbGoodsReturn.is_deleted == False,  # noqa: E712
        )
        .group_by(ui_state_expr)
    )
    rows = (await db.execute(q)).all()

    counters = {
        "ready_for_pickup": 0,
        "in_transit_to_pvz": 0,
        "soon_expires": 0,
        "pickup_planned": 0,
        "picked_up_pending_receipt": 0,
        "picked_without_receipt": 0,
        "expired": 0,
        "received": 0,
        "archived": 0,
    }
    for ui_state, cnt, soon_cnt in rows:
        if ui_state in counters:
            counters[ui_state] = int(cnt)
        if ui_state == "ready_for_pickup":
            counters["soon_expires"] = int(soon_cnt or 0)
    return counters


# ─── Create Receipt from returns ───────────────────────────────────────────


async def _get_or_create_nomenclature(
    db: AsyncSession,
    project_id: int,
    *,
    barcode: str | None,
    nm_id: int | None,
    subject_name: str | None,
) -> Nomenclature | None:
    """
    Return existing Nomenclature (project-scoped) by barcode.
    If not found, create a minimal stub record from the return's metadata.
    Returns None only if no barcode is available (skip receipt item).
    """
    if not barcode:
        return None
    result = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode == barcode,
        )
    )
    nom = result.scalar_one_or_none()
    if nom is not None:
        return nom
    # Create stub. Nomenclature.subject is String(100), WB returns up to 200 — truncate.
    nom = Nomenclature(
        project_id=project_id,
        barcode=barcode,
        article_wb=nm_id,
        subject=(subject_name or "")[:100] or None,
        article_seller=None,
    )
    db.add(nom)
    await db.flush()
    return nom


async def _next_receipt_number(db: AsyncSession, project_id: int) -> str:
    """Generate next ВЗ-{yymmdd}-{seq} number for a goods-return receipt.

    Транзакционный advisory lock на (namespace, project_id) исключает race
    condition при параллельных POST /create-receipt: два запроса в одном
    project_id сериализуются, один получает номер первым. Lock снимается при
    commit/rollback транзакции.

    Дата в номере — в MSK, чтобы не зависеть от TZ контейнера (UTC в Docker).
    MAX(suffix) используется вместо COUNT(*), чтобы не терять последовательность
    при soft-delete записей.
    """
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :pid)"),
        {"ns": _RECEIPT_NUMBER_LOCK_NS, "pid": project_id},
    )
    today_msk = datetime.now(tz=_MSK).date()
    prefix = f"ВЗ-{today_msk:%y%m%d}-"
    # MAX суффикса — включает soft-deleted, чтобы не коллидить с существующим номером.
    result = await db.execute(
        select(
            func.max(
                func.cast(
                    func.substring(InboundReceipt.number, len(prefix) + 1),
                    Integer,
                )
            )
        ).where(
            InboundReceipt.project_id == project_id,
            InboundReceipt.number.like(f"{prefix}%"),
        )
    )
    max_suffix = result.scalar() or 0
    return f"{prefix}{max_suffix + 1}"


async def create_receipt_from_returns(
    db: AsyncSession,
    project_id: int,
    *,
    warehouse_id: int,
    srids: list[str],
    comment: str | None = None,
) -> dict:
    """
    Create an EXPECTED defective InboundReceipt that aggregates the selected
    WB returns. Links every passed srid to the new receipt.

    Concurrency:
      - pg_advisory_xact_lock в _next_receipt_number сериализует выдачу номеров
        в рамках project_id.
      - SELECT ... FOR UPDATE на выбранных WbGoodsReturn блокирует параллельный
        POST с пересекающимися srid (второй получит 400 с конкретным списком).

    Returns {receipt_id, receipt_number, warehouse_id, status, items_count,
    linked_srids, skipped_srids}.
    skipped_srids — srid, у которых нет barcode (nomenclature не создать);
    они линкуются в приёмку, но без InboundReceiptItem. UI показывает
    предупреждение.

    Raises HTTPException(400/404) on warehouse / srid validation errors.
    """
    if not srids:
        raise HTTPException(status_code=400, detail="Empty srids list")

    # Warehouse: project-scoped, не soft-deleted, активен (inactive склад не принимает)
    wh_result = await db.execute(
        select(Warehouse).where(
            Warehouse.id == warehouse_id,
            Warehouse.project_id == project_id,
            Warehouse.is_deleted == False,  # noqa: E712
        )
    )
    warehouse = wh_result.scalar_one_or_none()
    if warehouse is None:
        raise HTTPException(status_code=404, detail=f"Warehouse {warehouse_id} not found")
    if not getattr(warehouse, "is_active", True):
        raise HTTPException(
            status_code=400,
            detail=f"Warehouse {warehouse_id} is inactive",
        )

    # Deduplicate srids while preserving order
    unique_srids = list(dict.fromkeys(srids))

    # FOR UPDATE — блокируем выбранные строки до конца транзакции.
    # Параллельный POST с тем же srid повиснет здесь до нашего commit,
    # потом увидит inbound_receipt_id != NULL и получит 400 «already linked».
    q = (
        select(WbGoodsReturn)
        .where(
            WbGoodsReturn.project_id == project_id,
            WbGoodsReturn.is_deleted == False,  # noqa: E712
            WbGoodsReturn.srid.in_(unique_srids),
            WbGoodsReturn.inbound_receipt_id.is_(None),
            # Архивные не должны уходить в приёмку — защита от race-condition
            # archive↔create-receipt на одном srid. Если archive закоммитится раньше,
            # второй вызов здесь не увидит архивную запись как «свободную».
            WbGoodsReturn.archived_at.is_(None),
        )
        .with_for_update()
    )
    result = await db.execute(q)
    available = list(result.scalars().all())
    available_srids = {r.srid for r in available}
    missing = [s for s in unique_srids if s not in available_srids]
    if missing:
        preview = ", ".join(missing[:10])
        raise HTTPException(
            status_code=400,
            detail=f"srids already linked, archived or not found: {preview}",
        )

    # Generate receipt number, create receipt
    number = await _next_receipt_number(db, project_id)
    receipt = InboundReceipt(
        project_id=project_id,
        warehouse_id=warehouse_id,
        number=number,
        status=InboundStatus.EXPECTED,
        is_defect=True,
        defect_reason="Возврат WB с ПВЗ",
        comment=comment,
    )
    db.add(receipt)
    await db.flush()

    # Create receipt items + link returns
    items_count = 0
    linked: list[str] = []
    skipped: list[str] = []
    for ret in available:
        nom = await _get_or_create_nomenclature(
            db,
            project_id,
            barcode=ret.barcode,
            nm_id=ret.nm_id,
            subject_name=ret.subject_name,
        )
        # Always link the return, even if nomenclature resolution failed
        ret.inbound_receipt_id = receipt.id
        linked.append(ret.srid)
        if nom is None:
            logger.warning(
                "wb_returns.create_receipt: skipped item without barcode (srid=%s)",
                ret.srid,
            )
            skipped.append(ret.srid)
            continue
        db.add(
            InboundReceiptItem(
                project_id=project_id,
                receipt_id=receipt.id,
                nomenclature_id=nom.id,
                barcode=ret.barcode or "",
                expected_qty=1,
                actual_qty=0,
            )
        )
        items_count += 1

    await db.commit()
    await db.refresh(receipt)

    return {
        "receipt_id": receipt.id,
        "receipt_number": receipt.number,
        "warehouse_id": receipt.warehouse_id,
        "status": receipt.status,
        "items_count": items_count,
        "linked_srids": linked,
        "skipped_srids": skipped,
    }


# ─── Archive ──────────────────────────────────────────────────────────────


async def archive_returns(
    db: AsyncSession,
    project_id: int,
    *,
    srids: list[str],
) -> dict:
    """
    Пометить возвраты как архивные (archived_at = now).

    Разрешено только для ui_state == picked_without_receipt — т.е. возвраты,
    которые уже забраны с ПВЗ, но приёмку мы создавать не собираемся (чаще
    всего безнадёжные — покупатель не донёс товар до ПВЗ, или это фейковая
    отметка в WB). Других состояний архивировать не даём, чтобы не потерять
    контроль над активным флоу.

    Returns {archived_count, skipped_srids, archived_srids}.
    """
    if not srids:
        raise HTTPException(status_code=400, detail="Empty srids list")
    unique_srids = list(dict.fromkeys(srids))

    q = (
        select(WbGoodsReturn)
        .where(
            WbGoodsReturn.project_id == project_id,
            WbGoodsReturn.is_deleted == False,  # noqa: E712
            WbGoodsReturn.srid.in_(unique_srids),
        )
        .options(selectinload(WbGoodsReturn.inbound_receipt))
    )
    rows = (await db.execute(q)).scalars().all()
    found_srids = {r.srid for r in rows}
    missing = [s for s in unique_srids if s not in found_srids]

    now = utcnow()
    archived: list[str] = []
    skipped: list[str] = list(missing)
    for r in rows:
        if r.archived_at is not None:
            # Уже в архиве — идемпотентно пропускаем.
            continue
        # Разрешаем архивировать только picked_without_receipt:
        # completed_dt set AND (no link OR link → soft-deleted receipt).
        receipt = r.inbound_receipt
        link_alive = receipt is not None and not receipt.is_deleted
        if r.completed_dt is None or link_alive:
            skipped.append(r.srid)
            continue
        r.archived_at = now
        archived.append(r.srid)

    await db.commit()
    return {
        "archived_count": len(archived),
        "archived_srids": archived,
        "skipped_srids": skipped,
    }


# ─── Sync from WB API ─────────────────────────────────────────────────────


async def sync_project_returns(
    db: AsyncSession,
    project_id: int,
    api_key: str,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    api_client: WBApiClient | None = None,
) -> dict:
    """
    Fetch goods-returns report from WB Seller Analytics API and upsert into DB.

    Default window: last 7 days (overlap with 30-min polling for safety).
    Returns {rows_fetched, rows_upserted, started_at, finished_at}.
    """
    started_at = utcnow()
    today = date.today()
    if date_to is None:
        date_to = today
    if date_from is None:
        date_from = today - timedelta(days=7)
    # Clamp window to 31 days max (WB API hard limit)
    if (date_to - date_from).days > 31:
        date_from = date_to - timedelta(days=31)

    client = api_client or WBApiClient(api_key, project_id=project_id)
    items = await client.get_goods_returns(date_from, date_to)
    rows_fetched = len(items)

    # Bulk-fetch existing by srid for this project to avoid N+1
    srids = [str(it.get("srid") or "").strip() for it in items if it.get("srid")]
    existing_map: dict[str, WbGoodsReturn] = {}
    if srids:
        existing_q = select(WbGoodsReturn).where(
            WbGoodsReturn.project_id == project_id,
            WbGoodsReturn.srid.in_(srids),
        )
        for row in (await db.execute(existing_q)).scalars().all():
            existing_map[row.srid] = row

    upserted = 0
    now = utcnow()
    for it in items:
        srid_raw = it.get("srid")
        if not srid_raw:
            continue
        srid = str(srid_raw).strip()
        if not srid:
            continue
        kwargs = _wb_to_model_fields(it)
        # Skip srid from kwargs for update-match safety (it's the key)
        kwargs.pop("srid", None)

        existing = existing_map.get(srid)
        if existing is None:
            ret = WbGoodsReturn(project_id=project_id, srid=srid, synced_at=now, **kwargs)
            db.add(ret)
        else:
            for key, value in kwargs.items():
                setattr(existing, key, value)
            existing.synced_at = now
            # Restore if it was soft-deleted but WB still returns it
            if existing.is_deleted:
                existing.is_deleted = False
                existing.deleted_at = None
        upserted += 1

    await db.commit()
    finished_at = utcnow()
    logger.info(
        "wb_returns.sync",
        extra={
            "project_id": project_id,
            "rows_fetched": rows_fetched,
            "rows_upserted": upserted,
        },
    )
    return {
        "rows_fetched": rows_fetched,
        "rows_upserted": upserted,
        "started_at": started_at,
        "finished_at": finished_at,
    }
