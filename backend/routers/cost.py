"""
Router: /cost — nomenclature, duty rules, cost orders & items, cost calculation
"""

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional
import io

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import (
    Nomenclature, DutyRule, DutyBasis, CostOrder, CostOrderItem,
    LeadTime, Order, PlannedPayment, CustomsDT, Project,
)
from backend.project_context import get_current_project

router = APIRouter(prefix="/cost")


async def _auto_link_customs_dt(order_no: str, dt_number: str, db):
    """Auto-link CustomsDT records matching dt_number to this order."""
    try:
        order_no_int = int(order_no)
    except (ValueError, TypeError):
        return
    result = await db.execute(
        select(CustomsDT).where(CustomsDT.dt_number == dt_number)
    )
    dts = result.scalars().all()
    for d in dts:
        if d.order_no != order_no_int:
            d.order_no = order_no_int
    if dts:
        await db.commit()

# VAT_RATE is now per-project: project.tax_rate / 100
# Fallback default if not set on project
DEFAULT_VAT_RATE = Decimal("0.22")


# ─── Nomenclature ─────────────────────────────────────────────────────────────

@router.get("/nomenclature")
async def get_nomenclature(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Nomenclature).order_by(Nomenclature.subject, Nomenclature.article_seller))
    return [
        {
            "id": n.id, "barcode": n.barcode, "brand": n.brand,
            "subject": n.subject, "article_seller": n.article_seller,
            "article_wb": n.article_wb, "volume_l": float(n.volume_l or 0),
        }
        for n in result.scalars().all()
    ]


@router.post("/nomenclature/upload")
async def upload_nomenclature(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()
    df = pd.read_excel(io.BytesIO(data))

    # Normalize columns
    col_map = {
        "Баркод": "barcode", "Бренд": "brand", "Предмет": "subject",
        "Артикул продавца": "article_seller", "Артикул WB": "article_wb",
        "Объем, л": "volume_l",
    }
    df = df.rename(columns=col_map)

    inserted = 0
    updated = 0
    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan":
            continue
        result = await db.execute(select(Nomenclature).where(Nomenclature.barcode == bc))
        nom = result.scalar_one_or_none()
        if nom:
            nom.brand = str(row.get("brand", "") or "").strip() or None
            nom.subject = str(row.get("subject", "") or "").strip() or None
            nom.article_seller = str(row.get("article_seller", "") or "").strip() or None
            try:
                nom.article_wb = int(row.get("article_wb")) if row.get("article_wb") else None
            except Exception:
                nom.article_wb = None
            try:
                nom.volume_l = Decimal(str(row.get("volume_l", 0) or 0))
            except Exception:
                nom.volume_l = None
            nom.updated_at = datetime.utcnow()
            updated += 1
        else:
            try:
                vol = Decimal(str(row.get("volume_l", 0) or 0))
            except Exception:
                vol = None
            try:
                awb = int(row.get("article_wb")) if row.get("article_wb") else None
            except Exception:
                awb = None
            nom = Nomenclature(
                barcode=bc,
                brand=str(row.get("brand", "") or "").strip() or None,
                subject=str(row.get("subject", "") or "").strip() or None,
                article_seller=str(row.get("article_seller", "") or "").strip() or None,
                article_wb=awb,
                volume_l=vol,
            )
            db.add(nom)
            inserted += 1

    await db.commit()
    return {"inserted": inserted, "updated": updated}


# ─── Duty Rules ───────────────────────────────────────────────────────────────

@router.get("/duty_rules")
async def get_duty_rules(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DutyRule).order_by(DutyRule.subject))
    return [
        {
            "id": r.id, "subject": r.subject, "basis": r.basis,
            "rate": float(r.rate), "util_collect_rub": float(r.util_collect_rub),
            "note": r.note,
        }
        for r in result.scalars().all()
    ]


@router.post("/duty_rules")
async def upsert_duty_rule(payload: dict, db: AsyncSession = Depends(get_db)):
    subject = payload.get("subject", "").strip()
    if not subject:
        raise HTTPException(400, "subject required")
    result = await db.execute(select(DutyRule).where(DutyRule.subject == subject))
    rule = result.scalar_one_or_none()
    if rule:
        rule.basis = payload.get("basis", rule.basis)
        rule.rate = Decimal(str(payload.get("rate", rule.rate)))
        rule.util_collect_rub = Decimal(str(payload.get("util_collect_rub", rule.util_collect_rub)))
        rule.note = payload.get("note", rule.note)
    else:
        rule = DutyRule(
            subject=subject,
            basis=payload.get("basis", "INVOICE"),
            rate=Decimal(str(payload.get("rate", 0))),
            util_collect_rub=Decimal(str(payload.get("util_collect_rub", 0))),
            note=payload.get("note"),
        )
        db.add(rule)
    await db.commit()
    return {"ok": True}


