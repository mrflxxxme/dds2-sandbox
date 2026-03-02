"""
Cost service — business logic for cost calculation, plan generation, and Excel normalization.

Extracted from routers/cost.py to enable reuse and testing.
"""

import io
import math
from datetime import timedelta
from decimal import Decimal
from typing import Optional

import pandas as pd
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CostOrder, CostOrderItem, Nomenclature, DutyRule, DutyBasis,
    LeadTime, Order, PlannedPayment,
)


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


# ─── Excel normalization ─────────────────────────────────────────────────────

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


def detect_and_normalize_excel(data: bytes) -> pd.DataFrame:
    """Detect Excel format by columns and normalize to standard schema."""
    df = pd.read_excel(io.BytesIO(data))
    cols = [str(c).strip() for c in df.columns]

    if "штрихкод" in cols or "штрихкод" in [c.lower() for c in cols]:
        return normalize_divandek(df)
    elif "条码" in cols:
        return normalize_carpet(df)
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
