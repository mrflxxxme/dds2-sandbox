"""
Cost service — business logic for cost module.

Handles:
- Nomenclature (get, upload Excel)
- Duty rules (CRUD)
- Cost orders (CRUD with aggregation)
- Cost order items (get, upload Excel with cost/duty/delivery calculation)
- Payment plan generation
- Excel normalization (Дивандек / Ковры formats)

Extracted from routers/cost.py to keep router thin (HTTP only).
"""

import io
import math
from datetime import date, timedelta, datetime, timezone
from decimal import Decimal
from typing import Optional

import pandas as pd
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CostOrder, CostOrderItem, Nomenclature, DutyRule, DutyBasis,
    LeadTime, Order, PlannedPayment, CustomsDT,
)

DEFAULT_VAT_RATE = Decimal("0.22")


# ─── Safe numeric helpers ────────────────────────────────────────────────────

def safe_float(val) -> float:
    """Convert value to float, treating NaN/None/invalid as 0.0."""
    try:
        f = float(val) if val is not None else 0.0
        return 0.0 if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return 0.0


def safe_decimal(val) -> Decimal:
    """Convert value to Decimal, treating NaN/None/invalid as 0."""
    try:
        f = float(val) if val is not None else 0.0
        if math.isnan(f) or math.isinf(f):
            return Decimal(0)
        return Decimal(str(f))
    except Exception:
        return Decimal(0)


# ─── Auto-link CustomsDT ─────────────────────────────────────────────────────

async def auto_link_customs_dt(order_no: str, dt_number: str, db: AsyncSession):
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


# ─── Nomenclature ─────────────────────────────────────────────────────────────

async def get_nomenclature(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(Nomenclature)
        .where(Nomenclature.project_id == project_id)
        .order_by(Nomenclature.subject, Nomenclature.article_seller)
    )
    return result.scalars().all()


async def upload_nomenclature(db: AsyncSession, project_id: int, data: bytes):
    """Upload nomenclature from Excel data, returns (inserted, updated)."""
    df = pd.read_excel(io.BytesIO(data))

    col_map = {
        "Баркод": "barcode", "Бренд": "brand", "Предмет": "subject",
        "Артикул продавца": "article_seller", "Артикул WB": "article_wb",
        "Объем, л": "volume_l",
    }
    df = df.rename(columns=col_map)

    inserted, updated = 0, 0
    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan":
            continue
        result = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode == bc,
            )
        )
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
            nom.updated_at = datetime.now(timezone.utc)
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
                project_id=project_id,
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
    return inserted, updated


# ─── Duty Rules ───────────────────────────────────────────────────────────────

async def get_duty_rules(db: AsyncSession, project_id: int):
    result = await db.execute(
        select(DutyRule)
        .where(DutyRule.project_id == project_id)
        .order_by(DutyRule.subject)
    )
    return result.scalars().all()


async def upsert_duty_rule(db: AsyncSession, project_id: int, payload: dict):
    subject = payload.get("subject", "").strip()
    if not subject:
        return None, "subject required"
    result = await db.execute(
        select(DutyRule).where(DutyRule.project_id == project_id, DutyRule.subject == subject)
    )
    rule = result.scalar_one_or_none()
    if rule:
        rule.basis = payload.get("basis", rule.basis)
        rule.rate = Decimal(str(payload.get("rate", rule.rate)))
        rule.util_collect_rub = Decimal(str(payload.get("util_collect_rub", rule.util_collect_rub)))
        rule.note = payload.get("note", rule.note)
    else:
        rule = DutyRule(
            project_id=project_id,
            subject=subject,
            basis=payload.get("basis", "INVOICE"),
            rate=Decimal(str(payload.get("rate", 0))),
            util_collect_rub=Decimal(str(payload.get("util_collect_rub", 0))),
            note=payload.get("note"),
        )
        db.add(rule)
    await db.commit()
    return True, None


