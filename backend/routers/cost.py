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
    LeadTime, Order, PlannedPayment,
)

router = APIRouter(prefix="/cost")

VAT_RATE = Decimal("0.22")


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
async def get_cost_orders(db: AsyncSession = Depends(get_db)):
    import math

    def _sf(val):
        try:
            f = float(val) if val is not None else 0.0
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except Exception:
            return 0.0

    result = await db.execute(select(CostOrder).order_by(CostOrder.created_at.desc()))
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
async def create_cost_order(payload: dict, db: AsyncSession = Depends(get_db)):
    order_no = payload.get("order_no", "").strip()
    if not order_no:
        raise HTTPException(400, "order_no required")
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no))
    if result.scalar_one_or_none():
        raise HTTPException(400, f"Заказ {order_no} уже существует")
    ship_date = None
    if payload.get("ship_date"):
        try:
            ship_date = date.fromisoformat(payload["ship_date"])
        except Exception:
            pass
    order = CostOrder(
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
    )
    db.add(order)
    await db.commit()
    return {"ok": True, "order_no": order_no}


@router.put("/orders/{order_no}")
async def update_cost_order(order_no: str, payload: dict, db: AsyncSession = Depends(get_db)):
    """Edit an existing cost order."""
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no))
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

    await db.commit()
    return {"ok": True, "order_no": order_no}


