"""
Cost — Cost Order Items (get, upload Excel, recalculate).
"""

from decimal import Decimal

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.etl.cost_parsers import detect_and_normalize_excel
from backend.models import CostOrder, CostOrderItem, DutyBasis, DutyRule, Nomenclature
from backend.services.cost.helpers import DEFAULT_VAT_RATE, safe_decimal


async def get_cost_order_items(db: AsyncSession, project_id: int, order_no: str):
    """Get items with nomenclature lookup for article_wb."""
    # Verify order belongs to project_id (CostOrderItem has no project_id)
    order_check = await db.execute(
        select(CostOrder.id).where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,
        )
    )
    if not order_check.scalar_one_or_none():
        return [], {}

    result = await db.execute(
        select(CostOrderItem)
        .where(CostOrderItem.order_no == order_no)
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


async def upload_order_items(
    db: AsyncSession, project_id: int, order_no: str, data: bytes, vat_rate: Decimal | None = None
):
    """Upload Excel items, calculate cost/duty/delivery per unit. Returns (inserted, unrecognized)."""
    # Check order exists
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no, CostOrder.project_id == project_id, CostOrder.is_deleted == False
        )
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
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(
        select(DutyRule).where(DutyRule.project_id == project_id, DutyRule.is_deleted == False)
    )
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Calculate totals for delivery split
    if "volume_m3" in df.columns and "qty" in df.columns:
        vol_series = pd.to_numeric(df["volume_m3"], errors="coerce").fillna(0)
        qty_series = pd.to_numeric(df["qty"], errors="coerce").fillna(1).astype(int)
        total_volume = float((vol_series * qty_series).sum())
    else:
        total_volume = 0.0
    total_qty_all = (
        int(pd.to_numeric(df.get("qty", 1), errors="coerce").fillna(1).sum()) if "qty" in df.columns else len(df)
    )
    delivery_rub_total = float(order.delivery_cost_cny) * float(order.rate_cny) + float(
        order.delivery_cost_usd
    ) * float(order.rate_usd)

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


async def recalculate_order_items(db: AsyncSession, project_id: int, order_no: str, vat_rate: Decimal | None = None):
    """Recalculate cost/duty/vat/delivery for existing items in an order."""
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no, CostOrder.project_id == project_id, CostOrder.is_deleted == False
        )
    )
    order = result.scalar_one_or_none()
    if not order:
        return None, f"Заказ {order_no} не найден"

    # Load items
    items_result = await db.execute(select(CostOrderItem).where(CostOrderItem.order_no == order_no))
    items = items_result.scalars().all()
    if not items:
        return 0, None

    # Load nomenclature and duty rules
    nom_result = await db.execute(select(Nomenclature).where(Nomenclature.project_id == project_id))
    nom_map = {n.barcode: n for n in nom_result.scalars().all()}

    duty_result = await db.execute(
        select(DutyRule).where(DutyRule.project_id == project_id, DutyRule.is_deleted == False)
    )
    duty_map = {r.subject: r for r in duty_result.scalars().all()}

    # Calculate total volume for delivery split
    total_volume = sum(float(item.volume_m3 or 0) * int(item.qty or 1) for item in items)
    total_qty_all = sum(int(item.qty or 1) for item in items)
    delivery_rub_total = float(order.delivery_cost_cny) * float(order.rate_cny) + float(
        order.delivery_cost_usd
    ) * float(order.rate_usd)

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