async def delete_duty_rule(db: AsyncSession, project_id: int, rule_id: int):
    result = await db.execute(
        select(DutyRule).where(DutyRule.id == rule_id, DutyRule.project_id == project_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return None
    await db.delete(rule)
    await db.commit()
    return True


# ─── Cost Orders ──────────────────────────────────────────────────────────────

async def get_cost_orders(db: AsyncSession, project_id: int):
    """Get cost orders with aggregated item totals."""
    result = await db.execute(
        select(CostOrder)
        .where(CostOrder.project_id == project_id)
        .order_by(CostOrder.created_at.desc())
    )
    orders = result.scalars().all()
    out = []
    for o in orders:
        items_result = await db.execute(
            select(CostOrderItem).where(CostOrderItem.order_no == o.order_no)
        )
        all_items = items_result.scalars().all()
        items = [i for i in all_items if i.barcode and str(i.barcode).strip() not in ("0", "")]

        total_qty = sum(i.qty for i in items)
        total = sum(safe_float(i.total_rub) * i.qty for i in items)
        total_cost = sum(safe_float(i.cost_rub) * i.qty for i in items)
        total_delivery = sum(safe_float(i.delivery_rub) * i.qty for i in items)
        total_duty = sum(safe_float(i.duty_rub) * i.qty for i in items)
        total_vat = sum(safe_float(i.vat_rub) * i.qty for i in items)
        total_util = sum(safe_float(i.util_rub) * i.qty for i in items)
        unrecognized = sum(1 for i in items if i.unrecognized)

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


async def create_cost_order(db: AsyncSession, project_id: int, payload: dict):
    order_no = payload.get("order_no", "").strip()
    if not order_no:
        return None, "order_no required"

    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    if result.scalar_one_or_none():
        return None, f"Заказ {order_no} уже существует"

    ship_date = None
    if payload.get("ship_date"):
        try:
            ship_date = date.fromisoformat(payload["ship_date"])
        except Exception:
            pass

    dt_number = (payload.get("dt_number") or "").strip() or None
    order = CostOrder(
        project_id=project_id,
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

    if dt_number:
        await auto_link_customs_dt(order_no, dt_number, db)

    return {"ok": True, "order_no": order_no}, None


async def update_cost_order(db: AsyncSession, project_id: int, order_no: str, payload: dict):
    """Update cost order fields, handle order_no rename with FK cascade."""
    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None, "Not found"

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

    new_order_no = None
    if "order_no" in payload and payload["order_no"] and str(payload["order_no"]).strip() != order_no:
        new_order_no = str(payload["order_no"]).strip()
        dup = await db.execute(select(CostOrder).where(CostOrder.order_no == new_order_no))
        if dup.scalar_one_or_none():
            return None, f"Заказ с номером {new_order_no} уже существует"
        from sqlalchemy import text as sql_text
        await db.execute(sql_text("SET CONSTRAINTS cost_order_items_order_no_fkey DEFERRED"))
        order.order_no = new_order_no
        await db.flush()
        await db.execute(sql_text(
            "UPDATE cost_order_items SET order_no = :new WHERE order_no = :old"
        ), {"new": new_order_no, "old": order_no})

    await db.commit()

    final_order_no = new_order_no or order_no
    if order.dt_number:
        await auto_link_customs_dt(final_order_no, order.dt_number, db)

    return {"ok": True, "order_no": final_order_no}, None


async def delete_cost_order(db: AsyncSession, project_id: int, order_no: str):
    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None
    await db.delete(order)
    await db.commit()
    return True


# ─── Cost Order Items ─────────────────────────────────────────────────────────

async def get_cost_order_items(db: AsyncSession, project_id: int, order_no: str):
    """Get items with nomenclature lookup for article_wb."""
    result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no)
        .order_by(CostOrderItem.subject, CostOrderItem.article_seller)
    )
    items = result.scalars().all()

    barcodes = [i.barcode for i in items if i.barcode]
    nom_map = {}
    if barcodes:
        nom_result = await db.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project_id,
                Nomenclature.barcode.in_(barcodes),
            )
        )
        nom_map = {n.barcode: n.article_wb for n in nom_result.scalars().all()}

    return items, nom_map