@router.delete("/orders/{order_no}")
async def delete_cost_order(order_no: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Not found")
    await db.delete(order)
    await db.commit()
    return {"ok": True}


# ─── Generate Payment Plan ───────────────────────────────────────────────────

@router.post("/orders/{order_no}/generate_plan")
async def generate_plan(order_no: str, db: AsyncSession = Depends(get_db)):
    """
    Generate planned payments from CostOrder data:
    - ЗАКАЗ: sum(price_cny × qty) → pay_date = ship_date
    - ДОСТАВКА: delivery_cost → pay_date = ship_date + transport_days
    - ТАМОЖНЯ: sum((duty + vat) × qty) → pay_date = ship_date + transport_days + customs_days
    Also creates/updates Order in planning module.
    """
    from datetime import timedelta
    import math

    def _sf(val):
        try:
            f = float(val) if val is not None else 0.0
            return 0.0 if (math.isnan(f) or math.isinf(f)) else f
        except Exception:
            return 0.0

    # 1. Get CostOrder
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no))
    cost_order = result.scalar_one_or_none()
    if not cost_order:
        raise HTTPException(404, "CostOrder not found")
    if not cost_order.ship_date:
        raise HTTPException(400, "Укажите дату отправки (ship_date) перед генерацией плана")

    # 2. Get items (filter barcode=0)
    items_result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no)
    )
    all_items = items_result.scalars().all()
    items = [i for i in all_items if i.barcode and str(i.barcode).strip() not in ("0", "")]

    if not items:
        raise HTTPException(400, "Нет позиций в заказе для генерации плана")

    # 3. Calculate totals
    order_cny = sum(_sf(i.price_cny) * i.qty for i in items)
    order_rub = order_cny * float(cost_order.rate_cny)
    delivery_rub = (
        float(cost_order.delivery_cost_cny) * float(cost_order.rate_cny)
        + float(cost_order.delivery_cost_usd) * float(cost_order.rate_usd)
    )
    duty_rub = sum(_sf(i.duty_rub) * i.qty for i in items)
    vat_rub = sum(_sf(i.vat_rub) * i.qty for i in items)
    customs_rub = duty_rub + vat_rub

    # 4. Get lead times
    lt_result = await db.execute(select(LeadTime))
    lt_map = {lt.direction: lt.days for lt in lt_result.scalars().all()}

    transport_key = cost_order.transport_type or "AUTO"
    transport_days = lt_map.get(transport_key, 14)
    order_days = lt_map.get("ORDER", 50)

    ship = cost_order.ship_date

    # Arrival date: actual if set, otherwise ship + transport
    arrival_date = cost_order.actual_arrival_date or (ship + timedelta(days=transport_days))

    pay_date_order = ship + timedelta(days=order_days)
    pay_date_delivery = ship + timedelta(days=transport_days)
    pay_date_customs = arrival_date  # Дата прихода = дата оплаты таможни

    # 5. Create/update Order in planning module
    order_no_int = int(order_no)
    ord_result = await db.execute(select(Order).where(Order.order_no == order_no_int))
    plan_order = ord_result.scalar_one_or_none()
    if not plan_order:
        plan_order = Order(
            order_no=order_no_int,
            order_name=f"Заказ {order_no} (инв. {cost_order.invoice_no or '—'})",
            category=None,
            transport_type=transport_key,
            supplier=None,
            planned_ship_date=ship,
            order_amount=Decimal(str(round(order_cny, 2))),
            logistics_cny=cost_order.delivery_cost_cny,
            customs_rub=Decimal(str(round(customs_rub, 2))),
        )
        db.add(plan_order)
    else:
        plan_order.transport_type = transport_key
        plan_order.planned_ship_date = ship
        plan_order.order_amount = Decimal(str(round(order_cny, 2)))
        plan_order.logistics_cny = cost_order.delivery_cost_cny
        plan_order.customs_rub = Decimal(str(round(customs_rub, 2)))

    await db.flush()

    # 6. Delete existing planned payments for this order (regenerate)
    await db.execute(
        delete(PlannedPayment).where(PlannedPayment.order_no == order_no_int)
    )

    # 7. Create planned payments
    payments = []

    # ЗАКАЗ (оплата поставщику в CNY)
    if order_cny > 0:
        payments.append(PlannedPayment(
            order_no=order_no_int,
            direction="ЗАКАЗ",
            pay_date=pay_date_order,
            amount=Decimal(str(round(order_cny, 2))),
            currency="CNY",
            fx_rate=cost_order.rate_cny,
            amount_rub=Decimal(str(round(order_rub, 2))),
            is_paid=False,
        ))

    # ДОСТАВКА (CNY + USD → RUB)
    if delivery_rub > 0:
        payments.append(PlannedPayment(
            order_no=order_no_int,
            direction="ДОСТАВКА",
            pay_date=pay_date_delivery,
            amount=cost_order.delivery_cost_cny + cost_order.delivery_cost_usd,
            currency="CNY/USD",
            fx_rate=None,
            amount_rub=Decimal(str(round(delivery_rub, 2))),
            is_paid=False,
        ))

    # ТАМОЖНЯ (пошлина + НДС в RUB)
    if customs_rub > 0:
        payments.append(PlannedPayment(
            order_no=order_no_int,
            direction="ТАМОЖНЯ",
            pay_date=pay_date_customs,
            amount=Decimal(str(round(customs_rub, 2))),
            currency="RUB",
            fx_rate=Decimal("1"),
            amount_rub=Decimal(str(round(customs_rub, 2))),
            is_paid=False,
        ))

    for p in payments:
        db.add(p)

    await db.commit()

    return {
        "ok": True,
        "order_no": order_no,
        "payments_created": len(payments),
        "plan": {
            "order_cny": round(order_cny, 2),
            "order_rub": round(order_rub, 2),
            "delivery_rub": round(delivery_rub, 2),
            "customs_rub": round(customs_rub, 2),
            "arrival_date": str(arrival_date),
            "pay_date_order": str(pay_date_order),
            "pay_date_delivery": str(pay_date_delivery),
            "pay_date_customs": str(pay_date_customs),
        },
    }


# ─── Cost Order Items ─────────────────────────────────────────────────────────

