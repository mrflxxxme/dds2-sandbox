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
from backend.models import Nomenclature, DutyRule, DutyBasis, CostOrder, CostOrderItem

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
    result = await db.execute(select(CostOrder).order_by(CostOrder.created_at.desc()))
    orders = result.scalars().all()
    out = []
    for o in orders:
        items_result = await db.execute(select(CostOrderItem).where(CostOrderItem.order_no == o.order_no))
        items = items_result.scalars().all()
        total = sum(float(i.total_rub or 0) * i.qty for i in items)
        unrecognized = sum(1 for i in items if i.unrecognized)
        out.append({
            "id": o.id, "order_no": o.order_no, "invoice_no": o.invoice_no,
            "ship_date": o.ship_date.isoformat() if o.ship_date else None,
            "delivery_cost_cny": float(o.delivery_cost_cny),
            "delivery_cost_usd": float(o.delivery_cost_usd),
            "rate_cny": float(o.rate_cny), "rate_eur": float(o.rate_eur),
            "rate_usd": float(o.rate_usd), "note": o.note,
            "total_rub": total, "items_count": len(items),
            "unrecognized_count": unrecognized,
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


@router.delete("/orders/{order_no}")
async def delete_cost_order(order_no: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(CostOrder).where(CostOrder.order_no == order_no))
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(404, "Not found")
    await db.delete(order)
    await db.commit()
    return {"ok": True}


# ─── Cost Order Items ─────────────────────────────────────────────────────────

@router.get("/orders/{order_no}/items")
async def get_cost_order_items(order_no: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no == order_no)
        .order_by(CostOrderItem.subject, CostOrderItem.article_seller)
    )
    return [
        {
            "id": i.id, "barcode": i.barcode, "subject": i.subject,
            "article_seller": i.article_seller, "qty": i.qty,
            "price_cny": float(i.price_cny), "weight_kg": float(i.weight_kg or 0),
            "area_m2": float(i.area_m2 or 0), "volume_m3": float(i.volume_m3 or 0),
            "cost_rub": float(i.cost_rub or 0), "delivery_rub": float(i.delivery_rub or 0),
            "duty_rub": float(i.duty_rub or 0), "vat_rub": float(i.vat_rub or 0),
            "util_rub": float(i.util_rub or 0), "total_rub": float(i.total_rub or 0),
            "total_cny": float(i.total_cny or 0), "unrecognized": i.unrecognized,
        }
        for i in result.scalars().all()
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
    total_volume = df["volume_m3"].sum() if "volume_m3" in df.columns else 1
    delivery_rub_total = (
        float(order.delivery_cost_cny) * float(order.rate_cny) +
        float(order.delivery_cost_usd) * float(order.rate_usd)
    )

    inserted = 0
    unrecognized = 0

    for _, row in df.iterrows():
        bc = str(row.get("barcode", "")).strip()
        if not bc or bc == "nan":
            continue

        qty = int(row.get("qty", 1) or 1)
        price_cny = Decimal(str(row.get("price_cny", 0) or 0))
        weight_kg = Decimal(str(row.get("weight_kg", 0) or 0))
        area_m2 = Decimal(str(row.get("area_m2", 0) or 0))
        volume_m3 = Decimal(str(row.get("volume_m3", 0) or 0))

        nom = nom_map.get(bc)
        subject = nom.subject if nom else None
        article_seller = nom.article_seller if nom else None
        is_unrecognized = nom is None

        if is_unrecognized:
            unrecognized += 1

        # Cost in RUB per unit
        cost_rub_unit = price_cny * order.rate_cny

        # Delivery per unit (by volume share)
        if total_volume > 0 and volume_m3 > 0:
            vol_share = float(volume_m3) / total_volume
        else:
            vol_share = 1.0 / max(len(df), 1)
        delivery_rub_unit = Decimal(str(delivery_rub_total * vol_share / qty)) if qty > 0 else Decimal(0)

        # Duty calculation
        duty_rub_unit = Decimal(0)
        util_rub_unit = Decimal(0)
        if subject and subject in duty_map:
            rule = duty_map[subject]
            util_rub_unit = rule.util_collect_rub
            if rule.basis == DutyBasis.WEIGHT:
                # евро за кг
                duty_rub_unit = weight_kg * Decimal(str(rule.rate)) * order.rate_eur
            elif rule.basis == DutyBasis.AREA:
                # евро за м²
                duty_rub_unit = area_m2 * Decimal(str(rule.rate)) * order.rate_eur
            elif rule.basis == DutyBasis.INVOICE:
                # % от (себестоимость + доставка/2)
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

    # Volume per unit in m3
    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        df["volume_m3"] = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0) / \
                          pd.to_numeric(df["qty_per_box"], errors="coerce").replace(0, 1)
    else:
        df["volume_m3"] = 0

    df["weight_kg"] = pd.to_numeric(df.get("weight_kg", 0), errors="coerce").fillna(0)
    df["area_m2"] = 0
    df["barcode"] = df.get("barcode", "").astype(str).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", 0), errors="coerce").fillna(0)

    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]


def _normalize_carpet(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize ковры format (Chinese headers)."""
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

    df["barcode"] = df.get("barcode", "").astype(str).str.strip()
    df["qty"] = pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).astype(int)
    df["price_cny"] = pd.to_numeric(df.get("price_cny", 0), errors="coerce").fillna(0)
    df["weight_kg"] = pd.to_numeric(df.get("weight_kg_per_unit", 0), errors="coerce").fillna(0)
    df["area_m2"] = pd.to_numeric(df.get("area_m2", 0), errors="coerce").fillna(0)

    if "volume_box_m3" in df.columns and "qty_per_box" in df.columns:
        df["volume_m3"] = pd.to_numeric(df["volume_box_m3"], errors="coerce").fillna(0) / \
                          pd.to_numeric(df["qty_per_box"], errors="coerce").replace(0, 1)
    else:
        df["volume_m3"] = 0

    return df[["barcode", "qty", "price_cny", "weight_kg", "area_m2", "volume_m3"]]