async def upload_order_items(db: AsyncSession, project_id: int, order_no: str,
                             data: bytes, vat_rate: Optional[Decimal] = None):
    """Upload Excel items, calculate cost/duty/delivery per unit. Returns (inserted, unrecognized)."""
    # Check order exists
    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None, None, f"Заказ {order_no} не найден"

    try:
        df = detect_and_normalize_excel(data)
    except ValueError as e:
        return None, None, str(e)

    # Delete existing items
    await db.execute(delete(CostOrderItem).where(CostOrderItem.order_no == order_no))

    # Load nomenclature and duty rules
    nom_result = await db.execute(
        select(Nomenclature).where(Nomenclature.project_id == project_id)
    )
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(
        select(DutyRule).where(DutyRule.project_id == project_id)
    )
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Calculate totals for delivery split
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
    vat_rate_dec = Decimal(str(vat_rate / 100)) if vat_rate else DEFAULT_VAT_RATE

    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan" or bc == "0":
            continue

        qty = int(row.get("qty", 1) or 1)
        price_cny = safe_decimal(row.get("price_cny", 0))
        weight_kg = safe_decimal(row.get("weight_kg", 0))
        area_m2 = safe_decimal(row.get("area_m2", 0))
        volume_m3 = safe_decimal(row.get("volume_m3", 0))

        nom = nom_map.get(bc)
        subject = nom.subject if nom else None
        article_seller = nom.article_seller if nom else None
        is_unrecognized = nom is None
        if is_unrecognized:
            unrecognized += 1

        cost_rub_unit = price_cny * order.rate_cny

        if total_volume > 0 and float(volume_m3) > 0:
            delivery_rub_unit = Decimal(str(delivery_rub_total * float(volume_m3) / total_volume))
        elif total_qty_all > 0:
            delivery_rub_unit = Decimal(str(delivery_rub_total / total_qty_all))
        else:
            delivery_rub_unit = Decimal(0)

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

        vat_base = cost_rub_unit + duty_rub_unit + delivery_rub_unit / 2
        vat_rub_unit = vat_base * vat_rate_dec

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
    return inserted, unrecognized, None


async def recalculate_order_items(db: AsyncSession, project_id: int, order_no: str,
                                  vat_rate: Optional[Decimal] = None):
    """Recalculate cost/duty/vat/delivery for existing items in an order."""
    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None, f"Заказ {order_no} не найден"

    # Load items
    items_result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no)
    )
    items = items_result.scalars().all()
    if not items:
        return 0, None

    # Load nomenclature and duty rules
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(select(DutyRule).where(DutyRule.project_id == project_id))
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Calculate total volume for delivery split
    total_volume = sum(float(item.volume_m3 or 0) * int(item.qty or 1) for item in items)
    total_qty_all = sum(int(item.qty or 1) for item in items)
    delivery_rub_total = (
        float(order.delivery_cost_cny) * float(order.rate_cny) +
        float(order.delivery_cost_usd) * float(order.rate_usd)
    )

    vat_rate_dec = Decimal(str(vat_rate / 100)) if vat_rate else DEFAULT_VAT_RATE
    updated = 0

    for item in items:
        cost_rub_unit = item.price_cny * order.rate_cny

        vol = float(item.volume_m3 or 0)
        if total_volume > 0 and vol > 0:
            delivery_rub_unit = Decimal(str(delivery_rub_total * vol / total_volume))
        elif total_qty_all > 0:
            delivery_rub_unit = Decimal(str(delivery_rub_total / total_qty_all))
        else:
            delivery_rub_unit = Decimal(0)

        duty_rub_unit = Decimal(0)
        util_rub_unit = Decimal(0)
        subject = item.subject
        if subject and subject in duty_map:
            rule = duty_map[subject]
            util_rub_unit = rule.util_collect_rub
            if rule.basis == DutyBasis.WEIGHT:
                duty_rub_unit = (item.weight_kg or Decimal(0)) * Decimal(str(rule.rate)) * order.rate_eur
            elif rule.basis == DutyBasis.AREA:
                duty_rub_unit = (item.area_m2 or Decimal(0)) * Decimal(str(rule.rate)) * order.rate_eur
            elif rule.basis == DutyBasis.INVOICE:
                base = cost_rub_unit + delivery_rub_unit / 2
                duty_rub_unit = base * Decimal(str(rule.rate)) / 100

        vat_base = cost_rub_unit + duty_rub_unit + delivery_rub_unit / 2
        vat_rub_unit = vat_base * vat_rate_dec

        total_rub_unit = cost_rub_unit + delivery_rub_unit + duty_rub_unit + vat_rub_unit + util_rub_unit
        total_cny_unit = total_rub_unit / order.rate_cny if order.rate_cny > 0 else Decimal(0)

        item.cost_rub = cost_rub_unit
        item.delivery_rub = delivery_rub_unit
        item.duty_rub = duty_rub_unit
        item.vat_rub = vat_rub_unit
        item.util_rub = util_rub_unit
        item.total_rub = total_rub_unit
        item.total_cny = total_cny_unit
        updated += 1

    await db.commit()
    return updated, None




