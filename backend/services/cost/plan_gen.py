"""
Cost — Payment plan generation from CostOrder data.
"""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    CostOrder,
    CostOrderItem,
    LeadTime,
    Order,
    PlannedPayment,
)
from backend.services.cost.helpers import _order_no_to_int, safe_float


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
    items_result = await db.execute(select(CostOrderItem).where(CostOrderItem.order_no == order_no))
    all_items = items_result.scalars().all()
    items = [i for i in all_items if i.barcode and str(i.barcode).strip() not in ("0", "")]

    if not items:
        return {"error": "Нет позиций в заказе для генерации плана", "status": 400}

    # 3. Calculate totals
    order_cny = sum(safe_float(i.price_cny) * i.qty for i in items)
    order_rub = order_cny * float(cost_order.rate_cny)
    delivery_rub = float(cost_order.delivery_cost_cny) * float(cost_order.rate_cny) + float(
        cost_order.delivery_cost_usd
    ) * float(cost_order.rate_usd)
    duty_rub = sum(safe_float(i.duty_rub) * i.qty for i in items)
    vat_rub = sum(safe_float(i.vat_rub) * i.qty for i in items)
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
        payments.append(
            PlannedPayment(
                project_id=project_id,
                order_no=order_no_int,
                direction="ЗАКАЗ",
                pay_date=pay_date_order,
                amount=Decimal(str(round(order_cny, 2))),
                currency="CNY",
                fx_rate=cost_order.rate_cny,
                amount_rub=Decimal(str(round(order_rub, 2))),
                is_paid=False,
            )
        )

    if delivery_rub > 0:
        payments.append(
            PlannedPayment(
                project_id=project_id,
                order_no=order_no_int,
                direction="ДОСТАВКА",
                pay_date=pay_date_delivery,
                amount=cost_order.delivery_cost_cny + cost_order.delivery_cost_usd,
                currency="CNY/USD",
                fx_rate=None,
                amount_rub=Decimal(str(round(delivery_rub, 2))),
                is_paid=False,
            )
        )

    if customs_rub > 0:
        payments.append(
            PlannedPayment(
                project_id=project_id,
                order_no=order_no_int,
                direction="ТАМОЖНЯ",
                pay_date=pay_date_customs,
                amount=Decimal(str(round(customs_rub, 2))),
                currency="RUB",
                fx_rate=Decimal("1"),
                amount_rub=Decimal(str(round(customs_rub, 2))),
                is_paid=False,
            )
        )

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