@router.delete("/duty_rules/{rule_id}")
async def delete_duty_rule(rule_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DutyRule).where(DutyRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(404, "Not found")
    await db.delete(rule)
    await db.commit()
    return {"ok": True}


# ─── Cost Orders ──────────────────────────────────────────────────────────────

@router.get("/orders")
async def get_cost_orders(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    import math

    def _sf(val):
        try:
            f = float(val) if val is not None else 0.0
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except Exception:
            return 0.0

    result = await db.execute(
        select(CostOrder)
        .where(CostOrder.project_id == project.id)
        .order_by(CostOrder.created_at.desc())
    )
    orders = result.scalars().all()
    out = []
    for o in orders:
        items_result = await db.execute(select(CostOrderItem).where(CostOrderItem.order_no == o.order_no))
        all_items = items_result.scalars().all()
        # Filter out invalid rows (barcode=0 or empty)
        items = [i for i in all_items if i.barcode and str(i.barcode).strip() not in ("0", "")]

        total_qty = sum(i.qty for i in items)
        total = sum(_sf(i.total_rub) * i.qty for i in items)
        total_cost = sum(_sf(i.cost_rub) * i.qty for i in items)
        total_delivery = sum(_sf(i.delivery_rub) * i.qty for i in items)
        total_duty = sum(_sf(i.duty_rub) * i.qty for i in items)
        total_vat = sum(_sf(i.vat_rub) * i.qty for i in items)
        total_util = sum(_sf(i.util_rub) * i.qty for i in items)
        unrecognized = sum(1 for i in items if i.unrecognized)

        # Check if planned payments exist for this order
        try:
            pp_result = await db.execute(
                select(PlannedPayment).where(PlannedPayment.order_no == int(o.order_no))
            )
            has_plan = len(pp_result.scalars().all()) > 0
        except (ValueError, TypeError):
            has_plan = False

        out.append({
            "id": o.id, "order_no": o.order_no, "invoice_no": o.invoice_no,
            "ship_date": o.ship_date.isoformat() if o.ship_date else None,
            "actual_arrival_date": o.actual_arrival_date.isoformat() if o.actual_arrival_date else None,
            "delivery_cost_cny": float(o.delivery_cost_cny),
            "delivery_cost_usd": float(o.delivery_cost_usd),
            "rate_cny": float(o.rate_cny), "rate_eur": float(o.rate_eur),
            "rate_usd": float(o.rate_usd), "note": o.note,
            "dt_number": o.dt_number,
            "transport_type": o.transport_type or "AUTO",
            "total_qty": total_qty,
            "total_rub": total, "total_cost_rub": total_cost,
            "total_delivery_rub": total_delivery, "total_duty_rub": total_duty,
            "total_vat_rub": total_vat, "total_util_rub": total_util,
            "items_count": len(items),
            "unrecognized_count": unrecognized,
            "has_plan": has_plan,
        })
    return out


@router.post("/orders")
async def create_cost_order(
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    order_no = payload.get("order_no", "").strip()
    if not order_no:
        raise HTTPException(400, "order_no required")
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project.id))
    if result.scalar_one_or_none():
        raise HTTPException(400, f"Заказ {order_no} уже существует")
    ship_date = None
    if payload.get("ship_date"):
        try:
            ship_date = date.fromisoformat(payload["ship_date"])
        except Exception:
            pass
    dt_number = (payload.get("dt_number") or "").strip() or None
    order = CostOrder(
        project_id=project.id,
        order_no=order_no,
        invoice_no=payload.get("invoice_no"),
        ship_date=ship_date,
        transport_type=payload.get("transport_type", "AUTO"),
        delivery_cost_cny=Decimal(str(payload.get("delivery_cost_cny", 0))),
        delivery_cost_usd=Decimal(str(payload.get("delivery_cost_usd", 0))),
        rate_cny=Decimal(str(payload.get("rate_cny", 1))),
        rate_eur=Decimal(str(payload.get("rate_eur", 1))),
        rate_usd=Decimal(str(payload.get("rate_usd", 1))),
        note=payload.get("note"),
        dt_number=dt_number,
    )
    db.add(order)
    await db.commit()

    # Auto-link CustomsDT by dt_number
    if dt_number:
        await _auto_link_customs_dt(order_no, dt_number, db)

    return {"ok": True, "order_no": order_no}


@router.put("/orders/{order_no}")
async def update_cost_order(
    order_no: str,
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Edit an existing cost order."""
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project.id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Not found")

    # Update allowed fields
    if "invoice_no" in payload:
        order.invoice_no = payload["invoice_no"] or None
    if "ship_date" in payload:
        try:
            order.ship_date = date.fromisoformat(payload["ship_date"]) if payload["ship_date"] else None
        except Exception:
            pass
    if "actual_arrival_date" in payload:
        try:
            order.actual_arrival_date = date.fromisoformat(payload["actual_arrival_date"]) if payload["actual_arrival_date"] else None
        except Exception:
            pass
    if "transport_type" in payload:
        order.transport_type = payload["transport_type"]
    if "delivery_cost_cny" in payload:
        order.delivery_cost_cny = Decimal(str(payload["delivery_cost_cny"]))
    if "delivery_cost_usd" in payload:
        order.delivery_cost_usd = Decimal(str(payload["delivery_cost_usd"]))
    if "rate_cny" in payload:
        order.rate_cny = Decimal(str(payload["rate_cny"]))
    if "rate_eur" in payload:
        order.rate_eur = Decimal(str(payload["rate_eur"]))
    if "rate_usd" in payload:
        order.rate_usd = Decimal(str(payload["rate_usd"]))
    if "note" in payload:
        order.note = payload["note"] or None
    if "dt_number" in payload:
        order.dt_number = (payload["dt_number"] or "").strip() or None

    await db.commit()

    # Auto-link CustomsDT by dt_number
    if order.dt_number:
        await _auto_link_customs_dt(order.order_no, order.dt_number, db)

    return {"ok": True, "order_no": order_no}


@router.delete("/orders/{order_no}")
async def delete_cost_order(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project.id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Not found")
    await db.delete(order)
    await db.commit()
    return {"ok": True}


# ─── Generate Payment Plan ───────────────────────────────────────────────────

@router.post("/orders/{order_no}/generate_plan")
async def generate_plan(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Generate planned payments from CostOrder data."""
    from backend.services.cost_service import generate_payment_plan

    result = await generate_payment_plan(db, project.id, order_no, project.tax_rate)
    if "error" in result:
        raise HTTPException(result.get("status", 400), result["error"])
    return result


# ─── Cost Order Items ─────────────────────────────────────────────────────────

@router.get("/orders/{order_no}/items")
async def get_cost_order_items(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    import math

    def _sf(val):
        """Safe float: NaN/None → 0."""
        try:
            f = float(val) if val is not None else 0.0
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except Exception:
            return 0.0

    result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no)
        .order_by(CostOrderItem.subject, CostOrderItem.article_seller)
    )
    items = result.scalars().all()

    # Lookup article_wb from nomenclature by barcode
    barcodes = [i.barcode for i in items if i.barcode]
    nom_map = {}
    if barcodes:
        nom_result = await db.execute(
            select(Nomenclature).where(Nomenclature.barcode.in_(barcodes))
        )
        nom_map = {n.barcode: n.article_wb for n in nom_result.scalars().all()}

    return [
        {
            "id": i.id, "barcode": i.barcode, "subject": i.subject,
            "article_seller": i.article_seller, "qty": i.qty,
            "article_wb": nom_map.get(i.barcode),
            "price_cny": _sf(i.price_cny), "weight_kg": _sf(i.weight_kg),
            "area_m2": _sf(i.area_m2), "volume_m3": _sf(i.volume_m3),
            "cost_rub": _sf(i.cost_rub), "delivery_rub": _sf(i.delivery_rub),
            "duty_rub": _sf(i.duty_rub), "vat_rub": _sf(i.vat_rub),
            "util_rub": _sf(i.util_rub), "total_rub": _sf(i.total_rub),
            "total_cny": _sf(i.total_cny), "unrecognized": i.unrecognized,
        }
        for i in items
    ]


@router.post("/orders/{order_no}/upload")
async def upload_order_items(
    order_no: str,
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload Excel file with order items, calculate cost per item."""
    # Check order exists
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project.id))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, f"Заказ {order_no} не найден")

    data = await file.read()

    # Detect file format and normalize
    from backend.services.cost_service import detect_and_normalize_excel
    try:
        df = detect_and_normalize_excel(data)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Delete existing items for this order
    await db.execute(delete(CostOrderItem).where(CostOrderItem.order_no == order_no))

    # Load nomenclature and duty rules
    nom_result = await db.execute(select(Nomenclature))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(select(DutyRule))
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Calculate totals for delivery split
    # total_volume = sum of (volume_per_unit × qty) = total cubic meters of entire order
    if "volume_m3" in df.columns and "qty" in df.columns:
        vol_series = pd.to_numeric(df["volume_m3"], errors="coerce").fillna(0)
        qty_series = pd.to_numeric(df["qty"], errors="coerce").fillna(1).astype(int)
        total_volume = float((vol_series * qty_series).sum())
    else:
        total_volume = 0.0
    total_qty_all = int(pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).sum()) if "qty" in df.columns else len(df)
    delivery_rub_total = (
        float(order.delivery_cost_cny) * float(order.rate_cny) +
        float(order.delivery_cost_usd) * float(order.rate_usd)
    )

    inserted = 0
    unrecognized = 0

    from backend.services.cost_service import safe_decimal as _safe_dec

    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan" or bc == "0":
            continue

        qty = int(row.get("qty", 1) or 1)
        price_cny = _safe_dec(row.get("price_cny", 0))
        weight_kg = _safe_dec(row.get("weight_kg", 0))
        area_m2 = _safe_dec(row.get("area_m2", 0))
        volume_m3 = _safe_dec(row.get("volume_m3", 0))

        nom = nom_map.get(bc)
        subject = nom.subject if nom else None
        article_seller = nom.article_seller if nom else None
        is_unrecognized = nom is None

        if is_unrecognized:
            unrecognized += 1

        # Cost in RUB per unit
        cost_rub_unit = price_cny * order.rate_cny

        # Delivery per unit (proportional to volume)
        # delivery_per_unit = delivery_total × vol_per_unit / total_volume
        if total_volume > 0 and float(volume_m3) > 0:
            delivery_rub_unit = Decimal(str(delivery_rub_total * float(volume_m3) / total_volume))
        elif total_qty_all > 0:
            # Fallback: equal split by qty
            delivery_rub_unit = Decimal(str(delivery_rub_total / total_qty_all))
        else:
            delivery_rub_unit = Decimal(0)

        # Duty calculation
        duty_rub_unit = Decimal(0)
        util_rub_unit = Decimal(0)
        if subject and subject in duty_map:
            rule = duty_map[subject]
            util_rub_unit = rule.util_collect_rub
            if rule.basis == DutyBasis.WEIGHT:
                duty_rub_unit = weight_kg * Decimal(str(rule.rate)) * order.rate_eur
            elif rule.basis == DutyBasis.AREA:
                duty_rub_unit = area_m2 * Decimal(str(rule.rate)) * order.rate_eur
            elif rule.basis == DutyBasis.INVOICE:
                base = cost_rub_unit + delivery_rub_unit / 2
                duty_rub_unit = base * Decimal(str(rule.rate)) / 100

        # НДС от (себестоимость + пошлина + доставка/2), ставка из проекта
        vat_rate = Decimal(str(project.tax_rate / 100)) if project.tax_rate else DEFAULT_VAT_RATE
        vat_base = cost_rub_unit + duty_rub_unit + delivery_rub_unit / 2
        vat_rub_unit = vat_base * vat_rate

        # Total per unit
        total_rub_unit = cost_rub_unit + delivery_rub_unit + duty_rub_unit + vat_rub_unit + util_rub_unit
        total_cny_unit = total_rub_unit / order.rate_cny if order.rate_cny > 0 else Decimal(0)

        item = CostOrderItem(
            order_no=order_no,
            barcode=bc,
            subject=subject,
            article_seller=article_seller,
            qty=qty,
            price_cny=price_cny,
            weight_kg=weight_kg,
            area_m2=area_m2,
            volume_m3=volume_m3,
            cost_rub=cost_rub_unit,
            delivery_rub=delivery_rub_unit,
            duty_rub=duty_rub_unit,
            vat_rub=vat_rub_unit,
            util_rub=util_rub_unit,
            total_rub=total_rub_unit,
            total_cny=total_cny_unit,
            unrecognized=is_unrecognized,
        )
        db.add(item)
        inserted += 1

    await db.commit()
    return {"inserted": inserted, "unrecognized": unrecognized}


# ─── Excel normalization helpers are now in services/cost_service.py ──────────
# Imported via detect_and_normalize_excel() in upload_order_items above.