@router.get("/orders/{order_no}/items")
async def get_cost_order_items(order_no: str, db: AsyncSession = Depends(get_db)):
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
    db: AsyncSession = Depends(get_db),
):
    """Upload Excel file with order items, calculate cost per item."""
    # Check order exists
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, f"Заказ {order_no} не найден")

    data = await file.read()
    df = pd.read_excel(io.BytesIO(data))

    # Detect file type by columns
    cols = [str(c).strip() for c in df.columns]

    # Normalize to standard columns
    if "штрихкод" in cols or "штрихкод" in [c.lower() for c in cols]:
        # Дивандек format
        df = _normalize_divandek(df)
    elif "条码" in cols or "条码" in cols:
        # Ковры format (Chinese headers)
        df = _normalize_carpet(df)
    else:
        raise HTTPException(400, f"Неизвестный формат файла. Колонки: {cols}")

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

    def _safe_dec(val) -> Decimal:
        """Convert value to Decimal, treating NaN/None/invalid as 0."""
        try:
            f = float(val) if val is not None else 0.0
            import math
            if math.isnan(f) or math.isinf(f):
                return Decimal(0)
            return Decimal(str(f))
        except Exception:
            return Decimal(0)

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

        # НДС 22% от (себестоимость + пошлина + доставка/2)
        vat_base = cost_rub_unit + duty_rub_unit + delivery_rub_unit / 2
        vat_rub_unit = vat_base * VAT_RATE

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


def _normalize_divandek(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize дивандек format."""
    col_map = {}
    for c in df.columns:
        cl = str(c).lower().strip()
        if "штрихкод" in cl or "barc" in cl:
            col_map[c] = "barcode"
        elif cl in ["количество", "кол-во", "кол"]:
            col_map[c] = "qty"
        elif "цена" in cl:
            col_map[c] = "price_cny"
        elif "вес 1 шт" in cl or "вес1" in cl:
            col_map[c] = "weight_kg"
        elif "объём" in cl and "одной" in cl:
            col_map[c] = "volume_box_m3"
        elif "кол-во в коробке" in cl or "кол-во в коробке" in cl:
            col_map[c] = "qty_per_box"
        elif "размер" in cl:
            col_map[c] = "size"

    df = df.rename(columns=col_map)

    # Drop totals/summary rows where barcode is not a valid number
    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()

    # Volume per unit in m3
    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    df["volume_m3"] = df["volume_m3"].fillna(0)
    df["weight_kg"] = pd.to_numeric(df.get("weight_kg", 0), errors="coerce").fillna(0)
    df["area_m2"] = 0
    df["barcode"] = pd.to_numeric(df.get("barcode", ""), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", 0), errors="coerce").fillna(0)

    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def _normalize_carpet(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize ковры format (Chinese headers)."""
    import datetime as dt

    col_map = {
        "条码": "barcode",
        "数量": "qty",
        "单价": "price_cny",
        "净重": "weight_kg_per_unit",
        "平方数": "area_m2",
        "单箱体积": "volume_box_m3",
        "内包": "qty_per_box",
        "尺寸": "size",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Drop totals/summary rows: barcode is NaN or qty is non-numeric (e.g. "总数")
    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()

    def _fix_numeric(series):
        """Fix comma decimals and Excel date-mangled values."""
        def _fix_val(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return 0.0
            # Excel sometimes parses "3.2" as a date
            if isinstance(v, (dt.datetime, dt.date)):
                # Recover: "3.2" → Excel date 2026-02-03 (day=3, month=2)
                return float(f"{v.day}.{v.month}")
            s = str(v).strip()
            if not s or s == "nan":
                return 0.0
            # Replace comma decimal: "33,4" → "33.4"
            s = s.replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return 0.0
        return series.apply(_fix_val)

    df["barcode"] = pd.to_numeric(df.get("barcode", ""), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = _fix_numeric(df.get("price_cny", pd.Series([0])))
    df["weight_kg"] = _fix_numeric(df.get("weight_kg_per_unit", pd.Series([0])))

    # Area: prefer calculation from size "160*230" → 1.6 * 2.3 = 3.68
    if "size" in df.columns:
        def _area_from_size(s):
            try:
                s = str(s).strip()
                if "*" in s:
                    parts = s.split("*")
                    return float(parts[0]) / 100 * float(parts[1]) / 100
            except Exception:
                pass
            return 0.0
        df["area_m2"] = df["size"].apply(_area_from_size)
    else:
        df["area_m2"] = _fix_numeric(df.get("area_m2", pd.Series([0])))

    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    # Final NaN cleanup
    df["volume_m3"] = df["volume_m3"].fillna(0)

    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]
