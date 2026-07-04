"""
Cost — Payment plan generation from CostOrder data.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CostOrder,
    CostOrderItem,
    LeadTime,
    Order,
    PlannedPayment,
)
from backend.services.cost.helpers import _order_no_to_int, safe_decimal


async def generate_payment_plan(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    tax_rate: float | None = None,
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
        select(CostOrder).where(
            CostOrder.order_no == order_no, CostOrder.project_id == project_id, CostOrder.is_deleted == False
        )
    )
    cost_order = result.scalar_one_or_none()
    if not cost_order:
        return {"error": "CostOrder not found", "status": 404}
    if not cost_order.ship_date:
        return {"error": "Укажите дату отправки (ship_date) перед генерацией плана", "status": 400}

    # 2. Get items (exclude barcode=0)
    items_result = await db.execute(
        select(CostOrderItem).where(
            CostOrderItem.order_no == order_no,
            CostOrderItem.is_deleted == False,
        )
    )
    all_items = items_result.scalars().all()
    items = [i for i in all_items if i.barcode and str(i.barcode).strip() not in ("0", "")]

    if not items:
        return {"error": "Нет позиций в заказе для генерации плана", "status": 400}

    # 3. Calculate totals (Decimal for financial precision)
    order_cny = sum((safe_decimal(i.price_cny) * i.qty for i in items), Decimal(0))
    order_rub = order_cny * safe_decimal(cost_order.rate_cny)
    delivery_rub = safe_decimal(cost_order.delivery_cost_cny) * safe_decimal(cost_order.rate_cny) + safe_decimal(
        cost_order.delivery_cost_usd
    ) * safe_decimal(cost_order.rate_usd)
    duty_rub = sum((safe_decimal(i.duty_rub) * i.qty for i in items), Decimal(0))
    vat_rub = sum((safe_decimal(i.vat_rub) * i.qty for i in items), Decimal(0))
    customs_rub = duty_rub + vat_rub

    # 4. Get lead times
    lt_result = await db.execute(select(LeadTime).where(LeadTime.project_id == project_id))
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
    order_no_int = _order_no_to_int(order_no)
    ord_result = await db.execute(
        select(Order).where(Order.order_no == order_no_int, Order.project_id == project_id, Order.is_deleted == False)
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

    # 6-7. Regenerate planned payments via UPSERT by direction. Hard-delete would
    # 500 on any PaymentFactLink FK and wipe paid_rub/paid_amount/is_paid — instead
    # update rows in place (id survives → fact-links survive) and soft-delete only
    # directions that dropped out of the plan.
    existing = {
        p.direction: p
        for p in (
            await db.execute(
                select(PlannedPayment).where(
                    PlannedPayment.order_no == order_no_int,
                    PlannedPayment.project_id == project_id,
                    PlannedPayment.is_deleted == False,  # noqa: E712
                )
            )
        ).scalars()
    }

    desired: list[dict] = []
    if order_cny > 0:
        desired.append(
            {
                "direction": "ЗАКАЗ",
                "pay_date": pay_date_order,
                "amount": Decimal(str(round(order_cny, 2))),
                "currency": "CNY",
                "fx_rate": cost_order.rate_cny,
                "amount_rub": Decimal(str(round(order_rub, 2))),
            }
        )
    if delivery_rub > 0:
        desired.append(
            {
                "direction": "ДОСТАВКА",
                "pay_date": pay_date_delivery,
                "amount": cost_order.delivery_cost_cny + cost_order.delivery_cost_usd,
                "currency": "CNY/USD",
                "fx_rate": None,
                "amount_rub": Decimal(str(round(delivery_rub, 2))),
            }
        )
    if customs_rub > 0:
        desired.append(
            {
                "direction": "ТАМОЖНЯ",
                "pay_date": pay_date_customs,
                "amount": Decimal(str(round(customs_rub, 2))),
                "currency": "RUB",
                "fx_rate": Decimal("1"),
                "amount_rub": Decimal(str(round(customs_rub, 2))),
            }
        )

    desired_dirs = {d["direction"] for d in desired}
    for d in desired:
        row = existing.get(d["direction"])
        if row is not None:
            row.pay_date = d["pay_date"]
            row.amount = d["amount"]
            row.currency = d["currency"]
            row.fx_rate = d["fx_rate"]
            row.amount_rub = d["amount_rub"]
            # amount may have changed — recompute paid state (mirrors update_payment_paid_amount)
            row.is_paid = row.paid_rub >= d["amount_rub"]
        else:
            db.add(
                PlannedPayment(
                    project_id=project_id,
                    order_no=order_no_int,
                    direction=d["direction"],
                    pay_date=d["pay_date"],
                    amount=d["amount"],
                    currency=d["currency"],
                    fx_rate=d["fx_rate"],
                    amount_rub=d["amount_rub"],
                    is_paid=False,
                )
            )

    # Directions no longer in the plan → soft-delete (keeps fact-links intact)
    for direction, row in existing.items():
        if direction not in desired_dirs:
            row.soft_delete()

    await db.commit()

    return {
        "ok": True,
        "order_no": order_no,
        "payments_created": len(desired),
        "plan": {
            "order_cny": float(round(order_cny, 2)),
            "order_rub": float(round(order_rub, 2)),
            "delivery_rub": float(round(delivery_rub, 2)),
            "customs_rub": float(round(customs_rub, 2)),
            "arrival_date": str(arrival_date),
            "pay_date_order": str(pay_date_order),
            "pay_date_delivery": str(pay_date_delivery),
            "pay_date_customs": str(pay_date_customs),
        },
    }
