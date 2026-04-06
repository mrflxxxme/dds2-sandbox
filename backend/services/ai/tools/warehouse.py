"""
Warehouse domain tools: stock info, warehouse need, warehouse stocks, logistics history.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.ai.tools.common import _json


async def get_stock_info(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.stock_forecast_service import get_stock_analytics

    mode = "wb_rf_transit" if inp.get("include_rf_stocks", True) else "wb"
    result = await get_stock_analytics(
        db,
        project_id,
        brand_filter=brand,
        article_filter=inp.get("search"),
        mode=mode,
    )
    articles = result.get("articles", [])
    traffic = result.get("traffic_light_counts", {})

    priority = {"red": 0, "orange": 1, "yellow": 2, "green": 3}
    sorted_articles = sorted(
        articles, key=lambda a: (priority.get(a.get("traffic_light", "green"), 3), -a.get("orders_30d", 0))
    )

    return _json(
        {
            "data_date": result.get("data_date"),
            "total_articles": len(articles),
            "traffic_light_counts": traffic,
            "articles": sorted_articles,
        }
    )


async def get_warehouse_need(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.warehouse_stock_service import get_warehouse_need as _get_warehouse_need

    result = await _get_warehouse_need(
        db,
        project_id,
        supply_days=inp.get("supply_days", 14),
        analysis_days=14,
        mode="actual",
    )
    warehouses = result.get("warehouses", [])
    wb_summary = []
    for wh in warehouses:
        articles = wh.get("articles", [])
        need_articles = [a for a in articles if isinstance(a, dict) and a.get("need", 0) > 0]
        if need_articles:
            wb_summary.append(
                {
                    "warehouse": wh.get("name", ""),
                    "total_need_units": sum(a.get("need", 0) for a in need_articles),
                    "articles_count": len(need_articles),
                    "top_articles": sorted(need_articles, key=lambda a: a.get("need", 0), reverse=True)[:5],
                }
            )

    rf_warehouses = result.get("rf_warehouses", [])
    enriched = result.get("articles", [])

    # Load cost_price and barcode per nm_id
    cost_barcode_map: dict[int, dict] = {}
    try:
        from sqlalchemy import func, select

        from backend.models.cost import Nomenclature
        from backend.models.warehouse import WarehouseStock

        nm_ids = [a.get("nm_id") for a in enriched if a.get("nm_id")]
        if nm_ids:
            cb_result = await db.execute(
                select(
                    Nomenclature.article_wb,
                    func.max(WarehouseStock.barcode).label("barcode"),
                    func.avg(WarehouseStock.cost_price).label("cost_price"),
                )
                .join(Nomenclature, Nomenclature.id == WarehouseStock.nomenclature_id)
                .where(
                    WarehouseStock.project_id == project_id,
                    Nomenclature.article_wb.in_(nm_ids),
                    WarehouseStock.quantity > 0,
                )
                .group_by(Nomenclature.article_wb)
            )
            for row in cb_result:
                if row.article_wb:
                    cost_barcode_map[int(row.article_wb)] = {
                        "barcode": row.barcode or "",
                        "cost_price": round(float(row.cost_price or 0), 2),
                    }
    except Exception:  # noqa: S110
        pass  # graceful: cost/barcode optional

    articles_with_rf = []
    for art in enriched:
        if art.get("can_send", 0) > 0 or art.get("deficit", 0) > 0:
            nm_id = art.get("nm_id")
            cb = cost_barcode_map.get(nm_id, {})
            articles_with_rf.append(
                {
                    "nm_id": nm_id,
                    "vendor_code": art.get("vendor_code", ""),
                    "brand": art.get("brand", ""),
                    "subject": art.get("subject", ""),
                    "barcode": cb.get("barcode", ""),
                    "cost_price": cb.get("cost_price", 0),
                    "total_need": art.get("total_need", 0),
                    "stocks_wb": art.get("stocks_wb", 0),
                    "rf_stocks": art.get("rf_stocks", {}),
                    "in_assembly": art.get("in_assembly", 0),
                    "in_transit": art.get("in_transit", 0),
                    "can_send": art.get("can_send", 0),
                    "deficit": art.get("deficit", 0),
                }
            )

    summary = result.get("summary", {})

    return _json(
        {
            "supply_days": inp.get("supply_days", 14),
            "wb_warehouses": wb_summary,
            "rf_warehouses": rf_warehouses,
            "articles": articles_with_rf,
            "summary": summary,
        }
    )


async def get_warehouse_stocks(db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict) -> str:
    from backend.services.warehouse_stock_service import get_warehouse_stocks as _get_warehouse_stocks

    result = await _get_warehouse_stocks(db, project_id)
    warehouses = result.get("warehouses", [])
    sorted_wh = sorted(warehouses, key=lambda w: w.get("total_qty", 0), reverse=True)
    return _json(
        {
            "total_warehouses": len(sorted_wh),
            "total_qty": sum(w.get("total_qty", 0) for w in sorted_wh),
            "warehouses": sorted_wh,
        }
    )


async def get_logistics_history(
    db: AsyncSession, project_id: int, tax_rate: float, brand: str | None, inp: dict
) -> str:
    """Get shipping history: assembly requests with costs, pallets, dates, warehouses."""
    from backend.models.assembly import AssemblyRequest, AssemblyStatus
    from backend.models.warehouse import Warehouse

    statuses = inp.get("statuses")
    if statuses:
        valid = []
        for s in statuses:
            try:  # noqa: SIM105
                valid.append(AssemblyStatus(s.upper() if isinstance(s, str) else s))
            except (ValueError, AttributeError):  # noqa: S110
                pass
        status_filter = valid if valid else None
    else:
        status_filter = None
    if not status_filter:
        status_filter = [
            AssemblyStatus.SHIPPED,
            AssemblyStatus.DELIVERED,
            AssemblyStatus.VEHICLE_ASSIGNED,
            AssemblyStatus.READY,
            AssemblyStatus.IN_PROGRESS,
            AssemblyStatus.PENDING,
        ]

    from sqlalchemy import func, select
    from sqlalchemy.orm import selectinload

    result = await db.execute(
        select(AssemblyRequest)
        .options(
            selectinload(AssemblyRequest.items),
            selectinload(AssemblyRequest.warehouse),
            selectinload(AssemblyRequest.wb_fbo_supply),
        )
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            AssemblyRequest.status.in_(status_filter),
        )
        .order_by(AssemblyRequest.created_at.desc())
        .limit(100)
    )
    requests = result.scalars().all()

    cost_stats_result = await db.execute(
        select(
            Warehouse.name.label("warehouse_name"),
            func.count(AssemblyRequest.id).label("shipments"),
            func.avg(AssemblyRequest.pickup_cost).label("avg_cost"),
            func.sum(AssemblyRequest.pallets_count).label("total_pallets"),
        )
        .join(Warehouse, Warehouse.id == AssemblyRequest.warehouse_id)
        .where(
            AssemblyRequest.project_id == project_id,
            AssemblyRequest.is_deleted.is_(False),
            Warehouse.is_deleted.is_(False),
            AssemblyRequest.pickup_cost.isnot(None),
            AssemblyRequest.pickup_cost > 0,
        )
        .group_by(Warehouse.name)
    )
    cost_stats = [
        {
            "warehouse": row.warehouse_name,
            "shipments": row.shipments,
            "avg_pickup_cost": round(float(row.avg_cost or 0), 0),
            "total_pallets": int(row.total_pallets or 0),
        }
        for row in cost_stats_result
    ]

    shipments = []
    for req in requests:
        total_qty = sum(item.quantity for item in req.items) if req.items else 0
        wh_name = req.warehouse.name if req.warehouse else ""
        destination = ""
        try:
            if req.wb_fbo_supply:
                destination = req.wb_fbo_supply.warehouse_name or ""
        except Exception:  # noqa: S110
            pass

        shipments.append(
            {
                "id": req.id,
                "number": req.number,
                "status": req.status.value if hasattr(req.status, "value") else str(req.status),
                "warehouse": wh_name,
                "destination": destination,
                "pallets_count": req.pallets_count or 0,
                "pallet_weight_kg": float(req.pallet_weight_kg or 0),
                "pickup_cost": float(req.pickup_cost or 0),
                "delivery_date": req.delivery_date.isoformat() if req.delivery_date else None,
                "shipped_at": req.shipped_at.isoformat() if hasattr(req, "shipped_at") and req.shipped_at else None,
                "created_at": req.created_at.isoformat() if req.created_at else None,
                "items_count": len(req.items) if req.items else 0,
                "total_qty": total_qty,
            }
        )

    return _json(
        {
            "total_shipments": len(shipments),
            "shipments": shipments,
            "cost_stats_by_warehouse": cost_stats,
        }
    )
