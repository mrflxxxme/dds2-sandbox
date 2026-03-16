"""
Cost — Cost Orders CRUD with aggregation.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import CostOrder, CostOrderItem, PlannedPayment
from backend.services.cost.helpers import _order_no_to_int, safe_float, auto_link_customs_dt
from backend.cache import invalidate_cache


async def get_cost_orders(db: AsyncSession, project_id: int, limit: int = 500, offset: int = 0):
    """Get cost orders with aggregated item totals."""
    result = await db.execute(
        select(CostOrder)
        .where(CostOrder.project_id == project_id, CostOrder.is_deleted == False)
        .order_by(CostOrder.created_at.desc())
        .limit(limit).offset(offset)
    )
    orders = result.scalars().all()
    if not orders:
        return []

    order_nos = [o.order_no for o in orders]

    # Batch-load all items for all orders (fixes N+1)
    all_items_result = await db.execute(
        select(CostOrderItem).where(CostOrderItem.order_no.in_(order_nos))
    )
    all_items_list = all_items_result.scalars().all()
    items_by_order: dict[str, list] = {}
    for i in all_items_list:
        if i.barcode and str(i.barcode).strip() not in ("0", ""):
            items_by_order.setdefault(i.order_no, []).append(i)

    # Batch-load planned payments existence (fixes N+1)
    order_no_ints = []
    order_no_map = {}
    for o in orders:
        try:
            oint = _order_no_to_int(o.order_no)
            order_no_ints.append(oint)
            order_no_map[o.order_no] = oint
        except (ValueError, TypeError):
            pass

    plans_set: set[int] = set()
    if order_no_ints:
        from sqlalchemy import func as sa_func
        pp_result = await db.execute(
            select(PlannedPayment.order_no)
            .where(
                PlannedPayment.order_no.in_(order_no_ints),
                PlannedPayment.project_id == project_id,
                PlannedPayment.is_deleted == False,
            )
            .group_by(PlannedPayment.order_no)
        )
        plans_set = {r[0] for r in pp_result}

    out = []
    for o in orders:
        items = items_by_order.get(o.order_no, [])

        total_qty = sum(i.qty for i in items)
        total = sum(safe_float(i.total_rub) * i.qty for i in items)
        total_cost = sum(safe_float(i.cost_rub) * i.qty for i in items)
        total_delivery = sum(safe_float(i.delivery_rub) * i.qty for i in items)
        total_duty = sum(safe_float(i.duty_rub) * i.qty for i in items)
        total_vat = sum(safe_float(i.vat_rub) * i.qty for i in items)
        total_util = sum(safe_float(i.util_rub) * i.qty for i in items)
        unrecognized = sum(1 for i in items if i.unrecognized)

        oint = order_no_map.get(o.order_no)
        has_plan = oint in plans_set if oint is not None else False

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

    await invalidate_cache("reports")
    import asyncio
    from backend.scheduler import prewarm_project
    asyncio.create_task(prewarm_project(project_id))
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

    # Auto-regenerate planned payments to keep them in sync with cost order data
    if order.ship_date:
        try:
            from backend.services.cost.plan_gen import generate_payment_plan
            await generate_payment_plan(db, project_id, final_order_no)
        except Exception:
            pass  # Don't fail the update if plan gen fails (e.g. no items yet)

    await invalidate_cache("reports")
    import asyncio
    from backend.scheduler import prewarm_project
    asyncio.create_task(prewarm_project(project_id))
    return {"ok": True, "order_no": final_order_no}, None


async def delete_cost_order(db: AsyncSession, project_id: int, order_no: str):
    result = await db.execute(
        select(CostOrder).where(CostOrder.order_no == order_no, CostOrder.project_id == project_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        return None
    order.soft_delete()
    await db.commit()
    await invalidate_cache("reports")
    import asyncio
    from backend.scheduler import prewarm_project
    asyncio.create_task(prewarm_project(project_id))
    return True