def normalize_divandek(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize дивандек format Excel to standard columns."""
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
        elif "кол-во в коробке" in cl:
            col_map[c] = "qty_per_box"
        elif "размер" in cl:
            col_map[c] = "size"

    df = df.rename(columns=col_map)

    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()

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


def normalize_carpet(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize ковры format (Chinese headers) Excel to standard columns."""
    import datetime as dt

    col_map = {
        "条码": "barcode", "数量": "qty", "单价": "price_cny",
        "净重": "weight_kg_per_unit", "平方数": "area_m2",
        "单箱体积": "volume_box_m3", "内包": "qty_per_box", "尺寸": "size",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()

    def _fix_numeric(series):
        def _fix_val(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return 0.0
            if isinstance(v, (dt.datetime, dt.date)):
                return float(f"{v.day}.{v.month}")
            s = str(v).strip()
            if not s or s == "nan":
                return 0.0
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

    df["volume_m3"] = df["volume_m3"].fillna(0)
    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def normalize_divandek_cn(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize дивандек format with Chinese headers (客户编码 = barcode)."""
    col_map = {
        "客户编号": "barcode",
        "数量": "qty",
        "单价": "price_cny",
        "总净重": "total_net_weight",
        "单箱体积": "volume_box_m3",
        "装箱数": "qty_per_box",
        "尺寸": "size",
    }
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    # Filter rows with valid numeric barcodes
    if "barcode" in df.columns:
        df = df[pd.to_numeric(df["barcode"], errors="coerce").notna()].copy()

    # Barcode cleanup
    df["barcode"] = pd.to_numeric(df.get("barcode", ""), errors="coerce").fillna(0).astype(int).astype(str)
    df["barcode"] = df["barcode"].str.replace(r'\.0$', '', regex=True).str.strip()

    df["qty"] = pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", 0), errors="coerce").fillna(0)

    # Weight per unit = total_net_weight / qty
    total_weight = pd.to_numeric(df.get("total_net_weight", 0), errors="coerce").fillna(0)
    qty_safe = df["qty"].replace(0, 1)
    df["weight_kg"] = total_weight / qty_safe

    # Area from size (e.g. "*240 + 50*7" or "180*200")
    if "size" in df.columns:
        def _area_from_size(s):
            try:
                s = str(s).strip()
                if "*" in s:
                    # Take the first two numeric dimensions
                    parts = [p.strip() for p in s.replace("+", "*").split("*") if p.strip()]
                    nums = []
                    for p in parts:
                        try:
                            nums.append(float(p))
                        except ValueError:
                            pass
                    if len(nums) >= 2:
                        return nums[0] / 100 * nums[1] / 100
            except Exception:
                pass
            return 0.0
        df["area_m2"] = df["size"].apply(_area_from_size)
    else:
        df["area_m2"] = 0

    # Volume per unit = volume_box_m3 / qty_per_box
    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        vol = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0)
        qpb = pd.to_numeric(df["qty_per_box"], errors="coerce").fillna(1).replace(0, 1)
        df["volume_m3"] = vol / qpb
    else:
        df["volume_m3"] = 0

    df["volume_m3"] = df["volume_m3"].fillna(0)
    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def detect_and_normalize_excel(data: bytes) -> pd.DataFrame:
    """Detect Excel format by columns and normalize to standard schema."""
    df = pd.read_excel(io.BytesIO(data))
    cols = [str(c).strip() for c in df.columns]

    if "штрихкод" in cols or "штрихкод" in [c.lower() for c in cols]:
        return normalize_divandek(df)
    elif "条码" in cols:
        return normalize_carpet(df)
    elif "客户编号" in cols:
        return normalize_divandek_cn(df)
    else:
        raise ValueError(f"Неизвестный формат файла. Колонки: {cols}")


# ─── Plan generation ─────────────────────────────────────────────────────────

async def generate_payment_plan(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    tax_rate: Optional[float] = None,
) -> dict:
    """
    Generate planned payments from CostOrder data:
    - ЗАКАЗ: sum(price_cny × qty) → pay_date = ship_date + order_days
    - ДОСТАВКА: delivery_cost → pay_date = ship_date + transport_days
    - ТАМОЖНЯ: sum((duty + vat) × qty) → pay_date = arrival_date
    Also creates/updates Order in planning module.

    Returns dict with ok, order_no, payments_created, and plan details.
    """
    # 1. Get CostOrder
    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    cost_order = result.scalar_one_or_none()
    if not cost_order:
        return {"error": "CostOrder not found", "status": 404}
    if not cost_order.ship_date:
        return {"error": "Укажите дату отправки (ship_date) перед генерацией плана", "status": 400}

    # 2. Get items (exclude barcode=0)
    items_result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no)
    )
    all_items = items_result.scalars().all()
    items = [i for i in all_items if i.barcode and str(i.barcode).strip() not in ("0", "")]

    if not items:
        return {"error": "Нет позиций в заказе для генерации плана", "status": 400}

    # 3. Calculate totals
    order_cny = sum(safe_float(i.price_cny) * i.qty for i in items)
    order_rub = order_cny * float(cost_order.rate_cny)
    delivery_rub = (
        float(cost_order.delivery_cost_cny) * float(cost_order.rate_cny)
        + float(cost_order.delivery_cost_usd) * float(cost_order.rate_usd)
    )
    duty_rub = sum(safe_float(i.duty_rub) * i.qty for i in items)
    vat_rub = sum(safe_float(i.vat_rub) * i.qty for i in items)
    customs_rub = duty_rub + vat_rub

    # 4. Get lead times
    lt_result = await db.execute(select(LeadTime))
    lt_map = {lt.direction: lt.days for lt in lt_result.scalars().all()}

    transport_key = cost_order.transport_type or "AUTO"
    transport_days = lt_map.get(transport_key, 14)
    order_days = lt_map.get("ORDER", 50)

    ship = cost_order.ship_date
    arrival_date = cost_order.actual_arrival_date or (ship + timedelta(days=transport_days))
    pay_date_order = ship + timedelta(days=order_days)
    pay_date_delivery = ship + timedelta(days=transport_days)
    pay_date_customs = arrival_date

    # 5. Create/update Order in planning module
    order_no_int = int(order_no)
    ord_result = await db.execute(
        select(Order).where(Order.order_no == order_no_int, Order.project_id == project_id)
    )
    plan_order = ord_result.scalar_one_or_none()
    if not plan_order:
        plan_order = Order(
            project_id=project_id,
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
        delete(PlannedPayment).where(
            PlannedPayment.order_no == order_no_int,
            PlannedPayment.project_id == project_id,
        )
    )

    # 7. Create planned payments
    payments = []

    if order_cny > 0:
        payments.append(PlannedPayment(
            project_id=project_id, order_no=order_no_int,
            direction="ЗАКАЗ", pay_date=pay_date_order,
            amount=Decimal(str(round(order_cny, 2))), currency="CNY",
            fx_rate=cost_order.rate_cny,
            amount_rub=Decimal(str(round(order_rub, 2))), is_paid=False,
        ))

    if delivery_rub > 0:
        payments.append(PlannedPayment(
            project_id=project_id, order_no=order_no_int,
            direction="ДОСТАВКА", pay_date=pay_date_delivery,
            amount=cost_order.delivery_cost_cny + cost_order.delivery_cost_usd,
            currency="CNY/USD", fx_rate=None,
            amount_rub=Decimal(str(round(delivery_rub, 2))), is_paid=False,
        ))

    if customs_rub > 0:
        payments.append(PlannedPayment(
            project_id=project_id, order_no=order_no_int,
            direction="ТАМОЖНЯ", pay_date=pay_date_customs,
            amount=Decimal(str(round(customs_rub, 2))), currency="RUB",
            fx_rate=Decimal("1"),
            amount_rub=Decimal(str(round(customs_rub, 2))), is_paid=False,
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
